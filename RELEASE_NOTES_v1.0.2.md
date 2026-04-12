# Release Notes - v1.0.2

## Resumo

Release funcional com consolidacao do fluxo oficial pass4 para PDFs de service guide, OCR sem admin e melhoria de rastreabilidade operacional.

## Principais melhorias

- endpoint oficial `POST /pdf/build-focused-subset` com perfis:
  - `acer_pass3_pure_schematics`
  - `acer_pass4_signal_dense`
- suporte de OCR com `TESSDATA_PREFIX` no perfil do usuario (sem admin)
- script de instalacao de idioma OCR por usuario (`por`) com copia de `eng` e `osd`
- script oficial de pipeline pass4 e script de producao com parametros fixos do ambiente Acer
- checklist por bloco com redes prioritarias e score por rede:
  - `priority_nets`
  - `priority_nets_confidence`
- relatorio automatico comparativo pass2/pass3/pass4 em JSON e Markdown

## Arquivos principais

- openwebui_proteus_bridge.py
- README.md
- instalar_idioma_ocr_por_usuario.ps1
- executar_pipeline_pass4_oficial.ps1
- executar_pipeline_pass4_producao.ps1
- gerar_relatorio_comparativo_passes.ps1

## Impacto tecnico

- aumenta previsibilidade do fluxo assistido para montagem no ISIS Proteus
- reduz dependencia de scripts temporarios para recortes focados de PDF
- melhora auditabilidade e comparacao entre passes

## Publicacao

```powershell
git push -u origin main
git push origin v1.0.2
```
