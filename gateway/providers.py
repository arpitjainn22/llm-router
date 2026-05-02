"""
Provider adapters.

Each adapter translates our internal request format into the
provider's native API format, then translates the response back.
This is the abstraction layer that lets customers use one API
regardless of which model we route to underneath.

OpenAI-compatible input/output format is used as the canonical format
(same choice as LiteLLM, Bifrost) so migration is trivial for customers.
"""

import time
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, AsyncIterator

import httpx
from gateway.config import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Canonical request / response types
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str      # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMRequest:
    messages: list[Message]
    model_id: str
    max_tokens: int = 2048
    temperature: float = 0.7
    stream: bool = False


@dataclass
class LLMResponse:
    content: str
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    finish_reason: str   # "stop" | "length" | "error"
    raw: dict = None     # full provider response for debugging


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class BaseAdapter(ABC):

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=120.0)

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        pass

    async def close(self):
        await self.client.aclose()


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

class AnthropicAdapter(BaseAdapter):

    BASE_URL = "https://api.anthropic.com/v1/messages"
    # Map our internal model IDs to Anthropic's actual model strings
    MODEL_MAP = {
        "claude-haiku-3":    "claude-haiku-3-20240307",
        "claude-sonnet-3-5": "claude-sonnet-4-20250514",
        "claude-opus-3":     "claude-opus-4-20250514",
    }

    async def complete(self, request: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        model_str = self.MODEL_MAP.get(request.model_id, request.model_id)

        # Separate system prompt from conversation
        system = None
        messages = []
        for msg in request.messages:
            if msg.role == "system":
                system = msg.content
            else:
                messages.append({"role": msg.role, "content": msg.content})

        payload = {
            "model": model_str,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system

        resp = await self.client.post(
            self.BASE_URL,
            json=payload,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        latency_ms = (time.monotonic() - t0) * 1000

        return LLMResponse(
            content=data["content"][0]["text"],
            model_id=request.model_id,
            input_tokens=data["usage"]["input_tokens"],
            output_tokens=data["usage"]["output_tokens"],
            latency_ms=latency_ms,
            finish_reason=data.get("stop_reason", "stop"),
            raw=data,
        )


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------

class OpenAIAdapter(BaseAdapter):

    BASE_URL = "https://api.openai.com/v1/chat/completions"
    MODEL_MAP = {
        "gpt-4o-mini": "gpt-4o-mini",
        "gpt-4o":      "gpt-4o",
    }

    async def complete(self, request: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        model_str = self.MODEL_MAP.get(request.model_id, request.model_id)

        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
        ]

        resp = await self.client.post(
            self.BASE_URL,
            json={
                "model": model_str,
                "messages": messages,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            },
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        latency_ms = (time.monotonic() - t0) * 1000

        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            model_id=request.model_id,
            input_tokens=data["usage"]["prompt_tokens"],
            output_tokens=data["usage"]["completion_tokens"],
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Google Gemini adapter
# ---------------------------------------------------------------------------

class GoogleAdapter(BaseAdapter):

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    MODEL_MAP = {
        # Current available models (verified May 2026)
        "gemini-2.0-flash":      "gemini-2.0-flash",
        "gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
        "gemini-2.5-flash":      "gemini-2.5-flash",
        "gemini-2.5-pro":        "gemini-2.5-pro",
        # Legacy names mapped to nearest current equivalent
        "gemini-1.5-flash":      "gemini-2.0-flash",
        "gemini-1.5-pro":        "gemini-2.5-pro",
    }

    async def complete(self, request: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        model_str = self.MODEL_MAP.get(request.model_id, request.model_id)

        # Convert to Google's parts format
        contents = []
        system_instruction = None
        for msg in request.messages:
            if msg.role == "system":
                system_instruction = {"parts": [{"text": msg.content}]}
            else:
                role = "model" if msg.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": msg.content}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        url = self.BASE_URL.format(model=model_str)
        # Retry up to 3 times on 429 rate limit with exponential backoff
        for attempt in range(3):
            resp = await self.client.post(
                url,
                json=payload,
                params={"key": settings.google_api_key},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 429 and attempt < 2:
                wait = 2 ** attempt  # 1s, 2s
                await asyncio.sleep(wait)
                continue
            break
        resp.raise_for_status()
        data = resp.json()
        latency_ms = (time.monotonic() - t0) * 1000

        candidate = data["candidates"][0]
        content = candidate["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})

        return LLMResponse(
            content=content,
            model_id=request.model_id,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            latency_ms=latency_ms,
            finish_reason=candidate.get("finishReason", "STOP").lower(),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_adapters = {}


def get_adapter(provider: str) -> BaseAdapter:
    """Return a cached adapter instance for the given provider."""
    if provider not in _adapters:
        if provider == "anthropic":
            _adapters[provider] = AnthropicAdapter()
        elif provider == "openai":
            _adapters[provider] = OpenAIAdapter()
        elif provider == "google":
            _adapters[provider] = GoogleAdapter()
        else:
            raise ValueError(f"Unknown provider: {provider}")
    return _adapters[provider]
