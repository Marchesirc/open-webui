param(
    [string]$OpenWebUIRoot = 'D:\open-webui',
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot 'RELATORIO_OpenWebUI_Stack.md'
}

function Get-HttpStatusInfo {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSec = 6
    )

    try {
        $res = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        [pscustomobject]@{
            Name = $Name
            Url = $Url
            Ok = $true
            Status = $res.StatusCode
            Detail = "bytes=$($res.Content.Length)"
        }
    }
    catch {
        [pscustomobject]@{
            Name = $Name
            Url = $Url
            Ok = $false
            Status = 0
            Detail = $_.Exception.Message
        }
    }
}

$checks = @(
    (Get-HttpStatusInfo -Name 'OpenWebUI_8080' -Url 'http://127.0.0.1:8080/api/config'),
    (Get-HttpStatusInfo -Name 'OpenWebUI_8082' -Url 'http://127.0.0.1:8082/api/config'),
    (Get-HttpStatusInfo -Name 'OpenWebUI_8081' -Url 'http://127.0.0.1:8081/api/config'),
    (Get-HttpStatusInfo -Name 'ProteusBridge_Health' -Url 'http://127.0.0.1:8001/health'),
    (Get-HttpStatusInfo -Name 'ProteusBridge_OpenAPI' -Url 'http://127.0.0.1:8001/openapi.json'),
    (Get-HttpStatusInfo -Name 'LocalPCMCP_Health' -Url 'http://127.0.0.1:8765/health')
)

$activeOpenWebUi = $checks | Where-Object { $_.Name -like 'OpenWebUI_*' -and $_.Ok } | Select-Object -First 1

$configSummary = $null
if ($activeOpenWebUi) {
    try {
        $configSummary = Invoke-RestMethod -Method Get -Uri $activeOpenWebUi.Url -TimeoutSec 6
    } catch {}
}

$ollamaList = ''
try {
    $ollamaList = (ollama list | Out-String).Trim()
} catch {
    $ollamaList = "Falha ao consultar ollama list: $($_.Exception.Message)"
}

$reportLines = @()
$reportLines += '# Relatorio Tecnico - OpenWebUI Professional Stack'
$reportLines += ''
$reportLines += "- Data: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$reportLines += "- OpenWebUIRoot: $OpenWebUIRoot"
$reportLines += ''
$reportLines += '## Endpoints'
foreach ($c in $checks) {
    $status = if ($c.Ok) { 'OK' } else { 'FAIL' }

    if (($c.Name -like 'OpenWebUI_*') -and (-not $c.Ok) -and ($activeOpenWebUi -ne $null)) {
        $status = 'INFO'
    }

    $reportLines += "- [$status] $($c.Name) -> $($c.Url) | status=$($c.Status) | $($c.Detail)"
}
$reportLines += ''

$reportLines += '## Open WebUI Runtime'
if ($configSummary) {
    $reportLines += "- Porta ativa detectada: $($activeOpenWebUi.Url)"
    $reportLines += "- Nome: $($configSummary.name)"
    $reportLines += "- Versao: $($configSummary.version)"
    $reportLines += "- Locale default runtime: $($configSummary.default_locale)"
    $reportLines += "- Modelos default runtime: $($configSummary.default_models)"
} else {
    $reportLines += '- Nao foi possivel ler /api/config de uma instancia ativa.'
}
$reportLines += ''

$reportLines += '## Modelos Ollama'
$reportLines += '```text'
$reportLines += $ollamaList
$reportLines += '```'
$reportLines += ''

$overallOk = ($activeOpenWebUi -ne $null) -and (($checks | Where-Object { $_.Name -notlike 'OpenWebUI_*' -and $_.Ok }).Count -eq 3)
$reportLines += '## Resultado'
if ($overallOk) {
    $reportLines += '- PASS: stack operacional.'
} else {
    $reportLines += '- FAIL: verificar itens com status FAIL.'
}

Set-Content -Path $OutputPath -Value ($reportLines -join [Environment]::NewLine) -Encoding UTF8
Write-Host "Relatorio gerado em: $OutputPath"
