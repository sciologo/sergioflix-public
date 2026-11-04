@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar-inicio-automatico.ps1"
if errorlevel 1 (
  echo No se pudo instalar el inicio automático. Ejecuta este acceso como administrador.
) else (
  echo Inicio automático instalado y servicio en ejecución.
)
pause
