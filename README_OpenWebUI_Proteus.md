# Open WebUI + ISIS Proteus Bridge

## O que este pacote faz

Este bridge expõe uma API local para o `Open WebUI` controlar seus projetos do `ISIS Proteus` no Windows.

Recursos principais:

- listar projetos `.pdsprj` e `.dsn`
- abrir um projeto no Proteus
- ler e escrever arquivos dentro da pasta dos projetos
- importar `.hex` / `.elf` para dentro do projeto
- criar backup `.zip`
- enviar atalhos de teclado para a janela do Proteus
- executar macros configuradas
- opcionalmente rodar um comando de build dentro da pasta do projeto

---

## 1) Ajuste o arquivo de configuração

Edite `proteus_bridge_config.json` e confirme:

- `proteus_exe`: caminho do `PDS.EXE`
- `projects_root`: pasta onde ficam seus projetos do Proteus
- `ui_macros.start`, `stop`, `pause`, `reset`: atalhos da sua versão do Proteus

> Se quiser liberar compilação externa, mude `allow_shell_commands` para `true`.

---

## 2) Inicie o servidor

No PowerShell:

```powershell
cd D:\Projetos
powershell -ExecutionPolicy Bypass -File .\start_proteus_bridge.ps1
```

Ou diretamente:

```powershell
python .\openwebui_proteus_bridge.py
```

A documentação automática ficará em:

- `http://127.0.0.1:8001/docs`
- `http://127.0.0.1:8001/openapi.json`

---

## 3) Conecte no Open WebUI

### Se o Open WebUI estiver no mesmo Windows
Use:

- `http://127.0.0.1:8001/openapi.json`

### Se o Open WebUI estiver em Docker
Use:

- `http://host.docker.internal:8001/openapi.json`

Cadastre isso no Open WebUI como ferramenta OpenAPI.

---

## 4) Automação completa em uma chamada

Agora você pode mandar o `Open WebUI` executar tudo de uma vez:

- compilar o firmware
- encontrar o artefato mais novo (`.hex` / `.elf`)
- copiar para a pasta do projeto
- abrir o `Proteus`
- disparar a macro de simulação

### Listar os perfis disponíveis

```http
GET /automation/profiles
```

### Executar o fluxo completo

```http
POST /automation/run
```

Exemplo com `arduino-cli`:

```json
{
  "profile": "arduino_cli_uno",
  "project": "meu_circuito.pdsprj",
  "source_dir": "D:/Arduino/Blink",
  "build_dir": "D:/Arduino/Blink/build",
  "run_macro": "start"
}
```

Exemplo só para copiar um `.hex` pronto e abrir o projeto:

```json
{
  "profile": "hex_copy_only",
  "project": "meu_circuito.pdsprj",
  "artifact_path": "D:/Builds/firmware.hex",
  "artifact_target_name": "firmware.hex",
  "run_macro": "start"
}
```

Você também pode testar sem executar nada usando:

```json
{
  "profile": "arduino_cli_uno",
  "project": "meu_circuito.pdsprj",
  "source_dir": "D:/Arduino/Blink",
  "dry_run": true
}
```

---

## 5) Endpoints úteis

### Verificar status

```http
GET /health
```

### Listar projetos

```http
GET /projects
```

### Abrir um projeto

```http
POST /projects/open
```

Body:

```json
{
  "project": "meu_circuito.pdsprj",
  "bring_to_front": true
}
```

### Ver arquivos do projeto

```http
GET /projects/files?project=meu_circuito.pdsprj
```

### Importar um `.hex`

```http
POST /projects/import-artifact
```

Body:

```json
{
  "project": "meu_circuito.pdsprj",
  "artifact_path": "D:/Builds/firmware.hex",
  "target_name": "firmware.hex",
  "overwrite": true
}
```

### Enviar teclas para o Proteus

```http
POST /proteus/send-keys
```

Body:

```json
{
  "keys": "^s"
}
```

### Rodar macro configurada

```http
POST /proteus/run-macro
```

Body:

```json
{
  "name": "start"
}
```

---

## 6) Sugestão de fluxo com IA

1. o Open WebUI gera ou ajusta o código-fonte
2. a bridge grava os arquivos do projeto
3. um build externo gera o `.hex`
4. a bridge copia o `.hex` para a pasta do projeto
5. a bridge abre o Proteus e envia a macro de `start`

---

## 7) Observação importante

O controle da interface do Proteus depende dos atalhos da sua versão instalada. Se a macro `start` não funcionar de primeira, basta ajustar `ui_macros` no arquivo `proteus_bridge_config.json`.
