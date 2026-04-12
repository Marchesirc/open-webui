# Publicacao do Repositorio

## Estado atual

- repositorio Git local inicializado
- branch atual preparada para publicacao
- tags locais criadas: v1.0.0, v1.0.1

## 1. Adicionar remoto

Exemplo GitHub:

```powershell
git remote add origin https://github.com/SEU_USUARIO/OpenWebUI_Proteus.git
```

Exemplo GitHub com SSH:

```powershell
git remote add origin git@github.com:SEU_USUARIO/OpenWebUI_Proteus.git
```

## 2. Publicar branch principal

```powershell
git push -u origin main
```

## 3. Publicar tag da release

```powershell
git push origin v1.0.1
```

## 4. Publicar todas as tags

```powershell
git push --tags
```

## 5. Criar release usando as notas

Use o conteudo de:

- RELEASE_NOTES_v1.0.1.md

## Observacao

Se o remoto usar outro nome de branch padrao, ajuste o comando de push conforme necessario.
