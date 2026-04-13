param(
    [string]$OutputDir = (Join-Path $PSScriptRoot 'auditorias')
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'

function Invoke-TimedHttp {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Url,
        [object]$Body = $null,
        [int]$TimeoutSec = 10,
        [int[]]$AcceptedStatusCodes = @(200)
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $statusCode = $null
    $responseObj = $null
    $ok = $false
    $errorText = $null

    try {
        if ($null -ne $Body) {
            $jsonBody = ($Body | ConvertTo-Json -Depth 8)
            $raw = Invoke-WebRequest -Uri $Url -Method $Method -Body $jsonBody -ContentType 'application/json' -TimeoutSec $TimeoutSec -UseBasicParsing
        } else {
            $raw = Invoke-WebRequest -Uri $Url -Method $Method -TimeoutSec $TimeoutSec -UseBasicParsing
        }

        $statusCode = [int]$raw.StatusCode
        $content = $raw.Content
        if (-not [string]::IsNullOrWhiteSpace($content)) {
            try {
                $responseObj = $content | ConvertFrom-Json
            } catch {
                $responseObj = $content
            }
        }

        if (-not $statusCode) {
            $statusCode = 200
        }

        $ok = ($AcceptedStatusCodes -contains [int]$statusCode)
    } catch {
        $statusCode = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]($_.Exception.Response.StatusCode.value__)
        }
        if (-not $statusCode) { $statusCode = 0 }
        $errorText = $_.Exception.Message
        $ok = ($AcceptedStatusCodes -contains [int]$statusCode)
    }

    $sw.Stop()

    return [pscustomobject]@{
        name = $Name
        method = $Method
        url = $Url
        status_code = [int]$statusCode
        latency_ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
        ok = [bool]$ok
        error = $errorText
        response = $responseObj
    }
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$Attempts = 12,
        [int]$DelayMs = 500,
        [int[]]$AcceptedStatusCodes = @(200)
    )

    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $raw = Invoke-WebRequest -Uri $Url -Method GET -TimeoutSec 5 -UseBasicParsing
            if ($AcceptedStatusCodes -contains [int]$raw.StatusCode) {
                return $true
            }
        } catch {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $code = [int]($_.Exception.Response.StatusCode.value__)
                if ($AcceptedStatusCodes -contains $code) {
                    return $true
                }
            }
        }
        Start-Sleep -Milliseconds $DelayMs
    }

    return $false
}

# Resolve active Open WebUI URL saved by the restart script.
$currentUrlFile = Join-Path (Join-Path (Split-Path $PSScriptRoot -Parent) '_Logs\OpenWebUI_Proteus') 'current_webui_url.txt'
$activeOpenWebUIBase = 'http://127.0.0.1:8080'
if (Test-Path $currentUrlFile) {
    try {
        $candidate = (Get-Content -Path $currentUrlFile -Raw -Encoding UTF8).Trim()
        if ($candidate -match '^http://127\.0\.0\.1:\d+$') {
            $activeOpenWebUIBase = $candidate
        }
    } catch {}
}

[void](Wait-HttpReady -Url "$activeOpenWebUIBase/api/config" -Attempts 20 -DelayMs 500 -AcceptedStatusCodes @(200))
[void](Wait-HttpReady -Url 'http://127.0.0.1:8001/health' -Attempts 20 -DelayMs 350 -AcceptedStatusCodes @(200))
[void](Wait-HttpReady -Url 'http://127.0.0.1:8765/health' -Attempts 20 -DelayMs 350 -AcceptedStatusCodes @(200))
[void](Wait-HttpReady -Url 'http://127.0.0.1:11434/api/tags' -Attempts 20 -DelayMs 350 -AcceptedStatusCodes @(200))

$checks = @()
$checks += Invoke-TimedHttp -Name 'openwebui_config_active' -Method 'GET' -Url "$activeOpenWebUIBase/api/config" -TimeoutSec 8 -AcceptedStatusCodes @(200)
$checks += Invoke-TimedHttp -Name 'openwebui_config_8080' -Method 'GET' -Url 'http://127.0.0.1:8080/api/config' -TimeoutSec 8 -AcceptedStatusCodes @(200)
$checks += Invoke-TimedHttp -Name 'bridge_health' -Method 'GET' -Url 'http://127.0.0.1:8001/health' -TimeoutSec 8 -AcceptedStatusCodes @(200)
$checks += Invoke-TimedHttp -Name 'bridge_capabilities' -Method 'GET' -Url 'http://127.0.0.1:8001/assistant/capabilities' -TimeoutSec 8 -AcceptedStatusCodes @(200)
$checks += Invoke-TimedHttp -Name 'mcp_health' -Method 'GET' -Url 'http://127.0.0.1:8765/health' -TimeoutSec 8 -AcceptedStatusCodes @(200)
$checks += Invoke-TimedHttp -Name 'mcp_stream_probe' -Method 'GET' -Url 'http://127.0.0.1:8765/mcp' -TimeoutSec 8 -AcceptedStatusCodes @(200, 406)
$checks += Invoke-TimedHttp -Name 'ollama_tags' -Method 'GET' -Url 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10 -AcceptedStatusCodes @(200)

$availableModels = @()
$ollamaTagsCheck = $checks | Where-Object { $_.name -eq 'ollama_tags' } | Select-Object -First 1
if ($ollamaTagsCheck -and $ollamaTagsCheck.ok -and $ollamaTagsCheck.response.models) {
    $availableModels = @($ollamaTagsCheck.response.models | ForEach-Object { $_.name })
}

$preferredModelOrder = @('qwen2.5:14b', 'qwen2.5-coder:7b', 'llama3.2:3b')
$generationModel = ($preferredModelOrder | Where-Object { $availableModels -contains $_ } | Select-Object -First 1)
if (-not $generationModel -and $availableModels.Count -gt 0) {
    $generationModel = $availableModels[0]
}

if ($generationModel) {
    $checks += Invoke-TimedHttp -Name 'ollama_generate_short' -Method 'POST' -Url 'http://127.0.0.1:11434/api/generate' -TimeoutSec 45 -AcceptedStatusCodes @(200) -Body @{
        model = $generationModel
        prompt = 'Responda somente OK.'
        stream = $false
        options = @{ temperature = 0.1 }
    }
}

$criticalNames = @('openwebui_config_active', 'bridge_health', 'mcp_health', 'ollama_tags')
$criticalChecks = @($checks | Where-Object { $criticalNames -contains $_.name })
$criticalOkCount = @($criticalChecks | Where-Object { $_.ok }).Count
$availabilityScore = 0.0
if ($criticalChecks.Count -gt 0) {
    $availabilityScore = [math]::Round((10.0 * $criticalOkCount / $criticalChecks.Count), 2)
}

$latencySource = @($checks | Where-Object { $_.ok -and $_.name -in @('openwebui_config_active', 'bridge_health', 'mcp_health', 'ollama_tags', 'ollama_generate_short') })
$avgLatency = 0.0
if ($latencySource.Count -gt 0) {
    $avgLatency = [math]::Round((($latencySource | Measure-Object -Property latency_ms -Average).Average), 1)
}

$latencyScore = 10.0
if ($avgLatency -gt 0) {
    $latencyScore = [math]::Max(1.0, [math]::Round((10.0 - ($avgLatency / 2000.0 * 10.0)), 2))
}

$localeValue = ''
$cfgActive = $checks | Where-Object { $_.name -eq 'openwebui_config_active' } | Select-Object -First 1
if ($cfgActive -and $cfgActive.ok -and $cfgActive.response.default_locale) {
    $localeValue = [string]$cfgActive.response.default_locale
}
$localeScore = if ($localeValue -eq 'pt-BR') { 10.0 } else { 4.5 }

$aiGenCheck = $checks | Where-Object { $_.name -eq 'ollama_generate_short' } | Select-Object -First 1
$aiScore = 6.0
if ($aiGenCheck) {
    if ($aiGenCheck.ok -and $aiGenCheck.response.response) {
        $aiScore = if ($aiGenCheck.latency_ms -le 15000) { 9.2 } else { 8.4 }
    } else {
        $aiScore = 5.0
    }
}

$mcpHealth = $checks | Where-Object { $_.name -eq 'mcp_health' } | Select-Object -First 1
$mcpProbe = $checks | Where-Object { $_.name -eq 'mcp_stream_probe' } | Select-Object -First 1
$mcpScore = 5.0
if ($mcpHealth.ok -and $mcpProbe.ok) {
    $mcpScore = 9.0
} elseif ($mcpHealth.ok) {
    $mcpScore = 7.8
}

$overall = [math]::Round((($availabilityScore * 0.30) + ($latencyScore * 0.15) + ($aiScore * 0.25) + ($mcpScore * 0.20) + ($localeScore * 0.10)), 2)

$result = [ordered]@{
    timestamp = (Get-Date).ToString('s')
    active_openwebui_base = $activeOpenWebUIBase
    model_used_for_generation = $generationModel
    locale_runtime = $localeValue
    scores = [ordered]@{
        availability = $availabilityScore
        latency = $latencyScore
        ai_practical = $aiScore
        mcp_maturity = $mcpScore
        locale_consistency = $localeScore
        overall = $overall
    }
    checks = @($checks | ForEach-Object {
        [ordered]@{
            name = $_.name
            method = $_.method
            url = $_.url
            status_code = $_.status_code
            latency_ms = $_.latency_ms
            ok = $_.ok
            error = $_.error
        }
    })
}

$jsonPath = Join-Path $OutputDir "benchmark_ia_mcp_$stamp.json"
$mdPath = Join-Path $OutputDir "benchmark_ia_mcp_$stamp.md"

($result | ConvertTo-Json -Depth 8) | Set-Content -Path $jsonPath -Encoding UTF8

$lines = @()
$lines += '# Benchmark IA/MCP - OpenWebUI Proteus'
$lines += ''
$lines += "Data: $($result.timestamp)"
$lines += "Open WebUI ativo: $($result.active_openwebui_base)"
$lines += "Modelo de teste: $($result.model_used_for_generation)"
$lines += "Locale runtime: $($result.locale_runtime)"
$lines += ''
$lines += '## Scores'
$lines += "- availability: $($result.scores.availability)/10"
$lines += "- latency: $($result.scores.latency)/10"
$lines += "- ai_practical: $($result.scores.ai_practical)/10"
$lines += "- mcp_maturity: $($result.scores.mcp_maturity)/10"
$lines += "- locale_consistency: $($result.scores.locale_consistency)/10"
$lines += "- overall: $($result.scores.overall)/10"
$lines += ''
$lines += '## Checks'
foreach ($c in $result.checks) {
    $lines += "- $($c.name): ok=$($c.ok) status=$($c.status_code) latency_ms=$($c.latency_ms)"
}

$lines | Set-Content -Path $mdPath -Encoding UTF8

Write-Host "Benchmark concluido." -ForegroundColor Green
Write-Host "JSON: $jsonPath"
Write-Host "MD:   $mdPath"
