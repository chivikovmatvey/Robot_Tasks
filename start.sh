#!/usr/bin/env bash
# Запуск всего: бэкенд (:8000) + фронтенд (:3000).
# Автоустановка зависимостей при первом запуске. Ctrl+C — гасит оба.
# Запуск: ./start.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

# ── автоустановка при необходимости ──────────────────────────────
if [ ! -d "$DIR/backend/.venv" ] || [ ! -d "$DIR/frontend/node_modules" ]; then
  echo "==> Первый запуск — ставлю зависимости…"
  bash "$DIR/install.sh"
fi

if [ ! -f "$DIR/backend/.env" ]; then
  cp "$DIR/backend/.env.example" "$DIR/backend/.env"
  echo "ВНИМАНИЕ: создан backend/.env — заполни ключи и перезапусти."
fi

# ── бэкенд в фоне ────────────────────────────────────────────────
cd "$DIR/backend"
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 \
  --reload --reload-exclude '.venv/*' --reload-exclude 'storage/*' &
BACK_PID=$!

cleanup() {
  echo ""
  echo "==> Останавливаю…"
  kill "$BACK_PID" 2>/dev/null || true
  wait "$BACK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── ждём, пока бэк поднимется ────────────────────────────────────
echo "==> Жду бэкенд на :8000…"
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "✓ Бэкенд готов"
    break
  fi
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    echo "✗ Бэкенд упал на старте"; exit 1
  fi
  sleep 1
done

# ── фронтенд в форграунде ────────────────────────────────────────
cd "$DIR/frontend"
echo "==> Фронтенд: http://localhost:3000"
npm run dev
