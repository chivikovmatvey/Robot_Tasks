@echo off
setlocal EnableExtensions

REM При двойном клике Windows часто закрывает окно при ошибке до pause.
REM Перезапускаем себя в окне cmd /k — останется открытым, будет виден текст ошибки.
if /i not "%~1"=="_KEEPOPEN" (
    cmd.exe /k "%~f0" _KEEPOPEN
    exit /b 0
)

chcp 65001 >nul 2>&1
title Offer Processor - Launcher

echo.
echo ================================
echo   OFFER PROCESSOR v0.1.0
echo ================================
echo.

cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: cannot cd to %~dp0
    goto :eof
)

REM STEP 1: venv
REM ------------------------------------------------------------
if not exist "backend\.venv\Scripts\python.exe" (
    echo [1/4] Creating Python virtual environment...
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERROR: Python not in PATH. Install Python 3.11+ with Add to PATH enabled.
        echo.
        goto :eof
    )
    python -m venv backend\.venv
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to create venv. Try: python --version
        echo.
        goto :eof
    )
    echo       Done.
    echo.
)

REM STEP 2: pip
REM ------------------------------------------------------------
if not exist "backend\.installed" (
    echo [2/4] Installing Python dependencies ^(1-2 min^)...
    echo.
    "backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 (
        echo ERROR: pip upgrade failed.
        goto :eof
    )
    echo.
    "backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed.
        goto :eof
    )
    echo. > backend\.installed
    echo       Done.
    echo.
)

REM STEP 3: npm
REM ------------------------------------------------------------
if not exist "frontend\node_modules" (
    echo [3/4] Installing Node dependencies ^(2-3 min^)...
    where npm >nul 2>&1
    if errorlevel 1 (
        echo ERROR: npm not found. Install Node.js from https://nodejs.org/
        goto :eof
    )
    echo.
    pushd frontend
    call npm install
    if errorlevel 1 (
        echo ERROR: npm install failed.
        popd
        goto :eof
    )
    popd
    echo       Done.
    echo.
)

REM STEP 4: services
REM ------------------------------------------------------------
echo [4/4] Starting services...
echo.

REM OFFER_PURGE_ON_SHUTDOWN: off, temp (default), all - see backend\utils\storage_purge.py
REM Example: set OFFER_PURGE_ON_SHUTDOWN=all

start "Offer Processor - Backend" cmd /k cd /d "%~dp0backend" ^&^& .venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000

timeout /t 4 /nobreak >nul

start "Offer Processor - Frontend" cmd /k cd /d "%~dp0frontend" ^&^& npm run dev

echo.
echo ================================
echo   Services launched!
echo ================================
echo.
echo   Backend:  http://localhost:8000/docs
echo   Frontend: http://localhost:3000
echo.
echo   Stop: close Backend and Frontend windows.
echo.
echo   This window stays open ^(cmd /k^). Close it when you like.
echo.
pause
endlocal
