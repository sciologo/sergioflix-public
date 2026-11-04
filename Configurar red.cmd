@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Solicitar-red.ps1"
if errorlevel 1 (echo No se pudo habilitar la red. Ejecuta Habilitar-red-local.ps1 como administrador.) else (echo Red local habilitada.)
pause
