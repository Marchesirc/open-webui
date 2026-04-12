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

### Pipeline pass4 producao (Acer)

```powershell
powershell -ExecutionPolicy Bypass -File D:\Projetos\OpenWebUI_Proteus\executar_pipeline_pass4_producao.ps1
```

Esse script usa parametros fixos de producao para o fluxo Acer e executa, em ordem:

- extracao de candidatos
- recorte focado pass4
- analise do PDF focado
- pacote assistido para montagem no ISIS

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

Para manuais grandes ou service guides, use antes:

```text
POST /pdf/extract-schematic-candidates
```

Para gerar subconjuntos focados com perfis oficiais (sem scripts temporarios), use:

```text
POST /pdf/build-focused-subset
```

Perfis oficiais atuais:

- `acer_pass3_pure_schematics`
- `acer_pass4_signal_dense`

Exemplo de payload para perfil pass4:

```json
{
  "source_pdf_path": "Acer_AcerNote_970/acer_acernote_970_service_guide.pdf",
  "profile_name": "acer_pass4_signal_dense",
  "candidate_manifest_path": "Acer_AcerNote_970/acer_acernote_970_service_guide_schematic_candidates_pass2/candidate_pages.json"
}
```

Esse endpoint faz triagem de páginas com maior chance de conter diagrama/esquemático, exporta as melhores páginas como PNG em alta resolução e gera um manifesto `candidate_pages.json` para revisão dirigida.

Se houver Tesseract OCR instalado localmente, o bridge agora tenta OCR automaticamente nas páginas com pouco texto extraível.

Exemplo de payload:

```json
{
  "pdf_path": "MeuProjeto/manual.pdf",
  "max_pages": 250,
  "top_k": 12,
  "export_pages": true,
  "export_dpi": 220,
  "use_ocr_if_needed": true,
  "ocr_languages": "eng"
}
```

Uso recomendado para service guides:

- rode `POST /pdf/extract-schematic-candidates`
- revise as páginas exportadas com maior score
- escolha as páginas realmente esquemáticas
- depois rode `POST /pdf/analyze-schematic` ou `POST /proteus/prepare-assisted-project` focando nesse subconjunto

Observações sobre OCR:

- OCR é opcional e só entra quando a página vier com pouco texto extraível
- o bridge detecta automaticamente `tesseract.exe` em caminhos comuns do Windows
- para PDFs mistos em inglês e português, prefira `ocr_languages: "eng+por"`
- sem o binário do Tesseract, o fluxo continua funcionando em modo degradado
- para idioma portugues sem permissao de admin, use o script `instalar_idioma_ocr_por_usuario.ps1` (instala `por.traineddata` em `%LOCALAPPDATA%\Tesseract-OCR\tessdata` e configura `TESSDATA_PREFIX` no usuario)

Exemplo de payload:

```json
{
  "pdf_path": "MeuProjeto/schematic.pdf",
  "max_pages": 12,
  "persist_output": true,
  "use_ocr_if_needed": true,
  "ocr_languages": "eng"
}
```

Saida esperada:

- JSON com componentes detectados por referencia
- sinais provaveis como GND, VCC, SDA, SCL, TX, RX
- metadados de OCR como `ocr_backend`, `ocr_attempted_pages` e `ocr_used_pages`
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
  "copy_source_pdf": true,
  "use_ocr_if_needed": true,
  "ocr_languages": "eng"
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
- `block_mount_checklist.csv` com colunas de redes prioritarias (`priority_nets`) e score de confianca por rede (`priority_nets_confidence`)
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

## Operacao oficial pass4 (sem script temporario)

Fluxo recomendado para service guide Acer:

1. `POST /pdf/extract-schematic-candidates`
2. `POST /pdf/build-focused-subset` com `profile_name: "acer_pass4_signal_dense"`
3. `POST /pdf/analyze-schematic` no PDF focado gerado
4. `POST /proteus/prepare-assisted-project`

Scripts auxiliares disponiveis:

- `instalar_idioma_ocr_por_usuario.ps1`: instala idioma OCR `por` sem admin no perfil do usuario
- `executar_pipeline_pass4_oficial.ps1`: executa pipeline pass4 completo via endpoints do bridge
- `gerar_relatorio_comparativo_passes.ps1`: cria comparativo pass2/pass3/pass4 em JSON e Markdown

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
