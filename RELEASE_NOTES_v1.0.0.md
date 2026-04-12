# Release Notes - v1.0.0

## Resumo

Primeira release versionada do pacote operacional OpenWebUI Proteus Professional, consolidando automacao local, integracao com Proteus/ISIS, MCP local, perfil profissional PT-BR e rotinas de manutencao, validacao e relatorio.

## Destaques

- perfil profissional persistido para Open WebUI
- integracao com Professional Workspace and Proteus Bridge
- integracao com Local PC MCP
- manutencao automatica em um clique
- validacao automatizada do stack
- relatorio tecnico gerado automaticamente
- modelo principal promovido para qwen2.5:14b
- grants de acesso dos tool servers restritos a principal especifico
- modo enterprise-safe com shell e escrita desabilitados por padrao
- modo profissional-local com shell e escrita habilitados

## Conteudo da release

### Operacao

- apply_openwebui_tool_server.py
- restart_openwebui_with_proteus_tool.ps1
- manutencao_openwebui_profissional.ps1
- validate_openwebui_professional_stack.ps1
- gerar_relatorio_openwebui_stack.ps1

### Integracao

- openwebui_proteus_bridge.py
- local_pc_mcp_server.py
- openwebui_tool_server_connections.json
- openwebui_professional_settings.json
- openwebui_restore_snapshot.json

### Perfis de seguranca

- proteus_bridge_config.professional-local.json
- proteus_bridge_config.enterprise-safe.json
- ativar_modo_profissional_local.ps1
- ativar_modo_enterprise_safe.ps1

### Documentacao

- README.md
- RESUMO_EXECUTIVO_OpenWebUI_Professional.md
- CHANGELOG.md
- RELATORIO_OpenWebUI_Stack.md

## Status tecnico

- Open WebUI operacional em 8080
- Proteus Bridge operacional em 8001
- Local PC MCP operacional em 8765
- stack validado com resultado PASS

## Observacoes

- esta release foi preparada localmente e esta pronta para publicacao em remoto Git
- a tag local criada para esta release e v1.0.0
