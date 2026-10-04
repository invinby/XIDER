@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Ostanovka XGENT Windows Agenta
echo ======================================
echo    Ostanovka XGENT-WDS
echo ======================================

rem Persist an intentional stop before ending the worker, otherwise Guardian
rem interprets the process exit as a crash and immediately starts it again.
set "XIDER_PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%XIDER_PYTHON%" set "XIDER_PYTHON=python.exe"
"%XIDER_PYTHON%" "%~dp0xider_guardian_wds.py" --set-desired-running stop
if errorlevel 1 (
  echo [ERROR] Не удалось сохранить команду остановки Guardian; агент не остановлен.
  exit /b 1
)

schtasks /End /TN "XIDER Agent" >nul 2>&1
taskkill /IM XGENT-WDS.exe /F >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*xgent_wds.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo [OK] Агент остановлен; Guardian сохранит это решение.
timeout /t 2 >nul
