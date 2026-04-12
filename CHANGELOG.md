# Changelog

## v1.0.1 - 2026-04-12

Release de documentacao e acabamento da publicacao.

### Ajustado

- correcoes de Markdown lint nos documentos principais do pacote
- conversao de URLs literais para autolinks no resumo executivo
- alinhamento da documentacao de publicacao para a proxima tag de release

## v1.0.0 - 2026-04-12

Primeira baseline versionada do pacote operacional OpenWebUI Proteus Professional.

### Adicionado

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

### Observacoes

- porta operacional principal consolidada em 8080 quando disponivel
- portas 8081 e 8082 tratadas como informativas no relatorio quando 8080 estiver saudavel
