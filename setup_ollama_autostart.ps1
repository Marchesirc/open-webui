[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = 'Stop'

$taskName = 'Ollama Auto Start (OpenWebUI)'
$scriptPath = Join-Path $PSScriptRoot 'start_ollama_background.ps1'

if (-not (Test-Path $scriptPath)) {
    throw "Script de inicialização não encontrado: $scriptPath"
}

$principalUser = if ($env:USERDOMAIN) { "$($env:USERDOMAIN)\$($env:USERNAME)" } else { $env:USERNAME }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $principalUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Description 'Inicia o Ollama automaticamente no login para o stack Open WebUI + Proteus.' -Force | Out-Null

& $scriptPath
if ($LASTEXITCODE -ne 0) {
    throw 'A tarefa foi criada, mas o start imediato do Ollama falhou.'
}

Write-Host "Tarefa agendada criada/atualizada: $taskName" -ForegroundColor Green
