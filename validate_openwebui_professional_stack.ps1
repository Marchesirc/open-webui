param(
    [string]$OpenWebUIRoot = 'D:\open-webui'
)

$ErrorActionPreference = 'Stop'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Test-Http {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSec = 6
    )

    try {
        $res = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return [pscustomobject]@{
            Name = $Name
            Url = $Url
            Ok = $true
            StatusCode = $res.StatusCode
            Detail = "bytes=$($res.Content.Length)"
        }
    }
    catch {
        return [pscustomobject]@{
            Name = $Name
            Url = $Url
            Ok = $false
            StatusCode = $null
            Detail = $_.Exception.Message
        }
    }
}

function Invoke-BackendSmoke {
    param([string]$BackendRoot)

    $recommendedCors = 'http://127.0.0.1:8080;http://127.0.0.1:8082;http://127.0.0.1:8081;http://localhost:8080;http://localhost:8082;http://localhost:8081'

    $pythonExe = Join-Path $BackendRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $pythonExe)) {
        return [pscustomobject]@{
            Ok = $false
            Detail = "Python da venv nao encontrado em: $pythonExe"
        }
    }

    $snippet = @"
import asyncio
import json
import os
import sys

backend_root = os.environ.get('OPENWEBUI_BACKEND_ROOT', '')
if backend_root and backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from open_webui.config import get_config
from open_webui.utils.tools import get_tool_servers_data, execute_tool_server

async def main():
    cfg = get_config() or {}
    conns = (((cfg.get('tool_server') or {}).get('connections')) or [])
    active = await get_tool_servers_data(conns)

    out = {
        'connections_count': len(conns),
        'active_servers_count': len(active),
        'active_server_names': [(s.get('name') or s.get('url')) for s in active],
        'smokes': [],
    }

    smoke_plan = [
        ("proteus bridge", "http://127.0.0.1:8001", "assistant_capabilities", {}),
        ("local pc mcp", "http://127.0.0.1:8765", "get_pc_summary", {}),
    ]

    for server_hint, server_url_hint, preferred_tool, params in smoke_plan:
        server = next(
            (
                s
                for s in active
                if (
                    server_hint in (s.get('name') or '').lower()
                    or str(s.get('url') or '').rstrip('/').startswith(server_url_hint)
                )
            ),
            None,
        )
        if not server:
            out['smokes'].append({
                'ok': False,
                'server_hint': server_hint,
                'error': 'server_not_active',
            })
            continue

        specs = server.get('specs') or []
        tool_name = preferred_tool
        if not any((sp or {}).get('name') == tool_name for sp in specs):
            tool_name = (specs[0] or {}).get('name') if specs else tool_name

        headers = dict(server.get('headers') or {})
        try:
            result, _headers = await execute_tool_server(
                url=(server.get('url') or '').rstrip('/'),
                headers=headers,
                cookies={},
                name=tool_name,
                params=params,
                server_data=server,
            )
            out['smokes'].append({
                'ok': True,
                'server': server.get('name') or server.get('url'),
                'tool': tool_name,
                'result_type': str(type(result)),
            })
        except Exception as e:
            out['smokes'].append({
                'ok': False,
                'server': server.get('name') or server.get('url'),
                'tool': tool_name,
                'error': f"{type(e).__name__}: {e}",
            })

    out['all_smokes_ok'] = all(bool(item.get('ok')) for item in out['smokes']) if out['smokes'] else False

    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
"@

    $tmp = Join-Path $env:TEMP 'openwebui_tool_smoke.py'
    Set-Content -Path $tmp -Value $snippet -Encoding UTF8

    Push-Location $BackendRoot
    try {
        $env:OPENWEBUI_BACKEND_ROOT = $BackendRoot
        $env:CORS_ALLOW_ORIGIN = $recommendedCors
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & $pythonExe $tmp 2>&1
        $ErrorActionPreference = $prevErrorAction
        if ($LASTEXITCODE -ne 0) {
            return [pscustomobject]@{
                Ok = $false
                Detail = ($output | Out-String)
            }
        }

        return [pscustomobject]@{
            Ok = $true
            Detail = ($output | Out-String)
        }
    }
    finally {
        Remove-Item Env:OPENWEBUI_BACKEND_ROOT -ErrorAction SilentlyContinue
        Remove-Item Env:CORS_ALLOW_ORIGIN -ErrorAction SilentlyContinue
        $ErrorActionPreference = 'Stop'
        Pop-Location
        if (Test-Path $tmp) {
            Remove-Item $tmp -Force
        }
    }
}

$webUiChecks = @(
    (Test-Http -Name 'OpenWebUI_8080' -Url 'http://127.0.0.1:8080/api/config'),
    (Test-Http -Name 'OpenWebUI_8082' -Url 'http://127.0.0.1:8082/api/config'),
    (Test-Http -Name 'OpenWebUI_8081' -Url 'http://127.0.0.1:8081/api/config')
)

$checks = @(
    $webUiChecks[0],
    $webUiChecks[1],
    $webUiChecks[2],
    (Test-Http -Name 'ProteusBridge_Health' -Url 'http://127.0.0.1:8001/health'),
    (Test-Http -Name 'ProteusBridge_OpenAPI' -Url 'http://127.0.0.1:8001/openapi.json'),
    (Test-Http -Name 'LocalPCMCP_Health' -Url 'http://127.0.0.1:8765/health')
)

$okCount = ($checks | Where-Object { $_.Ok }).Count
$openWebUiOk = ($webUiChecks | Where-Object { $_.Ok } | Select-Object -First 1)

$backendRoot = Join-Path $OpenWebUIRoot 'backend'
$smoke = Invoke-BackendSmoke -BackendRoot $backendRoot

Write-Host '========================================='
Write-Host ' Open WebUI Professional Stack Validation'
Write-Host '========================================='
foreach ($c in $checks) {
    $status = if ($c.Ok) { 'OK' } else { 'FAIL' }

    if (($c.Name -like 'OpenWebUI_*') -and (-not $c.Ok) -and ($null -ne $openWebUiOk)) {
        $status = 'INFO'
    }

    Write-Host "[$status] $($c.Name) :: $($c.Detail)"
}

if ($smoke.Ok) {
    Write-Host "[OK] Backend Tool Smoke :: $($smoke.Detail.Trim())"
} else {
    Write-Host "[FAIL] Backend Tool Smoke :: $($smoke.Detail.Trim())"
}

$smokeOk = ($smoke.Ok -and ($smoke.Detail -match '"all_smokes_ok": true'))
$overallOk = (($null -ne $openWebUiOk) -and (($checks | Where-Object { $_.Name -notlike 'OpenWebUI_*' -and $_.Ok }).Count -eq 3) -and $smokeOk)
if ($overallOk) {
    Write-Host ("Open WebUI ativo em: " + $openWebUiOk.Url)
    Write-Host 'RESULT: PASS (stack profissional operacional)'
    exit 0
}

Write-Host 'RESULT: FAIL (ha itens para corrigir)'
exit 1
