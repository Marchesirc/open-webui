[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

Set-Location $PSScriptRoot

Write-Host '===============================================' -ForegroundColor Cyan
Write-Host ' Local PC MCP Server' -ForegroundColor Cyan
Write-Host '===============================================' -ForegroundColor Cyan
Write-Host 'Health: http://127.0.0.1:8765/health' -ForegroundColor Yellow
Write-Host 'MCP:    http://127.0.0.1:8765/mcp' -ForegroundColor Yellow
Write-Host ''

$workspaceRoot = Split-Path $PSScriptRoot -Parent
$candidatePython = @(
    (Join-Path $workspaceRoot 'open-webui\backend\.venv\Scripts\python.exe'),
    'D:\open-webui\backend\.venv\Scripts\python.exe',
    (Join-Path $env:USERPROFILE 'open-webui\backend\.venv\Scripts\python.exe'),
    (Join-Path $workspaceRoot '.venv\Scripts\python.exe'),
    'C:/Users/rober/AppData/Local/Microsoft/WindowsApps/python3.12.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $candidatePython) {
    throw 'Python para o Local PC MCP não foi encontrado automaticamente.'
}

$env:LOCAL_PC_MCP_HOST = '127.0.0.1'
$env:LOCAL_PC_MCP_PORT = '8765'
$env:LOCAL_PC_MCP_WORKSPACE = $workspaceRoot

Write-Host "Usando Python: $candidatePython" -ForegroundColor Cyan

$listen = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listen) {
    Write-Host 'Parando instância anterior na porta 8765...' -ForegroundColor Yellow
    Stop-Process -Id $listen.OwningProcess -Force -ErrorAction SilentlyContinue
}

& $candidatePython .\local_pc_mcp_server.py
