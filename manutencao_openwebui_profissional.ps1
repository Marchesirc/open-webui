param(
    [string]$OpenWebUIRoot = 'D:\open-webui',
    [switch]$ForceRestart
)

$ErrorActionPreference = 'Stop'

$pythonExe = Join-Path $OpenWebUIRoot 'backend\.venv\Scripts\python.exe'
$applyScript = Join-Path $PSScriptRoot 'apply_openwebui_tool_server.py'
$restartScript = Join-Path $PSScriptRoot 'restart_openwebui_with_proteus_tool.ps1'
$validateScript = Join-Path $PSScriptRoot 'validate_openwebui_professional_stack.ps1'
$reportScript = Join-Path $PSScriptRoot 'gerar_relatorio_openwebui_stack.ps1'
$bridgeConfigPath = Join-Path $PSScriptRoot 'proteus_bridge_config.json'

if (-not (Test-Path $pythonExe)) { throw "Python da venv nao encontrado: $pythonExe" }
if (-not (Test-Path $applyScript)) { throw "Script ausente: $applyScript" }
if (-not (Test-Path $restartScript)) { throw "Script ausente: $restartScript" }
if (-not (Test-Path $validateScript)) { throw "Script ausente: $validateScript" }
if (-not (Test-Path $reportScript)) { throw "Script ausente: $reportScript" }

if (Test-Path $bridgeConfigPath) {
    try {
        $bridgeCfgRaw = Get-Content -Raw -Encoding UTF8 -Path $bridgeConfigPath
        $bridgeCfg = $bridgeCfgRaw | ConvertFrom-Json
        $bridgeToken = ($bridgeCfg.bridge_api_token | ForEach-Object { "$_" }).Trim()

        if ([string]::IsNullOrWhiteSpace($bridgeToken)) {
            $bridgeToken = [Guid]::NewGuid().ToString('N')
            $bridgeCfg.bridge_api_token = $bridgeToken
            $bridgeCfg | ConvertTo-Json -Depth 32 | Set-Content -Path $bridgeConfigPath -Encoding UTF8
            Write-Host 'bridge_api_token estava vazio e foi gerado automaticamente.' -ForegroundColor Yellow
        }

        if (-not [string]::IsNullOrWhiteSpace($bridgeToken)) {
            $env:OWUI_BRIDGE_TOKEN = $bridgeToken
            Write-Host 'Bridge token detectado no config e exportado para sincronizacao de conexoes.' -ForegroundColor DarkCyan
        }
    }
    catch {
        Write-Host "Aviso: nao foi possivel ler bridge_api_token em $bridgeConfigPath" -ForegroundColor Yellow
    }
}

Write-Host '========================================='
Write-Host ' Manutencao OpenWebUI Profissional'
Write-Host '========================================='
Write-Host '1) Aplicando perfil profissional e tool servers...'
& $pythonExe $applyScript (Join-Path $OpenWebUIRoot 'backend') 'http://127.0.0.1:8001'
if ($LASTEXITCODE -ne 0) { throw 'Falha ao aplicar perfil profissional.' }

Write-Host '2) Reiniciando stack...'
if ($ForceRestart) {
    & powershell -ExecutionPolicy Bypass -File $restartScript -ForceRestart
} else {
    & powershell -ExecutionPolicy Bypass -File $restartScript
}
if ($LASTEXITCODE -ne 0) { throw 'Falha no restart do stack.' }

Write-Host '3) Validando stack...'
& powershell -ExecutionPolicy Bypass -File $validateScript -OpenWebUIRoot $OpenWebUIRoot
$validationCode = $LASTEXITCODE

if ($validationCode -ne 0) {
    Write-Host 'Validacao falhou. Executando auto-correcao (reaplicar + restart forcado)...' -ForegroundColor Yellow
    & $pythonExe $applyScript (Join-Path $OpenWebUIRoot 'backend') 'http://127.0.0.1:8001'
    & powershell -ExecutionPolicy Bypass -File $restartScript -ForceRestart
    & powershell -ExecutionPolicy Bypass -File $validateScript -OpenWebUIRoot $OpenWebUIRoot
    $validationCode = $LASTEXITCODE
}

Write-Host '4) Gerando relatorio tecnico...'
& powershell -ExecutionPolicy Bypass -File $reportScript -OpenWebUIRoot $OpenWebUIRoot
if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar relatorio.' }

if ($validationCode -eq 0) {
    Write-Host 'RESULTADO FINAL: PASS (manutencao concluida com sucesso)' -ForegroundColor Green
    Remove-Item Env:OWUI_BRIDGE_TOKEN -ErrorAction SilentlyContinue
    exit 0
}

Write-Host 'RESULTADO FINAL: FAIL (verifique o relatorio e logs)' -ForegroundColor Red
Remove-Item Env:OWUI_BRIDGE_TOKEN -ErrorAction SilentlyContinue
exit 1
