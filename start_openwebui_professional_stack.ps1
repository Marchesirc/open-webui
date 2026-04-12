[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

$projectRoot = $PSScriptRoot
$managerScript = Join-Path $projectRoot 'Gerenciar_Tudo_Projetos.ps1'

if (-not (Test-Path $managerScript)) {
    throw "Script único não encontrado: $managerScript"
}

Write-Host '===============================================' -ForegroundColor Cyan
Write-Host ' Open WebUI Professional Stack' -ForegroundColor Cyan
Write-Host '===============================================' -ForegroundColor Cyan
Write-Host 'Inicialização unificada com limpeza, organização e fallback de porta.' -ForegroundColor Yellow
Write-Host ''

& $managerScript -ForceRestart
exit $LASTEXITCODE
