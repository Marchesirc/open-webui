# Projetos organizados em `D:\Projetos`

## Estrutura principal

### Projetos

- `Analisador_Pro_HDD/`
- `apply_tuf_bios_theme_v2/`
- `Criador_EXE_Python/`
- `download_arquivos/`
- `Editor_de_video/`

### Stack Open WebUI + Proteus

- `OpenWebUI_Proteus/` → implementação real dos scripts e integrações
- `Proteus_Projects/` → coloque aqui seus projetos `.pdsprj` / `.dsn`

### Utilitários e setups

- `Setup_Scripts/`
- `start_proteus_bridge.ps1`
- `restart_openwebui_with_proteus_tool.ps1`
- `start_openwebui_professional_stack.ps1`
- `instalar_tudo_openwebui_proteus.ps1`
- `setup_ollama_autostart.ps1`

## Organização atual

- a **lógica real** fica em `OpenWebUI_Proteus/`
- a **raiz** mantém apenas launchers rápidos e arquivos principais do workspace
- os scripts de reinstalação e restart agora **detectam automaticamente** a pasta do `open-webui`, a raiz do workspace e `Proteus_Projects`

## Configuração do Proteus

Para finalizar a automação do Proteus, use:

- `OpenWebUI_Proteus/configurar_proteus_automacao.ps1`

Esse script ajusta `projects_root`, macros (`start/stop/pause/reset`) e pode importar projetos para `Proteus_Projects/` sem depender de caminho fixo.

## Reinstalação completa após formatação

Para restaurar todo o ambiente (bridge, prompts, caminhos, usuário local, Ollama e Open WebUI em `8080`), use:

- `instalar_tudo_openwebui_proteus.ps1`

URL principal após a restauração:

- <http://127.0.0.1:8080/>
