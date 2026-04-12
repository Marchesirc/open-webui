param(
    [string]$OpenWebUIRoot = 'D:\open-webui'
)

$ErrorActionPreference = 'Stop'

$configPath = Join-Path $PSScriptRoot 'proteus_bridge_config.json'
$safeConfigPath = Join-Path $PSScriptRoot 'proteus_bridge_config.enterprise-safe.json'
$backupPath = Join-Path $PSScriptRoot 'proteus_bridge_config.before-enterprise-safe.backup.json'
$maintenanceScript = Join-Path $PSScriptRoot 'manutencao_openwebui_profissional.ps1'

if (-not (Test-Path $configPath)) { throw "Config atual nao encontrada: $configPath" }
if (-not (Test-Path $safeConfigPath)) { throw "Config enterprise-safe nao encontrada: $safeConfigPath" }
if (-not (Test-Path $maintenanceScript)) { throw "Script de manutencao nao encontrado: $maintenanceScript" }

Copy-Item $configPath $backupPath -Force
Copy-Item $safeConfigPath $configPath -Force

Write-Host 'Modo enterprise-safe aplicado ao bridge.' -ForegroundColor Green
Write-Host "Backup salvo em: $backupPath"

& powershell -ExecutionPolicy Bypass -File $maintenanceScript -OpenWebUIRoot $OpenWebUIRoot -ForceRestart
exit $LASTEXITCODE
