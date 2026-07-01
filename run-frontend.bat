@echo off
chcp 65001 >nul
title Offer Processor - Frontend ONLY
cd /d "%~dp0frontend"

if not exist "node_modules" (
    echo node_modules not found. Run install.bat first.
    pause
    exit /b 1
)

echo Starting frontend on http://localhost:3000
echo.
echo If you see errors below, copy them and send to support.
echo.
call npm run dev
pause
