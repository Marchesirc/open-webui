param(
    [switch]$ForceRestart,
    [switch]$ArchiveGenerated,
    [switch]$SkipShellCleanup
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

$projectRoot = $PSScriptRoot
$workspaceRoot = Split-Path $projectRoot -Parent
$logsRoot = Join-Path $workspaceRoot '_Logs\OpenWebUI_Proteus'
$archiveRoot = Join-Path $workspaceRoot '_Arquivos_Gerados'
$restartScript = Join-Path $projectRoot 'restart_openwebui_with_proteus_tool.ps1'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'

function New-DirectoryIfMissing {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Test-WebUiHttp {
    param([int]$Port)
    try {
        $response = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$Port/api/config" -TimeoutSec 3
        return ($response.status -eq $true -and $response.name -match 'Open WebUI')
    } catch {
        return $false
    }
}

function Get-ProjectListenerSummary {
    $ports = 8000, 8001, 8080, 8081, 8082, 8765, 11434
    return Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in $ports } |
        Sort-Object LocalPort |
        Select-Object LocalAddress, LocalPort, OwningProcess, State
}

function Stop-ProjectShellClutter {
    $patterns = @(
        'OpenWebUI_Proteus\\start_proteus_bridge.ps1',
        'OpenWebUI_Proteus\\start_local_pc_mcp.ps1',
        'OpenWebUI_Proteus\\restart_openwebui_with_proteus_tool.ps1',
        'OpenWebUI_Proteus\\start_openwebui_professional_stack.ps1',
        'OpenWebUI_Proteus\\Gerenciar_Tudo_Projetos.ps1'
    )

    $targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cmdLine = $_.CommandLine
        if ($_.ProcessId -eq $PID -or $_.Name -notin @('powershell.exe', 'pwsh.exe', 'cmd.exe') -or -not $cmdLine) {
            return $false
        }

        foreach ($pattern in $patterns) {
            if ($cmdLine -match $pattern) {
                return $true
            }
        }

        return $false
    }

    foreach ($proc in ($targets | Sort-Object ProcessId -Descending)) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

function Move-GeneratedArtifactsToArchive {
    param([string]$DestinationRoot, [string]$Stamp)

    New-DirectoryIfMissing -Path $DestinationRoot
    $destination = Join-Path $DestinationRoot $Stamp
    New-DirectoryIfMissing -Path $destination

    $itemsToArchive = @('__pycache__', 'build', 'dist')
    $moved = New-Object System.Collections.Generic.List[string]

    foreach ($name in $itemsToArchive) {
        $source = Join-Path $workspaceRoot $name
        if (Test-Path $source) {
            $target = Join-Path $destination $name
            Move-Item -Path $source -Destination $target -Force
            $moved.Add($name)
        }
    }

    return $moved
}

New-DirectoryIfMissing -Path $logsRoot
New-DirectoryIfMissing -Path $archiveRoot

Write-Host '===============================================' -ForegroundColor Cyan
Write-Host ' Gerenciador Único de Projetos' -ForegroundColor Cyan
Write-Host '===============================================' -ForegroundColor Cyan
Write-Host "Raiz:  $workspaceRoot" -ForegroundColor Yellow
Write-Host "Logs:  $logsRoot" -ForegroundColor Yellow
Write-Host ''

if (-not $SkipShellCleanup) {
    Write-Host 'Limpando shells antigos relacionados ao projeto...' -ForegroundColor Yellow
    Stop-ProjectShellClutter
}

if ($ArchiveGenerated) {
    $moved = Move-GeneratedArtifactsToArchive -DestinationRoot $archiveRoot -Stamp $timestamp
    if ($moved.Count -gt 0) {
        Write-Host ('Arquivos gerados arquivados em ' + (Join-Path $archiveRoot $timestamp) + ': ' + ($moved -join ', ')) -ForegroundColor Green
    } else {
        Write-Host 'Nenhum artefato gerado para arquivar.' -ForegroundColor DarkGray
    }
}

if (-not (Test-Path $restartScript)) {
    throw "Script principal não encontrado: $restartScript"
}

& $restartScript -ForceRestart:$ForceRestart -LogsRoot $logsRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Falha ao iniciar o stack principal.'
}

$webUrl = $null
$currentWebUiFile = Join-Path $logsRoot 'current_webui_url.txt'
if (Test-Path $currentWebUiFile) {
    $candidateUrl = (Get-Content $currentWebUiFile -TotalCount 1 -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($candidateUrl -match '^http://127\.0\.0\.1:(\d+)$') {
        $webUrl = $candidateUrl
        $candidatePort = [int]$Matches[1]
        for ($i = 0; $i -lt 12; $i++) {
            if (Test-WebUiHttp -Port $candidatePort) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

if (-not $webUrl) {
    foreach ($port in @(8082, 8080, 8081)) {
        if (Test-WebUiHttp -Port $port) {
            $webUrl = "http://127.0.0.1:$port"
            break
        }
    }
}

Write-Host ''
Write-Host 'Resumo dos serviços:' -ForegroundColor Cyan
Get-ProjectListenerSummary | Format-Table -AutoSize

if ($webUrl) {
    Write-Host ''
    Write-Host "Open WebUI pronto em: $webUrl" -ForegroundColor Green
} else {
    Write-Host ''
    Write-Host 'Open WebUI ainda está iniciando; consulte os logs em _Logs\OpenWebUI_Proteus.' -ForegroundColor Yellow
}
