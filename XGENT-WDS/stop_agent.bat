@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Ostanovka XGENT Windows Agenta
echo ======================================
echo    Ostanovka XGENT-WDS
echo ======================================

schtasks /End /TN "XIDER Agent" >nul 2>&1
taskkill /IM XGENT-WDS.exe /F >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*xgent_wds.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo [OK] Agent ostanovlen.
timeout /t 2 >nul
