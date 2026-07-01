@echo off
chcp 65001 >nul
title Offer Processor - Backend ONLY
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual env not found. Run install.bat first.
    pause
    exit /b 1
)

echo Starting backend on http://localhost:8000
echo.
echo If you see errors below, copy them and send to support.
echo.
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
pause
