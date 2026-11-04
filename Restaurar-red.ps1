#Requires -RunAsAdministrator
# Retira únicamente la regla creada por Habilitar-red-local.ps1.
$ErrorActionPreference='Stop'
Get-NetFirewallRule -Name 'SergioFlix-LAN' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Output 'Regla de red local de SergioFlix retirada.'
