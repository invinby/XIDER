@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Ostanovka XIDER TG-BOT Servera
echo ======================================
echo    Ostanovka servera bota XIDER
echo ======================================

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo [OK] Bot ostanovlen.
timeout /t 2 >nul
