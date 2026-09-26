@echo off
schtasks.exe /End /TN "XIDER Guardian" >nul 2>&1
schtasks.exe /Delete /TN "XIDER Guardian" /F
