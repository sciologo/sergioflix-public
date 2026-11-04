#Requires -RunAsAdministrator
# Regla propia limitada a la IPv4 configurada, al proceso Python y a la red privada.
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$basePython = & '.\.venv\Scripts\python.exe' -c 'import sys; print(sys._base_executable)'
if ($LASTEXITCODE -ne 0) { throw 'Crea primero el entorno Python e instala las dependencias.' }
$lanAddress = & '.\.venv\Scripts\python.exe' -c 'from app.settings import Settings; print(Settings().lan_ip)'
if ($LASTEXITCODE -ne 0) { throw 'Genera primero la configuración .env.' }
if ($lanAddress -eq '127.0.0.1') { throw 'Configura VIDEO_LAN_IP con la IPv4 LAN de este PC.' }
$interface = (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $lanAddress -ErrorAction Stop).InterfaceAlias
$ruleName='SergioFlix-LAN'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName -DisplayName 'SergioFlix: red local privada' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443,8765 -RemoteAddress LocalSubnet -LocalAddress $lanAddress -InterfaceAlias $interface -Program $basePython -Profile Private | Out-Null
}
# No se modifican reglas ajenas; Restaurar-red.ps1 retira solo esta excepción.
$private=Join-Path $PSScriptRoot 'private'
New-Item -ItemType Directory -Path $private -Force | Out-Null
'Red local preparada.' | Set-Content -LiteralPath (Join-Path $private 'network-enabled.txt') -Encoding UTF8
Write-Output 'Regla SergioFlix-LAN preparada. La conexión de Windows debe ser de tipo Privada.'
