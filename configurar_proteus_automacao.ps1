param(
    [string]$ProjectsRoot = '',
    [string]$CopyFrom = '',
    [string]$StartMacro,
    [string]$StopMacro,
    [string]$PauseMacro,
    [string]$ResetMacro,
    [object]$RestartBridge = $true
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

if ([string]::IsNullOrWhiteSpace($ProjectsRoot)) {
    $ProjectsRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'Proteus_Projects'
}

$configPath = Join-Path $PSScriptRoot 'proteus_bridge_config.json'
$bridgeLauncher = Join-Path $PSScriptRoot 'start_proteus_bridge.ps1'

if (-not (Test-Path $configPath)) {
    throw "Arquivo de configuração não encontrado: $configPath"
}

New-Item -ItemType Directory -Force -Path $ProjectsRoot | Out-Null

if ($CopyFrom -and (Test-Path $CopyFrom)) {
    Write-Host "Importando projetos do Proteus de: $CopyFrom" -ForegroundColor Cyan
    $projectFiles = Get-ChildItem -Path $CopyFrom -Recurse -File -Include *.pdsprj,*.dsn -ErrorAction SilentlyContinue
    foreach ($file in $projectFiles) {
        $sourceDir = $file.Directory.FullName
        $targetDir = Join-Path $ProjectsRoot (Split-Path $sourceDir -Leaf)
        if (-not (Test-Path $targetDir)) {
            Copy-Item -Path $sourceDir -Destination $targetDir -Recurse -Force
            Write-Host "Copiado: $sourceDir -> $targetDir" -ForegroundColor Green
        }
    }
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json
$config.projects_root = ($ProjectsRoot -replace '\\', '/')

if ($PSBoundParameters.ContainsKey('StartMacro')) { $config.ui_macros.start = $StartMacro }
if ($PSBoundParameters.ContainsKey('StopMacro')) { $config.ui_macros.stop = $StopMacro }
if ($PSBoundParameters.ContainsKey('PauseMacro')) { $config.ui_macros.pause = $PauseMacro }
if ($PSBoundParameters.ContainsKey('ResetMacro')) { $config.ui_macros.reset = $ResetMacro }

$configJson = $config | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($configPath, $configJson, [System.Text.UTF8Encoding]::new($false))

Write-Host "Configuração atualizada em: $configPath" -ForegroundColor Green
Write-Host "projects_root = $ProjectsRoot" -ForegroundColor Yellow
Write-Host "ui_macros.start = $($config.ui_macros.start)" -ForegroundColor Yellow
Write-Host "ui_macros.stop  = $($config.ui_macros.stop)" -ForegroundColor Yellow
Write-Host "ui_macros.pause = $($config.ui_macros.pause)" -ForegroundColor Yellow
Write-Host "ui_macros.reset = $($config.ui_macros.reset)" -ForegroundColor Yellow

$shouldRestart = $true
if ($null -ne $RestartBridge) {
    $normalizedRestart = ("$RestartBridge").Trim().ToLowerInvariant()
    if ($normalizedRestart -in @('false', '$false', '0', 'no', 'n')) {
        $shouldRestart = $false
    } elseif ($normalizedRestart -in @('true', '$true', '1', 'yes', 'y', '')) {
        $shouldRestart = $true
    }
}

if ($shouldRestart -and (Test-Path $bridgeLauncher)) {
    Write-Host 'Reiniciando o bridge do Proteus...' -ForegroundColor Cyan
    $listener = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    }

    Start-Process powershell -ArgumentList @('-ExecutionPolicy', 'Bypass', '-File', $bridgeLauncher)

    $bridgeOk = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 2
            if ($health.ok -eq $true) {
                $bridgeOk = $true
                break
            }
        } catch {}
        Start-Sleep -Milliseconds 500
    }

    if ($bridgeOk) {
        Write-Host 'Bridge reiniciado com sucesso em http://127.0.0.1:8001' -ForegroundColor Green
    } else {
        Write-Host 'Bridge foi iniciado, mas ainda não respondeu dentro do tempo esperado.' -ForegroundColor Yellow
    }
}

$found = Get-ChildItem -Path $ProjectsRoot -Recurse -File -Include *.pdsprj,*.dsn -ErrorAction SilentlyContinue
Write-Host "Projetos detectados: $($found.Count)" -ForegroundColor Cyan
$found | Select-Object FullName | Format-Table -HideTableHeaders
