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

## Analise confiavel de schematic PDF

O bridge agora suporta uma etapa confiavel de pre-processamento para PDF de esquematico: extrai texto do PDF, identifica referencias de componentes e sinais provaveis, e gera um JSON intermediario para revisao humana antes da montagem no ISIS Proteus.

Limites importantes:

- nao cria automaticamente o esquematico completo no Proteus
- depende de PDF com texto extraivel; scans/imagens podem exigir OCR antes
- o resultado deve ser revisado antes de virar BOM, netlist ou projeto ISIS

Endpoint OpenAPI:

```text
POST /pdf/analyze-schematic
```

Exemplo de payload:

```json
{
  "pdf_path": "MeuProjeto/schematic.pdf",
  "max_pages": 12,
  "persist_output": true
}
```

Saida esperada:

- JSON com componentes detectados por referencia
- sinais provaveis como GND, VCC, SDA, SCL, TX, RX
- `readiness` para indicar se o PDF serve como base razoavel para fluxo assistido
- arquivo `.schematic.analysis.json` salvo ao lado do PDF, quando `persist_output=true`

## Montagem assistida para o Proteus

O bridge agora consegue transformar a analise do PDF em um pacote assistido de projeto dentro de `Proteus_Projects`, com arquivos prontos para montagem manual e revisao tecnica no ISIS Proteus.

Endpoint OpenAPI:

```text
POST /proteus/prepare-assisted-project
```

Payload minimo com PDF:

```json
{
  "project_folder": "MeuProjeto_Assistido",
  "pdf_path": "MeuProjeto/schematic.pdf",
  "overwrite": true,
  "copy_source_pdf": true
}
```

Payload alternativo com JSON ja analisado:

```json
{
  "project_folder": "MeuProjeto_Assistido",
  "analysis_relative_path": "MeuProjeto/schematic.schematic.analysis.json",
  "overwrite": true
}
```

Arquivos gerados no pacote assistido:

- `schematic_analysis.json`
- `components_detected.json`
- `components_bom.csv`
- `signal_candidates.json`
- `signal_candidates.csv`
- `draft_netlist.json`
- `draft_netlist_review.csv`
- `draft_netlist_nets.csv`
- `functional_blocks.json`
- `functional_blocks.csv`
- `block_mount_checklist.json`
- `block_mount_checklist.csv`
- `MONTAGEM_ASSISTIDA_PROTEUS.md`
- `assisted_project_manifest.json`

Uso recomendado:

- gere o pacote assistido
- abra `MONTAGEM_ASSISTIDA_PROTEUS.md`
- consulte `functional_blocks.csv` para montar primeiro por blocos como fonte, controle, interface e conectores
- siga `block_mount_checklist.csv` como ordem operacional de montagem por prioridade tecnica e confianca
- revise `draft_netlist_review.csv` e `draft_netlist_nets.csv` para validar conexoes inferidas
- use `confidence` e `confidence_score` para priorizar primeiro os componentes e redes com melhor evidência
- monte o projeto no ISIS com base no BOM CSV e nos sinais detectados
- depois use o bridge atual para abrir projeto, importar firmware e simular

Blocos funcionais atualmente inferidos com regras especificas:

- `power_supply` e `power_regulation`
- `mcu_control`
- `display_ui`, `display_i2c` e `display_spi`
- `sensor_frontend`, `sensor_analog` e `sensor_digital`
- `uart_interface`, `can_interface`, `can_transceiver`, `rs485_interface`, `usb_interface` e `interface_comms`
- `connectors_io`, `drivers_outputs`, `clock_timing`, `passive_support` e `misc_control`

## Documentacao complementar

- RESUMO_EXECUTIVO_OpenWebUI_Professional.md
- README_OpenWebUI_Professional.md
- README_OpenWebUI_Proteus.md
- README_Reinstalacao_OpenWebUI_Proteus.md
- PACOTE_1_CLICK_README.md
