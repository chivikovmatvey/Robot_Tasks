#!/usr/bin/env bash
# Установка зависимостей (Linux/macOS). Запуск: ./install.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "✗ $PY не найден. Установи Python 3.11+."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "✗ node не найден. Установи Node.js 18+."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "✗ npm не найден. Установи Node.js 18+ (идёт вместе с npm)."; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "✗ curl не найден (нужен для start.sh). Установи через пакетный менеджер дистрибутива."; exit 1; }

echo "==> Backend: venv + зависимости"
cd "$DIR/backend"
if [ ! -d .venv ]; then
  if ! "$PY" -m venv .venv 2>/tmp/venv_err.$$; then
    cat /tmp/venv_err.$$ >&2
    rm -f /tmp/venv_err.$$
    echo "✗ Не удалось создать venv. На Debian/Ubuntu поставь: sudo apt install python3-venv"
    exit 1
  fi
  rm -f /tmp/venv_err.$$
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "==> Playwright: браузер Chromium + системные зависимости"
if ! .venv/bin/python -m playwright install --with-deps chromium; then
  echo "    --with-deps не сработал (не Debian/Ubuntu или нет sudo) — ставлю без системных пакетов"
  .venv/bin/python -m playwright install chromium
  echo "    Если браузер не запустится, поставь системные библиотеки вручную:"
  echo "    .venv/bin/python -m playwright install-deps  (нужны root-права)"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Создан backend/.env — заполни KEITARO_* / ADROBOT_* / TELEGRAM_*"
fi

echo "==> Frontend: npm install"
cd "$DIR/frontend"
npm install

echo "✓ Готово. Запуск: ./start.sh"
