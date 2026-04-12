[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

$ollamaApp = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
$ollamaCli = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'

function Test-OllamaReady {
    param([string]$CliPath)
    try {
        $null = & $CliPath list 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

if (-not (Test-Path $ollamaCli)) {
    throw "Ollama não encontrado em: $ollamaCli"
}

if ((Get-Process ollama -ErrorAction SilentlyContinue) -and (Test-OllamaReady -CliPath $ollamaCli)) {
    Write-Host 'Ollama já está ativo.' -ForegroundColor Green
    exit 0
}

if (Test-Path $ollamaApp) {
    Start-Process -FilePath $ollamaApp -WindowStyle Minimized | Out-Null
} else {
    Start-Process -FilePath $ollamaCli -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
}

for ($i = 0; $i -lt 20; $i++) {
    if (Test-OllamaReady -CliPath $ollamaCli) {
        Write-Host 'Ollama iniciado com sucesso.' -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Milliseconds 1000
}

throw 'Ollama não respondeu a tempo após a inicialização.'
