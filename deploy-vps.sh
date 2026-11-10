#!/usr/bin/env bash
# Instala o HypeCut num VPS Ubuntu/Debian com HTTPS automático.
# Uso: sudo bash deploy-vps.sh seudominio.com.br
set -e
DOMAIN="$1"; [ -n "$DOMAIN" ] || { echo "Uso: sudo bash deploy-vps.sh seudominio.com.br"; exit 1; }
cd "$(dirname "$0")"
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
[ -f .env ] || cp .env.example .env
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$DOMAIN|" .env
grep -q '^SECRET_KEY=troque' .env && sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env
echo "DOMAIN=$DOMAIN" > .domain.env
docker compose -f docker-compose.yml -f docker-compose.vps.yml --env-file .domain.env up -d --build
echo "Pronto: https://$DOMAIN  (aponte o DNS do domínio para o IP deste servidor; edite .env para as chaves e rode de novo)"
