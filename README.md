# Offer Processor

Локальный веб-инструмент. Запускается на твоём компе, открывается в браузере.

## Требования

- **Python 3.11+**
- **Node.js 18+** (идёт вместе с npm)
- **curl** (нужен `start.sh`, чтобы дождаться поднятия бэкенда)

### Linux / macOS

Проверь что установлено: `python3 --version`, `node --version`, `npm --version`.

На Debian/Ubuntu, если чего-то не хватает:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm curl
```

### Windows

- **Python 3.11+** — https://www.python.org/downloads/
  При установке поставь галочку **"Add Python to PATH"**
- **Node.js 18+** — https://nodejs.org/

Проверь что установлено: открой cmd, выполни `python --version` и `node --version`.

## Как запустить

### Linux / macOS

```bash
git clone https://github.com/chivikovmatvey/Robot_Tasks.git
cd Robot_Tasks
./start.sh
```

При первом запуске `start.sh` сам вызовет `install.sh` (venv, зависимости
Python, браузер Chromium для Playwright, `npm install`) — это займёт пару
минут. Дальше — быстро. Также при первом запуске создастся `backend/.env` из
`backend/.env.example` — его нужно заполнить своими ключами
(`KEITARO_*` / `ADROBOT_*` / `AITUNNEL_*` / `TELEGRAM_*`) и перезапустить
`./start.sh`. Без ключей приложение всё равно поднимется, но интеграции с
Keitaro/AdRobot/AITUNNEL/Telegram работать не будут.

Ctrl+C — останавливает и бэк, и фронт.

Если нужно только поставить зависимости без запуска: `./install.sh`.

### Windows

Двойной клик по **start.bat**.
При первом запуске установятся зависимости (~3 минуты), потом будет быстро.

Если start.bat не работает — запускай по частям:

1. **Двойной клик по install.bat** — установит зависимости. Если упадёт — увидишь ошибку, окно не закроется.

2. **Двойной клик по run-backend.bat** — запустит только бэк. Должно появиться:
   ```
   INFO:     Uvicorn running on http://127.0.0.1:8000
   ```

3. **Двойной клик по run-frontend.bat** — фронт. Должно быть:
   ```
   VITE v5.x  ready in xxx ms
   Local:   http://localhost:3000/
   ```

4. Открой http://localhost:3000

## Диагностика

### Тест 1 — бэк
http://localhost:8000/api/health должен вернуть JSON {"status":"ok",...}

### Тест 2 — фронт
http://localhost:3000 — должна открыться приложуха

### Частые проблемы

- **"port already in use"** — порт 8000 или 3000 уже занят другим процессом
  (Linux/macOS: `lsof -i :8000`, Windows: перезагрузи комп)
- **"failed building wheel"** при pip — Python слишком свежий, скажи мне
- **Окно вылетает (Windows)** — открой cmd в папке проекта руками, запусти батник из cmd
- **Playwright/Chromium не запускается (Linux)** — не хватает системных
  библиотек. Поставь их вручную: `.venv/bin/python -m playwright install-deps`
  (нужны root-права; на Debian/Ubuntu ставится через apt автоматически)

## Структура

```
offer_web/
├── backend/main.py          FastAPI сервер
├── frontend/src/            React + TypeScript
├── start.sh / start.bat     Главный запуск (Linux-macOS / Windows)
├── install.sh / install.bat Только установка зависимостей
├── run-backend.sh / .bat    Только бэк
└── run-frontend.sh / .bat   Только фронт
```

## Адреса

- http://localhost:3000 — приложуха
- http://localhost:8000/docs — Swagger API
