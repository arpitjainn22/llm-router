# Contributing to LLM Router

Thank you for your interest in contributing. This guide covers everything
you need to get started.

---

## Ways to contribute

- **Bug reports** — open an issue with steps to reproduce
- **Feature requests** — open an issue describing the use case
- **Code** — fix a bug, add a feature, improve tests
- **Documentation** — fix typos, improve examples, add guides
- **New provider adapters** — add support for Cohere, Mistral, Together AI etc.

---

## Local setup

```bash
git clone https://github.com/yourusername/llm-router.git
cd llm-router

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add at least one LLM provider key to .env

docker compose up -d postgres redis
uvicorn gateway.main:app --reload --port 8000
```

---

## Running tests

```bash
python -m pytest tests/ -v
```

All 54 tests must pass before submitting a PR.

---

## Good first issues

These are great starting points if you're new to the codebase:

| Issue | File | Difficulty |
|-------|------|-----------|
| Add Mistral provider adapter | `gateway/providers.py` | Easy |
| Add Together AI adapter | `gateway/providers.py` | Easy |
| Add Cohere adapter | `gateway/providers.py` | Easy |
| Improve creative task detection regex | `classifier/rule_based.py` | Easy |
| Add streaming support | `gateway/main.py` | Medium |
| Add per-tenant rate limiting | `gateway/main.py` | Medium |
| Write provider integration tests | `tests/` | Medium |

---

## Adding a new provider adapter

1. Add the adapter class to `gateway/providers.py` following the pattern of
   `AnthropicAdapter`, `OpenAIAdapter`, or `GoogleAdapter`
2. Register it in `get_adapter()` at the bottom of `providers.py`
3. Add your models to `MODEL_REGISTRY` in `gateway/config.py`
4. Add the models to `TIER_PREFERENCES` in `classifier/rule_based.py`
5. Add the API key to `.env.example` and `gateway/config.py` Settings class
6. Write at least one integration test

---

## PR checklist

- [ ] All existing tests pass (`python -m pytest tests/ -v`)
- [ ] New code has tests where appropriate
- [ ] `.env.example` updated if new env vars added
- [ ] README updated if user-facing behaviour changes

---

## Code style

- Python 3.11+
- Type hints on all function signatures
- Docstrings on all public classes and methods
- No external formatter required — just keep it readable

---

## Questions?

Open an issue with the `question` label.
