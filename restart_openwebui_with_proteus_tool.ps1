param(
    [switch]$ForceRestart,
    [switch]$Foreground,
    [string]$OpenWebUIRepo = '',
    [string]$LogsRoot = ''
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

function Test-OpenWebUIRepoCandidate {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path (Join-Path $Path 'backend\open_webui\main.py'))
}

function Resolve-OpenWebUIRepo {
    param([string]$PreferredPath, [string]$ScriptRoot)

    $workspaceRoot = Split-Path $ScriptRoot -Parent
    $candidates = @(
        $PreferredPath,
        (Join-Path $workspaceRoot 'open-webui'),
        (Join-Path $ScriptRoot 'open-webui'),
        (Join-Path (Split-Path $workspaceRoot -Parent) 'open-webui'),
        (Join-Path $env:USERPROFILE 'open-webui'),
        'C:\Users\rober\open-webui'
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($candidate in $candidates) {
        if (Test-OpenWebUIRepoCandidate -Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Open WebUI não encontrado automaticamente. Informe -OpenWebUIRepo apontando para a pasta do repositório."
}

$resolvedRepo = Resolve-OpenWebUIRepo -PreferredPath $OpenWebUIRepo -ScriptRoot $PSScriptRoot
$repoBackend = Join-Path $resolvedRepo 'backend'
$pythonExe = Join-Path $repoBackend '.venv\Scripts\python.exe'
$applyScript = Join-Path $PSScriptRoot 'apply_openwebui_tool_server.py'
$bridgeScript = Join-Path $PSScriptRoot 'start_proteus_bridge.ps1'
$localMcpScript = Join-Path $PSScriptRoot 'start_local_pc_mcp.ps1'
$preferredWebUiPorts = @(8080, 8082, 8081)
$webUiPort = 8080
$legacyPorts = @(8080, 8081, 8082)
$recommendedCors = 'http://127.0.0.1:8080;http://127.0.0.1:8082;http://127.0.0.1:8081;http://localhost:8080;http://localhost:8082;http://localhost:8081'

if ([string]::IsNullOrWhiteSpace($LogsRoot)) {
    $LogsRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '_Logs\OpenWebUI_Proteus'
}
if (-not (Test-Path $LogsRoot)) {
    New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
}

function Start-HiddenPowerShellScript {
    param(
        [string]$ScriptPath,
        [string]$LogPrefix
    )

    if (-not (Test-Path $ScriptPath)) {
        return
    }

    $stdoutLog = Join-Path $LogsRoot ("$LogPrefix.out.log")
    $stderrLog = Join-Path $LogsRoot ("$LogPrefix.err.log")

    Start-Process powershell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $ScriptPath
    ) -WorkingDirectory (Split-Path $ScriptPath -Parent) -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null
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

function Test-PortAvailable {
    param([int]$Port)
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return ($null -eq $listener)
}

function Resolve-WebUiPort {
    param(
        [int[]]$Candidates,
        [int]$PreferredPort = 0
    )

    if ($PreferredPort -gt 0) {
        if ($Candidates -notcontains $PreferredPort) {
            $Candidates = @($PreferredPort) + $Candidates
        } else {
            $Candidates = @($PreferredPort) + ($Candidates | Where-Object { $_ -ne $PreferredPort })
        }
    }

    foreach ($port in $Candidates) {
        if (Test-PortAvailable -Port $port) {
            return $port
        }
    }

    return $Candidates[-1]
}

function Get-RecordedWebUiPort {
    param([string]$LogsDir)

    $urlFile = Join-Path $LogsDir 'current_webui_url.txt'
    if (-not (Test-Path $urlFile)) {
        return $null
    }

    try {
        $urlText = (Get-Content -Path $urlFile -Raw -Encoding UTF8).Trim()
        if ($urlText -match 'http://127\.0\.0\.1:(\d+)') {
            return [int]$matches[1]
        }
    } catch {}

    return $null
}

function Get-RunningOpenWebUiPort {
    param([int[]]$Candidates)

    foreach ($port in $Candidates) {
        if (Test-WebUiHttp -Port $port) {
            return $port
        }
    }

    return $null
}

function Stop-OpenWebUiProcesses {
    $stopped = New-Object System.Collections.Generic.List[string]

    $listeners = Get-NetTCPConnection -LocalPort $legacyPorts -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            $stopped.Add("pid=$($listener.OwningProcess) port=$($listener.LocalPort)")
        } catch {}
    }

    $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -match 'open_webui\.main:app' -or
            $_.CommandLine -match '--port 8080' -or
            $_.CommandLine -match '--port 8081' -or
            $_.CommandLine -match '--port 8082'
        )
    }

    foreach ($proc in ($candidates | Sort-Object ProcessId -Descending)) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped.Add("pid=$($proc.ProcessId)")
        } catch {}
    }

    return $stopped | Select-Object -Unique
}

function Test-BridgeHttp {
    try {
        $response = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        return ($response.ok -eq $true)
    } catch {
        return $false
    }
}

function Test-LocalPcMcpHttp {
    try {
        $response = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 3
        return ($response.ok -eq $true)
    } catch {
        return $false
    }
}

function Stop-ServiceListener {
    param([int]$Port, [string]$Name)

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host "$Name encerrado na porta $Port (pid=$($listener.OwningProcess))" -ForegroundColor Yellow
        } catch {}
    }
}

if (-not (Test-Path $pythonExe)) {
    throw "Python do Open WebUI não encontrado em $pythonExe"
}

if ($ForceRestart) {
    Stop-ServiceListener -Port 8001 -Name 'Bridge do Proteus'
    Stop-ServiceListener -Port 8765 -Name 'Local PC MCP'
}

Set-Location $repoBackend

if (-not (Test-BridgeHttp) -and (Test-Path $bridgeScript)) {
    Write-Host 'Bridge do Proteus não está ativo; iniciando em background...' -ForegroundColor Yellow
    Start-HiddenPowerShellScript -ScriptPath $bridgeScript -LogPrefix 'proteus_bridge'

    $bridgeReady = $false
    for ($i = 0; $i -lt 12; $i++) {
        if (Test-BridgeHttp) {
            $bridgeReady = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if ($bridgeReady) {
        Write-Host 'Bridge do Proteus confirmado em http://127.0.0.1:8001' -ForegroundColor Green
    } else {
        Write-Host 'Bridge ainda não respondeu a tempo; seguirei com a configuração do Open WebUI.' -ForegroundColor Yellow
    }
}

if (-not (Test-LocalPcMcpHttp) -and (Test-Path $localMcpScript)) {
    Write-Host 'Local PC MCP não está ativo; iniciando em background...' -ForegroundColor Yellow
    Start-HiddenPowerShellScript -ScriptPath $localMcpScript -LogPrefix 'local_pc_mcp'

    $mcpReady = $false
    for ($i = 0; $i -lt 12; $i++) {
        if (Test-LocalPcMcpHttp) {
            $mcpReady = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if ($mcpReady) {
        Write-Host 'Local PC MCP confirmado em http://127.0.0.1:8765/mcp' -ForegroundColor Green
    } else {
        Write-Host 'Local PC MCP ainda não respondeu a tempo; seguirei com a configuração do Open WebUI.' -ForegroundColor Yellow
    }
}

& $pythonExe $applyScript

if (-not $ForceRestart) {
    if (Test-WebUiHttp -Port $webUiPort) {
        Write-Host "Open WebUI já está em execução em http://127.0.0.1:$webUiPort" -ForegroundColor Green
        exit 0
    }
}

$runningPort = Get-RunningOpenWebUiPort -Candidates $preferredWebUiPorts
$recordedPort = Get-RecordedWebUiPort -LogsDir $LogsRoot
$preferredStablePort = $null

if ($runningPort) {
    $preferredStablePort = $runningPort
} elseif ($recordedPort) {
    $preferredStablePort = $recordedPort
}

$stopped = Stop-OpenWebUiProcesses
if ($stopped.Count -gt 0) {
    Write-Host ('Processos Open WebUI encerrados: ' + ($stopped -join ', ')) -ForegroundColor Yellow
}

$runningAfterCleanup = Get-RunningOpenWebUiPort -Candidates $preferredWebUiPorts
if ($runningAfterCleanup -and -not $ForceRestart) {
    $currentWebUiUrl = "http://127.0.0.1:$runningAfterCleanup"
    Set-Content -Path (Join-Path $LogsRoot 'current_webui_url.txt') -Value $currentWebUiUrl -Encoding UTF8
    Write-Host "Instância Open WebUI já ativa em $currentWebUiUrl; reutilizando porta estável." -ForegroundColor Green
    exit 0
}

if ($runningAfterCleanup -and $ForceRestart) {
    Write-Host "Instância ainda ativa após limpeza; forçando reinício limpo da porta $runningAfterCleanup." -ForegroundColor Yellow
    Stop-ServiceListener -Port $runningAfterCleanup -Name 'Open WebUI residual'
}

$preferredPortForSelection = 0
if ($preferredStablePort) {
    $preferredPortForSelection = [int]$preferredStablePort
}

$webUiPort = Resolve-WebUiPort -Candidates $preferredWebUiPorts -PreferredPort $preferredPortForSelection
$currentWebUiUrl = "http://127.0.0.1:$webUiPort"
Set-Content -Path (Join-Path $LogsRoot 'current_webui_url.txt') -Value $currentWebUiUrl -Encoding UTF8

if ($preferredStablePort -and $webUiPort -eq $preferredStablePort) {
    Write-Host "Mantendo porta estável do Open WebUI: $webUiPort" -ForegroundColor Green
}

if ($webUiPort -ne 8080) {
    Write-Host "Porta 8080 continua ocupada ou protegida; usando fallback $currentWebUiUrl" -ForegroundColor Yellow
}

Write-Host "Iniciando Open WebUI em $currentWebUiUrl" -ForegroundColor Cyan

if ([string]::IsNullOrWhiteSpace($env:CORS_ALLOW_ORIGIN)) {
    $env:CORS_ALLOW_ORIGIN = $recommendedCors
    Write-Host "CORS_ALLOW_ORIGIN aplicado (escopo do processo): $recommendedCors" -ForegroundColor Green
}

if ($env:DEFAULT_LOCALE -ne 'pt-BR') {
    $env:DEFAULT_LOCALE = 'pt-BR'
    Write-Host 'DEFAULT_LOCALE aplicado (escopo do processo): pt-BR' -ForegroundColor Green
}

if ($env:PYTHONUTF8 -ne '1') {
    $env:PYTHONUTF8 = '1'
    Write-Host 'PYTHONUTF8 aplicado (escopo do processo): 1' -ForegroundColor Green
}

if ($env:PYTHONIOENCODING -ne 'utf-8') {
    $env:PYTHONIOENCODING = 'utf-8'
    Write-Host 'PYTHONIOENCODING aplicado (escopo do processo): utf-8' -ForegroundColor Green
}

if ($Foreground) {
    & $pythonExe -m uvicorn open_webui.main:app --host 127.0.0.1 --port $webUiPort
} else {
    $stdoutLog = Join-Path $LogsRoot ("openwebui_$webUiPort.out.log")
    $stderrLog = Join-Path $LogsRoot ("openwebui_$webUiPort.err.log")

    Start-Process -FilePath $pythonExe -ArgumentList @(
        '-m', 'uvicorn',
        'open_webui.main:app',
        '--host', '127.0.0.1',
        '--port', "$webUiPort"
    ) -WorkingDirectory $repoBackend -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null

    Write-Host "Open WebUI iniciado em background. Logs: $stdoutLog" -ForegroundColor Green
    exit 0
}
