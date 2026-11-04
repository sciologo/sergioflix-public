@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SergioFlix - instalar en el televisor
setlocal enabledelayedexpansion

echo.
echo   SergioFlix para televisor
echo   =========================
echo.

rem ---------------------------------------------------------------- herramientas
set "TIZEN="
if defined TIZEN_HOME if exist "%TIZEN_HOME%\tools\ide\bin\tizen.bat" set "TIZEN=%TIZEN_HOME%"
if not defined TIZEN if exist "%USERPROFILE%\tizen-studio\tools\ide\bin\tizen.bat" set "TIZEN=%USERPROFILE%\tizen-studio"
if not defined TIZEN if exist "C:\tizen-studio\tools\ide\bin\tizen.bat" set "TIZEN=C:\tizen-studio"

if not defined TIZEN (
  echo   No encuentro Tizen Studio en este equipo.
  echo.
  echo   Hace falta una vez, para poder firmar e instalar:
  echo     1. Descarga Tizen Studio desde developer.tizen.org
  echo     2. En Package Manager, instala "TV Extensions" y "Samsung Certificate Extension"
  echo     3. En Certificate Manager, crea un certificado Samsung con tu cuenta,
  echo        incluyendo tus televisores. Anota el nombre del perfil.
  echo.
  echo   Despues vuelve a abrir este acceso.
  echo.
  pause
  exit /b 1
)
set "TIZENBAT=%TIZEN%\tools\ide\bin\tizen.bat"
set "SDB=%TIZEN%\tools\sdb.exe"
echo   Tizen Studio: %TIZEN%

rem ------------------------------------------------------------------- datos
set "TVIP=%~1"
if "%TVIP%"=="" set /p TVIP=  Direccion IP del televisor (Ajustes ^> General ^> Red):
if "%TVIP%"=="" (
  echo   Sin direccion no se puede continuar.
  pause
  exit /b 1
)

set "PERFIL=%~2"
if "%PERFIL%"=="" set /p PERFIL=  Nombre del perfil de certificado (Certificate Manager):
if "%PERFIL%"=="" (
  echo   Sin perfil de certificado el televisor rechaza la instalacion.
  pause
  exit /b 1
)

rem -------------------------------------------------- direccion de la videoteca
echo.
echo   Ajustando la direccion de la videoteca...
".venv\Scripts\python.exe" -X utf8 -m tizen.direccion
if errorlevel 1 goto :error

rem ------------------------------------------------------------------ conectar
echo.
echo   Conectando con el televisor %TVIP%...
echo   (el televisor debe estar encendido y con el modo desarrollador activado)
"%SDB%" connect %TVIP%:26101
if errorlevel 1 goto :sinTele
"%SDB%" devices

rem ----------------------------------------------------------------- empaquetar
echo.
echo   Empaquetando...
call "%TIZENBAT%" build-web -- "private\tizen-app"
if errorlevel 1 goto :error
call "%TIZENBAT%" package -t wgt -s "%PERFIL%" -- "private\tizen-app\.buildResult"
if errorlevel 1 goto :sinFirma

rem ------------------------------------------------------------------ instalar
echo.
echo   Instalando en el televisor...
call "%TIZENBAT%" install -n "SergioFlix.wgt" -- "private\tizen-app\.buildResult"
if errorlevel 1 goto :error

echo.
echo   Listo. Busca SergioFlix entre las aplicaciones del televisor.
echo   La interfaz se sirve desde este equipo, asi que las mejoras futuras
echo   llegan solas: no hace falta volver a instalar.
echo.
pause
exit /b 0

:sinTele
echo.
echo   No respondio el televisor en %TVIP%.
echo   En el televisor: Aplicaciones ^> pulsa 1 2 3 4 5 con el mando ^>
echo   Modo desarrollador ENCENDIDO ^> escribe la IP de este equipo ^> reinicia el televisor.
echo.
pause
exit /b 1

:sinFirma
echo.
echo   Fallo la firma con el perfil "%PERFIL%".
echo   Comprueba en Certificate Manager que el perfil existe y que incluye
echo   el identificador (DUID) de este televisor.
echo.
pause
exit /b 1

:error
echo.
echo   La instalacion no se completo. Revisa el mensaje anterior.
echo.
pause
exit /b 1
