"""
Tenant and API key management.

How it works:
  1. A tenant (your customer) is created via POST /admin/tenants
  2. One or more API keys are generated for that tenant via POST /admin/tenants/{id}/keys
  3. Keys are stored hashed (SHA-256) — we never store the raw key
  4. On every request, the raw key is hashed and looked up in the DB
  5. The key is returned to the customer ONCE at creation time — just like Stripe/OpenAI

Key format:  rk-<environment>-<32 random hex chars>
Example:     rk-live-a3f8c2d1e4b7a9f0c2e5d8b1a4f7c0e3
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, String, Float, Boolean, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from fastapi import HTTPException

from logger.models import Base


# ---------------------------------------------------------------------------
# DB models
# ---------------------------------------------------------------------------

class Tenant(Base):
    __tablename__ = "tenants"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    name          = Column(String(128), nullable=False)
    email         = Column(String(256), nullable=False, unique=True)
    company       = Column(String(128), nullable=True)

    # Routing config per tenant
    budget_usd         = Column(Float, default=0.01)       # per-request budget
    provider_preference = Column(String(32), default="anthropic")
    monthly_limit_usd  = Column(Float, nullable=True)      # optional hard monthly cap

    is_active     = Column(Boolean, default=True)
    plan          = Column(String(32), default="free")     # free | starter | pro | enterprise
    notes         = Column(Text, nullable=True)


class APIKey(Base):
    __tablename__ = "api_keys"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tenant_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    name          = Column(String(128), default="default")   # e.g. "production", "staging"

    key_hash      = Column(String(64), nullable=False, unique=True)  # SHA-256 of raw key
    key_prefix    = Column(String(16), nullable=False)               # first 8 chars for display
    environment   = Column(String(8), default="live")                # live | test

    is_active     = Column(Boolean, default=True)
    last_used_at  = Column(DateTime(timezone=True), nullable=True)
    request_count = Column(Integer, default=0)
    revoked_at    = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Key generation helpers
# ---------------------------------------------------------------------------

def _generate_raw_key(environment: str = "live") -> str:
    """
    Generate a new raw API key.
    Format: rk-<env>-<32 random hex chars>
    The 'rk' prefix makes it easy to identify in logs/config.
    """
    random_part = secrets.token_hex(16)   # 32 hex chars = 128 bits of entropy
    return f"rk-{environment}-{random_part}"


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw key — this is what we store in the DB."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _prefix(raw_key: str) -> str:
    """First 10 chars for display purposes (e.g. 'rk-live-a3')."""
    return raw_key[:12] + "..."


# ---------------------------------------------------------------------------
# Auth service
# ---------------------------------------------------------------------------

class AuthService:

    def __init__(self, session_factory):
        self.session_factory = session_factory

    # ── Tenant management ──────────────────────────────────────────────────

    async def create_tenant(
        self,
        name: str,
        email: str,
        company: str = None,
        budget_usd: float = 0.01,
        plan: str = "free",
        provider_preference: str = "anthropic",
    ) -> Tenant:
        tenant = Tenant(
            name=name,
            email=email,
            company=company,
            budget_usd=budget_usd,
            plan=plan,
            provider_preference=provider_preference,
        )
        async with self.session_factory() as session:
            session.add(tenant)
            await session.commit()
            await session.refresh(tenant)
        return tenant

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            return result.scalar_one_or_none()

    async def list_tenants(self) -> list[Tenant]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Tenant).order_by(Tenant.created_at.desc())
            )
            return result.scalars().all()

    # ── Key management ─────────────────────────────────────────────────────

    async def create_api_key(
        self,
        tenant_id: str,
        name: str = "default",
        environment: str = "live",
    ) -> dict:
        """
        Create a new API key for a tenant.
        Returns the raw key ONCE — it is never stored and cannot be retrieved again.
        The caller must show it to the customer immediately.
        """
        raw_key = _generate_raw_key(environment)
        key_hash = _hash_key(raw_key)
        prefix = _prefix(raw_key)

        api_key = APIKey(
            tenant_id=uuid.UUID(tenant_id),
            name=name,
            key_hash=key_hash,
            key_prefix=prefix,
            environment=environment,
        )

        async with self.session_factory() as session:
            session.add(api_key)
            await session.commit()
            await session.refresh(api_key)

        return {
            "key_id":     str(api_key.id),
            "raw_key":    raw_key,        # shown ONCE — store it now
            "prefix":     prefix,
            "tenant_id":  tenant_id,
            "name":       name,
            "environment": environment,
            "created_at": api_key.created_at.isoformat(),
            "warning":    "Save this key now. It will not be shown again.",
        }

    async def list_keys(self, tenant_id: str) -> list[dict]:
        """List all keys for a tenant — raw key never returned."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(APIKey)
                .where(APIKey.tenant_id == uuid.UUID(tenant_id))
                .order_by(APIKey.created_at.desc())
            )
            keys = result.scalars().all()
        return [
            {
                "key_id":      str(k.id),
                "prefix":      k.key_prefix,
                "name":        k.name,
                "environment": k.environment,
                "is_active":   k.is_active,
                "created_at":  k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "request_count": k.request_count,
            }
            for k in keys
        ]

    async def revoke_key(self, key_id: str, tenant_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(APIKey).where(
                    APIKey.id == uuid.UUID(key_id),
                    APIKey.tenant_id == uuid.UUID(tenant_id),
                )
            )
            key = result.scalar_one_or_none()
            if not key:
                return False
            key.is_active = False
            key.revoked_at = datetime.now(timezone.utc)
            await session.commit()
        return True

    # ── Authentication (called on every request) ───────────────────────────

    async def authenticate(self, raw_key: str) -> dict:
        """
        Validate a raw API key and return tenant context.
        Called on every request — must be fast.
        Uses DB lookup by hash (indexed) — typically < 2ms.
        """
        if not raw_key or not raw_key.startswith("rk-"):
            raise HTTPException(status_code=401, detail="Invalid API key format")

        key_hash = _hash_key(raw_key)

        async with self.session_factory() as session:
            # Join api_keys → tenants in one query
            result = await session.execute(
                text("""
                    SELECT
                        ak.id          AS key_id,
                        ak.tenant_id,
                        ak.is_active   AS key_active,
                        ak.environment,
                        t.name         AS tenant_name,
                        t.is_active    AS tenant_active,
                        t.budget_usd,
                        t.provider_preference,
                        t.plan,
                        t.monthly_limit_usd
                    FROM api_keys ak
                    JOIN tenants t ON t.id = ak.tenant_id
                    WHERE ak.key_hash = :hash
                    LIMIT 1
                """),
                {"hash": key_hash},
            )
            row = result.fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")

        if not row.key_active:
            raise HTTPException(status_code=401, detail="API key has been revoked")

        if not row.tenant_active:
            raise HTTPException(status_code=403, detail="Tenant account is inactive")

        # Fire-and-forget usage update (don't await — keep hot path fast)
        # In production: use Redis incr + batch flush to DB
        return {
            "key_id":              str(row.key_id),
            "tenant_id":           str(row.tenant_id),
            "tenant_name":         row.tenant_name,
            "environment":         row.environment,
            "budget_usd":          row.budget_usd,
            "provider_preference": row.provider_preference,
            "plan":                row.plan,
            "monthly_limit_usd":   row.monthly_limit_usd,
        }

    async def record_key_usage(self, key_id: str):
        """Bump request count and last_used_at. Called async after response."""
        async with self.session_factory() as session:
            await session.execute(
                text("""
                    UPDATE api_keys
                    SET request_count = request_count + 1,
                        last_used_at  = NOW()
                    WHERE id = :kid
                """),
                {"kid": key_id},
            )
            await session.commit()
