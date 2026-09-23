@echo off
chcp 65001 >nul
cd /d "%~dp0"
title XIDER TG-BOT SERVER
color 0B

echo ======================================================================
echo                  XIDER TELEGRAM BOT SERVER
echo ======================================================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [..] Sozdanie virtualnogo okruzheniya...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Ne udalos sozdat venv! Ubedites chto Python ustanovlen.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Ne udalos aktivirovat venv!
    pause
    exit /b 1
)

echo [..] Proverka zavisimostey...
python -m pip install -q -r requirements.txt

echo.
echo [SERVER] Zapusk bota...
echo [SERVER] Dlya ostanovki nazhmite Ctrl+C
echo ======================================================================
echo.

python -u bot.py

echo.
echo ======================================================================
echo [SERVER] Server zavershil rabotu.
echo ======================================================================
pause
