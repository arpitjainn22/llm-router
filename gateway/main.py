"""
RouteEase — Intelligent LLM Routing Gateway

OpenAI-compatible API endpoint. Customers point their existing
OpenAI SDK at this server by changing just the base_url:

    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        base_url="https://api.routease.io/v1",
        api_key="rk-live-your-routease-key",
    )

No other code changes needed. We route transparently underneath.
Bring your own LLM provider keys — we handle the routing.
"""

import asyncio
import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import os
from pydantic import BaseModel, Field
from prometheus_fastapi_instrumentator import Instrumentator

from gateway.config import get_settings, MODEL_REGISTRY, get_default_provider
from gateway.providers import get_adapter, LLMRequest, Message
from gateway.auth import AuthService
from gateway.signup import router as signup_router
from gateway.vault import KeyVault, ProviderKey
from classifier.rule_based import RuleBasedRouter
from logger.models import RequestLogger, get_engine, get_session_factory, Base

log = structlog.get_logger()
settings = get_settings()

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

engine = None
session_factory = None
router = None
request_logger = None
auth_service = None
key_vault = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, session_factory, router, request_logger, auth_service, key_vault

    # DB setup
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session_factory(engine)
    router = RuleBasedRouter(settings)
    request_logger = RequestLogger(session_factory)
    auth_service = AuthService(session_factory)
    key_vault = KeyVault(session_factory)

    log.info("routease_started", service="RouteEase", environment=settings.environment)
    yield

    await engine.dispose()
    log.info("routease_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RouteEase",
    description="Intelligent multi-LLM routing gateway. Route with ease.",
    version="0.1.0",
    lifespan=lifespan,
)

# Self-serve signup
app.include_router(signup_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics at /metrics
Instrumentator().instrument(app).expose(app)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

async def authenticate(authorization: str = Header(None)) -> dict:
    """Validate Bearer API key and return tenant context."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    raw_key = authorization.split(" ", 1)[1]
    return await auth_service.authenticate(raw_key)


# ---------------------------------------------------------------------------
# OpenAI-compatible request/response schemas
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "auto"              # "auto" = let router decide
    messages: list[ChatMessage]
    max_tokens: int = 2048
    temperature: float = 0.7
    stream: bool = False
    # Router extensions
    x_cost_budget_usd: Optional[float] = Field(None, alias="x-cost-budget-usd")
    x_provider_preference: Optional[str] = Field("anthropic", alias="x-provider-preference")
    x_session_id: Optional[str] = Field(None, alias="x-session-id")
    x_turn_index: Optional[int] = Field(0, alias="x-turn-index")

    class Config:
        populate_by_name = True


class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: UsageInfo
    # Router metadata (non-standard, ignored by most SDKs)
    x_router_model: str = Field(alias="x-router-model")
    x_router_tier: int = Field(alias="x-router-tier")
    x_router_score: float = Field(alias="x-router-score")
    x_router_shadow: bool = Field(alias="x-router-shadow")
    x_cost_usd: Optional[float] = Field(None, alias="x-cost-usd")

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------------
# Main completion endpoint
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    body: ChatCompletionRequest,
    tenant: dict = Depends(authenticate),
    x_provider_preference: Optional[str] = Header(None, alias="x-provider-preference"),
    x_session_id: Optional[str] = Header(None, alias="x-session-id"),
    x_turn_index: Optional[int] = Header(None, alias="x-turn-index"),
    x_cost_budget_usd: Optional[float] = Header(None, alias="x-cost-budget-usd"),
):
    tenant_id = tenant["tenant_id"]

    # Header values take priority over body fields, fall back to tenant default
    provider_preference = x_provider_preference or tenant.get("provider_preference") or get_default_provider()
    budget = x_cost_budget_usd or body.x_cost_budget_usd or tenant.get("budget_usd") or settings.default_cost_budget_usd
    session_id = x_session_id or body.x_session_id or str(uuid.uuid4())
    turn_index = x_turn_index or body.x_turn_index or 0

    # Extract the last user message as the prompt for routing
    user_messages = [m for m in body.messages if m.role == "user"]
    prompt = user_messages[-1].content if user_messages else body.messages[-1].content

    # --- ROUTING DECISION ---
    force_model = None if body.model == "auto" else body.model
    decision = router.route(
        prompt=prompt,
        turn_index=turn_index,
        cost_budget_usd=budget,
        provider_preference=provider_preference,
        force_model=force_model,
    )

    # --- LLM CALL ---
    # Get customer's provider key from vault
    customer_key = await key_vault.get_key(tenant_id, decision.provider)
    if not customer_key:
        # Try fallback to another provider the customer has a key for
        available = await key_vault.get_available_providers(tenant_id)
        if not available:
            raise HTTPException(
                status_code=402,
                detail="No LLM provider keys configured. Add your Google, OpenAI, or Anthropic key at /v1/keys"
            )
        # Re-route to a provider they have a key for
        fallback_provider = available[0]
        fallback_models = [
            m for m, meta in MODEL_REGISTRY.items()
            if meta["provider"] == fallback_provider
        ]
        if fallback_models:
            decision.model_id = fallback_models[0]
            decision.provider = fallback_provider
            customer_key = await key_vault.get_key(tenant_id, fallback_provider)

    adapter = get_adapter(decision.provider, customer_key)
    llm_request = LLMRequest(
        messages=[Message(role=m.role, content=m.content) for m in body.messages],
        model_id=decision.model_id,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
    )

    provider_error = False
    error_message = None
    llm_response = None
    t0 = time.monotonic()

    try:
        llm_response = await adapter.complete(llm_request)
    except Exception as exc:
        provider_error = True
        error_message = str(exc)
        log.error("provider_error", model=decision.model_id, error=str(exc))

        # Failover: use customer vault keys only
        available_providers = await key_vault.get_available_providers(tenant_id)
        fallback_pool = [
            m for m, meta in MODEL_REGISTRY.items()
            if meta["provider"] in available_providers
            and m != decision.model_id
        ]

        if fallback_pool:
            same_provider = [m for m in fallback_pool
                             if MODEL_REGISTRY[m]["provider"] == provider_preference]
            fallback_model = same_provider[0] if same_provider else fallback_pool[0]
            fallback_provider = MODEL_REGISTRY[fallback_model]["provider"]
            fallback_key = await key_vault.get_key(tenant_id, fallback_provider)
            log.info("failover_attempt", fallback=fallback_model)
            try:
                fallback_adapter = get_adapter(fallback_provider, fallback_key)
                llm_request.model_id = fallback_model
                llm_response = await fallback_adapter.complete(llm_request)
                decision.model_id = fallback_model
                provider_error = False
            except Exception as fallback_exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"All providers failed. Last error: {fallback_exc}"
                )
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Provider failed and no fallback available. Error: {error_message}"
            )

    latency_ms = (time.monotonic() - t0) * 1000

    # --- LOG (fire-and-forget, no await in hot path) ---
    api_key_hash = hashlib.sha256(
        tenant.get("api_key", tenant_id).encode()
    ).hexdigest()[:16]

    asyncio.create_task(
        request_logger.log_request(
            tenant_id=tenant_id,
            api_key_hash=api_key_hash,
            prompt=prompt,
            decision=decision,
            session_id=session_id,
            system_prompt=next(
                (m.content for m in body.messages if m.role == "system"), None
            ),
            message_count=len(body.messages),
            input_tokens=llm_response.input_tokens,
            output_tokens=llm_response.output_tokens,
            latency_ms=latency_ms,
            provider_error=provider_error,
            error_message=error_message,
        )
    )

    # --- RESPONSE ---
    cost_usd = None
    if llm_response.input_tokens and llm_response.output_tokens:
        cost_usd = request_logger._compute_cost(
            decision.model_id,
            llm_response.input_tokens,
            llm_response.output_tokens,
        )

    return ChatCompletionResponse(**{
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "created": int(time.time()),
        "model": body.model,
        "choices": [ChatChoice(
            message=ChatMessage(role="assistant", content=llm_response.content),
            finish_reason=llm_response.finish_reason,
        )],
        "usage": UsageInfo(
            prompt_tokens=llm_response.input_tokens,
            completion_tokens=llm_response.output_tokens,
            total_tokens=llm_response.input_tokens + llm_response.output_tokens,
        ),
        "x-router-model": decision.model_id,
        "x-router-tier": decision.tier,
        "x-router-score": round(decision.complexity_score, 1),
        "x-router-shadow": decision.is_shadow,
        "x-cost-usd": round(cost_usd, 6) if cost_usd else None,
    })


# ---------------------------------------------------------------------------
# Health + info endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status":  "ok",
        "version": "0.1.0",
        "service": "RouteEase",
        "docs":    "https://github.com/arpitjainn22/routease",
    }


@app.get("/")
async def landing_page():
    """Serve the RouteEase landing page."""
    landing_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "landing", "index.html"
    )
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    return {
        "message": "RouteEase API",
        "tagline": "Route with ease.",
        "docs":    "/docs",
        "health":  "/health",
        "github":  "https://github.com/arpitjainn22/routease",
    }


@app.get("/v1/models")
async def list_models(tenant: dict = Depends(authenticate)):
    """List all routable models — mimics OpenAI's /v1/models endpoint."""
    return {
        "object":  "list",
        "service": "RouteEase",
        "data": [
            {
                "id":          model_id,
                "object":      "model",
                "owned_by":    meta["provider"],
                "router_tier": meta["tier"],
            }
            for model_id, meta in MODEL_REGISTRY.items()
        ],
    }


@app.get("/v1/router/explain")
async def explain_routing(
    prompt: str,
    tenant: dict = Depends(authenticate),
):
    """
    Debug endpoint: shows what routing decision would be made for a prompt.
    Useful for customers tuning their integration.
    """
    decision = router.route(
        prompt=prompt,
        cost_budget_usd=tenant.get("budget_usd"),
    )
    f = decision.features
    return {
        "routed_model": decision.model_id,
        "tier": decision.tier,
        "complexity_score": round(decision.complexity_score, 1),
        "reasoning": decision.reasoning,
        "features": {
            "token_count_est": f.token_count_est,
            "has_code_block": f.has_code_block,
            "has_code_keywords": f.has_code_keywords,
            "constraint_count": f.constraint_count,
            "is_specialist_domain": f.is_specialist_domain,
            "is_factual_lookup": f.is_factual_lookup,
            "is_creative": f.is_creative,
            "is_code_task": f.is_code_task,
        },
    }


# ---------------------------------------------------------------------------
# Admin endpoints — tenant + API key management
# ---------------------------------------------------------------------------
# Protect these with ADMIN_SECRET_KEY in production (env var).
# In a real deployment, put these behind a separate internal port
# or VPN so they are never exposed to the public internet.
# ---------------------------------------------------------------------------

from pydantic import BaseModel as PydanticBase
from fastapi import Body

ADMIN_KEY = settings.app_secret_key   # set APP_SECRET_KEY in .env


def require_admin(authorization: str = Header(None)):
    if not authorization or authorization != f"Bearer {ADMIN_KEY}":
        raise HTTPException(status_code=403, detail="Admin access required")


class CreateTenantRequest(PydanticBase):
    name: str
    email: str
    company: str = None
    budget_usd: float = 0.01
    plan: str = "free"
    provider_preference: str = "anthropic"


class CreateKeyRequest(PydanticBase):
    name: str = "default"
    environment: str = "live"   # "live" or "test"


# ── Tenant routes ──────────────────────────────────────────────────────────

@app.post("/admin/tenants", dependencies=[Depends(require_admin)])
async def create_tenant(body: CreateTenantRequest):
    """
    Create a new customer tenant.
    After creating a tenant, generate an API key for them.
    """
    tenant = await auth_service.create_tenant(
        name=body.name,
        email=body.email,
        company=body.company,
        budget_usd=body.budget_usd,
        plan=body.plan,
        provider_preference=body.provider_preference,
    )
    return {
        "tenant_id": str(tenant.id),
        "name":      tenant.name,
        "email":     tenant.email,
        "plan":      tenant.plan,
        "created_at": tenant.created_at.isoformat(),
        "next_step": f"POST /admin/tenants/{tenant.id}/keys to generate an API key",
    }


@app.get("/admin/tenants", dependencies=[Depends(require_admin)])
async def list_tenants():
    """List all tenants."""
    tenants = await auth_service.list_tenants()
    return {
        "tenants": [
            {
                "tenant_id": str(t.id),
                "name":      t.name,
                "email":     t.email,
                "plan":      t.plan,
                "is_active": t.is_active,
                "created_at": t.created_at.isoformat(),
            }
            for t in tenants
        ]
    }


# ── API key routes ─────────────────────────────────────────────────────────

@app.post("/admin/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
async def create_api_key(tenant_id: str, body: CreateKeyRequest):
    """
    Generate a new API key for a tenant.

    IMPORTANT: The raw key is returned ONCE in this response.
    It is stored hashed and cannot be retrieved again.
    Share it with your customer immediately.
    """
    result = await auth_service.create_api_key(
        tenant_id=tenant_id,
        name=body.name,
        environment=body.environment,
    )
    return result


@app.get("/admin/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
async def list_api_keys(tenant_id: str):
    """List all API keys for a tenant (raw keys never returned)."""
    keys = await auth_service.list_keys(tenant_id)
    return {"tenant_id": tenant_id, "keys": keys}


@app.delete("/admin/tenants/{tenant_id}/keys/{key_id}", dependencies=[Depends(require_admin)])
async def revoke_api_key(tenant_id: str, key_id: str):
    """Revoke an API key immediately. Existing requests in flight will still complete."""
    success = await auth_service.revoke_key(key_id, tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"revoked": True, "key_id": key_id}
