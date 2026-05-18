# LLM Router — Setup Guide

Complete step-by-step guide to run the project locally.

---

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Python | 3.11+ | `python3 --version` |
| Docker Desktop | Latest | `docker --version` |
| Git | Any | `git --version` |

---

## Step 1 — Navigate into project

```bash
cd llm-router
```

## Step 2 — Create virtual environment

```bash
python3.11 -m venv venv
```

## Step 3 — Activate it

```bash
# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate.bat
```

Your prompt will show `(venv)` when active.

## Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

## Step 5 — Configure environment

```bash
cp .env.example .env
```

Open `.env` and set:
```
DATABASE_URL=postgresql+asyncpg://llmrouter:llmrouter@localhost:5432/llmrouter
REDIS_URL=redis://localhost:6379
APP_SECRET_KEY=any-long-random-string
GOOGLE_API_KEY=your-key-for-local-testing
```

Leave `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` blank if unused.

## Step 6 — Start Postgres and Redis

```bash
docker compose up -d postgres redis

# Wait 10 seconds, then verify:
docker compose ps
# Both must show (healthy)
```

## Step 7 — Run tests

```bash
python -m pytest tests/ -v
# 54 passed
```

## Step 8 — Start gateway

```bash
uvicorn gateway.main:app --reload --port 8000
```

## Step 9 — Get your API key

```bash
python manage.py quickstart --name "My App" --email "you@example.com"
```

Save the printed key — shown once only.

## Step 10 — Test it

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}

curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer rk-live-your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"What is the capital of France?"}]}'
```

---

## Common errors

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError` | Run `source venv/bin/activate` first |
| `Connection refused` port 5432 | Run `docker compose up -d postgres redis` |
| `Invalid API key` | Use key from `manage.py quickstart`, not the old demo keys |
| `401 Unauthorized` from LLM | Your provider key in `.env` is wrong or empty |
| `429 Too Many Requests` | Google free tier limit — wait 60 seconds |

---

## Quick reference

```bash
source venv/bin/activate                          # activate env
docker compose up -d postgres redis               # start DB
uvicorn gateway.main:app --reload --port 8000     # start gateway
python -m pytest tests/ -v                        # run tests
python manage.py list-tenants                     # see all tenants
python manage.py create-key --tenant-id <id>      # new API key
docker compose down                               # stop everything
docker compose down -v                            # stop + wipe DB
```
