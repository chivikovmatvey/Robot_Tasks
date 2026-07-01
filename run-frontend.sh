#!/usr/bin/env bash
# Только фронтенд (Vite на :3000). Запуск: ./run-frontend.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR/frontend"
if [ ! -d node_modules ]; then
  echo "Нет node_modules — сначала ./install.sh (или npm install)"; exit 1
fi
exec npm run dev
