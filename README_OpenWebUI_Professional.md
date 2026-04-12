# Open WebUI — Configuração Profissional

## O que foi configurado

Seu `Open WebUI` foi preparado para operar com um perfil mais profissional e abrangente, incluindo:

- `Professional Workspace & Proteus Bridge`
  - automação do `ISIS Proteus`
  - leitura e busca em arquivos do workspace
  - criação, movimentação e remoção de diretórios no workspace
  - execução controlada de comandos PowerShell
  - busca e coleta de conteúdo web
  - diagnóstico de rede e serviços locais
- busca web nativa do `Open WebUI` habilitada com `DuckDuckGo`
- carregamento de páginas com `safe_web`
- sugestões de prompt persistentes para novos chats
- biblioteca de prompts fixos em JSON
- arquivo de prompt profissional em `openwebui_professional_system_prompt.md`
- snapshot das configurações em `openwebui_professional_settings.json`

---

## Endereços locais

- Open WebUI: `http://127.0.0.1:8080`
- Bridge profissional: `http://127.0.0.1:8001/docs`
- OpenAPI da bridge: `http://127.0.0.1:8001/openapi.json`

---

## Arquivos principais

- `openwebui_proteus_bridge.py`
- `proteus_bridge_config.json`
- `apply_openwebui_tool_server.py`
- `openwebui_tool_server_connections.json`
- `openwebui_professional_settings.json`
- `openwebui_professional_system_prompt.md`
- `openwebui_default_prompt_suggestions.json`
- `openwebui_fixed_prompts.json`

---

## Prompts fixos e persistência

As sugestões padrão de prompt já ficam salvas no `Open WebUI` e aparecem em novos chats.

Também foi preparada uma biblioteca fixa com comandos/padrões profissionais em:

- `openwebui_fixed_prompts.json`

> Se ainda não existir usuário cadastrado no seu `Open WebUI`, os prompts fixos por usuário serão sincronizados automaticamente assim que o primeiro usuário existir e você rodar novamente o script de inicialização.

---

## Como usar melhor no chat

### 1. Cole o prompt profissional
No modelo ou agente que você usar no `Open WebUI`, copie o conteúdo de:

- `openwebui_professional_system_prompt.md`

### 2. Faça perguntas com intenção clara
Exemplos:

- `Pesquise na web as melhores práticas para X e resuma com fontes.`
- `Procure no meu workspace onde a função Y é usada.`
- `Abra o projeto do Proteus e importe o firmware mais recente.`
- `Analise este erro e, se faltar contexto local, pesquise na web.`

### 3. Para projetos do Proteus
Garanta que `projects_root` em `proteus_bridge_config.json` aponta para a pasta real dos seus `.pdsprj`.

---

## Observação

A parte visual do chat, modelos ativos e permissões por usuário ainda dependem da sua sessão no `Open WebUI`. Mas a infraestrutura local para respostas mais profissionais, busca ampla e automação já foi preparada.
