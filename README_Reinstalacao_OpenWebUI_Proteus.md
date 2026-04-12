# Reinstalação rápida do Open WebUI + Proteus Bridge

## Script principal
Use:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\Projetos\instalar_tudo_openwebui_proteus.ps1"
```

## O que o script restaura
- ambiente virtual do bridge em `D:\Projetos\.venv`
- dependências do `Open WebUI` backend
- `Professional Workspace & Proteus Bridge`
- prompts profissionais públicos
- configurações de busca web e permissões
- usuário local administrativo:
  - `admin@local`
  - `Admin@123456`
- `Proteus_Projects` como pasta padrão dos projetos do Proteus
- reinício do Open WebUI em `http://127.0.0.1:8080/`

## Pré-requisitos
- repositório do Open WebUI em `C:\Users\rober\open-webui`
- Python 3.12 disponível no Windows
- Proteus instalado na máquina
