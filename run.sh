#!/usr/bin/env bash
# Roda local sem Docker (precisa de Python 3.10+ e ffmpeg instalados)
set -e
cd "$(dirname "$0")"
[ -f .env ] || { cp .env.example .env; echo "Criei o .env — preencha as chaves e rode de novo."; exit 1; }
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
