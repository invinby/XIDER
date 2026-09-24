@echo off
setlocal
cd /d "%~dp0"
title XGENT-WDS secure build

echo ==========================================
echo   XGENT-WDS: build without embedded secrets
echo ==========================================

if not exist ".env" (
  echo [FAIL] .env is required for local testing, but it is never embedded in the EXE.
  echo        Copy .env.example to .env and fill it locally, then run this again.
  exit /b 2
)

if not exist "venv\Scripts\python.exe" (
  echo [..] Creating virtual environment...
  python -m venv venv
  if errorlevel 1 exit /b 1
)

echo [..] Installing dependencies and PyInstaller...
venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

echo [..] Building the sidecar-configured executable...
venv\Scripts\python.exe -m PyInstaller --clean --noconfirm XGENT-WDS.spec
if errorlevel 1 exit /b 1

copy /y "dist\XGENT-WDS.exe" "XGENT-WDS.exe" >nul
copy /y ".env.example" "dist\.env.example" >nul

echo.
echo [OK] dist\XGENT-WDS.exe is ready.
echo      Keep a real .env beside the EXE at runtime; secrets are not inside the binary.
exit /b 0
