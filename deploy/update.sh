#!/bin/bash
# =====================================================
# LLM Router — Update live server with latest code
# Run this on your Hostinger server after pushing to GitHub
# Usage: bash /opt/llm-router/deploy/update.sh
# =====================================================

set -e
APP_DIR="/opt/llm-router"
cd $APP_DIR

echo ""
echo "→ Pulling latest code from GitHub..."
git pull origin main

echo "→ Rebuilding and restarting gateway..."
docker-compose up -d --build gateway

echo "→ Waiting for gateway to be healthy..."
for i in {1..12}; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✓ Gateway is up"
        break
    fi
    echo "  Waiting... ($i/12)"
    sleep 5
done

echo ""
echo "✓ Update complete"
curl -s http://localhost:8000/health
echo ""
