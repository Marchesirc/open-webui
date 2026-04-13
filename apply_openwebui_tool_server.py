from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests


SCRIPT_DIR = Path(__file__).resolve().parent


def is_valid_backend_dir(path: Path) -> bool:
    return (path / "open_webui" / "main.py").exists()


def detect_default_backend_dir() -> Path:
    env_candidates = [
        Path(p).expanduser()
        for p in [
            __import__("os").environ.get("OPENWEBUI_BACKEND_DIR", ""),
            __import__("os").environ.get("OPENWEBUI_HOME", ""),
            __import__("os").environ.get("OPENWEBUI_REPO", ""),
        ]
        if p
    ]

    candidates = []
    for candidate in env_candidates:
        if candidate.name.lower() == "backend":
            candidates.append(candidate)
        else:
            candidates.append(candidate / "backend")

    candidates.extend(
        [
            SCRIPT_DIR / "backend",
            SCRIPT_DIR.parent / "open-webui" / "backend",
            SCRIPT_DIR.parent.parent / "open-webui" / "backend",
            Path.home() / "open-webui" / "backend",
            Path.home() / "source" / "open-webui" / "backend",
            Path(r"C:\Users\rober\open-webui\backend"),
        ]
    )

    for candidate in candidates:
        if is_valid_backend_dir(candidate):
            return candidate

    return Path.home() / "open-webui" / "backend"


DEFAULT_BACKEND_DIR = detect_default_backend_dir()
DEFAULT_BRIDGE_BASE = "http://127.0.0.1:8001"
CONFIG_JSON_PATH = Path(__file__).with_name("openwebui_tool_server_connections.json")
SETTINGS_JSON_PATH = Path(__file__).with_name("openwebui_professional_settings.json")
PROMPT_SUGGESTIONS_PATH = Path(__file__).with_name("openwebui_default_prompt_suggestions.json")
FIXED_PROMPTS_PATH = Path(__file__).with_name("openwebui_fixed_prompts.json")
SYSTEM_PROMPT_PATH = Path(__file__).with_name("openwebui_professional_system_prompt.md")
PROFILE_SNAPSHOT_PATH = Path(__file__).with_name("openwebui_restore_snapshot.json")
RECOMMENDED_LOCAL_CORS = "http://127.0.0.1:8080;http://127.0.0.1:8082;http://127.0.0.1:8081;http://localhost:8080;http://localhost:8082;http://localhost:8081"

TRUSTED_WEB_DOMAINS = [
    "docs.python.org",
    "pypi.org",
    "fastapi.tiangolo.com",
    "www.starlette.io",
    "docs.pydantic.dev",
    "learn.microsoft.com",
    "stackoverflow.com",
    "github.com",
    "developer.mozilla.org",
]

PROTEUS_BRIDGE_ALLOWED_FUNCTIONS = [
    "assistant_capabilities",
    "assistant_pipeline",
    "assistant_quality_gate",
    "assistant_memory_add",
    "assistant_memory_search",
    "assistant_orchestrate",
    "assistant_checkpoint_decision",
    "assistant_checkpoints",
    "assistant_checkpoints_cleanup",
    "assistant_sla_summary",
    "assistant_checkpoints_escalate",
    "assistant_operations_dashboard",
    "assistant_operations_policies",
    "assistant_operations_escalate",
    "assistant_executive_dashboard",
    "assistant_executive_dashboard_scoped",
    "assistant_executive_policy",
    "assistant_executive_policy_list",
    "assistant_executive_dashboard_scoped_policy",
    "assistant_executive_policy_enforce",
    "assistant_executive_policy_enforce_escalate",
    "assistant_checkpoints_deduplicate",
    "assistant_executive_report",
    "assistant_executive_report_snapshot",
    "assistant_executive_report_index",
    "assistant_executive_report_cleanup",
    "assistant_executive_report_schedule",
    "assistant_executive_report_schedule_list",
    "assistant_executive_report_schedule_run",
    "assistant_audit_log",
    "assistant_audit_logs",
    "assistant_audit_summary",
    "assistant_incidents",
    "assistant_incidents_summary",
    "assistant_incidents_close_resolved",
    "assistant_incidents_reopen_regressed",
    "assistant_incidents_deduplicate",
    "assistant_incidents_correlation",
    "assistant_incidents_correlation_impact",
    "assistant_incidents_correlation_forecast",
    "assistant_incidents_playbooks_run",
    "assistant_incidents_playbooks_history",
    "assistant_incidents_postmortem",
    "assistant_incidents_postmortems",
    "assistant_incidents_metrics",
    "assistant_incidents_anomalies",
    "assistant_routing_rules_upsert",
    "assistant_routing_rules_list",
    "assistant_routing_resolve",
    "assistant_routing_rules_delete",
    "assistant_incidents_auto_ack_assign",
    "assistant_incidents_sla_breach_forecast",
    "assistant_incidents_remediation_run",
    "assistant_incidents_remediation_history",
    "assistant_incidents_escalation_run",
    "assistant_incidents_escalation_history",
    "assistant_incidents_rca_cluster",
    "assistant_incidents_rca_history",
    "health",
    "workspace_list",
    "workspace_search",
    "workspace_mkdir",
    "workspace_move",
    "workspace_delete",
    "web_search",
    "web_fetch",
    "network_check",
    "list_automation_profiles",
    "automation_run",
    "pdf_analyze_schematic",
    "pdf_extract_schematic_candidates",
    "pdf_build_focused_subset",
    "proteus_prepare_assisted_project",
]

LOCAL_PC_MCP_ALLOWED_FUNCTIONS = [
    "get_pc_summary",
    "check_local_services",
    "list_directory",
    "read_text_file",
    "list_processes",
    "list_available_automations",
    "run_automation",
    "open_target",
]


def load_default_system_prompt() -> str:
    if SYSTEM_PROMPT_PATH.exists():
        content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        if content:
            return content

    return (
        "Você é um assistente técnico-profissional em português do Brasil. "
        "Para código, arquivos e projetos locais, verifique com evidências antes de concluir. "
        "Para bugs, investigue a causa raiz, aplique a menor correção necessária e valide o resultado."
    )


DEFAULT_SYSTEM_PROMPT = load_default_system_prompt()
FALLBACK_DEFAULT_MODEL = "qwen2.5-coder:7b"


def detect_preferred_local_model() -> str | None:
    candidates = [
        "qwen2.5:14b",
        "qwen2.5-coder:7b",
        "qwen2.5:7b",
        "llama3.2:3b",
    ]

    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return None

        installed = {line.split()[0].strip() for line in result.stdout.splitlines()[1:] if line.strip()}
        for candidate in candidates:
            if candidate in installed:
                return candidate
    except Exception:
        return FALLBACK_DEFAULT_MODEL

    return FALLBACK_DEFAULT_MODEL


PREFERRED_LOCAL_MODEL = detect_preferred_local_model()

PROMPT_SUGGESTIONS = [
    {
        "title": ["Modo engenharia", "objetivo e verificável"],
        "content": "A partir de agora, responda em português do Brasil como um assistente técnico-profissional. Verifique fatos com evidências locais sempre que possível, seja direto, evite rodeios e finalize com próximos passos objetivos."
    },
    {
        "title": ["Analisar projeto", "arquitetura e prioridades"],
        "content": "Analise este projeto agora e entregue em formato objetivo: 1) objetivo do sistema, 2) arquitetura resumida, 3) riscos ou falhas prováveis, 4) plano de ação com prioridades alta, média e baixa."
    },
    {
        "title": ["Pesquisar na web", "fontes e conclusão prática"],
        "content": "Pesquise este tema em fontes confiáveis, compare os resultados, cite os links principais e finalize com uma recomendação prática em português do Brasil."
    },
    {
        "title": ["Organizar workspace", "sem quebrar o que funciona"],
        "content": "Mapeie este workspace, proponha uma estrutura profissional de pastas, nomes e scripts, e destaque mudanças seguras que melhoram a organização sem quebrar o que já funciona."
    },
    {
        "title": ["Automatizar Proteus", "abrir, importar e simular"],
        "content": "Use o Proteus Bridge para localizar o projeto, importar o firmware mais recente, abrir o Proteus e iniciar a simulação. Relate cada etapa executada e qualquer falha encontrada."
    },
    {
        "title": ["Diagnosticar serviços", "portas e endpoints locais"],
        "content": "Verifique agora as portas, os serviços e os endpoints locais deste ambiente. Aponte o que está ativo, o que falhou, a causa raiz e a correção exata."
    },
    {
        "title": ["Depurar erro", "causa raiz com prova"],
        "content": "Investigue este erro com método de engenharia: reproduza o problema, encontre a causa raiz, aplique a menor correção segura e prove o resultado com teste ou saída verificável."
    },
    {
        "title": ["Fase 1", "planner executor verifier"],
        "content": "Execute a Fase 1: monte um plano objetivo, rode verificacoes seguras com ferramentas locais, valide o resultado e finalize com score de confianca (0-100) e secoes obrigatorias da resposta."
    },
    {
        "title": ["Fase 2", "memoria semantica local"],
        "content": "Antes de responder tarefas tecnicas, recupere memorias locais relevantes, priorize conhecimento interno validado e use a web apenas para complementar lacunas com fontes confiaveis."
    },
    {
        "title": ["Fase 3", "orquestracao e checkpoint"],
        "content": "Execute a Fase 3: orquestre planner executor verifier, rode pipeline com qualidade minima e abra checkpoint de aprovacao quando houver risco, baixa confianca ou impacto alto."
    },
    {
        "title": ["Fase 4", "governanca de risco"],
        "content": "Execute a Fase 4: classifique risco por tipo de tarefa e confianca, aplique politica de aprovacao automatica, consulte auditoria de checkpoints e limpe historico antigo quando necessario."
    },
    {
        "title": ["Fase 5", "sla e escalonamento"],
        "content": "Execute a Fase 5: monitore aging de checkpoints pendentes, gere resumo SLA operacional, acione escalonamento automatico para itens fora do prazo e mantenha trilha de alerta objetiva."
    },
    {
        "title": ["Fase 6", "filas operacionais"],
        "content": "Execute a Fase 6: separe filas de risco, SLA e compliance, consulte dashboard operacional consolidado, aplique politicas independentes por fila e escale apenas o dominio correto."
    },
    {
        "title": ["Fase 7", "painel executivo"],
        "content": "Execute a Fase 7: consolide metricas historicas por fila, gere tendencias temporais, identifique eventos duplicados no historico e exponha um painel executivo objetivo para priorizacao."
    },
    {
        "title": ["Fase 8", "relatorio exportavel"],
        "content": "Execute a Fase 8: gere relatorio executivo exportavel em json, csv ou markdown com snapshot operacional, tendencias por fila e recomendacoes automaticas de priorizacao."
    },
    {
        "title": ["Fase 9", "snapshots e retencao"],
        "content": "Execute a Fase 9: publique snapshots versionados dos relatorios executivos, mantenha indice consultavel e aplique retencao historica para evitar crescimento operacional descontrolado."
    },
    {
        "title": ["Fase 10", "agendamento automatico"],
        "content": "Execute a Fase 10: configure agendas horarias ou diarias de snapshots por ambiente, calcule proxima execucao, dispare jobs vencidos automaticamente e aplique retencao conforme a politica do ambiente."
    },
    {
        "title": ["Fase 11", "multi-tenant e projetos"],
        "content": "Execute a Fase 11: segregue relatorios, snapshots e agendas por tenant, projeto e ambiente, mantenha filtros compostos consistentes e preserve compatibilidade com operacao existente."
    },
    {
        "title": ["Fase 12", "dashboard por escopo"],
        "content": "Execute a Fase 12: gere dashboard executivo filtrado por tenant, projeto e ambiente, acompanhe snapshots recentes, agendas vencidas e cobertura de escopo para decisao operacional." 
    },
    {
        "title": ["Fase 13", "politica por escopo"],
        "content": "Execute a Fase 13: configure politica de conformidade por tenant, projeto e ambiente, valide snapshots e agendas contra os limites e reporte pass ou fail com recomendacoes objetivas."
    },
    {
        "title": ["Fase 14", "auditoria e conformidade"],
        "content": "Execute a Fase 14: registre trilha completa de operacoes de politicas, liste logs filtrados por operacao, status, tenant e ambiente, e exponga sumario de conformidade para rastreabilidade regulatoria."
    },
    {
        "title": ["Fase 15", "enforcement automatico"],
        "content": "Execute a Fase 15: avalie conformidade por escopo e, em caso de falha, aplique remediacao automatica com execucao de agendas vencidas e geracao de snapshot para restaurar cobertura operacional."
    },
    {
        "title": ["Fase 16", "escalonamento de incidentes"],
        "content": "Execute a Fase 16: apos enforcement da politica, abra incidente automatico quando o escopo permanecer em fail, classifique severidade e consolide visao de incidentes por tenant/projeto/ambiente."
    },
    {
        "title": ["Fase 17", "fechamento automatico"],
        "content": "Execute a Fase 17: quando o escopo voltar para conformidade, feche incidentes abertos automaticamente, registre trilha de auditoria e mantenha apenas pendencias realmente ativas."
    },
    {
        "title": ["Fase 18", "reabertura por regressao"],
        "content": "Execute a Fase 18: se um escopo que estava estavel voltar a falhar, reabra incidente fechado automaticamente com severidade atualizada e rastreabilidade completa de regressao."
    },
    {
        "title": ["Fase 19", "deduplicacao inteligente"],
        "content": "Execute a Fase 19: aplique deduplicacao de incidentes por fingerprint e janela temporal, evite abrir duplicatas em regressao repetida e consolide itens redundantes no incidente principal."
    },
    {
        "title": ["Fase 20", "correlacao multi-escopo"],
        "content": "Execute a Fase 20: correlacione incidentes entre projetos de um mesmo tenant/ambiente, calcule impacto agregado por severidade e entregue priorizacao executiva baseada em grupos correlacionados."
    },
    {
        "title": ["Fase 21", "forecast de risco"],
        "content": "Execute a Fase 21: projete risco por grupo correlacionado usando tendencia recente vs janela anterior, estime crescimento de incidentes e priorize acoes preventivas antes do escalonamento critico."
    }
]

FIXED_PROMPTS = [
    {
        "command": "assistente-profissional",
        "name": "Assistente Profissional PT-BR",
        "tags": ["profissional", "pt-br", "workspace"],
        "content": "Atue como um assistente técnico-profissional em português do Brasil. Use ferramentas locais para analisar arquivos, organizar pastas, executar comandos com segurança e, quando faltar contexto, complemente com busca web e cite fontes. Responda com clareza, plano de ação e evidências verificáveis."
    },
    {
        "command": "pesquisa-web-profissional",
        "name": "Pesquisa Web com Fontes",
        "tags": ["web", "pesquisa", "fontes"],
        "content": "Pesquise na web de forma abrangente, priorize fontes confiáveis, compare resultados e responda em português do Brasil com resumo objetivo, pontos principais e links úteis."
    },
    {
        "command": "organizar-workspace",
        "name": "Organizar Workspace e Diretórios",
        "tags": ["workspace", "pastas", "organização"],
        "content": "Analise a estrutura atual do workspace e proponha ou execute uma organização profissional de pastas, diretórios, scripts, nomes de arquivos e documentação, minimizando riscos e preservando o que já funciona."
    },
    {
        "command": "proteus-workflow",
        "name": "Automação Profissional do Proteus",
        "tags": ["proteus", "isis", "automação"],
        "content": "Use o bridge do Proteus para localizar projetos, importar firmware, abrir o ISIS Proteus, executar macros de simulação e relatar claramente cada etapa executada e qualquer falha encontrada."
    },
    {
        "command": "diagnostico-rede",
        "name": "Diagnóstico de Rede e Serviços",
        "tags": ["rede", "serviços", "diagnóstico"],
        "content": "Verifique portas, conectividade, serviços locais, endpoints HTTP e comunicação de rede. Explique os achados com objetividade e proponha correções práticas."
    },
    {
        "command": "modo-copilot",
        "name": "Modo Copilot PT-BR",
        "tags": ["copilot", "engenharia", "workspace"],
        "content": "Atue como um assistente de engenharia de software em português do Brasil. Antes de concluir qualquer tarefa, verifique com evidências locais. Para bugs, reproduza, identifique a causa raiz, aplique a menor correção necessária e valide o resultado. Use o workspace, shell, busca web e ferramentas disponíveis de forma disciplinada e objetiva."
    },
    {
        "command": "debug-raiz",
        "name": "Debug de Causa Raiz",
        "tags": ["debug", "causa-raiz", "engenharia"],
        "content": "Investigue este problema com método: reproduza o erro, localize a causa raiz, evite suposições, faça uma correção mínima e prove o resultado com saídas verificáveis ou testes."
    },
    {
        "command": "fase1-pev",
        "name": "Fase 1 Planner Executor Verifier",
        "tags": ["fase1", "planner", "verifier"],
        "content": "Atue no modo Fase 1. Estruture a resposta em: Objetivo, Plano, Execucao, Validacao e Confianca (0-100). Sempre use evidencias locais quando possivel, destaque riscos e informe claramente se faltou alguma secao obrigatoria do contrato."
    },
    {
        "command": "fase2-memoria",
        "name": "Fase 2 Memoria Semantica",
        "tags": ["fase2", "memoria", "contexto"],
        "content": "Atue no modo Fase 2. Antes de responder, consulte memoria semantica local, reutilize padroes que funcionaram e indique explicitamente quais memorias sustentam a recomendacao."
    },
    {
        "command": "fase3-orquestracao",
        "name": "Fase 3 Orquestracao com Checkpoint",
        "tags": ["fase3", "orquestracao", "checkpoint"],
        "content": "Atue no modo Fase 3. Orquestre planner executor verifier, valide qualidade da resposta e abra checkpoint de aprovacao para decisoes sensiveis, informando claramente risco, confianca e proxima acao."
    },
    {
        "command": "fase4-governanca-risco",
        "name": "Fase 4 Governanca de Risco",
        "tags": ["fase4", "risco", "auditoria"],
        "content": "Atue no modo Fase 4. Classifique o risco da tarefa, siga a politica de aprovacao (auto, obrigatoria ou bloqueio), consulte checkpoints recentes para auditoria e mantenha trilha de decisao objetiva."
    },
    {
        "command": "fase5-sla-escalonamento",
        "name": "Fase 5 SLA e Escalonamento",
        "tags": ["fase5", "sla", "escalonamento"],
        "content": "Atue no modo Fase 5. Monitore o aging de checkpoints pendentes, resuma o estado SLA, destaque alertas warning e critical e acione escalonamento automatico quando o prazo operacional for excedido."
    },
    {
        "command": "fase6-filas-operacionais",
        "name": "Fase 6 Filas Operacionais",
        "tags": ["fase6", "operacoes", "compliance"],
        "content": "Atue no modo Fase 6. Separe os itens operacionais em filas independentes de risco, SLA e compliance, use o dashboard consolidado para priorizacao e escale somente a fila correta com justificativa objetiva."
    },
    {
        "command": "fase7-painel-executivo",
        "name": "Fase 7 Painel Executivo",
        "tags": ["fase7", "executivo", "tendencias"],
        "content": "Atue no modo Fase 7. Gere um painel executivo com metricas historicas por fila, tendencias por periodo, identificacao de eventos duplicados e recomendacoes curtas de priorizacao operacional."
    },
    {
        "command": "fase8-relatorio-executivo",
        "name": "Fase 8 Relatorio Executivo Exportavel",
        "tags": ["fase8", "relatorio", "exportacao"],
        "content": "Atue no modo Fase 8. Gere um relatorio executivo exportavel em json, csv ou markdown com snapshot operacional, alertas ativos, tendencias por fila e recomendacoes automaticas para a proxima acao."
    },
    {
        "command": "fase9-snapshots-retencao",
        "name": "Fase 9 Snapshots e Retencao",
        "tags": ["fase9", "snapshot", "retencao"],
        "content": "Atue no modo Fase 9. Gere snapshots versionados dos relatorios executivos, mantenha um indice consultavel das publicacoes e aplique politicas de retencao historica com limpeza controlada."
    },
    {
        "command": "fase10-agendamento-relatorios",
        "name": "Fase 10 Agendamento de Relatorios",
        "tags": ["fase10", "agendamento", "automacao"],
        "content": "Atue no modo Fase 10. Configure agendas horarias ou diarias de relatorios executivos por ambiente, acompanhe a proxima execucao, rode jobs vencidos e mantenha retencao automatica coerente com a politica operacional."
    },
    {
        "command": "fase11-multi-tenant-relatorios",
        "name": "Fase 11 Multi-Tenant e Projetos",
        "tags": ["fase11", "tenant", "projetos"],
        "content": "Atue no modo Fase 11. Separe relatorios, snapshots e agendas por tenant, projeto e ambiente, consulte filtros compostos antes de agir e mantenha o historico operacional isolado por escopo."
    },
    {
        "command": "fase12-dashboard-escopo",
        "name": "Fase 12 Dashboard Executivo por Escopo",
        "tags": ["fase12", "dashboard", "tenant"],
        "content": "Atue no modo Fase 12. Monte um dashboard executivo filtrado por tenant, projeto e ambiente, destaque snapshots recentes, agendas vencidas e lacunas de cobertura para orientar a proxima acao." 
    },
    {
        "command": "fase13-politica-escopo",
        "name": "Fase 13 Politica de Conformidade por Escopo",
        "tags": ["fase13", "politica", "tenant"],
        "content": "Atue no modo Fase 13. Defina politica por tenant, projeto e ambiente, avalie conformidade do dashboard scoped (snapshots, idade, formatos e agendas) e entregue recomendacoes acionaveis quando houver violacao." 
    },
    {
        "command": "fase14-auditoria-conformidade",
        "name": "Fase 14 Auditoria e Logs de Conformidade",
        "tags": ["fase14", "auditoria", "compliance"],
        "content": "Atue no modo Fase 14. Registre trilha completa de operacoes, liste logs filtrados por operacao/status/tenant/ambiente com prazo configuravel, e exponha sumario consolidado de conformidade para validacao regulatoria."
    },
    {
        "command": "fase15-enforcement-politica",
        "name": "Fase 15 Enforcement de Politica",
        "tags": ["fase15", "enforcement", "remediacao"],
        "content": "Atue no modo Fase 15. Quando uma politica scoped falhar, execute remediacao automatica segura (rodar agendas vencidas e gerar snapshot), reavalie conformidade e registre trilha de auditoria da acao."
    },
    {
        "command": "fase16-escalonamento-incidentes",
        "name": "Fase 16 Escalonamento de Incidentes",
        "tags": ["fase16", "incidentes", "escalonamento"],
        "content": "Atue no modo Fase 16. Se a politica continuar em fail apos enforcement, abra incidente automatico com severidade, owner e canal, e entregue resumo consolidado de incidentes por escopo."
    },
    {
        "command": "fase17-fechamento-incidentes",
        "name": "Fase 17 Fechamento Automatico de Incidentes",
        "tags": ["fase17", "incidentes", "resolucao"],
        "content": "Atue no modo Fase 17. Ao detectar conformidade restaurada no escopo, feche incidentes abertos automaticamente, registre motivo de fechamento e atualize o resumo de incidentes sem perder rastreabilidade."
    },
    {
        "command": "fase18-reabertura-regressao",
        "name": "Fase 18 Reabertura por Regressao",
        "tags": ["fase18", "incidentes", "regressao"],
        "content": "Atue no modo Fase 18. Ao detectar regressao de conformidade em escopo com historico fechado, reabra o incidente mais recente, atualize severidade e registre trilha de auditoria da transicao closed para open."
    },
    {
        "command": "fase19-deduplicacao-incidentes",
        "name": "Fase 19 Deduplicacao Inteligente de Incidentes",
        "tags": ["fase19", "incidentes", "deduplicacao"],
        "content": "Atue no modo Fase 19. Calcule fingerprint por escopo e checks falhos, reutilize incidente aberto na janela configurada e execute deduplicacao batch para marcar duplicados sem perder rastreabilidade."
    },
    {
        "command": "fase20-correlacao-impacto",
        "name": "Fase 20 Correlacao e Impacto Multi-Escopo",
        "tags": ["fase20", "correlacao", "impacto"],
        "content": "Atue no modo Fase 20. Correlacione incidentes por tenant e ambiente, consolide impacto agregado por severidade e causas, e entregue ranking priorizado dos grupos com recomendacoes executivas objetivas."
    },
    {
        "command": "fase21-forecast-risco",
        "name": "Fase 21 Forecast de Risco",
        "tags": ["fase21", "forecast", "risco"],
        "content": "Atue no modo Fase 21. Compare tendencia recente com janela anterior por grupo correlacionado, estime crescimento de incidentes e entregue previsao de risco com priorizacao preventiva por tier."
    },
    {
        "command": "fase22-playbooks-preventivos",
        "name": "Fase 22 Playbooks Preventivos por Forecast",
        "tags": ["fase22", "playbooks", "preventivo", "forecast"],
        "content": "Atue no modo Fase 22. Avalie o forecast de risco por grupo correlacionado e execute playbooks preventivos automaticos para grupos em tier high ou critical: forcanda intervalo de schedule, abrindo incidentes preditivos, atribuindo owner e alertando canais ops antes do escalonamento real."
    },
    {
        "command": "fase23-postmortem",
        "name": "Fase 23 Postmortem Automatico",
        "tags": ["fase23", "postmortem", "incidentes"],
        "content": "Atue no modo Fase 23. Para um incidente fechado, gere postmortem estruturado com timeline, checks falhos, politicas violadas, playbooks executados, duracao e action items priorizados para prevencao de reincidencia."
    },
    {
        "command": "fase24-metricas-sre",
        "name": "Fase 24 Metricas SRE MTTR MTTD MTTA",
        "tags": ["fase24", "sre", "mttr", "mttd", "mtta"],
        "content": "Atue no modo Fase 24. Calcule MTTD, MTTA e MTTR por severidade e escopo para o periodo configurado. Identifique gargalos de deteccao, reconhecimento e resolucao com ranking comparativo."
    },
    {
        "command": "fase25-anomalia-incidentes",
        "name": "Fase 25 Deteccao de Anomalias",
        "tags": ["fase25", "anomalia", "spike", "z-score"],
        "content": "Atue no modo Fase 25. Detecte spikes e padroes anomalos na frequencia de incidentes usando z-score por bucket de tempo configuravel. Identifique escopos com comportamento fora do padrao antes do forecast."
    },
    {
        "command": "fase26-matriz-roteamento",
        "name": "Fase 26 Matriz de Roteamento",
        "tags": ["fase26", "routing", "owner", "sla"],
        "content": "Atue no modo Fase 26. Crie e consulte regras de roteamento por tier, tenant e ambiente para centralizar owner, canal de alerta e SLA target. Resolva o roteamento dinamicamente para qualquer combinacao tier/tenant/env."
    },
    {
        "command": "fase27-auto-ack-sla",
        "name": "Fase 27 Auto Ack Assignment e Preditor SLA",
        "tags": ["fase27", "auto-ack", "assignment", "sla"],
        "content": "Atue no modo Fase 27. Aplique auto-ack e auto-assignment para incidentes abertos usando matriz de roteamento por tier/tenant/ambiente, e gere previsao de violacao de SLA com risco por incidente para priorizacao operacional imediata."
    },
    {
        "command": "fase28-auto-remediacao",
        "name": "Fase 28 Auto Remediacao Guiada por Playbook",
        "tags": ["fase28", "auto-remediacao", "playbook", "approval"],
        "content": "Atue no modo Fase 28. Execute remediacao automatizada para incidentes com risco alto de SLA usando playbooks seguros, com suporte a dry-run e aprovacao explicita antes da execucao efetiva."
    },
    {
        "command": "fase29-escalonamento-temporal-sla",
        "name": "Fase 29 Escalonamento Temporal de SLA",
        "tags": ["fase29", "sla", "escalonamento", "t-15", "t-5"],
        "content": "Atue no modo Fase 29. Execute governanca de SLA com escalonamento temporal por estagios T-15, T-5 e breached, aplicando prioridade progressiva, bloqueio por aprovacao opcional e historico auditavel de escalacoes."
    },
    {
        "command": "fase30-rca-clusterizacao",
        "name": "Fase 30 RCA Assistido com Clusterizacao de Causa Raiz",
        "tags": ["fase30", "rca", "clusterizacao", "causa-raiz", "incidentes"],
        "content": "Atue no modo Fase 30. Execute analise de causa raiz assistida com clusterizacao de incidentes por fingerprint de causa. Agrupe incidentes recorrentes por categoria, calcule taxa de recorrencia e confidence score, e persista os clusters para auditoria e acompanhamento de tendencias."
    }
]


def create_local_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies.update({"http": None, "https": None})
    return session


def verify_bridge(base_url: str) -> dict:
    base_url = base_url.rstrip('/')
    try:
        with create_local_session() as session:
            health = session.get(f"{base_url}/health", timeout=8)
            health.raise_for_status()

            openapi = session.get(f"{base_url}/openapi.json", timeout=8)
            openapi.raise_for_status()
        return {"ok": True, "url": base_url}
    except requests.RequestException as exc:
        return {
            "ok": False,
            "url": base_url,
            "warning": f"Bridge indisponível no momento: {exc.__class__.__name__}: {exc}",
        }


def build_openapi_connection(base_url: str, principal_id: str) -> dict:
    bridge_token = (__import__("os").environ.get("OWUI_BRIDGE_TOKEN", "") or "").strip()
    headers = {"X-Bridge-Token": bridge_token} if bridge_token else {}
    base_url = base_url.rstrip("/")
    name = "Professional Workspace & Proteus Bridge"
    description = "Ferramenta profissional para Open WebUI com automação do Proteus, busca no workspace, execução controlada de PowerShell e apoio de busca web."
    return {
        "name": name,
        "description": description,
        "info": {
            "id": "proteus-bridge",
            "name": name,
            "description": description,
        },
        "url": base_url,
        "path": "/openapi.json",
        "type": "openapi",
        "auth_type": "none",
        "key": "",
        "headers": headers,
        "config": {
            "enable": True,
            "access_grants": [
                {
                    "principal_type": "user",
                    "principal_id": principal_id,
                    "permission": "read",
                }
            ],
            "function_name_filter_list": PROTEUS_BRIDGE_ALLOWED_FUNCTIONS,
        },
    }


def build_pc_mcp_connection(principal_id: str, base_url: str = "http://127.0.0.1:8765") -> dict:
    base_url = base_url.rstrip("/")
    name = "Local PC MCP"
    description = "Servidor MCP local deste PC para diagnóstico do Windows, serviços do Open WebUI e leitura controlada do workspace."
    return {
        "name": name,
        "description": description,
        "info": {
            "id": "local-pc-mcp",
            "name": name,
            "description": description,
        },
        "url": base_url,
        "path": "/mcp",
        "type": "mcp",
        "auth_type": "none",
        "key": "",
        "headers": {},
        "config": {
            "enable": True,
            "access_grants": [
                {
                    "principal_type": "user",
                    "principal_id": principal_id,
                    "permission": "read",
                }
            ],
            "function_name_filter_list": LOCAL_PC_MCP_ALLOWED_FUNCTIONS,
        },
    }


def build_connections(
    bridge_base: str,
    principal_id: str,
    pc_mcp_base: str = "http://127.0.0.1:8765",
) -> list[dict]:
    return [
        build_openapi_connection(bridge_base, principal_id),
        build_pc_mcp_connection(principal_id, pc_mcp_base),
    ]


def resolve_primary_principal_id() -> str:
    import os
    from open_webui.internal.db import get_db
    from open_webui.models.users import User

    override = (os.environ.get("OPENWEBUI_ACCESS_PRINCIPAL", "") or "").strip()
    if override:
        return override

    with get_db() as db:
        admin = db.query(User).filter(User.role == 'admin').order_by(User.created_at.asc()).first()
        if admin is not None:
            return str(admin.id)

        first_user = db.query(User).order_by(User.created_at.asc()).first()
        if first_user is not None:
            return str(first_user.id)

    return "*"


def apply_professional_settings(config: dict) -> dict:
    ui = config.setdefault("ui", {})
    ui["prompt_suggestions"] = PROMPT_SUGGESTIONS
    ui["default_locale"] = "pt-BR"

    # Open WebUI 0.8.x lê estas opções no nível raiz do config.
    config["default_prompt_suggestions"] = PROMPT_SUGGESTIONS

    models = config.setdefault("models", {})
    default_params = dict(models.get("default_params") or {})

    target_default_model = PREFERRED_LOCAL_MODEL or FALLBACK_DEFAULT_MODEL

    if target_default_model:
        config["default_models"] = target_default_model
        ui["default_models"] = target_default_model
        ui["default_pinned_models"] = target_default_model
        model_params = dict(default_params.get(target_default_model) or {})
        model_params.update(
            {
                "temperature": 0.2,
                "top_p": 0.9,
                "system": DEFAULT_SYSTEM_PROMPT,
                "function_calling": "default",
            }
        )
        default_params[target_default_model] = model_params
        models["default_params"] = default_params

    user_permissions = config.setdefault("user_permissions", {})
    workspace_permissions = user_permissions.setdefault("workspace", {})
    workspace_permissions.update(
        {
            "prompts": True,
            "tools": True,
            "skills": True,
            "prompts_import": True,
            "prompts_export": True,
            "tools_import": True,
            "tools_export": True,
        }
    )

    sharing_permissions = user_permissions.setdefault("sharing", {})
    sharing_permissions.update(
        {
            "prompts": True,
            "public_prompts": True,
            "tools": True,
            "public_tools": True,
            "skills": True,
            "public_skills": True,
        }
    )

    features_permissions = user_permissions.setdefault("features", {})
    features_permissions.update(
        {
            "direct_tool_servers": True,
            "web_search": True,
            "memories": True,
            "notes": True,
            "folders": True,
        }
    )

    rag = config.setdefault("rag", {})
    web = rag.setdefault("web", {})
    search = web.setdefault("search", {})
    search.update(
        {
            "enable": True,
            "engine": "duckduckgo",
            "result_count": 8,
            "concurrent_requests": 6,
            "bypass_embedding_and_retrieval": False,
            "bypass_web_loader": False,
            "trust_env": False,
            "ddgs_backend": "auto",
            "fetch_url_max_content_length": 12000,
            "domain": {"filter_list": TRUSTED_WEB_DOMAINS},
        }
    )

    loader = web.setdefault("loader", {})
    loader.update(
        {
            "engine": "safe_web",
            "concurrent_requests": 6,
            "timeout": "15",
            "ssl_verification": True,
        }
    )

    return {
        "default_prompt_suggestions": config.get("default_prompt_suggestions", PROMPT_SUGGESTIONS),
        "default_models": config.get("default_models"),
        "ui": {
            "prompt_suggestions": PROMPT_SUGGESTIONS,
            "default_locale": ui.get("default_locale"),
            "default_models": config.get("default_models") or ui.get("default_models"),
            "default_pinned_models": ui.get("default_pinned_models"),
        },
        "models": {"default_params": models.get("default_params", {})},
        "user_permissions": user_permissions,
        "rag": {"web": {"search": search, "loader": loader}},
    }


def sync_fixed_prompts(principal_id: str) -> dict:
    from open_webui.internal.db import get_db
    from open_webui.models.prompts import PromptForm, Prompts
    from open_webui.models.users import User

    created = []
    updated = []

    with get_db() as db:
        owner = db.query(User).filter(User.role == 'admin').order_by(User.created_at.asc()).first()
        if owner is None:
            owner = db.query(User).order_by(User.created_at.asc()).first()

        if owner is None:
            now = int(time.time())
            owner = User(
                id='system-prompts-owner',
                email='system-prompts@local',
                username='system_prompts',
                role='admin',
                name='System Prompts',
                profile_image_url='/static/favicon.png',
                info={},
                settings={},
                oauth={},
                scim={},
                last_active_at=now,
                updated_at=now,
                created_at=now,
            )
            db.add(owner)
            db.commit()
            db.refresh(owner)

        for prompt in FIXED_PROMPTS:
            form = PromptForm(
                command=prompt["command"],
                name=prompt["name"],
                content=prompt["content"],
                tags=prompt.get("tags", []),
                data={"scope": "professional-profile"},
                meta={"category": "professional", "language": "pt-BR"},
                access_grants=[
                    {
                        "principal_type": "user",
                        "principal_id": principal_id,
                        "permission": "read",
                    }
                ],
                commit_message="Professional prompt sync",
                is_production=True,
            )

            existing = Prompts.get_prompt_by_command(prompt["command"], db=db)
            if existing:
                Prompts.update_prompt_by_command(prompt["command"], form, owner.id, db=db)
                updated.append(prompt["command"])
            else:
                Prompts.insert_new_prompt(owner.id, form, db=db)
                created.append(prompt["command"])

    return {"created": created, "updated": updated}


def sync_user_settings(
    default_system_prompt: str,
    desired_tool_servers: list[dict],
    default_model: str | None,
) -> dict:
    from open_webui.internal.db import get_db
    from open_webui.models.users import User

    updated = []
    unchanged = []

    with get_db() as db:
        users = db.query(User).order_by(User.created_at.asc()).all()

        for user in users:
            settings = dict(user.settings or {})
            ui = dict(settings.get('ui') or {})
            changed = False

            if ui.get('version') is None:
                ui['version'] = '0.8.12'
                changed = True

            if not ui.get('locale'):
                ui['locale'] = 'pt-BR'
                changed = True

            if default_model:
                if ui.get('defaultModels') != default_model:
                    ui['defaultModels'] = default_model
                    changed = True

                if settings.get('default_models') != default_model:
                    settings['default_models'] = default_model
                    changed = True

            existing_system = (settings.get('system') or '').strip()
            should_apply_system = (
                not existing_system
                or existing_system.startswith('# Open WebUI — Perfil Profissional PT-BR')
            )
            if should_apply_system and existing_system != default_system_prompt:
                settings['system'] = default_system_prompt
                changed = True

            existing_tool_servers = list(ui.get('toolServers') or settings.get('toolServers') or [])
            merged_tool_servers = []
            matched_names = set()
            for server in existing_tool_servers:
                matched = next(
                    (
                        desired
                        for desired in desired_tool_servers
                        if server.get('name') == desired.get('name') or server.get('url') == desired.get('url')
                    ),
                    None,
                )
                if matched:
                    merged_tool_servers.append(matched)
                    matched_names.add(matched.get('name'))
                else:
                    merged_tool_servers.append(server)

            for desired in desired_tool_servers:
                if desired.get('name') not in matched_names:
                    merged_tool_servers.append(desired)

            if ui.get('toolServers') != merged_tool_servers:
                ui['toolServers'] = merged_tool_servers
                changed = True
            settings['toolServers'] = merged_tool_servers

            desired_tools = [f'direct_server:{idx}' for idx, _ in enumerate(merged_tool_servers)]

            existing_tools = [tool_id for tool_id in list(ui.get('tools') or settings.get('tools') or []) if tool_id]
            non_server_tools = [
                tool_id
                for tool_id in existing_tools
                if not str(tool_id).startswith('direct_server:')
                and not str(tool_id).startswith('server:mcp:')
            ]
            merged_tools = list(dict.fromkeys(non_server_tools + desired_tools))
            if ui.get('tools') != merged_tools:
                ui['tools'] = merged_tools
                changed = True
            settings['tools'] = merged_tools

            settings['ui'] = ui

            if changed:
                user.settings = settings
                db.commit()
                db.refresh(user)
                updated.append(user.email)
            else:
                unchanged.append(user.email)

    return {
        'updated': updated,
        'unchanged': unchanged,
        'system_prompt_preview': default_system_prompt.splitlines()[0][:120] if default_system_prompt else '',
    }


def main() -> int:
    import os

    if not (os.environ.get("CORS_ALLOW_ORIGIN", "") or "").strip():
        os.environ["CORS_ALLOW_ORIGIN"] = RECOMMENDED_LOCAL_CORS

    backend_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BACKEND_DIR
    bridge_base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BRIDGE_BASE

    if not backend_dir.exists():
        raise SystemExit(f"Backend do Open WebUI não encontrado: {backend_dir}")

    bridge_status = verify_bridge(bridge_base)

    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from open_webui.config import get_config, save_config
    from open_webui.env import DATA_DIR, DATABASE_URL

    config = get_config() or {"version": 0, "ui": {}}
    tool_server = config.setdefault("tool_server", {})
    connections = tool_server.setdefault("connections", [])

    principal_id = resolve_primary_principal_id()
    desired_connections = build_connections(bridge_base, principal_id)

    new_connections = []
    matched_names = set()
    for conn in connections:
        matched = next(
            (
                desired
                for desired in desired_connections
                if conn.get("url") == desired["url"] or conn.get("name") == desired["name"]
            ),
            None,
        )
        if matched:
            new_connections.append(matched)
            matched_names.add(matched["name"])
        else:
            new_connections.append(conn)

    for desired in desired_connections:
        if desired["name"] not in matched_names:
            new_connections.append(desired)

    tool_server["connections"] = new_connections
    professional_settings = apply_professional_settings(config)

    if not save_config(config):
        raise SystemExit("Falha ao salvar a configuração do Open WebUI")

    prompt_sync = sync_fixed_prompts(principal_id)
    target_default_model = PREFERRED_LOCAL_MODEL or FALLBACK_DEFAULT_MODEL
    user_sync = sync_user_settings(DEFAULT_SYSTEM_PROMPT, new_connections, target_default_model)

    CONFIG_JSON_PATH.write_text(
        json.dumps({"TOOL_SERVER_CONNECTIONS": new_connections}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    SETTINGS_JSON_PATH.write_text(
        json.dumps(
            {
                "tool_server": {"connections": new_connections},
                **professional_settings,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    PROMPT_SUGGESTIONS_PATH.write_text(
        json.dumps(PROMPT_SUGGESTIONS, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    FIXED_PROMPTS_PATH.write_text(
        json.dumps(FIXED_PROMPTS, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    PROFILE_SNAPSHOT_PATH.write_text(
        json.dumps(
            {
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "prompt_suggestions": PROMPT_SUGGESTIONS,
                "default_prompt_suggestions": PROMPT_SUGGESTIONS,
                "default_models": target_default_model,
                "fixed_prompts": FIXED_PROMPTS,
                "bridge_url": bridge_base,
                "backend_dir": str(backend_dir),
                "preferred_local_model": target_default_model,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "ok": True,
            "backend_dir": str(backend_dir),
            "data_dir": str(DATA_DIR),
            "database_url": str(DATABASE_URL),
            "connections": new_connections,
            "bridge_status": bridge_status,
            "access_principal_id": principal_id,
            "preferred_local_model": PREFERRED_LOCAL_MODEL,
            "target_default_model": target_default_model,
            "professional_settings": professional_settings,
            "prompt_sync": prompt_sync,
            "user_sync": user_sync,
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
