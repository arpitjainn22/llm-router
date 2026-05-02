"""
Logging layer — the most strategically important file in Phase 1.

Every row written here is a future training example for Phase 2.
Schema is designed so that running:
    SELECT * FROM request_logs WHERE quality_score IS NOT NULL
gives you a ready-to-use XGBoost training dataset.

Columns map directly to QueryFeatures fields so there's zero
transformation needed between log → training data.
"""

import uuid
import time
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, Text, Index, text
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
import structlog

from gateway.config import get_settings

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


def get_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        echo=(settings.environment == "development"),
    )


def get_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Request log table — core Phase 2 training dataset
# ---------------------------------------------------------------------------

class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    # Customer context
    tenant_id = Column(String(64), nullable=False, index=True)
    api_key_hash = Column(String(64), nullable=False)
    session_id = Column(String(64), nullable=True)
    turn_index = Column(Integer, default=0)

    # Raw request
    prompt_text = Column(Text, nullable=False)      # store for Phase 2 fine-tuning
    prompt_hash = Column(String(64), nullable=False) # sha256 for dedup
    system_prompt = Column(Text, nullable=True)
    message_count = Column(Integer, default=1)

    # === Feature vector — Phase 2 training columns ===
    token_count_est = Column(Integer, nullable=False)
    char_count = Column(Integer, nullable=False)
    has_code_block = Column(Boolean, default=False)
    has_code_keywords = Column(Boolean, default=False)
    constraint_count = Column(Integer, default=0)
    question_count = Column(Integer, default=0)
    is_specialist_domain = Column(Boolean, default=False)
    is_factual_lookup = Column(Boolean, default=False)
    is_creative = Column(Boolean, default=False)
    is_code_task = Column(Boolean, default=False)
    complexity_score = Column(Float, nullable=False)

    # Routing decision
    routed_model = Column(String(64), nullable=False, index=True)
    routed_tier = Column(Integer, nullable=False)
    routed_provider = Column(String(32), nullable=False)
    is_shadow_route = Column(Boolean, default=False)
    routing_reasoning = Column(Text, nullable=True)
    cost_budget_usd = Column(Float, nullable=True)

    # Response metadata
    response_model = Column(String(64), nullable=True)  # actual model that responded
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    cost_usd = Column(Float, nullable=True)
    provider_error = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)

    # === Quality signal — the Phase 2 training label ===
    # Filled in asynchronously by the quality scorer
    quality_score = Column(Float, nullable=True)        # 0.0–1.0 from LLM-as-judge
    user_feedback = Column(Integer, nullable=True)      # 1=thumbs up, -1=thumbs down
    quality_scored_at = Column(DateTime(timezone=True), nullable=True)
    quality_scorer_model = Column(String(64), nullable=True)

    # Metadata
    extra = Column(JSONB, nullable=True)                # any additional context

    __table_args__ = (
        Index("ix_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_logs_training_ready", "quality_score", "complexity_score"),
        Index("ix_logs_shadow", "is_shadow_route", "created_at"),
    )


# ---------------------------------------------------------------------------
# Cost summary table — drives the dashboard
# ---------------------------------------------------------------------------

class DailyCostSummary(Base):
    __tablename__ = "daily_cost_summary"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    tenant_id = Column(String(64), nullable=False, index=True)
    model_id = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False)

    request_count = Column(Integer, default=0)
    total_input_tokens = Column(Integer, default=0)
    total_output_tokens = Column(Integer, default=0)
    total_cost_usd = Column(Float, default=0.0)
    avg_latency_ms = Column(Float, nullable=True)
    avg_quality_score = Column(Float, nullable=True)
    error_count = Column(Integer, default=0)

    # Savings vs always routing to GPT-4o (Phase 2 selling point)
    baseline_cost_usd = Column(Float, nullable=True)
    saved_usd = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_cost_tenant_date", "tenant_id", "date"),
    )


# ---------------------------------------------------------------------------
# Async logger — called by the gateway on every request
# ---------------------------------------------------------------------------

import hashlib
from classifier.rule_based import RoutingDecision


class RequestLogger:
    """
    Async logger that writes request logs without blocking the gateway.
    Designed to be called with asyncio.create_task() so it never adds
    latency to the customer response path.
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.settings = get_settings()

    def _compute_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate actual cost from token counts and model pricing."""
        s = self.settings
        pricing = {
            "claude-haiku-3":    (s.haiku_input_price,       s.haiku_output_price),
            "claude-sonnet-3-5": (s.sonnet_input_price,      s.sonnet_output_price),
            "claude-opus-3":     (s.sonnet_input_price * 5,  s.sonnet_output_price * 5),
            "gpt-4o-mini":       (s.gpt4o_mini_input_price,  s.gpt4o_mini_output_price),
            "gpt-4o":            (s.gpt4o_input_price,       s.gpt4o_output_price),
            "gemini-1.5-flash":  (s.gemini_flash_input_price, s.gemini_flash_output_price),
        }
        if model_id not in pricing:
            return 0.0
        in_price, out_price = pricing[model_id]
        return (input_tokens * in_price + output_tokens * out_price) / 1000

    def _baseline_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Cost if we had always used GPT-4o — the 'without router' baseline."""
        s = self.settings
        return (input_tokens * s.gpt4o_input_price + output_tokens * s.gpt4o_output_price) / 1000

    async def log_request(
        self,
        tenant_id: str,
        api_key_hash: str,
        prompt: str,
        decision: RoutingDecision,
        session_id: str = None,
        system_prompt: str = None,
        message_count: int = 1,
        # Response fields (filled after LLM responds)
        input_tokens: int = None,
        output_tokens: int = None,
        latency_ms: float = None,
        provider_error: bool = False,
        error_message: str = None,
        extra: dict = None,
    ) -> str:
        """
        Write a request log row. Returns the log ID for later update
        (quality scoring is written back via update_quality_score).
        """
        f = decision.features
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

        cost_usd = None
        baseline = None
        if input_tokens and output_tokens:
            cost_usd = self._compute_cost(decision.model_id, input_tokens, output_tokens)
            baseline = self._baseline_cost(input_tokens, output_tokens)

        record = RequestLog(
            tenant_id=tenant_id,
            api_key_hash=api_key_hash,
            session_id=session_id,
            turn_index=f.turn_index if f else 0,
            prompt_text=prompt[:4000],   # cap at 4K chars to keep DB sane
            prompt_hash=prompt_hash,
            system_prompt=system_prompt,
            message_count=message_count,
            # Features
            token_count_est=f.token_count_est if f else 0,
            char_count=f.char_count if f else 0,
            has_code_block=f.has_code_block if f else False,
            has_code_keywords=f.has_code_keywords if f else False,
            constraint_count=f.constraint_count if f else 0,
            question_count=f.question_count if f else 0,
            is_specialist_domain=f.is_specialist_domain if f else False,
            is_factual_lookup=f.is_factual_lookup if f else False,
            is_creative=f.is_creative if f else False,
            is_code_task=f.is_code_task if f else False,
            complexity_score=decision.complexity_score,
            # Routing
            routed_model=decision.model_id,
            routed_tier=decision.tier,
            routed_provider=decision.provider,
            is_shadow_route=decision.is_shadow,
            routing_reasoning=decision.reasoning,
            cost_budget_usd=f.cost_budget_usd if f else None,
            # Response
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            provider_error=provider_error,
            error_message=error_message,
            extra=extra,
        )

        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            log_id = str(record.id)

        log.info(
            "request_logged",
            tenant=tenant_id,
            model=decision.model_id,
            tier=decision.tier,
            score=f"{decision.complexity_score:.1f}",
            shadow=decision.is_shadow,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
            cost_usd=f"{cost_usd:.5f}" if cost_usd else None,
            saved_usd=f"{(baseline - cost_usd):.5f}" if (baseline and cost_usd) else None,
            latency_ms=f"{latency_ms:.0f}" if latency_ms else None,
        )
        return log_id

    async def update_quality_score(
        self,
        log_id: str,
        quality_score: float,
        scorer_model: str,
        user_feedback: int = None,
    ):
        """
        Called asynchronously by the quality scorer (Phase 2).
        Backfills the quality label onto an existing log row.
        This is what turns a request log into a training example.
        """
        async with self.session_factory() as session:
            result = await session.execute(
                text("SELECT id FROM request_logs WHERE id = :id"),
                {"id": log_id}
            )
            if not result.fetchone():
                log.warning("quality_score_update_miss", log_id=log_id)
                return

            await session.execute(
                text("""
                    UPDATE request_logs
                    SET quality_score = :score,
                        quality_scorer_model = :scorer,
                        quality_scored_at = NOW(),
                        user_feedback = :feedback
                    WHERE id = :id
                """),
                {
                    "score": quality_score,
                    "scorer": scorer_model,
                    "feedback": user_feedback,
                    "id": log_id,
                }
            )
            await session.commit()

        log.info("quality_score_written", log_id=log_id, score=quality_score)
