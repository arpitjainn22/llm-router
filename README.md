<div align="center">

# 🔀 LLM Router

**Intelligent multi-model LLM routing that cuts your AI costs by 40%**

Drop-in replacement for the OpenAI API. One line of code change.  
Automatically routes each query to the cheapest model that can answer it well.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[Quick Start](#quick-start) · [How it works](#how-it-works) · [Docs](#documentation) · [Contributing](CONTRIBUTING.md)

</div>

---

## The problem

You're paying for GPT-4o on every request — even "What is the capital of France?"  
80% of LLM queries don't need a frontier model. You're overpaying by 3-5x.

## The solution

LLM Router analyses each query in <1ms and routes it to the right model:

| Query | Without router | With router | Saving |
|-------|---------------|-------------|--------|
| "What is 2+2?" | GPT-4o — $0.015 | Gemini Flash — $0.0001 | **99%** |
| "Write a Python function" | GPT-4o — $0.015 | Gemini 2.0 Flash — $0.001 | **93%** |
| "Implement lock-free Rust with linearisability" | GPT-4o — $0.015 | GPT-4o — $0.015 | 0% |

The router only upgrades to expensive models when it actually needs to.  
**Average saving across real workloads: 35–45%.**

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/arpitjainn22/llm-router.git
cd llm-router

# 2. Setup environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure — add at least one LLM provider key
cp .env.example .env

# 4. Start infrastructure
docker compose up -d postgres redis

# 5. Start the gateway
uvicorn gateway.main:app --reload --port 8000

# 6. Get your router API key
python manage.py quickstart --name "My App" --email "you@example.com"
```

---

## One line of code change

```python
# Before — direct OpenAI call
from openai import AsyncOpenAI
client = AsyncOpenAI(api_key="sk-...")

# After — route through LLM Router (everything else stays identical)
from openai import AsyncOpenAI
client = AsyncOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="rk-live-your-router-key",
)
```

Your existing code — prompts, parsing, streaming — works without any other changes.

---

## How it works

Every request is scored using 12 signals extracted from the query in under 1ms:

```
Incoming query
      │
      ▼
┌─────────────────────┐
│   Feature extractor  │  token count, code detection, constraint count,
│   (12 signals, <1ms) │  domain signals, turn index, factual lookup...
└──────────┬──────────┘
           │  complexity score 0–100
           ▼
┌─────────────────────┐
│   Rule-based router  │  Tier 0: score < 30  → Gemini Flash Lite
│   (Phase 1)          │  Tier 1: score 30–60 → Gemini 2.0 Flash
└──────────┬──────────┘  Tier 2: score > 60  → Gemini 2.5 Pro
           │
           ▼
    LLM API call + response logged for Phase 2 training
```

**Supported providers:** Google Gemini · Anthropic Claude · OpenAI GPT

---

## Routing tiers

| Tier | Models | Score | Triggers |
|------|--------|-------|----------|
| 0 — Fast | Gemini Flash Lite · Haiku · GPT-4o-mini | < 30 | Factual lookups, simple Q&A, classification |
| 1 — Smart | Gemini 2.0 Flash · Sonnet · GPT-4o | 30–60 | Coding, analysis, multi-step reasoning |
| 2 — Frontier | Gemini 2.5 Pro · Opus | > 60 or hard signals | Complex constraints, specialist domains, long docs |

Hard signals that force a tier upgrade:
- Code block + 2+ constraints → **Tier 2**
- Specialist domain (legal/medical/financial) + any constraint → **Tier 2**
- Prompt > 1200 tokens → **Tier 2**
- Any code task → **Tier 1 minimum**

---

## Debug any routing decision

```bash
curl "http://localhost:8000/v1/router/explain?prompt=Implement+a+binary+search+tree" \
  -H "Authorization: Bearer rk-live-your-key"
```

```json
{
  "routed_model": "gemini-2.0-flash",
  "tier": 1,
  "complexity_score": 55.9,
  "reasoning": "score=56 | code_keywords=True | code_task=True",
  "features": {
    "token_count_est": 9,
    "has_code_block": false,
    "has_code_keywords": true,
    "is_code_task": true,
    "constraint_count": 0,
    "is_specialist_domain": false,
    "is_factual_lookup": false
  }
}
```

---

## API key management

```bash
# Create a tenant + key in one command
python manage.py quickstart --name "My App" --email "me@example.com"

# List all tenants
python manage.py list-tenants

# Generate additional keys
python manage.py create-key --tenant-id <id> --name "production"

# Revoke a key
python manage.py revoke-key --tenant-id <id> --key-id <id>
```

Keys are stored hashed (SHA-256) — the raw key is shown once and never stored.  
Same security model as Stripe and OpenAI.

---

## Per-request controls

```python
client.chat.completions.create(
    model="auto",                           # let router decide
    messages=[...],
    extra_headers={
        "x-provider-preference": "google",  # prefer Google models
        "x-cost-budget-usd": "0.005",       # hard cost cap per request
        "x-turn-index": "3",                # conversation turn (improves routing)
    }
)
```

---

## Project structure

```
llm-router/
├── gateway/
│   ├── main.py          # FastAPI app — OpenAI-compatible API
│   ├── config.py        # Settings, model registry, pricing
│   ├── providers.py     # Anthropic / OpenAI / Google adapters
│   └── auth.py          # Tenant + API key management
├── classifier/
│   └── rule_based.py    # 12-signal feature extractor + tier router
├── logger/
│   └── models.py        # PostgreSQL schema + async request logger
├── tests/
│   └── test_classifier.py  # 54 tests
├── manage.py            # CLI for tenant + key management
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Roadmap

| Phase | What | Status |
|-------|------|--------|
| **1** | Rule-based router · logging · key management | ✅ Complete |
| **2** | XGBoost ML classifier trained on real query data | 🔨 Building |
| **3** | Fine-tuned DeBERTa semantic classifier · per-tenant policies | 📋 Planned |
| **4** | Contextual bandit RL self-improving policy | 📋 Planned |

---

## Running tests

```bash
python -m pytest tests/ -v
# 54 passed
```

---

## Contributing

We welcome contributions — see [CONTRIBUTING.md](CONTRIBUTING.md) for how to get started.  
Good first issues are tagged [`good first issue`](https://github.com/arpitjainn22/llm-router/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">
Built with ❤️ · If this saved you money, give it a ⭐
</div>
