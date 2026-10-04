@echo off
setlocal
cd /d "%~dp0"
title XIDER Agent

if /I "%~1"=="status" (
  schtasks /Query /TN "XIDER Agent" /FO LIST /V
  exit /b %errorlevel%
)

rem Record the owner's explicit start before Task Scheduler starts the worker;
rem Guardian can then recover it after a later crash in this session.
set "XIDER_PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%XIDER_PYTHON%" set "XIDER_PYTHON=python.exe"
"%XIDER_PYTHON%" "%~dp0xider_guardian_wds.py" --set-desired-running start
if errorlevel 1 (
  echo [ERROR] Не удалось сохранить команду запуска Guardian; агент не запущен.
  exit /b 1
)

schtasks /Run /TN "XIDER Agent" >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0install_agent.ps1"
) else (
  echo [OK] XIDER Agent started in background.
)
endlocal
