# OpenWebUI Proteus Professional Stack

Stack operacional local para Open WebUI com perfil profissional em PT-BR, integracao com ISIS Proteus, tool server OpenAPI, MCP local e manutencao automatizada.

## Estado atual

- Open WebUI operacional em 8080
- Proteus Bridge operacional em 8001
- Local PC MCP operacional em 8765
- Modelo principal configurado: qwen2.5:14b
- Perfil profissional persistido e validado
- Grants de acesso restritos a principal especifico

## Modos disponiveis

- Modo profissional local: shell e escrita habilitados para automacao local controlada
- Modo enterprise-safe: shell e escrita desabilitados por padrao no bridge

## Arquivos principais

- apply_openwebui_tool_server.py
- restart_openwebui_with_proteus_tool.ps1
- manutencao_openwebui_profissional.ps1
- validate_openwebui_professional_stack.ps1
- gerar_relatorio_openwebui_stack.ps1
- openwebui_proteus_bridge.py
- proteus_bridge_config.json
- proteus_bridge_config.enterprise-safe.json

## Operacao recomendada

### Manutencao completa

```powershell
powershell -ExecutionPolicy Bypass -File D:\Projetos\OpenWebUI_Proteus\manutencao_openwebui_profissional.ps1 -OpenWebUIRoot D:\open-webui -ForceRestart
```

### Validacao rapida

```powershell
powershell -ExecutionPolicy Bypass -File D:\Projetos\OpenWebUI_Proteus\validate_openwebui_professional_stack.ps1 -OpenWebUIRoot D:\open-webui
```

### Geracao de relatorio

```powershell
powershell -ExecutionPolicy Bypass -File D:\Projetos\OpenWebUI_Proteus\gerar_relatorio_openwebui_stack.ps1 -OpenWebUIRoot D:\open-webui
```

## Enterprise-safe

Para aplicar a variante endurecida do bridge:

```powershell
powershell -ExecutionPolicy Bypass -File D:\Projetos\OpenWebUI_Proteus\ativar_modo_enterprise_safe.ps1 -OpenWebUIRoot D:\open-webui
```

Essa variante desabilita:

- allow_write
- allow_shell_commands

Mantem:

- leitura do workspace
- busca web
- health checks
- integracao MCP
- integracao OpenAPI do bridge

## Restaurar modo profissional-local

Para voltar ao modo local completo com shell e escrita habilitados:

```powershell
powershell -ExecutionPolicy Bypass -File D:\Projetos\OpenWebUI_Proteus\ativar_modo_profissional_local.ps1 -OpenWebUIRoot D:\open-webui
```

## Documentacao complementar

- RESUMO_EXECUTIVO_OpenWebUI_Professional.md
- README_OpenWebUI_Professional.md
- README_OpenWebUI_Proteus.md
- README_Reinstalacao_OpenWebUI_Proteus.md
- PACOTE_1_CLICK_README.md
