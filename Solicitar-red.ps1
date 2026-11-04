$scriptPath=Join-Path $PSScriptRoot 'Habilitar-red-local.ps1'
try {
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$scriptPath+'"'
    $result=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $arguments
    exit $result.ExitCode
} catch {exit 1}
