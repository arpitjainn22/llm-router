# Contributing to LLM Router

Thank you for your interest in contributing.

## Ways to contribute

- Bug reports — open an issue with steps to reproduce
- Feature requests — open an issue describing the use case
- Code — fix a bug, add a feature, improve tests
- New provider adapters — add Mistral, Cohere, Together AI etc.

## Local setup

```bash
git clone https://github.com/arpitjainn22/llm-router.git
cd llm-router
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d postgres redis
uvicorn gateway.main:app --reload --port 8000
```

## Running tests

```bash
python -m pytest tests/ -v
```

All 54 tests must pass before submitting a PR.

## Good first issues

| Task | File | Difficulty |
|------|------|-----------|
| Add Mistral provider adapter | `gateway/providers.py` | Easy |
| Add Together AI adapter | `gateway/providers.py` | Easy |
| Add streaming support | `gateway/main.py` | Medium |
| Add per-tenant rate limiting | `gateway/main.py` | Medium |

## Adding a new provider

1. Add adapter class in `gateway/providers.py`
2. Register it in `get_adapter()`
3. Add models to `MODEL_REGISTRY` in `gateway/config.py`
4. Add models to `TIER_PREFERENCES` in `classifier/rule_based.py`
5. Add API key field to `gateway/config.py` Settings
6. Add key validation in `gateway/signup.py`

## PR checklist

- [ ] All tests pass
- [ ] New code has tests
- [ ] README updated if user-facing behaviour changed
