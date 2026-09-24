@echo off
setlocal
cd /d "%~dp0"
title XIDER Agent

schtasks /Run /TN "XIDER Agent" >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0install_agent.ps1"
) else (
  echo [OK] XIDER Agent started in background.
)
endlocal
