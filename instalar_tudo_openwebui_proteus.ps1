param(
    [string]$OpenWebUIRepo = '',
    [string]$WorkspaceRoot = '',
    [string]$ProjectsRoot = '',
    [string]$AdminEmail = 'marchesirc@gmail.com',
    [string]$AdminPassword = '1598753',
    [string]$AdminName = 'Administrador Local'
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$env:PIP_NO_CACHE_DIR = '1'
$PipInstallArgs = @('--disable-pip-version-check', '--no-cache-dir')

function Test-OpenWebUIRepoCandidate {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path (Join-Path $Path 'backend\open_webui\main.py'))
}

function Resolve-OpenWebUIRepo {
    param(
        [string]$PreferredPath,
        [string]$WorkspacePath,
        [string]$ScriptRoot
    )

    $candidates = @(
        $PreferredPath,
        (Join-Path $WorkspacePath 'open-webui'),
        (Join-Path $ScriptRoot 'open-webui'),
        (Join-Path (Split-Path $WorkspacePath -Parent) 'open-webui'),
        (Join-Path $env:USERPROFILE 'open-webui'),
        'C:\Users\rober\open-webui'
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($candidate in $candidates) {
        if (Test-OpenWebUIRepoCandidate -Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Repositório do Open WebUI não encontrado automaticamente. Informe -OpenWebUIRepo apontando para a pasta que contém 'backend'."
}

function Resolve-PythonCommand {
    $candidates = @(
        'C:/Users/rober/AppData/Local/Microsoft/WindowsApps/python3.12.exe',
        ((Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)),
        ((Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1))
    ) | Where-Object { $_ } | Select-Object -Unique

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw 'Python não encontrado automaticamente. Instale o Python 3.11+ ou ajuste o PATH.'
}

$bridgeRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = Split-Path $bridgeRoot -Parent
}
if ([string]::IsNullOrWhiteSpace($ProjectsRoot)) {
    $ProjectsRoot = Join-Path $WorkspaceRoot 'Proteus_Projects'
}
$OpenWebUIRepo = Resolve-OpenWebUIRepo -PreferredPath $OpenWebUIRepo -WorkspacePath $WorkspaceRoot -ScriptRoot $bridgeRoot
$backendDir = Join-Path $OpenWebUIRepo 'backend'
$systemPython = Resolve-PythonCommand
$bridgeVenv = Join-Path $WorkspaceRoot '.venv\Scripts\python.exe'
$backendVenv = Join-Path $backendDir '.venv\Scripts\python.exe'
$bridgeRequirements = Join-Path $bridgeRoot 'openwebui_bridge_requirements.txt'
$backendRequirements = Join-Path $backendDir 'requirements.txt'
$bridgeLauncher = Join-Path $bridgeRoot 'start_proteus_bridge.ps1'
$webuiLauncher = Join-Path $bridgeRoot 'restart_openwebui_with_proteus_tool.ps1'
$configHelper = Join-Path $bridgeRoot 'configurar_proteus_automacao.ps1'
$applyScript = Join-Path $bridgeRoot 'apply_openwebui_tool_server.py'
$ollamaAutostart = Join-Path $bridgeRoot 'setup_ollama_autostart.ps1'

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$Attempts = 30,
        [int]$DelayMs = 1000
    )

    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 3
            return $response
        } catch {
            Start-Sleep -Milliseconds $DelayMs
        }
    }

    throw "Serviço não respondeu em tempo: $Url"
}

Write-Host '===============================================' -ForegroundColor Cyan
Write-Host ' Instalação Open WebUI + Proteus Bridge' -ForegroundColor Cyan
Write-Host '===============================================' -ForegroundColor Cyan
Write-Host "Workspace detectado: $WorkspaceRoot" -ForegroundColor DarkCyan
Write-Host "Open WebUI detectado: $OpenWebUIRepo" -ForegroundColor DarkCyan
Write-Host "Projetos do Proteus: $ProjectsRoot" -ForegroundColor DarkCyan

if (-not (Test-Path $backendDir)) {
    throw "Backend do Open WebUI não encontrado em: $backendDir"
}

if (-not (Test-Path $systemPython)) {
    throw "Python 3.12 não encontrado em: $systemPython"
}

New-Item -ItemType Directory -Force -Path $WorkspaceRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ProjectsRoot | Out-Null

if (-not (Test-Path $bridgeVenv)) {
    Write-Host 'Criando ambiente virtual do bridge em D:\Projetos\.venv...' -ForegroundColor Yellow
    & $systemPython -m venv (Join-Path $WorkspaceRoot '.venv')
}

Write-Host 'Instalando dependências do bridge...' -ForegroundColor Yellow
& $bridgeVenv -m pip install @PipInstallArgs -r $bridgeRequirements
if ($LASTEXITCODE -ne 0) {
    throw 'Falha ao instalar dependências do bridge.'
}

if (-not (Test-Path $backendVenv)) {
    Write-Host 'Criando ambiente virtual do Open WebUI backend...' -ForegroundColor Yellow
    & $systemPython -m venv (Join-Path $backendDir '.venv')
}

if (Test-Path $backendRequirements) {
    Write-Host 'Garantindo dependências do Open WebUI backend...' -ForegroundColor Yellow
    & $backendVenv -m pip install @PipInstallArgs -r $backendRequirements
    if ($LASTEXITCODE -ne 0) {
        throw 'Falha ao instalar dependências do Open WebUI backend.'
    }
}

if (Test-Path $configHelper) {
    Write-Host 'Aplicando caminho padrão dos projetos do Proteus...' -ForegroundColor Yellow
    & $configHelper -ProjectsRoot $ProjectsRoot -RestartBridge $false
}

$bootstrapPy = @"
import sys
sys.path.insert(0, r'$backendDir')
from open_webui.models.auths import Auths
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash

email = r'$AdminEmail'.lower()
password = r'$AdminPassword'
name = r'$AdminName'
user = Users.get_user_by_email(email)
if not user:
    user = Auths.insert_new_auth(
        email=email,
        password=get_password_hash(password),
        name=name,
        role='admin'
    )
    print('ADMIN_CREATED', bool(user))
else:
    print('ADMIN_EXISTS', user.id)
"@

Write-Host 'Garantindo usuário administrativo local...' -ForegroundColor Yellow
$bootstrapPy | & $backendVenv -

if (Test-Path $ollamaAutostart) {
    Write-Host 'Configurando inicialização automática do Ollama no Windows...' -ForegroundColor Yellow
    & $ollamaAutostart
}

Write-Host 'Iniciando o Proteus bridge...' -ForegroundColor Yellow
Start-Process powershell -ArgumentList @('-ExecutionPolicy', 'Bypass', '-File', $bridgeLauncher)
Wait-HttpOk -Url 'http://127.0.0.1:8001/health' | Out-Null

Write-Host 'Aplicando prompts, ferramentas, idioma PT-BR e perfil profissional padrão do chat...' -ForegroundColor Yellow
& $backendVenv $applyScript $backendDir 'http://127.0.0.1:8001'
if ($LASTEXITCODE -ne 0) {
    throw 'Falha ao aplicar a configuração profissional do Open WebUI.'
}

Write-Host 'Reiniciando o Open WebUI fixo na porta 8080...' -ForegroundColor Yellow
Start-Process powershell -ArgumentList @('-ExecutionPolicy', 'Bypass', '-File', $webuiLauncher, '-ForceRestart')
Wait-HttpOk -Url 'http://127.0.0.1:8080/api/config' | Out-Null

Write-Host ''
Write-Host '✅ Instalação concluída com sucesso.' -ForegroundColor Green
Write-Host 'Open WebUI: http://127.0.0.1:8080/' -ForegroundColor Green
Write-Host 'Proteus Bridge: http://127.0.0.1:8001/docs' -ForegroundColor Green
Write-Host 'Ollama autostart: habilitado para o login do Windows' -ForegroundColor Green
Write-Host 'Perfil padrão restaurado: Modo Copilot PT-BR + prompt profissional persistente' -ForegroundColor Green
Write-Host "Login local: $AdminEmail" -ForegroundColor Green
Write-Host "Senha local: $AdminPassword" -ForegroundColor Green
Write-Host "Pasta dos projetos do Proteus: $ProjectsRoot" -ForegroundColor Green
