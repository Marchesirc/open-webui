param(
    [string]$OpenWebUIRoot = 'D:\open-webui'
)

$ErrorActionPreference = 'Stop'

$configPath = Join-Path $PSScriptRoot 'proteus_bridge_config.json'
$localConfigPath = Join-Path $PSScriptRoot 'proteus_bridge_config.professional-local.json'
$maintenanceScript = Join-Path $PSScriptRoot 'manutencao_openwebui_profissional.ps1'

if (-not (Test-Path $configPath)) { throw "Config atual nao encontrada: $configPath" }
if (-not (Test-Path $localConfigPath)) { throw "Config profissional-local nao encontrada: $localConfigPath" }
if (-not (Test-Path $maintenanceScript)) { throw "Script de manutencao nao encontrado: $maintenanceScript" }

Copy-Item $localConfigPath $configPath -Force

Write-Host 'Modo profissional-local aplicado ao bridge.' -ForegroundColor Green

& powershell -ExecutionPolicy Bypass -File $maintenanceScript -OpenWebUIRoot $OpenWebUIRoot -ForceRestart
exit $LASTEXITCODE
