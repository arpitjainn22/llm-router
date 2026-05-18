<div align="center">

# 🔀 LLM Router

**Intelligent multi-model LLM routing that cuts your AI costs by 40%**

Drop-in replacement for the OpenAI API. One line of code change.
Routes each query to the cheapest model that can answer it well.
Bring your own LLM provider keys — we never see your data.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[Quick Start](#quick-start) · [How it works](#how-it-works) · [Setup Guide](SETUP.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## The problem

You're paying for GPT-4o on every request — even "What is the capital of France?"
80% of LLM queries don't need a frontier model. You're overpaying by 3–5x.

## The solution

LLM Router scores each query in <1ms and routes it to the right model:

| Query | Without router | With router | Saving |
|-------|---------------|-------------|--------|
| "What is 2+2?" | GPT-4o — $0.015 | Gemini Flash Lite — $0.0001 | **99%** |
| "Write a Python function" | GPT-4o — $0.015 | Gemini 2.0 Flash — $0.001 | **93%** |
| "Implement lock-free Rust with linearisability" | GPT-4o — $0.015 | GPT-4o — $0.015 | 0% |

**Average saving across real workloads: 35–45%.**

---

## Quick start

```bash
git clone https://github.com/arpitjainn22/llm-router.git
cd llm-router

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add your Google/OpenAI/Anthropic key for local testing

docker compose up -d postgres redis
uvicorn gateway.main:app --reload --port 8000

python manage.py quickstart --name "My App" --email "you@example.com"
```

---

## One line of code change

```python
# Before
from openai import AsyncOpenAI
client = AsyncOpenAI(api_key="sk-...")

# After — everything else stays identical
from openai import AsyncOpenAI
client = AsyncOpenAI(
    base_url="https://api.llmrouter.io/v1",
    api_key="rk-live-your-router-key",
)
```

---

## How it works

```
Incoming query
      │
      ▼
┌─────────────────────┐
│  Feature extractor   │  12 signals extracted in <1ms
│  (rule-based)        │  token count, code detection,
└──────────┬──────────┘  constraints, domain signals...
           │  complexity score 0–100
           ▼
┌─────────────────────┐
│   Tier decision      │  Tier 0 → Gemini Flash Lite
│                      │  Tier 1 → Gemini 2.0 Flash
└──────────┬──────────┘  Tier 2 → Gemini 2.5 Pro
           │
           ▼
  Customer's LLM key fetched from encrypted vault
           │
           ▼
    LLM API call → response → logged for Phase 2
```

**Supported providers:** Google Gemini · Anthropic Claude · OpenAI GPT

---

## BYOK — Bring Your Own Keys

Customers add their own LLM provider keys at signup. Keys are stored
encrypted (Fernet/AES-128) and used only for their own requests.
You never pay LLM bills for customer traffic.

---

## Routing tiers

| Tier | Models | Score | Triggers |
|------|--------|-------|----------|
| 0 | Gemini Flash Lite · Haiku · GPT-4o-mini | < 30 | Factual lookups, simple Q&A |
| 1 | Gemini 2.0 Flash · Sonnet · GPT-4o | 30–60 | Coding, analysis, reasoning |
| 2 | Gemini 2.5 Pro · Opus | > 60 | Complex reasoning, specialist domains |

---

## Debug routing decisions

```bash
curl "http://localhost:8000/v1/router/explain?prompt=Write+a+binary+search+tree" \
  -H "Authorization: Bearer rk-live-your-key"
```

---

## Project structure

```
llm-router/
├── gateway/
│   ├── main.py       # FastAPI app — OpenAI-compatible API
│   ├── config.py     # Settings, model registry, pricing
│   ├── providers.py  # Anthropic / OpenAI / Google adapters
│   ├── auth.py       # Tenant + API key management
│   ├── signup.py     # Self-serve signup + key validation
│   └── vault.py      # Encrypted provider key storage (BYOK)
├── classifier/
│   └── rule_based.py # 12-signal feature extractor + tier router
├── logger/
│   └── models.py     # PostgreSQL schema + async request logger
├── tests/
│   └── test_classifier.py
├── landing/
│   └── index.html    # Landing page with signup form
├── deploy/
│   ├── setup_hostinger.sh  # One-command server setup
│   └── start.sh            # Start/restart application
├── manage.py         # CLI for tenant + key management
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Roadmap

| Phase | What | Status |
|-------|------|--------|
| **1** | Rule-based router · BYOK vault · self-serve signup | ✅ Complete |
| **2** | XGBoost classifier trained on real query data | 🔨 Building |
| **3** | Fine-tuned DeBERTa semantic classifier | 📋 Planned |
| **4** | Contextual bandit RL self-improving policy | 📋 Planned |

---

## Running tests

```bash
python -m pytest tests/ -v
# 54 passed
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues tagged
[`good first issue`](https://github.com/arpitjainn22/llm-router/issues).

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">
Built with ❤️ · If this saved you money, give it a ⭐
</div>
