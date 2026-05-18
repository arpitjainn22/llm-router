#!/bin/bash
# =============================================================
# LLM Router — Hostinger VPS Deployment Script
# Tested on Hostinger KVM1 with Ubuntu 22.04
#
# Run this on your Hostinger server after first SSH login:
# ssh root@your-server-ip
# bash <(curl -s https://raw.githubusercontent.com/arpitjainn22/llm-router/main/deploy/setup_hostinger.sh)
# =============================================================

set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   LLM Router — Hostinger Setup           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── CONFIG — edit these before running ──────────────────────
DOMAIN="api.llmrouter.io"          # your domain
EMAIL="arpit@llmrouter.io"         # for SSL cert
REPO="https://github.com/arpitjainn22/llm-router.git"
APP_DIR="/opt/llm-router"
# ────────────────────────────────────────────────────────────

echo "→ [1/9] Updating system..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
  git curl wget ufw fail2ban \
  nginx certbot python3-certbot-nginx \
  python3-pip

echo "→ [2/9] Installing Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
# Add current user to docker group
usermod -aG docker root

echo "→ [3/9] Installing Docker Compose..."
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
  -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
docker-compose --version

echo "→ [4/9] Configuring firewall..."
ufw --force enable
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw status

echo "→ [5/9] Configuring fail2ban (brute force protection)..."
systemctl enable fail2ban
systemctl start fail2ban

echo "→ [6/9] Cloning LLM Router..."
mkdir -p $APP_DIR
if [ -d "$APP_DIR/.git" ]; then
  echo "   Repo already exists — pulling latest..."
  cd $APP_DIR && git pull
else
  git clone $REPO $APP_DIR
fi
cd $APP_DIR

echo "→ [7/9] Creating .env file..."
cp .env.example .env

# Generate secure random secret key
SECRET=$(openssl rand -hex 32)
sed -i "s/change-this-to-a-random-secret/$SECRET/" .env

# Update database URL for docker networking
sed -i "s|postgresql+asyncpg://user:password@localhost:5432|postgresql+asyncpg://llmrouter:llmrouter@postgres:5432|" .env
sed -i "s|postgresql+asyncpg://llmrouter:llmrouter@localhost:5432|postgresql+asyncpg://llmrouter:llmrouter@postgres:5432|" .env

echo "→ [8/9] Configuring Nginx..."
cat > /etc/nginx/sites-available/llm-router << NGINX
# Rate limiting zone
limit_req_zone \$binary_remote_addr zone=api_limit:10m rate=30r/m;
limit_req_zone \$binary_remote_addr zone=signup_limit:10m rate=5r/m;

server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    # Serve landing page on root
    location = / {
        root $APP_DIR/landing;
        try_files /index.html =404;
    }

    location ~* \.(css|js|png|jpg|ico|woff2|svg)$ {
        root $APP_DIR/landing;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # Signup endpoint — stricter rate limit
    location /api/signup {
        limit_req zone=signup_limit burst=3 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # All API traffic
    location / {
        limit_req zone=api_limit burst=20 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 120s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/llm-router /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "→ [9/9] Getting SSL certificate..."
certbot --nginx \
  -d $DOMAIN \
  --non-interactive \
  --agree-tos \
  --email $EMAIL \
  --redirect

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✓ Server setup complete!               ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo ""
echo "  1. Add your API keys to .env:"
echo "     nano $APP_DIR/.env"
echo ""
echo "     Required keys:"
echo "     GOOGLE_API_KEY=your-key"
echo "     RESEND_API_KEY=your-key (from resend.com)"
echo ""
echo "  2. Start the application:"
echo "     bash $APP_DIR/deploy/start.sh"
echo ""
echo "  3. Test it:"
echo "     curl https://$DOMAIN/health"
echo ""
