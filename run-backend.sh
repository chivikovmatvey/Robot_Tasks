#!/usr/bin/env bash
# Только бэкенд (FastAPI на :8000). Запуск: ./run-backend.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR/backend"
if [ ! -d .venv ]; then
  echo "Нет .venv — сначала ./install.sh"; exit 1
fi
exec .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 \
  --reload --reload-exclude '.venv/*' --reload-exclude 'storage/*'
