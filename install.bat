@echo off
chcp 65001 >nul
title Offer Processor - Install Dependencies
setlocal

echo.
echo ================================
echo   INSTALL DEPENDENCIES ONLY
echo ================================
echo.

cd /d "%~dp0"

echo Python version:
python --version
if errorlevel 1 (
    echo.
    echo ERROR: Python not found. Install from python.org and check "Add to PATH".
    pause
    exit /b 1
)

echo.
echo Node version:
node --version
if errorlevel 1 (
    echo.
    echo ERROR: Node.js not found. Install from nodejs.org.
    pause
    exit /b 1
)

echo.
echo === Creating Python venv ===
if exist "backend\.venv" (
    echo Already exists, skipping.
) else (
    python -m venv backend\.venv
)

echo.
echo === Upgrading pip ===
backend\.venv\Scripts\python.exe -m pip install --upgrade pip

echo.
echo === Installing Python dependencies ===
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. See errors above.
    pause
    exit /b 1
)
echo. > backend\.installed

echo.
echo === Installing Node dependencies ===
pushd frontend
call npm install
if errorlevel 1 (
    echo.
    echo ERROR: npm install failed. See errors above.
    popd
    pause
    exit /b 1
)
popd

echo.
echo ================================
echo   ALL INSTALLED!
echo ================================
echo.
echo Now run: start.bat
echo.
pause
endlocal
