from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_secret_key: str = "dev-secret-change-in-production"
    environment: str = "development"
    log_level: str = "INFO"

    # LLM provider keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""

    # Infrastructure
    database_url: str = "postgresql+asyncpg://llmrouter:llmrouter@localhost:5432/llmrouter"

    @property
    def async_database_url(self) -> str:
        """
        Railway provides DATABASE_URL as postgresql:// 
        We need postgresql+asyncpg:// for async SQLAlchemy.
        This property converts automatically.
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url
    redis_url: str = "redis://localhost:6379"

    # Email — Gmail SMTP (recommended, free, no domain needed)
    gmail_user: str = ""              # your Gmail address
    gmail_app_password: str = ""      # Gmail app password (not your login password)

    # Email — Resend (alternative, requires domain)
    resend_api_key: str = ""

    # Routing behaviour
    shadow_routing_fraction: float = 0.05
    default_cost_budget_usd: float = 0.01

    # Model pricing (USD per 1K tokens)
    haiku_input_price: float = 0.00025
    haiku_output_price: float = 0.00125
    sonnet_input_price: float = 0.003
    sonnet_output_price: float = 0.015
    gpt4o_mini_input_price: float = 0.00015
    gpt4o_mini_output_price: float = 0.0006
    gpt4o_input_price: float = 0.005
    gpt4o_output_price: float = 0.015
    gemini_flash_input_price: float = 0.000075
    gemini_flash_output_price: float = 0.0003

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


# Model registry — single source of truth for all supported models
MODEL_REGISTRY = {
    # Tier 0: ultra-cheap, fast, simple tasks
    "claude-haiku-3": {
        "provider": "anthropic",
        "tier": 0,
        "context_window": 200000,
        "max_output": 4096,
        "strengths": ["simple_qa", "summarisation", "classification"],
    },
    "gemini-2.0-flash-lite": {
        "provider": "google",
        "tier": 0,
        "context_window": 1000000,
        "max_output": 8192,
        "strengths": ["simple_qa", "summarisation", "classification"],
    },
    "gemini-2.0-flash": {
        "provider": "google",
        "tier": 1,
        "context_window": 1000000,
        "max_output": 8192,
        "strengths": ["coding", "analysis", "reasoning", "long_context"],
    },
    "gemini-2.5-flash": {
        "provider": "google",
        "tier": 1,
        "context_window": 1000000,
        "max_output": 65536,
        "strengths": ["coding", "analysis", "reasoning", "long_context"],
    },
    "gemini-2.5-pro": {
        "provider": "google",
        "tier": 2,
        "context_window": 1000000,
        "max_output": 65536,
        "strengths": ["complex_reasoning", "research", "nuanced_writing"],
    },
    # Legacy alias — maps to gemini-2.0-flash in providers.py
    "gemini-1.5-flash": {
        "provider": "google",
        "tier": 0,
        "context_window": 1000000,
        "max_output": 8192,
        "strengths": ["simple_qa", "long_context", "summarisation"],
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "tier": 0,
        "context_window": 128000,
        "max_output": 16384,
        "strengths": ["simple_qa", "classification", "extraction"],
    },
    # Tier 1: mid-range, most tasks
    "claude-sonnet-3-5": {
        "provider": "anthropic",
        "tier": 1,
        "context_window": 200000,
        "max_output": 8192,
        "strengths": ["coding", "analysis", "reasoning", "writing"],
    },
    "gpt-4o": {
        "provider": "openai",
        "tier": 1,
        "context_window": 128000,
        "max_output": 16384,
        "strengths": ["coding", "analysis", "multimodal", "reasoning"],
    },
    # Tier 2: frontier, hardest tasks only
    "claude-opus-3": {
        "provider": "anthropic",
        "tier": 2,
        "context_window": 200000,
        "max_output": 4096,
        "strengths": ["complex_reasoning", "research", "nuanced_writing"],
    },
}


def get_default_provider() -> str:
    """
    Returns the first available provider based on which API keys
    are actually set in .env. Priority: anthropic > openai > google.
    Falls back to google since it has a free tier.
    """
    s = get_settings()
    placeholder_prefixes = ("sk-ant-...", "sk-...", "AIza...")

    if s.anthropic_api_key and not any(s.anthropic_api_key.startswith(p) for p in placeholder_prefixes):
        return "anthropic"
    if s.openai_api_key and not any(s.openai_api_key.startswith(p) for p in placeholder_prefixes):
        return "openai"
    if s.google_api_key and not any(s.google_api_key.startswith(p) for p in placeholder_prefixes):
        return "google"
    # No valid key found — return google and let it fail with a clear 401
    return "google"
