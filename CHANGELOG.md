# Changelog

## v1.0.2 - 2026-04-12

Release funcional com consolidacao do fluxo oficial pass4 para PDFs de service guide.

### Adicionado

- endpoint oficial `POST /pdf/build-focused-subset` com perfis `acer_pass3_pure_schematics` e `acer_pass4_signal_dense`
- script `executar_pipeline_pass4_producao.ps1` com parametros fixos do ambiente Acer
- script `instalar_idioma_ocr_por_usuario.ps1` para idioma OCR sem permissao admin
- script `gerar_relatorio_comparativo_passes.ps1` para comparacao pass2/pass3/pass4

### Melhorado

- checklist por bloco com redes prioritarias e score de confianca por rede (`priority_nets`, `priority_nets_confidence`)
- robustez do OCR com fallback seguro para combinacoes de idioma

### Observacoes

- baseline recomendada de execucao passou a ser o fluxo pass4 sinal-denso
- comparativo tecnico pass2/pass3/pass4 agora pode ser gerado automaticamente em JSON/Markdown

## v1.0.1 - 2026-04-12

Release de documentacao e acabamento da publicacao.

### Ajustado (v1.0.1)

- correcoes de Markdown lint nos documentos principais do pacote
- conversao de URLs literais para autolinks no resumo executivo
- alinhamento da documentacao de publicacao para a proxima tag de release

## v1.0.0 - 2026-04-12

Primeira baseline versionada do pacote operacional OpenWebUI Proteus Professional.

### Adicionado (v1.0.0)

- toolkit de operacao profissional para Open WebUI + Proteus
- perfil profissional PT-BR com prompts, sugestoes e defaults persistidos
- integracao OpenAPI do Proteus Bridge
- integracao Local PC MCP
- manutencao automatica em um clique
- validacao automatizada do stack
- geracao de relatorio tecnico
- README operacional principal
- resumo executivo operacional
- modo enterprise-safe com shell e escrita desabilitados
- modo profissional-local com shell e escrita habilitados
- configuracoes de bridge separadas por perfil
- repositório Git dedicado para o pacote `OpenWebUI_Proteus`

### Seguranca

- grants dos tool servers restringidos a principal especifico, removendo wildcard global
- CORS local seguro aplicado no fluxo de configuracao operacional

### Observacoes (v1.0.0)

- porta operacional principal consolidada em 8080 quando disponivel
- portas 8081 e 8082 tratadas como informativas no relatorio quando 8080 estiver saudavel
