[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

Set-Location $PSScriptRoot

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host " Open WebUI + ISIS Proteus Bridge" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "API docs: http://127.0.0.1:8001/docs" -ForegroundColor Yellow
Write-Host "OpenAPI:  http://127.0.0.1:8001/openapi.json`n" -ForegroundColor Yellow

$localVenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$rootVenvPython = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'
$systemPython = 'C:/Users/rober/AppData/Local/Microsoft/WindowsApps/python3.12.exe'
$requirementsFile = Join-Path $PSScriptRoot 'openwebui_bridge_requirements.txt'

if (Test-Path $localVenvPython) {
    $pythonCmd = $localVenvPython
} elseif (Test-Path $rootVenvPython) {
    $pythonCmd = $rootVenvPython
} else {
    $pythonCmd = $systemPython
}

Write-Host "Usando Python: $pythonCmd" -ForegroundColor Cyan

& $pythonCmd -c "import fastapi, uvicorn, pydantic, psutil, requests, bs4, pypdf, fitz, pytesseract; print('deps_ok')" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Instalando dependências do bridge...' -ForegroundColor Yellow
    & $pythonCmd -m pip install -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
        throw 'Falha ao instalar as dependências do bridge.'
    }
}

$listen = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listen) {
    Write-Host 'Parando instância anterior na porta 8001...' -ForegroundColor Yellow
    Stop-Process -Id $listen.OwningProcess -Force -ErrorAction SilentlyContinue
}

& $pythonCmd .\openwebui_proteus_bridge.py
