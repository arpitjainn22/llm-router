#!/bin/bash
# Start or restart LLM Router
# Run after editing .env or after code updates

set -e
cd /opt/llm-router

echo "→ Pulling latest code..."
git pull origin main

echo "→ Starting services..."
docker-compose up -d --build

echo "→ Waiting for Postgres to be healthy..."
sleep 5

echo "→ Checking gateway health..."
for i in {1..10}; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✓ Gateway is up"
        break
    fi
    echo "  Waiting... ($i/10)"
    sleep 3
done

echo ""
echo "✓ LLM Router is running"
echo "  Health:  http://localhost:8000/health"
echo "  Metrics: http://localhost:9090"
echo ""
docker-compose ps
