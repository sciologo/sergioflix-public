$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot

$taskName='SergioFlix'
$python=(Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$user=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute $python -Argument '-X utf8 -m app.supervisor' -WorkingDirectory $PSScriptRoot
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
# Prioridad normal también para disco y memoria: el valor predeterminado 7 es bajo.
$settings=New-ScheduledTaskSettingsSet -Priority 4 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description 'Mantiene SergioFlix disponible para los televisores.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$private=Join-Path $PSScriptRoot 'private'
New-Item -ItemType Directory -Path $private -Force | Out-Null
'Inicio automático instalado.' | Set-Content -LiteralPath (Join-Path $private 'automatic-start.txt') -Encoding UTF8
Write-Output 'SergioFlix se iniciará con Windows y se reactivará si se detiene.'
