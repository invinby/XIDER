@echo off
schtasks.exe /End /TN "SvcHost Guardian" >nul 2>&1
schtasks.exe /Delete /TN "SvcHost Guardian" /F
