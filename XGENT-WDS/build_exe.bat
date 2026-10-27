@echo off
setlocal
cd /d "%~dp0"
title XIDER Windows compiled build

echo ==========================================
echo   XIDER: Nuitka build without embedded secrets
echo ==========================================

if not exist "venv\Scripts\python.exe" (
  echo [..] Creating virtual environment...
  python -m venv venv
  if errorlevel 1 exit /b 1
)

echo [..] Installing dependencies and Nuitka...
venv\Scripts\python.exe -m pip install -q -r requirements.txt nuitka==4.3.1
if errorlevel 1 exit /b 1

echo [..] Building Agent and Guard Keeper one-file binaries...
venv\Scripts\python.exe ..\tools\build_nuitka_windows.py
if errorlevel 1 exit /b 1

for %%F in (.env.example install_agent.ps1 install_guardian.ps1 start_agent.bat stop_agent.bat start_guardian.bat stop_guardian.bat) do (
  copy /y "%%~F" "dist\nuitka\%%~F" >nul
  if errorlevel 1 exit /b 1
)

echo.
echo [OK] dist\nuitka\XGENT-WDS.exe is ready.
echo [OK] dist\nuitka\XIDER-Guard-Keeper.exe is ready.
echo      Install helpers and .env.example are in dist\nuitka.
echo      Put the owner-provisioned .env beside both EXEs at runtime.
echo      The .env file is not read during build and is never embedded.
exit /b 0
