from __future__ import annotations

import csv
import glob
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import psutil
import requests
import uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "proteus_bridge_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "proteus_exe": r"C:/Users/rober/AppData/Local/Programs/Labcenter Electronics/Proteus 9 Professional/Bin/PDS.exe",
    "projects_root": str(BASE_DIR),
    "host": "127.0.0.1",
    "port": 8001,
    "allow_write": False,
    "allow_shell_commands": False,
    "bridge_api_token": "",
    "allowed_origins": [
        "http://127.0.0.1",
        "http://localhost",
    ],
    "shell_timeout_seconds": 60,
    "shell_max_output_chars": 12000,
    "web_search_max_results": 8,
    "web_fetch_max_chars": 12000,
    "web_user_agent": "Mozilla/5.0",
    "project_extensions": [".pdsprj", ".dsn"],
    "editable_extensions": [
        ".txt",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".ino",
        ".asm",
        ".hex",
        ".elf",
        ".json",
        ".md",
        ".py",
        ".xml",
        ".ps1",
        ".bat",
    ],
    "default_launch_args": [],
    "proteus_window_title_hint": "Proteus",
    "ui_macros": {
        "save": "^s",
        "start": "",
        "stop": "",
        "pause": "",
        "reset": "",
    },
    "automation_profiles": {
        "hex_copy_only": {
            "description": "Usa um HEX/ELF já compilado, copia para a pasta do projeto e abre o Proteus.",
            "build": {"enabled": False},
            "artifact": {
                "path": "",
                "glob": "",
                "target_name": "firmware.hex",
                "overwrite": True
            },
            "actions": {
                "backup": True,
                "open_project": True,
                "focus": True,
                "macro_after_open": "",
                "delay_after_open_ms": 1200,
                "send_keys": []
            }
        },
        "arduino_cli_uno": {
            "description": "Compila um sketch Arduino UNO com arduino-cli e injeta o HEX no projeto.",
            "build": {
                "enabled": True,
                "command": "arduino-cli",
                "args": [
                    "compile",
                    "--fqbn",
                    "arduino:avr:uno",
                    "{source_dir}",
                    "--output-dir",
                    "{build_dir}"
                ],
                "cwd": "{source_dir}",
                "timeout_seconds": 300
            },
            "artifact": {
                "path": "{build_dir}/{source_name}.ino.hex",
                "glob": "{build_dir}/**/*.hex",
                "target_name": "firmware.hex",
                "overwrite": True
            },
            "actions": {
                "backup": True,
                "open_project": True,
                "focus": True,
                "macro_after_open": "start",
                "delay_after_open_ms": 1500,
                "send_keys": []
            }
        },
        "platformio_generic": {
            "description": "Roda PlatformIO, encontra o ELF mais novo e inicia o Proteus.",
            "build": {
                "enabled": True,
                "command": "pio",
                "args": ["run"],
                "cwd": "{source_dir}",
                "timeout_seconds": 300
            },
            "artifact": {
                "glob": "{source_dir}/.pio/build/**/*.elf",
                "target_name": "firmware.elf",
                "overwrite": True
            },
            "actions": {
                "backup": True,
                "open_project": True,
                "focus": True,
                "macro_after_open": "start",
                "delay_after_open_ms": 1500,
                "send_keys": []
            }
        },
        "custom_external_builder": {
            "description": "Executa um script PowerShell de build e importa o artefato gerado.",
            "build": {
                "enabled": True,
                "command": "powershell",
                "args": [
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    "{source_dir}/build.ps1"
                ],
                "cwd": "{source_dir}",
                "timeout_seconds": 600
            },
            "artifact": {
                "glob": "{build_dir}/**/*.hex",
                "target_name": "firmware.hex",
                "overwrite": True
            },
            "actions": {
                "backup": True,
                "open_project": True,
                "focus": True,
                "macro_after_open": "start",
                "delay_after_open_ms": 1500,
                "send_keys": []
            }
        }
    },
}


def deep_update(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_update(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        return dict(DEFAULT_CONFIG)

    try:
        user_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        return deep_update(DEFAULT_CONFIG, user_config)
    except Exception as exc:
        raise RuntimeError(f"Falha ao ler {CONFIG_PATH}: {exc}") from exc


CONFIG = load_config()

_METRICS_LOCK = Lock()
_BRIDGE_METRICS: dict[str, Any] = {
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "requests_total": 0,
    "requests_2xx": 0,
    "requests_4xx": 0,
    "requests_5xx": 0,
    "requests_denied": 0,
    "by_route": {},
}


def _bridge_token() -> str:
    token = str(CONFIG.get("bridge_api_token", "")).strip()
    if token:
        return token
    return (__import__("os").environ.get("OWUI_BRIDGE_TOKEN", "") or "").strip()


def _is_public_path(path: str) -> bool:
    return (
        path in {"/", "/health", "/metrics", "/openapi.json", "/docs", "/redoc"}
        or path.startswith("/docs")
        or path.startswith("/redoc")
    )


def _record_metric(path: str, method: str, status_code: int, duration_ms: float) -> None:
    bucket = "requests_5xx"
    if 200 <= status_code < 300:
        bucket = "requests_2xx"
    elif 400 <= status_code < 500:
        bucket = "requests_4xx"

    route = f"{method} {path}"
    with _METRICS_LOCK:
        _BRIDGE_METRICS["requests_total"] = int(_BRIDGE_METRICS.get("requests_total", 0)) + 1
        _BRIDGE_METRICS[bucket] = int(_BRIDGE_METRICS.get(bucket, 0)) + 1
        by_route = _BRIDGE_METRICS.setdefault("by_route", {})
        stats = dict(by_route.get(route) or {})
        stats["count"] = int(stats.get("count", 0)) + 1
        stats["last_status"] = int(status_code)
        stats["last_latency_ms"] = round(float(duration_ms), 2)
        stats["avg_latency_ms"] = round(
            ((float(stats.get("avg_latency_ms", 0.0)) * (stats["count"] - 1)) + float(duration_ms)) / stats["count"],
            2,
        )
        by_route[route] = stats

@asynccontextmanager
async def bridge_lifespan(_app: FastAPI):
    try:
        _phase10_run_due_schedules(limit=10)
    except Exception:
        pass
    yield


app = FastAPI(
    title="Professional Workspace & Proteus Bridge",
    version="1.2.0",
    description="Ponte local para o Open WebUI com automação do ISIS Proteus, busca web, leitura do workspace e execução controlada de comandos.",
    lifespan=bridge_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.get("allowed_origins", ["http://127.0.0.1", "http://localhost"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bridge_auth_and_metrics(request: Request, call_next):
    started = time.perf_counter()
    expected_token = _bridge_token()
    path = request.url.path

    if expected_token and (not _is_public_path(path)):
        provided = (request.headers.get("x-bridge-token") or "").strip()
        if provided != expected_token:
            with _METRICS_LOCK:
                _BRIDGE_METRICS["requests_denied"] = int(_BRIDGE_METRICS.get("requests_denied", 0)) + 1
            duration_ms = (time.perf_counter() - started) * 1000.0
            _record_metric(path, request.method, 401, duration_ms)
            return JSONResponse(status_code=401, content={"ok": False, "error": "invalid_bridge_token"})

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000.0
        _record_metric(path, request.method, 500, duration_ms)
        raise

    duration_ms = (time.perf_counter() - started) * 1000.0
    _record_metric(path, request.method, int(response.status_code), duration_ms)
    response.headers["x-bridge-service"] = "openwebui-proteus-bridge"
    return response


class OpenProjectRequest(BaseModel):
    project: str = Field(..., description="Nome, caminho relativo, .pdsprj ou .dsn")
    extra_args: list[str] = Field(default_factory=list)
    bring_to_front: bool = True


class BackupProjectRequest(BaseModel):
    project: str
    destination_dir: Optional[str] = None


class ImportArtifactRequest(BaseModel):
    project: str
    artifact_path: str
    target_name: Optional[str] = None
    overwrite: bool = True


class FileWriteRequest(BaseModel):
    relative_path: str
    content: str
    overwrite: bool = True


class SendKeysRequest(BaseModel):
    keys: str = Field(..., description="Sintaxe WScript.Shell SendKeys, ex.: ^s, {F5}, %{F4}")
    window_title: Optional[str] = None
    delay_ms: int = 300


class MacroRequest(BaseModel):
    name: str
    delay_ms: int = 300


class BuildRequest(BaseModel):
    project: str
    command: str
    args: list[str] = Field(default_factory=list)
    timeout_seconds: int = 180


class CloseRequest(BaseModel):
    force: bool = False


class ConfigUpdateRequest(BaseModel):
    proteus_exe: Optional[str] = None
    projects_root: Optional[str] = None
    allow_write: Optional[bool] = None
    allow_shell_commands: Optional[bool] = None
    proteus_window_title_hint: Optional[str] = None
    shell_timeout_seconds: Optional[int] = None
    shell_max_output_chars: Optional[int] = None
    web_search_max_results: Optional[int] = None
    web_fetch_max_chars: Optional[int] = None
    ui_macros: Optional[dict[str, str]] = None
    automation_profiles: Optional[dict[str, Any]] = None


class AutomationRunRequest(BaseModel):
    profile: str
    project: str
    source_dir: Optional[str] = None
    build_dir: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_target_name: Optional[str] = None
    backup: Optional[bool] = None
    open_project: Optional[bool] = None
    run_macro: Optional[str] = None
    extra_args: list[str] = Field(default_factory=list)
    send_keys: list[str] = Field(default_factory=list)
    extra_vars: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False


class WorkspaceSearchRequest(BaseModel):
    query: str
    relative_path: str = "."
    is_regex: bool = False
    max_results: int = 50


class WorkspaceMkdirRequest(BaseModel):
    relative_path: str
    exist_ok: bool = True


class WorkspaceMoveRequest(BaseModel):
    source_path: str
    destination_path: str
    overwrite: bool = False


class WorkspaceDeleteRequest(BaseModel):
    relative_path: str
    recursive: bool = False


class ShellCommandRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout_seconds: Optional[int] = None


class WebSearchRequest(BaseModel):
    query: str
    max_results: int = 5


class WebFetchRequest(BaseModel):
    url: str
    max_chars: Optional[int] = None


class NetworkCheckRequest(BaseModel):
    target: str
    port: Optional[int] = None
    url: Optional[str] = None
    timeout_seconds: int = 5


class AnalyzeSchematicPdfRequest(BaseModel):
    pdf_path: str = Field(..., description="Caminho relativo ao projects_root para o PDF do esquemático")
    max_pages: int = Field(default=12, ge=1, le=100)
    persist_output: bool = True
    output_relative_path: Optional[str] = None
    use_ocr_if_needed: bool = True
    ocr_languages: str = Field(default="eng", description="Idiomas OCR no formato aceito pelo Tesseract, ex.: eng ou eng+por")
    ocr_min_text_chars: int = Field(default=80, ge=0, le=2000)


class ExtractSchematicCandidatesRequest(BaseModel):
    pdf_path: str = Field(..., description="Caminho relativo ao projects_root para o PDF do manual ou esquemático")
    max_pages: int = Field(default=250, ge=1, le=2000)
    top_k: int = Field(default=12, ge=1, le=100)
    export_pages: bool = True
    export_dpi: int = Field(default=220, ge=72, le=600)
    output_dir: Optional[str] = None
    use_ocr_if_needed: bool = True
    ocr_languages: str = Field(default="eng", description="Idiomas OCR no formato aceito pelo Tesseract, ex.: eng ou eng+por")
    ocr_min_text_chars: int = Field(default=80, ge=0, le=2000)


class BuildFocusedSubsetRequest(BaseModel):
    source_pdf_path: str = Field(..., description="PDF original dentro do projects_root")
    profile_name: str = Field(default="acer_pass4_signal_dense", description="Perfil oficial de foco para service guides")
    candidate_manifest_path: Optional[str] = Field(default=None, description="Manifesto candidate_pages.json gerado por /pdf/extract-schematic-candidates")
    output_pdf_path: Optional[str] = Field(default=None, description="Caminho relativo de saida para PDF focado")
    output_selection_json_path: Optional[str] = Field(default=None, description="Caminho relativo de saida para JSON de selecao")
    min_score_override: Optional[int] = Field(default=None, ge=0, le=100)


class PrepareProteusAssistedProjectRequest(BaseModel):
    project_folder: str = Field(..., description="Pasta relativa dentro do projects_root para gerar o pacote assistido")
    analysis_relative_path: Optional[str] = Field(default=None, description="JSON gerado por /pdf/analyze-schematic")
    pdf_path: Optional[str] = Field(default=None, description="PDF original dentro do projects_root, usado se não houver JSON")
    project_display_name: Optional[str] = None
    max_pages: int = Field(default=12, ge=1, le=100)
    overwrite: bool = False
    copy_source_pdf: bool = True
    use_ocr_if_needed: bool = True
    ocr_languages: str = Field(default="eng", description="Idiomas OCR no formato aceito pelo Tesseract, ex.: eng ou eng+por")
    ocr_min_text_chars: int = Field(default=80, ge=0, le=2000)


class AssistantPipelineRequest(BaseModel):
    task_type: str = Field(default="diagnose", description="diagnose|workspace|research|proteus|generic")
    objective: str = Field(..., description="Objetivo principal da tarefa")
    execute: bool = Field(default=False, description="Quando true, executa checagens seguras para validar o plano")
    workspace_path: str = Field(default=".", description="Caminho relativo no workspace para contexto")
    web_query: Optional[str] = Field(default=None, description="Consulta web opcional para tarefas de research")
    network_target: str = Field(default="127.0.0.1", description="Alvo de rede para diagnose")
    network_port: Optional[int] = Field(default=None)
    network_url: Optional[str] = Field(default=None)
    max_results: int = Field(default=5, ge=1, le=20)
    response_text: Optional[str] = Field(default=None, description="Resposta gerada para validação opcional do contrato")


class AssistantQualityGateRequest(BaseModel):
    task_type: str = Field(default="generic")
    response_text: str = Field(..., description="Resposta final para validação")
    evidence_items: list[str] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    has_root_cause: bool = False
    has_validation: bool = False


class AssistantMemoryAddRequest(BaseModel):
    category: str = Field(default="general", description="Categoria da memoria: incident|decision|pattern|general")
    title: str = Field(..., description="Titulo curto da memoria")
    content: str = Field(..., description="Conteudo principal da memoria")
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="manual")


class AssistantMemorySearchRequest(BaseModel):
    query: str
    category: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)


class AssistantOrchestrateRequest(BaseModel):
    objective: str
    task_type: str = Field(default="generic")
    auto_execute: bool = False
    require_approval: bool = True
    workspace_path: str = "."
    web_query: Optional[str] = None
    max_results: int = Field(default=5, ge=1, le=20)


class AssistantCheckpointDecisionRequest(BaseModel):
    checkpoint_id: str
    decision: str = Field(description="approve|reject")
    note: str = ""


class AssistantCheckpointCleanupRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=365)


class AssistantCheckpointEscalationRequest(BaseModel):
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=50, ge=1, le=200)


class AssistantOperationsEscalationRequest(BaseModel):
    queues: list[str] = Field(default_factory=lambda: ["risk", "sla", "compliance"])
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=50, ge=1, le=200)


class AssistantCheckpointDedupRequest(BaseModel):
    apply_changes: bool = False
    limit: int = Field(default=5000, ge=100, le=50000)


class AssistantExecutiveReportRequest(BaseModel):
    report_format: str = Field(default="markdown", description="json|csv|markdown")
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    persist: bool = True
    output_name: Optional[str] = None
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    environment: str = Field(default="default", min_length=1, max_length=80)


class AssistantExecutiveSnapshotRequest(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["markdown", "csv", "json"])
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    snapshot_name: Optional[str] = None
    environment: str = Field(default="manual")
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)


class AssistantReportCleanupRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=3650)
    apply_changes: bool = False
    environment: Optional[str] = None
    tenant_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    project_scope: Optional[str] = Field(default=None, min_length=1, max_length=120)


class AssistantReportScheduleRequest(BaseModel):
    schedule_id: str = Field(..., min_length=3, max_length=80)
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    enabled: bool = True
    cadence: str = Field(default="daily", description="hourly|daily")
    interval_hours: int = Field(default=24, ge=1, le=168)
    hour_of_day: int = Field(default=8, ge=0, le=23)
    formats: list[str] = Field(default_factory=lambda: ["markdown", "json"])
    retention_days: int = Field(default=30, ge=1, le=3650)
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    snapshot_prefix: Optional[str] = None


class AssistantReportScheduleRunRequest(BaseModel):
    environment: Optional[str] = None
    tenant_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    project_scope: Optional[str] = Field(default=None, min_length=1, max_length=120)
    force: bool = False
    limit: int = Field(default=10, ge=1, le=100)


class AssistantScopedPolicyRequest(BaseModel):
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    enabled: bool = True
    min_recent_snapshots: int = Field(default=1, ge=0, le=500)
    max_snapshot_age_hours: int = Field(default=72, ge=1, le=8760)
    max_due_schedules: int = Field(default=0, ge=0, le=500)
    required_formats: list[str] = Field(default_factory=lambda: ["json"])
    note: Optional[str] = None


class AssistantAuditLogRequest(BaseModel):
    operation: str = Field(..., description="policy_upsert|policy_list|policy_evaluate|policy_delete|policy_enforce|policy_escalate")
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    details: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="success", description="success|failure|warning")
    error_message: Optional[str] = None


class AssistantPolicyEnforcementRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    run_due_schedules_on_fail: bool = True
    create_snapshot_on_fail: bool = True
    snapshot_formats: list[str] = Field(default_factory=lambda: ["markdown", "json"])
    dry_run: bool = False


class AssistantPolicyEscalationRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    run_due_schedules_on_fail: bool = True
    create_snapshot_on_fail: bool = True
    snapshot_formats: list[str] = Field(default_factory=lambda: ["markdown", "json"])
    force_incident: bool = False
    severity_on_fail: str = Field(default="sev2", description="sev1|sev2|sev3|sev4")
    owner: Optional[str] = None
    channel: str = Field(default="operations")
    dedup_enabled: bool = True
    dedup_window_hours: int = Field(default=24, ge=1, le=168)
    dry_run: bool = False


class AssistantIncidentResolveRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    force_close: bool = False
    close_note: Optional[str] = None
    dry_run: bool = False


class AssistantIncidentReopenRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    warning_after_hours: int = Field(default=4, ge=1, le=720)
    critical_after_hours: int = Field(default=24, ge=1, le=1440)
    limit: int = Field(default=200, ge=10, le=2000)
    environment: str = Field(default="default", min_length=1, max_length=80)
    tenant_id: str = Field(default="shared", min_length=1, max_length=80)
    project_scope: str = Field(default="general", min_length=1, max_length=120)
    force_reopen: bool = False
    reopen_note: Optional[str] = None
    severity_on_reopen: str = Field(default="sev2", description="sev1|sev2|sev3|sev4")
    owner: Optional[str] = None
    channel: str = Field(default="operations")
    dedup_enabled: bool = True
    dedup_window_hours: int = Field(default=24, ge=1, le=168)
    dry_run: bool = False


class AssistantIncidentDedupRequest(BaseModel):
    environment: Optional[str] = Field(default=None, min_length=1, max_length=80)
    tenant_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    project_scope: Optional[str] = Field(default=None, min_length=1, max_length=120)
    days: int = Field(default=7, ge=1, le=90)
    window_hours: int = Field(default=24, ge=1, le=168)
    apply_changes: bool = False
    limit: int = Field(default=5000, ge=100, le=50000)


class AssistantIncidentCorrelationRequest(BaseModel):
    environment: Optional[str] = Field(default=None, min_length=1, max_length=80)
    tenant_id: Optional[str] = Field(default=None, min_length=1, max_length=80)
    project_scope: Optional[str] = Field(default=None, min_length=1, max_length=120)
    status: str = Field(default="open")
    days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=5000, ge=100, le=50000)


def project_root() -> Path:
    return Path(CONFIG["projects_root"]).expanduser()


def ensure_root_exists() -> Path:
    root = project_root().resolve()
    if not root.exists():
        raise HTTPException(status_code=500, detail=f"projects_root não existe: {root}")
    return root


def allowed_project_extensions() -> set[str]:
    return {ext.lower() for ext in CONFIG.get("project_extensions", [])}


def allowed_edit_extensions() -> set[str]:
    return {ext.lower() for ext in CONFIG.get("editable_extensions", [])}


def safe_relative_path(relative_path: str) -> Path:
    root = ensure_root_exists()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Caminho fora do diretório permitido") from exc
    return candidate


def resolve_project_path(project: str) -> Path:
    root = ensure_root_exists()
    candidate = (root / project).resolve()
    if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in allowed_project_extensions():
        return candidate

    search = project.strip().lower()
    matches: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in allowed_project_extensions():
            rel = path.relative_to(root).as_posix().lower()
            if search in {path.name.lower(), path.stem.lower(), rel}:
                matches.append(path.resolve())

    if not matches:
        raise HTTPException(status_code=404, detail=f"Projeto não encontrado: {project}")
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Mais de um projeto encontrado. Use o caminho relativo completo.",
                "matches": [str(p.relative_to(root)) for p in matches[:20]],
            },
        )
    return matches[0]


def ps_quote(value: str) -> str:
    return value.replace("'", "''")


def send_keys_to_window(keys: str, window_title: Optional[str], delay_ms: int = 300) -> dict[str, Any]:
    title = window_title or CONFIG.get("proteus_window_title_hint", "Proteus")
    script = f"""
$ErrorActionPreference = 'Stop'
$wshell = New-Object -ComObject WScript.Shell
if (-not $wshell.AppActivate('{ps_quote(title)}')) {{
    throw 'Janela não encontrada: {ps_quote(title)}'
}}
Start-Sleep -Milliseconds {int(delay_ms)}
$wshell.SendKeys('{ps_quote(keys)}')
Write-Output 'OK'
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Falha ao enviar teclas para a janela do Proteus",
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            },
        )
    return {"ok": True, "window_title": title, "keys": keys, "stdout": result.stdout.strip()}


def get_proteus_processes() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe = (proc.info.get("exe") or "").lower()
            if "pds.exe" in name or "proteus" in name or "pds.exe" in exe or "proteus" in exe:
                processes.append(
                    {
                        "pid": proc.info.get("pid"),
                        "name": proc.info.get("name"),
                        "exe": proc.info.get("exe"),
                        "cmdline": proc.info.get("cmdline") or [],
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes


def get_project_dir(project_file: Path) -> Path:
    return project_file.parent


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        safe_context = SafeFormatDict({k: str(v) for k, v in context.items()})
        return value.format_map(safe_context)
    if isinstance(value, list):
        return [format_value(item, context) for item in value]
    if isinstance(value, dict):
        return {k: format_value(v, context) for k, v in value.items()}
    return value


def resolve_template_path(template: str, context: dict[str, Any], base_path: Optional[Path] = None) -> Path:
    rendered = format_value(template, context)
    candidate = Path(rendered).expanduser()
    if not candidate.is_absolute():
        candidate = ((base_path or Path(context["project_dir"])) / candidate).resolve()
    return candidate.resolve()


def find_latest_match(pattern: str, context: dict[str, Any], base_path: Optional[Path] = None) -> Optional[Path]:
    rendered = format_value(pattern, context)
    search_pattern = rendered
    if not Path(rendered).is_absolute():
        search_pattern = str(((base_path or Path(context["project_dir"])) / rendered).resolve())

    matches = [Path(item).resolve() for item in glob.glob(search_pattern, recursive=True)]
    files = [item for item in matches if item.exists() and item.is_file()]
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_mtime)


def build_workflow_context(project_file: Path, request: AutomationRunRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    project_dir = project_file.parent.resolve()

    raw_source = Path(request.source_dir).expanduser() if request.source_dir else project_dir
    source_entry = (project_dir / raw_source).resolve() if not raw_source.is_absolute() else raw_source.resolve()
    source_dir = source_entry.parent if source_entry.suffix else source_entry
    source_name = source_entry.stem if source_entry.suffix else source_entry.name

    raw_build = Path(request.build_dir).expanduser() if request.build_dir else (project_dir / "build")
    build_dir = (project_dir / raw_build).resolve() if not raw_build.is_absolute() else raw_build.resolve()

    context = {
        "workspace_root": str(root),
        "project_file": str(project_file),
        "project_name": project_file.name,
        "project_stem": project_file.stem,
        "project_dir": str(project_dir),
        "source_dir": str(source_dir),
        "source_name": source_name,
        "build_dir": str(build_dir),
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
    }
    context.update({k: str(v) for k, v in request.extra_vars.items()})
    return context


def get_automation_profile(profile_name: str) -> dict[str, Any]:
    profiles = CONFIG.get("automation_profiles", {})
    if profile_name not in profiles:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Perfil de automação não encontrado: {profile_name}",
                "available_profiles": sorted(profiles.keys()),
            },
        )
    return profiles[profile_name]


def run_command_capture(command: str, args: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [command, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": -1,
            "cwd": str(cwd),
            "command": [command, *args],
            "stdout": "",
            "stderr": f"Comando não encontrado: {command}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": -2,
            "cwd": str(cwd),
            "command": [command, *args],
            "stdout": exc.stdout or "",
            "stderr": f"Tempo excedido após {timeout_seconds}s",
        }

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "cwd": str(cwd),
        "command": [command, *args],
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def truncate_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... saída truncada ...]"


def read_text_content(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


SCHEMATIC_REFERENCE_RE = re.compile(
    r"\b(?:R|C|L|D|Q|U|IC|J|JP|K|T|Y|X|SW|F|FB|LED|RV|VR|P|CN|CONN)\d{1,4}[A-Z]?\b",
    re.IGNORECASE,
)
SCHEMATIC_SIGNAL_RE = re.compile(
    r"\b(?:GND|AGND|DGND|PGND|VCC|VDD|VSS|VIN|VBAT|3V3|5V|12V|24V|SCL|SDA|TX|RX|MISO|MOSI|SCK|CLK|RST|RESET|EN|CS|INT|PWM\d*|ADC\d*|GPIO\d+)\b",
    re.IGNORECASE,
)
SCHEMATIC_VALUE_RE = re.compile(
    r"\b(?:\d+(?:[\.,]\d+)?\s?(?:R|K|M|OHM|F|UF|NF|PF|H|MH|UH|V|A|W|HZ|KHZ|MHZ)|ATMEGA\w+|ESP32\S*|STM32\S*|PIC\d+\w*|LM\d+|NE555|AMS1117\S*|78\d{2}|74HC\d+|BC\d+|2N\d+)\b",
    re.IGNORECASE,
)


def import_pdf_reader() -> Any:
    try:
        from pypdf import PdfReader  # type: ignore import-not-found
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Dependência 'pypdf' não encontrada. Execute start_proteus_bridge.ps1 ou instale as dependências de "
                f"{BASE_DIR / 'openwebui_bridge_requirements.txt'}"
            ),
        ) from exc
    return PdfReader


def import_fitz_module() -> Any:
    try:
        import fitz  # type: ignore import-not-found
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Dependência 'PyMuPDF' não encontrada. Execute start_proteus_bridge.ps1 ou instale as dependências de "
                f"{BASE_DIR / 'openwebui_bridge_requirements.txt'}"
            ),
        ) from exc
    return fitz


def import_pytesseract_module(optional: bool = False) -> Any:
    try:
        import pytesseract  # type: ignore import-not-found
    except ImportError:
        if optional:
            return None
        raise HTTPException(
            status_code=500,
            detail=(
                "Dependência 'pytesseract' não encontrada. Execute start_proteus_bridge.ps1 ou instale as dependências de "
                f"{BASE_DIR / 'openwebui_bridge_requirements.txt'}"
            ),
        )
    return pytesseract


def detect_ocr_backend() -> dict[str, Any]:
    pytesseract = import_pytesseract_module(optional=True)
    if pytesseract is None:
        return {
            "name": "none",
            "module": None,
            "warnings": ["Dependência opcional 'pytesseract' não está instalada; OCR ficará desabilitado."],
        }

    candidate_paths = [
        shutil.which("tesseract"),
        str(Path("C:/Program Files/Tesseract-OCR/tesseract.exe")),
        str(Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe")),
    ]
    tesseract_cmd = next((path for path in candidate_paths if path and Path(path).exists()), None)
    if not tesseract_cmd:
        return {
            "name": "none",
            "module": None,
            "warnings": [
                "Binário do Tesseract não foi encontrado. Instale o Tesseract OCR para habilitar OCR forte em PDFs escaneados."
            ],
        }

    user_tessdata_dir = Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tessdata"
    user_tessdata_prefix = user_tessdata_dir.parent if user_tessdata_dir.exists() else None
    if user_tessdata_prefix:
        # Allow non-admin language packs in the user profile.
        os.environ["TESSDATA_PREFIX"] = str(user_tessdata_prefix)

    try:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        version = str(pytesseract.get_tesseract_version())
    except Exception as exc:
        return {
            "name": "none",
            "module": None,
            "warnings": [f"Falha ao inicializar OCR Tesseract em '{tesseract_cmd}': {exc}"],
        }

    available_languages = []
    if user_tessdata_dir.exists():
        available_languages.extend(path.stem for path in user_tessdata_dir.glob("*.traineddata"))

    return {
        "name": "tesseract",
        "module": pytesseract,
        "command": tesseract_cmd,
        "version": version,
        "tessdata_dir": str(user_tessdata_dir) if user_tessdata_dir.exists() else None,
        "available_languages": sorted(set(available_languages)),
        "warnings": [],
    }


def run_ocr_on_pdf_page(
    document: Any,
    page_index: int,
    fitz_module: Any,
    pytesseract_module: Any,
    languages: str,
    dpi: int,
    tessdata_dir: Optional[str],
) -> str:
    page = document.load_page(page_index)
    zoom = dpi / 72.0
    matrix = fitz_module.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        pixmap.save(str(temp_path))
        attempts: list[tuple[str, Optional[str]]] = [(languages, tessdata_dir), (languages, None)]
        if "+" in languages:
            attempts.append(("eng", tessdata_dir))
            attempts.append(("eng", None))

        for lang_attempt, tessdata_attempt in attempts:
            try:
                config = None
                if tessdata_attempt:
                    config = f'--tessdata-dir "{tessdata_attempt}"'
                result = pytesseract_module.image_to_string(str(temp_path), lang=lang_attempt, config=config) or ""
                if result.strip():
                    return result.strip()
            except Exception:
                continue
        return ""
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def extract_page_text(
    reader_page: Any,
    document: Any,
    page_index: int,
    use_ocr_if_needed: bool,
    ocr_languages: str,
    ocr_min_text_chars: int,
    ocr_context: dict[str, Any],
    fitz_module: Any,
    ocr_dpi: int,
) -> dict[str, Any]:
    raw_text = (reader_page.extract_text() or "").strip()
    result = {
        "text": raw_text,
        "text_source": "pdf_text",
        "ocr_attempted": False,
        "ocr_applied": False,
    }

    if not use_ocr_if_needed or ocr_context.get("name") == "none":
        return result

    if len(raw_text) >= ocr_min_text_chars:
        return result

    result["ocr_attempted"] = True
    ocr_text = run_ocr_on_pdf_page(
        document=document,
        page_index=page_index,
        fitz_module=fitz_module,
        pytesseract_module=ocr_context["module"],
        languages=ocr_languages,
        dpi=ocr_dpi,
        tessdata_dir=ocr_context.get("tessdata_dir"),
    )
    if len(ocr_text) > len(raw_text):
        result["text"] = ocr_text
        result["text_source"] = f"ocr_{ocr_context['name']}"
        result["ocr_applied"] = True

    return result


def infer_component_family(reference: str) -> str:
    prefix = re.match(r"[A-Z]+", reference.upper())
    token = prefix.group(0) if prefix else ""
    mapping = {
        "R": "resistor",
        "RV": "potentiometer",
        "VR": "voltage_regulator_or_trim",
        "C": "capacitor",
        "L": "inductor",
        "D": "diode_or_led",
        "LED": "led",
        "Q": "transistor",
        "U": "integrated_circuit",
        "IC": "integrated_circuit",
        "J": "connector",
        "JP": "jumper",
        "P": "connector",
        "CN": "connector",
        "CONN": "connector",
        "K": "relay",
        "SW": "switch",
        "Y": "crystal_or_oscillator",
        "X": "crystal_or_connector",
        "F": "fuse",
        "FB": "ferrite_bead",
        "T": "transformer_or_testpoint",
    }
    return mapping.get(token, "unknown")


def build_schematic_pdf_analysis(
    pdf_path: Path,
    max_pages: int,
    use_ocr_if_needed: bool,
    ocr_languages: str,
    ocr_min_text_chars: int,
) -> dict[str, Any]:
    PdfReader = import_pdf_reader()
    fitz = import_fitz_module()
    reader = PdfReader(str(pdf_path))
    document = fitz.open(str(pdf_path))
    pages = list(reader.pages[:max_pages])
    text_by_page: list[dict[str, Any]] = []
    component_map: dict[str, dict[str, Any]] = {}
    signal_map: dict[str, dict[str, Any]] = {}
    extracted_chars = 0
    ocr_context = detect_ocr_backend() if use_ocr_if_needed else {"name": "none", "module": None, "warnings": []}
    ocr_attempted_pages = 0
    ocr_used_pages = 0

    for index, page in enumerate(pages, start=1):
        text_result = extract_page_text(
            reader_page=page,
            document=document,
            page_index=index - 1,
            use_ocr_if_needed=use_ocr_if_needed,
            ocr_languages=ocr_languages,
            ocr_min_text_chars=ocr_min_text_chars,
            ocr_context=ocr_context,
            fitz_module=fitz,
            ocr_dpi=220,
        )
        text = text_result["text"]
        if text_result["ocr_attempted"]:
            ocr_attempted_pages += 1
        if text_result["ocr_applied"]:
            ocr_used_pages += 1
        text_by_page.append(
            {
                "page_number": index,
                "characters": len(text),
                "preview": text[:500],
                "text_source": text_result["text_source"],
            }
        )
        if not text:
            continue

        extracted_chars += len(text)
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue

            references = sorted({match.group(0).upper() for match in SCHEMATIC_REFERENCE_RE.finditer(line)})
            values = sorted({match.group(0).upper() for match in SCHEMATIC_VALUE_RE.finditer(line)})
            signals = sorted({match.group(0).upper() for match in SCHEMATIC_SIGNAL_RE.finditer(line)})

            for reference in references:
                entry = component_map.setdefault(
                    reference,
                    {
                        "reference": reference,
                        "family": infer_component_family(reference),
                        "candidate_values": [],
                        "evidence_lines": [],
                        "pages": [],
                    },
                )
                for value in values:
                    if value not in entry["candidate_values"]:
                        entry["candidate_values"].append(value)
                if line not in entry["evidence_lines"] and len(entry["evidence_lines"]) < 3:
                    entry["evidence_lines"].append(line[:220])
                if index not in entry["pages"]:
                    entry["pages"].append(index)

            for signal in signals:
                net_entry = signal_map.setdefault(signal, {"name": signal, "count": 0, "evidence_lines": []})
                net_entry["count"] += 1
                if line not in net_entry["evidence_lines"] and len(net_entry["evidence_lines"]) < 3:
                    net_entry["evidence_lines"].append(line[:220])

    components = sorted(component_map.values(), key=lambda item: item["reference"])
    signals = sorted(signal_map.values(), key=lambda item: (-item["count"], item["name"]))
    warnings = list(ocr_context.get("warnings", []))

    if extracted_chars == 0:
        warnings.append("O PDF não retornou texto extraível. Se for um scan/imagem, este pipeline não reconstrói o esquemático automaticamente.")
    if not components:
        warnings.append("Nenhum designador de referência típico foi encontrado no texto extraído.")
    if len(components) < 5:
        warnings.append("Poucos componentes foram detectados. Revise o PDF ou use OCR antes de gerar artefatos para o Proteus.")

    if extracted_chars == 0:
        readiness = "low"
    elif len(components) >= 15 and len(signals) >= 5:
        readiness = "medium"
    else:
        readiness = "low"

    document.close()

    return {
        "analysis_type": "schematic_pdf_preflight",
        "pdf_name": pdf_path.name,
        "page_count_total": len(reader.pages),
        "pages_processed": len(pages),
        "extracted_text_characters": extracted_chars,
        "ocr_backend": ocr_context.get("name", "none"),
        "ocr_available_languages": ocr_context.get("available_languages", []),
        "ocr_attempted_pages": ocr_attempted_pages,
        "ocr_used_pages": ocr_used_pages,
        "readiness": readiness,
        "warnings": warnings,
        "components_count": len(components),
        "signal_candidates_count": len(signals),
        "components": components,
        "signal_candidates": signals[:100],
        "page_previews": text_by_page,
        "suggested_next_steps": [
            "Revise o JSON gerado para confirmar referência, valor e sinais detectados.",
            "Monte ou corrija um BOM/netlist intermediário antes de tentar construir o projeto no ISIS Proteus.",
            "Use esse resultado como entrada assistida para criar o projeto e depois importar firmware ou simular via bridge.",
        ],
    }


def score_schematic_candidate_page(page_text: str) -> dict[str, Any]:
    text = page_text.upper()
    score = 0
    reasons = []

    component_refs = sorted({match.group(0).upper() for match in SCHEMATIC_REFERENCE_RE.finditer(text)})
    signals = sorted({match.group(0).upper() for match in SCHEMATIC_SIGNAL_RE.finditer(text)})

    weighted_keywords = {
        "SCHEMATIC": 60,
        "CIRCUIT DIAGRAM": 50,
        "WIRING DIAGRAM": 45,
        "POWER DIAGRAM": 35,
        "PIN DIAGRAM": 20,
        "BLOCK DIAGRAM": 15,
        "BOARD LAYOUT": 10,
        "DC-DC": 10,
        "CONVERTER": 8,
        "MCU": 8,
        "AUDIO": 6,
        "LCD": 6,
        "USB": 6,
        "MODEM": 4,
    }
    for keyword, weight in weighted_keywords.items():
        if keyword in text:
            score += weight
            reasons.append(f"keyword:{keyword}")

    if component_refs:
        ref_score = min(30, len(component_refs) * 3)
        score += ref_score
        reasons.append(f"component_refs:{len(component_refs)}")
    if signals:
        sig_score = min(20, len(signals) * 4)
        score += sig_score
        reasons.append(f"signals:{len(signals)}")
    if len(text.strip()) < 80:
        score -= 15
        reasons.append("low_text_density")

    final_score = max(score, 0)
    return {
        "score": final_score,
        "reasons": reasons,
        "component_references": component_refs[:50],
        "signal_candidates": signals[:50],
    }


def extract_schematic_candidate_pages(request: ExtractSchematicCandidatesRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    pdf_path = safe_relative_path(request.pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Informe um arquivo .pdf dentro do projects_root")
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail=f"PDF não encontrado: {pdf_path}")

    PdfReader = import_pdf_reader()
    fitz = import_fitz_module()
    reader = PdfReader(str(pdf_path))
    document = fitz.open(str(pdf_path))
    page_count = len(reader.pages)
    pages_to_process = min(page_count, request.max_pages)
    ocr_context = detect_ocr_backend() if request.use_ocr_if_needed else {"name": "none", "module": None, "warnings": []}
    ocr_attempted_pages = 0
    ocr_used_pages = 0

    page_scores = []
    for idx in range(pages_to_process):
        text_result = extract_page_text(
            reader_page=reader.pages[idx],
            document=document,
            page_index=idx,
            use_ocr_if_needed=request.use_ocr_if_needed,
            ocr_languages=request.ocr_languages,
            ocr_min_text_chars=request.ocr_min_text_chars,
            ocr_context=ocr_context,
            fitz_module=fitz,
            ocr_dpi=request.export_dpi,
        )
        raw_text = text_result["text"]
        if text_result["ocr_attempted"]:
            ocr_attempted_pages += 1
        if text_result["ocr_applied"]:
            ocr_used_pages += 1
        score_info = score_schematic_candidate_page(raw_text)
        page_scores.append(
            {
                "page_number": idx + 1,
                "score": score_info["score"],
                "reasons": score_info["reasons"],
                "component_references": score_info["component_references"],
                "signal_candidates": score_info["signal_candidates"],
                "preview": raw_text[:500],
                "text_characters": len(raw_text),
                "text_source": text_result["text_source"],
            }
        )

    ranked_pages = sorted(page_scores, key=lambda item: (-item["score"], -item["text_characters"], item["page_number"]))
    selected_pages = [item for item in ranked_pages[: request.top_k] if item["score"] > 0]

    output_dir_relative = request.output_dir or str(pdf_path.with_suffix("")) + "_schematic_candidates"
    exported_files = []
    if request.export_pages and selected_pages:
        output_dir = safe_relative_path(output_dir_relative)
        output_dir.mkdir(parents=True, exist_ok=True)
        zoom = request.export_dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for item in selected_pages:
            page_index = item["page_number"] - 1
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_name = f"page_{item['page_number']:03d}_score_{item['score']:03d}.png"
            image_path = output_dir / image_name
            pixmap.save(str(image_path))
            item["exported_image_relative_path"] = str(image_path.relative_to(root))
            exported_files.append(str(image_path.relative_to(root)))

    document.close()

    output_manifest_relative = output_dir_relative + "/candidate_pages.json"
    output_manifest_path = safe_relative_path(output_manifest_relative)
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    ocr_backend = ocr_context.get("name", "none")
    warnings = list(ocr_context.get("warnings", []))
    if not selected_pages:
        warnings.append("Nenhuma página com score positivo foi encontrada. Esse manual pode exigir OCR externo ou seleção manual de páginas.")
    elif ocr_backend == "none" and request.use_ocr_if_needed:
        warnings.append("OCR forte não foi executado automaticamente porque nenhum backend OCR local foi detectado.")

    payload = {
        "ok": True,
        "pdf_relative_path": str(pdf_path.relative_to(root)),
        "page_count_total": page_count,
        "pages_processed": pages_to_process,
        "ocr_backend": ocr_backend,
        "ocr_available_languages": ocr_context.get("available_languages", []),
        "ocr_attempted_pages": ocr_attempted_pages,
        "ocr_used_pages": ocr_used_pages,
        "selected_pages_count": len(selected_pages),
        "selected_pages": selected_pages,
        "ranked_pages": ranked_pages[: min(50, len(ranked_pages))],
        "exported_files": exported_files,
        "warnings": warnings,
    }
    output_manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["manifest_relative_path"] = str(output_manifest_path.relative_to(root))
    return payload


def analyze_schematic_pdf(request: AnalyzeSchematicPdfRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    pdf_path = safe_relative_path(request.pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Informe um arquivo .pdf dentro do projects_root")
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail=f"PDF não encontrado: {pdf_path}")

    analysis = build_schematic_pdf_analysis(
        pdf_path=pdf_path,
        max_pages=request.max_pages,
        use_ocr_if_needed=request.use_ocr_if_needed,
        ocr_languages=request.ocr_languages,
        ocr_min_text_chars=request.ocr_min_text_chars,
    )
    response = {
        "ok": True,
        "pdf_relative_path": str(pdf_path.relative_to(root)),
        "analysis": analysis,
    }

    if request.persist_output:
        output_relative = request.output_relative_path or str(
            pdf_path.relative_to(root).with_suffix(".schematic.analysis.json")
        )
        output_path = safe_relative_path(output_relative)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_payload = dict(response)
        output_payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        output_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        response["output_relative_path"] = str(output_path.relative_to(root))

    return response


def load_schematic_analysis_payload(
    analysis_relative_path: Optional[str],
    pdf_path: Optional[str],
    max_pages: int,
    use_ocr_if_needed: bool,
    ocr_languages: str,
    ocr_min_text_chars: int,
) -> dict[str, Any]:
    root = ensure_root_exists()
    if analysis_relative_path:
        analysis_path = safe_relative_path(analysis_relative_path)
        if not analysis_path.exists() or not analysis_path.is_file():
            raise HTTPException(status_code=404, detail=f"Arquivo de análise não encontrado: {analysis_path}")
        try:
            payload = json.loads(analysis_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"JSON de análise inválido: {analysis_path}") from exc
        analysis = payload.get("analysis") if isinstance(payload, dict) else None
        if not isinstance(analysis, dict):
            raise HTTPException(status_code=400, detail="O JSON informado não contém a chave 'analysis'")
        return {
            "analysis": analysis,
            "source_analysis_relative_path": str(analysis_path.relative_to(root)),
            "source_pdf_relative_path": payload.get("pdf_relative_path"),
        }

    if pdf_path:
        payload = analyze_schematic_pdf(
            AnalyzeSchematicPdfRequest(
                pdf_path=pdf_path,
                max_pages=max_pages,
                persist_output=False,
                use_ocr_if_needed=use_ocr_if_needed,
                ocr_languages=ocr_languages,
                ocr_min_text_chars=ocr_min_text_chars,
            )
        )
        return {
            "analysis": payload["analysis"],
            "source_analysis_relative_path": None,
            "source_pdf_relative_path": payload.get("pdf_relative_path"),
        }

    raise HTTPException(status_code=400, detail="Informe 'analysis_relative_path' ou 'pdf_path'")


def component_family_rank(family: str) -> int:
    order = {
        "connector": 0,
        "voltage_regulator_or_trim": 1,
        "relay": 2,
        "switch": 3,
        "integrated_circuit": 4,
        "transistor": 5,
        "diode_or_led": 6,
        "led": 7,
        "crystal_or_oscillator": 8,
        "capacitor": 9,
        "resistor": 10,
        "potentiometer": 11,
        "inductor": 12,
        "ferrite_bead": 13,
        "fuse": 14,
        "jumper": 15,
        "transformer_or_testpoint": 16,
        "crystal_or_connector": 17,
        "unknown": 99,
    }
    return order.get(family, 99)


def build_component_rows(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    ordered = sorted(
        components,
        key=lambda item: (component_family_rank(str(item.get("family", "unknown"))), str(item.get("reference", ""))),
    )
    for item in ordered:
        rows.append(
            {
                "reference": str(item.get("reference", "")),
                "family": str(item.get("family", "unknown")),
                "candidate_value": ", ".join(item.get("candidate_values", [])[:5]),
                "pages": ", ".join(str(page) for page in item.get("pages", [])),
                "evidence": " | ".join(item.get("evidence_lines", [])[:2]),
            }
        )
    return rows


def build_signal_rows(signals: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for item in signals:
        rows.append(
            {
                "signal": str(item.get("name", "")),
                "count": str(item.get("count", 0)),
                "evidence": " | ".join(item.get("evidence_lines", [])[:2]),
            }
        )
    return rows


def write_csv_rows(path: Path, rows: list[dict[str, str]]) -> None:
    headers = list(rows[0].keys()) if rows else ["item"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def confidence_label_from_score(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def score_component_confidence(component: dict[str, Any], inferred_signals: list[str]) -> dict[str, Any]:
    score = 0
    reasons = []
    evidence_lines = component.get("evidence_lines", [])
    candidate_values = component.get("candidate_values", [])
    pages = component.get("pages", [])

    if evidence_lines:
        score += min(20, 8 * len(evidence_lines))
        reasons.append(f"{len(evidence_lines)} linha(s) de evidencia textual")
    if candidate_values:
        score += min(20, 10 * len(candidate_values))
        reasons.append(f"{len(candidate_values)} valor(es) candidato(s)")
    if len(pages) >= 2:
        score += 10
        reasons.append("componente apareceu em multiplas paginas")
    elif len(pages) == 1:
        score += 5
        reasons.append("componente apareceu em uma pagina identificada")
    if inferred_signals:
        score += min(30, 15 * len(inferred_signals))
        reasons.append(f"{len(inferred_signals)} rede(s) inferida(s)")

    family = str(component.get("family", "unknown"))
    if family != "unknown":
        score += 10
        reasons.append(f"familia inferida: {family}")

    final_score = min(score, 100)
    return {
        "score": final_score,
        "label": confidence_label_from_score(final_score),
        "reasons": reasons,
    }


def score_net_confidence(net_name: str, members: list[str], evidence: list[str]) -> dict[str, Any]:
    score = 0
    reasons = []
    canonical_power_nets = {"GND", "AGND", "DGND", "PGND", "VCC", "VDD", "VSS", "VIN", "VBAT", "3V3", "5V", "12V", "24V"}

    if net_name.upper() in canonical_power_nets:
        score += 20
        reasons.append("rede com nome canonico de alimentacao/terra")
    if members:
        score += min(40, 10 * len(members))
        reasons.append(f"{len(members)} componente(s) associado(s)")
    if evidence:
        score += min(30, 10 * len(evidence))
        reasons.append(f"{len(evidence)} evidencia(s) textual(is)")

    final_score = min(score, 100)
    return {
        "score": final_score,
        "label": confidence_label_from_score(final_score),
        "reasons": reasons,
    }


def build_draft_netlist(analysis: dict[str, Any]) -> dict[str, Any]:
    components = analysis.get("components", [])
    signals = analysis.get("signal_candidates", [])
    signal_names = [str(item.get("name", "")).upper() for item in signals if item.get("name")]
    nets: dict[str, dict[str, Any]] = {
        name: {"name": name, "members": [], "evidence": [], "confidence": "low", "confidence_score": 0, "confidence_reasons": []}
        for name in signal_names
    }
    component_nodes = []
    review_rows = []
    unresolved_components = []

    for component in components:
        reference = str(component.get("reference", ""))
        evidence_lines = [str(line) for line in component.get("evidence_lines", [])]
        inferred_signals = []
        for signal_name in signal_names:
            if any(signal_name in line.upper() for line in evidence_lines):
                inferred_signals.append(signal_name)

        confidence_info = score_component_confidence(component, inferred_signals)
        confidence = confidence_info["label"]
        if not inferred_signals:
            unresolved_components.append(reference)

        for signal_name in inferred_signals:
            net = nets.setdefault(
                signal_name,
                {"name": signal_name, "members": [], "evidence": [], "confidence": "low", "confidence_score": 0, "confidence_reasons": []},
            )
            net["members"].append(reference)
            for line in evidence_lines[:2]:
                if line not in net["evidence"] and len(net["evidence"]) < 4:
                    net["evidence"].append(line)

        component_nodes.append(
            {
                "reference": reference,
                "family": component.get("family", "unknown"),
                "candidate_value": ", ".join(component.get("candidate_values", [])[:5]),
                "inferred_nets": inferred_signals,
                "confidence": confidence,
                "confidence_score": confidence_info["score"],
                "confidence_reasons": confidence_info["reasons"],
                "pages": component.get("pages", []),
                "evidence_lines": evidence_lines[:3],
            }
        )
        review_rows.append(
            {
                "reference": reference,
                "family": str(component.get("family", "unknown")),
                "candidate_value": ", ".join(component.get("candidate_values", [])[:5]),
                "inferred_nets": ", ".join(inferred_signals),
                "confidence": confidence,
                "confidence_score": str(confidence_info["score"]),
                "confidence_reasons": " | ".join(confidence_info["reasons"]),
                "review_status": "pending",
            }
        )

    net_rows = []
    for net_name, net in sorted(nets.items(), key=lambda item: item[0]):
        unique_members = sorted(set(net["members"]))
        net_confidence = score_net_confidence(net_name, unique_members, net.get("evidence", []))
        net["confidence"] = net_confidence["label"]
        net["confidence_score"] = net_confidence["score"]
        net["confidence_reasons"] = net_confidence["reasons"]
        net_rows.append(
            {
                "net": net_name,
                "member_count": str(len(unique_members)),
                "members": ", ".join(unique_members),
                "confidence": str(net.get("confidence", "low")),
                "confidence_score": str(net.get("confidence_score", 0)),
                "confidence_reasons": " | ".join(net.get("confidence_reasons", [])),
                "evidence": " | ".join(net.get("evidence", [])[:3]),
            }
        )

    return {
        "analysis_type": "draft_netlist_from_pdf_analysis",
        "confidence_model": "text_cooccurrence",
        "limitations": [
            "Esta netlist e apenas um rascunho derivado de coocorrencia textual no PDF.",
            "Nao ha inferencia confiavel de pinos, encapsulamento ou topologia completa.",
            "Toda conexao deve ser revisada manualmente antes de virar esquematico definitivo no Proteus.",
        ],
        "summary": {
            "components_total": len(components),
            "nets_total": len(net_rows),
            "components_with_inferred_nets": sum(1 for item in component_nodes if item["inferred_nets"]),
            "components_without_inferred_nets": len(unresolved_components),
        },
        "components": component_nodes,
        "nets": [
            {
                "name": row["net"],
                "member_count": int(row["member_count"]),
                "members": row["members"].split(", ") if row["members"] else [],
                "confidence": row["confidence"],
                "confidence_score": int(row["confidence_score"]),
                "confidence_reasons": row["confidence_reasons"].split(" | ") if row["confidence_reasons"] else [],
                "evidence": row["evidence"].split(" | ") if row["evidence"] else [],
            }
            for row in net_rows
        ],
        "unresolved_components": unresolved_components,
        "review_rows": review_rows,
        "net_rows": net_rows,
    }


def infer_functional_block(component_node: dict[str, Any]) -> dict[str, str]:
    family = str(component_node.get("family", "unknown"))
    reference = str(component_node.get("reference", "")).upper()
    value = str(component_node.get("candidate_value", "")).upper()
    nets = [str(item).upper() for item in component_node.get("inferred_nets", [])]
    nets_blob = " ".join(nets)
    value_blob = f"{reference} {value}"

    power_tokens = {"GND", "AGND", "DGND", "PGND", "VCC", "VDD", "VSS", "VIN", "VBAT", "3V3", "5V", "12V", "24V"}
    comms_tokens = {"TX", "RX", "SDA", "SCL", "MISO", "MOSI", "SCK", "CLK", "CS", "INT", "CAN", "RS485", "USB"}
    mcu_tokens = ("ATMEGA", "STM32", "ESP32", "PIC", "RP2040", "ARDUINO", "MCU")
    display_tokens = ("LCD", "OLED", "TFT", "ILI", "ST77", "SSD1306", "HD44780", "DISPLAY", "SEG", "COM")
    sensor_tokens = ("SENSOR", "TEMP", "HUM", "PRESS", "GYRO", "ACCEL", "MPU", "BME", "BMP", "DHT", "NTC", "LDR", "HALL", "ACS", "INA", "LM35")
    usb_tokens = {"USB", "D+", "D-", "VBUS"}
    can_tokens = {"CAN", "CANH", "CANL", "TXCAN", "RXCAN"}
    uart_tokens = {"TX", "RX", "UART", "USART"}
    rs485_tokens = {"RS485", "A", "B", "DE", "RE", "DI", "RO"}
    i2c_tokens = {"I2C", "SCL", "SDA"}
    spi_tokens = {"SPI", "MISO", "MOSI", "SCK", "CS"}
    analog_sensor_tokens = ("NTC", "LDR", "LM35", "ACS", "INA", "PRESS", "THERM", "CURRENT", "SHUNT")
    digital_sensor_tokens = ("DHT", "BME", "BMP", "MPU", "GYRO", "ACCEL", "HALL", "SENSOR", "I2C", "SPI")
    regulator_tokens = ("AMS1117", "LM7805", "LM1117", "BUCK", "BOOST", "LDO", "REG")
    can_transceiver_tokens = ("MCP2551", "SN65HVD", "TJA1050", "MCP256", "TCAN")
    rs485_transceiver_tokens = ("MAX485", "SP3485", "SN75176", "ADM485", "RS485")

    if family in {"connector", "jumper"}:
        if any(token in nets for token in usb_tokens) or any(token in value_blob for token in usb_tokens):
            return {"block": "usb_interface", "reason": "conector associado a sinais USB"}
        if any(token in nets for token in can_tokens):
            return {"block": "can_interface", "reason": "conector associado a sinais CAN"}
        if any(token in nets for token in rs485_tokens) or any(token in value_blob for token in rs485_transceiver_tokens):
            return {"block": "rs485_interface", "reason": "conector associado a sinais RS485"}
        if any(token in nets for token in uart_tokens):
            return {"block": "uart_interface", "reason": "conector associado a sinais seriais TX/RX"}
        return {"block": "connectors_io", "reason": "familia de conector/jumper"}

    if family in {"voltage_regulator_or_trim", "fuse", "inductor", "ferrite_bead"} or any(token in power_tokens for token in nets):
        if any(token in value_blob for token in regulator_tokens) or family == "voltage_regulator_or_trim":
            return {"block": "power_regulation", "reason": "regulador ou circuito de condicionamento de alimentacao"}
        return {"block": "power_supply", "reason": "familia ou sinais associados a alimentacao/terra"}

    if any(token in value_blob for token in display_tokens) or any(token in nets_blob for token in ("LCD", "OLED", "TFT", "SEG", "COM", "BL", "BACKLIGHT")):
        if any(token in nets for token in i2c_tokens):
            return {"block": "display_i2c", "reason": "display com sinais I2C detectados"}
        if any(token in nets for token in spi_tokens):
            return {"block": "display_spi", "reason": "display com sinais SPI detectados"}
        return {"block": "display_ui", "reason": "indicadores de display/interface visual detectados"}

    if any(token in value_blob for token in sensor_tokens) or any(token in nets_blob for token in ("SENSOR", "ADC", "THERM", "TEMP", "HALL", "PRESS", "HUM")):
        if any(token in value_blob for token in analog_sensor_tokens) or any(token in nets_blob for token in ("ADC", "AN", "AOUT", "AIN", "CURRENT", "THERM")):
            return {"block": "sensor_analog", "reason": "sensor analogico ou condicionamento analogico detectado"}
        if any(token in value_blob for token in digital_sensor_tokens) or any(token in nets for token in i2c_tokens.union(spi_tokens)):
            return {"block": "sensor_digital", "reason": "sensor digital com barramento ou interface detectada"}
        return {"block": "sensor_frontend", "reason": "indicadores de sensor ou condicionamento analogico detectados"}

    if family == "integrated_circuit" and (any(token in value_blob for token in mcu_tokens) or any(token.startswith("GPIO") for token in nets) or any(token in nets for token in comms_tokens)):
        return {"block": "mcu_control", "reason": "circuito integrado com caracteristicas de microcontrolador/controle"}

    if family in {"crystal_or_oscillator", "crystal_or_connector"} or "NE555" in value_blob:
        return {"block": "clock_timing", "reason": "componente de clock/temporizacao"}

    if any(token in value_blob for token in can_transceiver_tokens) or any(token in nets for token in can_tokens):
        return {"block": "can_transceiver", "reason": "transceptor ou sinais dedicados a barramento CAN detectados"}

    if any(token in value_blob for token in rs485_transceiver_tokens) or any(token in nets for token in rs485_tokens):
        return {"block": "rs485_interface", "reason": "transceptor ou sinais dedicados a RS485 detectados"}

    if any(token in nets for token in can_tokens) or any(token in nets_blob for token in ("CAN", "CANH", "CANL", "MCP25")):
        return {"block": "can_interface", "reason": "sinais ou referencias de barramento CAN detectados"}

    if any(token in nets for token in usb_tokens) or any(token in nets_blob for token in ("USB", "VBUS", "D+", "D-")):
        return {"block": "usb_interface", "reason": "sinais ou referencias de interface USB detectados"}

    if any(token in nets for token in uart_tokens) or any(token in nets_blob for token in ("UART", "USART", "TX", "RX", "RS232", "RS485")):
        return {"block": "uart_interface", "reason": "sinais ou referencias de interface serial detectados"}

    if any(token in nets for token in comms_tokens) or any(token in nets_blob for token in ("I2C", "SPI", "CS", "SCL", "SDA", "MISO", "MOSI", "SCK")):
        return {"block": "interface_comms", "reason": "sinais de interface/comunicacao detectados"}

    if family in {"relay", "switch", "led", "diode_or_led", "transistor"}:
        return {"block": "drivers_outputs", "reason": "familia ligada a acionamento, chaveamento ou saida"}

    if family in {"resistor", "capacitor", "potentiometer", "unknown"}:
        return {"block": "passive_support", "reason": "componente passivo ou sem bloco dominante inferido"}

    return {"block": "misc_control", "reason": "bloco funcional nao determinado com alta certeza"}


def block_priority(block_name: str) -> int:
    order = {
        "power_supply": 10,
        "power_regulation": 20,
        "connectors_io": 30,
        "mcu_control": 40,
        "clock_timing": 50,
        "display_i2c": 60,
        "display_spi": 61,
        "display_ui": 62,
        "sensor_analog": 70,
        "sensor_digital": 71,
        "sensor_frontend": 72,
        "uart_interface": 80,
        "usb_interface": 81,
        "can_transceiver": 82,
        "can_interface": 83,
        "rs485_interface": 84,
        "interface_comms": 85,
        "drivers_outputs": 90,
        "passive_support": 95,
        "misc_control": 99,
    }
    return order.get(block_name, 999)


def net_tokens_for_block(block_name: str) -> set[str]:
    mapping = {
        "power_supply": {"GND", "AGND", "DGND", "PGND", "VCC", "VDD", "VSS", "VIN", "VBAT", "3V3", "5V", "12V", "24V"},
        "power_regulation": {"GND", "VCC", "VDD", "VIN", "3V3", "5V", "12V"},
        "mcu_control": {"RESET", "RST", "CLK", "GPIO", "CS", "INT"},
        "clock_timing": {"CLK", "OSC"},
        "display_i2c": {"SCL", "SDA", "VCC", "GND"},
        "display_spi": {"MOSI", "MISO", "SCK", "CS", "CLK"},
        "display_ui": {"LCD", "BL", "COM", "SEG"},
        "sensor_analog": {"ADC", "AOUT", "AIN", "GND", "VCC"},
        "sensor_digital": {"SCL", "SDA", "MOSI", "MISO", "SCK", "CS", "INT"},
        "sensor_frontend": {"ADC", "INT", "SCL", "SDA"},
        "uart_interface": {"TX", "RX"},
        "can_interface": {"CAN", "CANH", "CANL"},
        "can_transceiver": {"CAN", "CANH", "CANL", "TX", "RX"},
        "rs485_interface": {"RS485", "A", "B", "DE", "RE", "DI", "RO"},
        "usb_interface": {"USB", "VBUS", "D+", "D-"},
        "interface_comms": {"TX", "RX", "SCL", "SDA", "MOSI", "MISO", "SCK", "CS", "INT"},
    }
    return mapping.get(block_name, set())


def build_functional_blocks_summary(component_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    block_map: dict[str, dict[str, Any]] = {}
    for component in component_nodes:
        block_info = infer_functional_block(component)
        block_name = block_info["block"]
        component["functional_block"] = block_name
        component["functional_block_reason"] = block_info["reason"]

        block = block_map.setdefault(
            block_name,
            {
                "block": block_name,
                "component_count": 0,
                "references": [],
                "families": set(),
                "avg_confidence_score": 0,
                "confidence_label": "low",
                "reason_samples": [],
            },
        )
        block["component_count"] += 1
        block["references"].append(component.get("reference", ""))
        block["families"].add(str(component.get("family", "unknown")))
        block["avg_confidence_score"] += int(component.get("confidence_score", 0))
        if block_info["reason"] not in block["reason_samples"] and len(block["reason_samples"]) < 3:
            block["reason_samples"].append(block_info["reason"])

    rows = []
    blocks = []
    for block_name, block in sorted(
        block_map.items(),
        key=lambda item: (block_priority(item[0]), -item[1]["component_count"], -item[1]["avg_confidence_score"], item[0]),
    ):
        component_count = int(block["component_count"])
        avg_score = int(round(block["avg_confidence_score"] / component_count)) if component_count else 0
        confidence_label = confidence_label_from_score(avg_score)
        references = sorted(str(item) for item in block["references"] if item)
        families = sorted(block["families"])
        priority = block_priority(block_name)
        block_entry = {
            "block": block_name,
            "priority": priority,
            "component_count": component_count,
            "references": references,
            "families": families,
            "avg_confidence_score": avg_score,
            "confidence_label": confidence_label,
            "reason_samples": block["reason_samples"],
        }
        blocks.append(block_entry)
        rows.append(
            {
                "block": block_name,
                "priority": str(priority),
                "component_count": str(component_count),
                "avg_confidence_score": str(avg_score),
                "confidence_label": confidence_label,
                "families": ", ".join(families),
                "references": ", ".join(references[:20]),
                "reason_samples": " | ".join(block["reason_samples"]),
            }
        )

    return {
        "blocks": blocks,
        "rows": rows,
    }


def build_block_checklist(functional_blocks: list[dict[str, Any]], net_rows: list[dict[str, str]]) -> dict[str, Any]:
    checklist_rows = []
    checklist_items = []
    step_number = 1

    action_map = {
        "power_supply": "Inserir fontes, conectores de alimentacao e terras principais",
        "power_regulation": "Montar reguladores, filtros e condicionamento de alimentacao",
        "mcu_control": "Inserir MCU e circuitos de suporte imediato",
        "display_ui": "Montar interface de display e sinais auxiliares",
        "display_i2c": "Montar display I2C e validar SDA/SCL/alimentacao",
        "display_spi": "Montar display SPI e validar MOSI/MISO/SCK/CS",
        "sensor_frontend": "Montar sensores e entradas de condicionamento",
        "sensor_analog": "Montar sensores analogicos e validar rotas para ADC",
        "sensor_digital": "Montar sensores digitais e barramentos associados",
        "uart_interface": "Montar interface serial UART/USART e conectores associados",
        "can_interface": "Montar interface CAN e conectores do barramento",
        "can_transceiver": "Montar transceptor CAN e validar CANH/CANL",
        "rs485_interface": "Montar interface/transceptor RS485 e validar A/B/DE/RE",
        "usb_interface": "Montar interface USB e validar VBUS/D+/D-",
        "interface_comms": "Montar interfaces de comunicacao restantes",
        "connectors_io": "Montar conectores de I/O e jumpers",
        "drivers_outputs": "Montar estagios de acionamento, rele, transistor e saidas",
        "clock_timing": "Montar clock, cristal e temporizacao",
        "passive_support": "Adicionar passivos de suporte apos os blocos principais",
        "misc_control": "Revisar e encaixar itens restantes sem bloco dominante",
    }

    verification_map = {
        "power_supply": "Confirmar nomes das redes de alimentacao e GND no ISIS",
        "power_regulation": "Confirmar tensoes esperadas e entrada/saida dos reguladores",
        "mcu_control": "Confirmar pinos principais, clock, reset e alimentacao do MCU",
        "display_ui": "Confirmar linhas de dados e alimentacao do display",
        "display_i2c": "Confirmar SDA, SCL, endereco/modulo e alimentacao",
        "display_spi": "Confirmar SCK, MOSI, MISO, CS, DC e reset se existirem",
        "sensor_frontend": "Confirmar sinais de leitura e alimentacao dos sensores",
        "sensor_analog": "Confirmar saidas analogicas, referencia e conexao aos ADCs",
        "sensor_digital": "Confirmar barramentos digitais e pull-ups/pull-downs necessarios",
        "uart_interface": "Confirmar cruzamento ou nao de TX/RX conforme o circuito",
        "can_interface": "Confirmar topologia do barramento CAN e terminacao se aplicavel",
        "can_transceiver": "Confirmar ligacao MCU <-> transceptor <-> CANH/CANL",
        "rs485_interface": "Confirmar ligacao DI/RO/DE/RE e terminais A/B",
        "usb_interface": "Confirmar VBUS, D+, D-, protecao e conector",
        "interface_comms": "Confirmar sinais de interface e sentidos de dados",
        "connectors_io": "Confirmar pinagem e rotulacao dos conectores",
        "drivers_outputs": "Confirmar sentido do acionamento e componentes de protecao",
        "clock_timing": "Confirmar frequencia e conexoes do clock/cristal",
        "passive_support": "Confirmar valores e localizacao dos passivos auxiliares",
        "misc_control": "Confirmar funcao e encaixe dos itens restantes no esquematico",
    }

    ordered_blocks = sorted(
        functional_blocks,
        key=lambda block: (
            int(block.get("priority", block_priority(str(block.get("block", "misc_control"))))),
            -int(block.get("avg_confidence_score", 0)),
            -int(block.get("component_count", 0)),
            str(block.get("block", "misc_control")),
        ),
    )

    parsed_nets = []
    for row in net_rows:
        parsed_nets.append(
            {
                "name": str(row.get("net", "")).upper(),
                "confidence": str(row.get("confidence", "low")),
                "confidence_score": int(row.get("confidence_score", 0)),
                "members": str(row.get("members", "")),
            }
        )

    for block in ordered_blocks:
        block_name = str(block.get("block", "misc_control"))
        references = ", ".join(block.get("references", [])[:20])
        priority = int(block.get("priority", block_priority(block_name)))
        block_tokens = net_tokens_for_block(block_name)
        matched_nets = []
        for net in parsed_nets:
            name = net["name"]
            if name in block_tokens or any(token in name for token in block_tokens):
                matched_nets.append(net)
        matched_nets = sorted(matched_nets, key=lambda item: (-item["confidence_score"], item["name"]))[:3]
        top_nets = ", ".join(net["name"] for net in matched_nets)
        top_nets_confidence = ", ".join(f"{net['name']}({net['confidence_score']})" for net in matched_nets)

        item = {
            "order": step_number,
            "priority": priority,
            "block": block_name,
            "component_count": block.get("component_count", 0),
            "avg_confidence_score": block.get("avg_confidence_score", 0),
            "confidence_label": block.get("confidence_label", "low"),
            "mount_action": action_map.get(block_name, "Montar bloco e revisar componentes associados"),
            "verification": verification_map.get(block_name, "Revisar ligacoes e coerencia do bloco no ISIS"),
            "priority_nets": top_nets,
            "priority_nets_confidence": top_nets_confidence,
            "references": block.get("references", []),
            "status": "pending",
        }
        checklist_items.append(item)
        checklist_rows.append(
            {
                "order": str(step_number),
                "priority": str(priority),
                "block": block_name,
                "component_count": str(block.get("component_count", 0)),
                "avg_confidence_score": str(block.get("avg_confidence_score", 0)),
                "confidence_label": str(block.get("confidence_label", "low")),
                "mount_action": item["mount_action"],
                "verification": item["verification"],
                "priority_nets": top_nets,
                "priority_nets_confidence": top_nets_confidence,
                "references": references,
                "status": "pending",
            }
        )
        step_number += 1

    return {
        "items": checklist_items,
        "rows": checklist_rows,
    }


def build_assisted_mounting_markdown(
    project_name: str,
    analysis: dict[str, Any],
    source_pdf_relative_path: Optional[str],
    generated_files: dict[str, str],
    functional_blocks: list[dict[str, Any]],
    block_checklist: list[dict[str, Any]],
) -> str:
    components = analysis.get("components", [])
    signals = analysis.get("signal_candidates", [])
    warnings = analysis.get("warnings", [])
    top_signals = ", ".join(item.get("name", "") for item in signals[:12]) or "nenhum sinal identificado"

    lines = [
        f"# Montagem Assistida - {project_name}",
        "",
        "## Objetivo",
        "",
        "Usar a analise do PDF como base confiavel para montar o projeto no ISIS Proteus com revisao humana, sem tentar gerar um esquematico automaticamente e sem validar conexoes que o parser nao conseguiu confirmar.",
        "",
        "## Entradas",
        "",
        f"- PDF de origem: {source_pdf_relative_path or 'nao informado'}",
        f"- Readiness da analise: {analysis.get('readiness', 'unknown')}",
        f"- Componentes detectados: {analysis.get('components_count', 0)}",
        f"- Sinais candidatos: {analysis.get('signal_candidates_count', 0)}",
        f"- Arquivo BOM CSV: {generated_files['bom_csv']}",
        f"- Arquivo de analise JSON: {generated_files['analysis_json']}",
        f"- Arquivo de sinais CSV: {generated_files['signals_csv']}",
        f"- Netlist draft JSON: {generated_files['netlist_json']}",
        f"- Revisao de netlist CSV: {generated_files['netlist_review_csv']}",
        f"- Resumo de blocos funcionais CSV: {generated_files['functional_blocks_csv']}",
        f"- Checklist de montagem por bloco CSV: {generated_files['block_checklist_csv']}",
        "",
        "## Alertas",
        "",
    ]

    if warnings:
        lines.extend([f"- {warning}" for warning in warnings])
    else:
        lines.append("- Nenhum alerta relevante foi emitido pela analise inicial.")

    lines.extend(
        [
            "",
            "## Sequencia recomendada no Proteus",
            "",
            "1. Crie manualmente um novo projeto ISIS Proteus dentro desta pasta assistida ou em uma subpasta dedicada do mesmo projeto.",
            "2. Abra o PDF original e o arquivo de analise JSON lado a lado para validar referencias e valores antes de inserir qualquer simbolo.",
            "3. Consulte primeiro o resumo de blocos funcionais para montar o circuito por subsistemas: fonte, controle, interface e conectores.",
            "4. Insira os passivos e discretos usando o BOM CSV como checklist; marque os itens revisados conforme forem colocados no ISIS.",
            "5. Nomeie primeiro as redes principais e barramentos usando os sinais candidatos detectados: " + top_signals + ".",
            "6. Use a netlist draft para priorizar quais componentes ja possuem alguma associacao com sinais e quais ainda estao sem conexao inferida.",
            "7. Compare as evidencias por pagina do JSON quando houver ambiguidade de valor, encapsulamento ou familia do componente.",
            "8. So depois de revisar simbolos e conexoes, salve o projeto e use o bridge atual para abrir o Proteus, importar firmware e simular.",
            "",
            "## Blocos funcionais inferidos",
            "",
        ]
    )

    if functional_blocks:
        for block in functional_blocks:
            lines.append(
                f"- prioridade {block['priority']} | {block['block']} | {block['component_count']} componente(s) | score medio {block['avg_confidence_score']} | {block['confidence_label']}"
            )
    else:
        lines.append("- Nenhum bloco funcional pode ser inferido com os dados atuais.")

    lines.extend(
        [
            "",
            "## Checklist por bloco funcional",
            "",
        ]
    )

    if block_checklist:
        for item in block_checklist:
            lines.append(
                f"- Etapa {item['order']} (prioridade {item['priority']}): {item['block']} | {item['mount_action']} | verificar: {item['verification']}"
            )
    else:
        lines.append("- Nenhum checklist por bloco foi gerado porque nao ha blocos funcionais inferidos.")

    lines.extend(
        [
            "",
            "## Critérios de aceite",
            "",
            "- Cada referencia do BOM CSV foi confirmada ou descartada manualmente.",
            "- As alimentacoes e terras do circuito estao identificadas no ISIS com nomes coerentes.",
            "- Os componentes ambiguos do JSON foram validados contra o PDF original.",
            "- O projeto Proteus resultante pode ser salvo e reaberto normalmente antes de qualquer simulacao.",
            "",
            "## Componentes prioritarios",
            "",
        ]
    )

    if components:
        for item in build_component_rows(components)[:30]:
            value = item["candidate_value"] or "valor nao inferido"
            lines.append(f"- {item['reference']} | {item['family']} | {value} | paginas {item['pages'] or '-'}")
    else:
        lines.append("- Nenhum componente detectado automaticamente. Revise o PDF e considere aplicar OCR antes da montagem.")

    return "\n".join(lines) + "\n"


def prepare_proteus_assisted_project(request: PrepareProteusAssistedProjectRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    package = load_schematic_analysis_payload(
        request.analysis_relative_path,
        request.pdf_path,
        request.max_pages,
        request.use_ocr_if_needed,
        request.ocr_languages,
        request.ocr_min_text_chars,
    )
    analysis = package["analysis"]
    source_pdf_relative_path = package.get("source_pdf_relative_path")

    project_dir = safe_relative_path(request.project_folder)
    if project_dir.exists() and any(project_dir.iterdir()) and not request.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"A pasta de projeto assistido já existe e não está vazia: {project_dir}",
        )
    project_dir.mkdir(parents=True, exist_ok=True)

    project_name = request.project_display_name or project_dir.name
    analysis_json_path = project_dir / "schematic_analysis.json"
    components_json_path = project_dir / "components_detected.json"
    components_csv_path = project_dir / "components_bom.csv"
    signals_json_path = project_dir / "signal_candidates.json"
    signals_csv_path = project_dir / "signal_candidates.csv"
    guide_md_path = project_dir / "MONTAGEM_ASSISTIDA_PROTEUS.md"
    manifest_json_path = project_dir / "assisted_project_manifest.json"
    netlist_json_path = project_dir / "draft_netlist.json"
    netlist_review_csv_path = project_dir / "draft_netlist_review.csv"
    netlist_nets_csv_path = project_dir / "draft_netlist_nets.csv"
    functional_blocks_json_path = project_dir / "functional_blocks.json"
    functional_blocks_csv_path = project_dir / "functional_blocks.csv"
    block_checklist_json_path = project_dir / "block_mount_checklist.json"
    block_checklist_csv_path = project_dir / "block_mount_checklist.csv"

    components = analysis.get("components", [])
    signals = analysis.get("signal_candidates", [])
    draft_netlist = build_draft_netlist(analysis)
    functional_blocks = build_functional_blocks_summary(draft_netlist["components"])
    block_checklist = build_block_checklist(functional_blocks["blocks"], draft_netlist["net_rows"])

    analysis_json_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    components_json_path.write_text(json.dumps(components, indent=2, ensure_ascii=False), encoding="utf-8")
    signals_json_path.write_text(json.dumps(signals, indent=2, ensure_ascii=False), encoding="utf-8")
    netlist_json_path.write_text(json.dumps(draft_netlist, indent=2, ensure_ascii=False), encoding="utf-8")
    functional_blocks_json_path.write_text(json.dumps(functional_blocks["blocks"], indent=2, ensure_ascii=False), encoding="utf-8")
    block_checklist_json_path.write_text(json.dumps(block_checklist["items"], indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv_rows(components_csv_path, build_component_rows(components))
    write_csv_rows(signals_csv_path, build_signal_rows(signals))
    write_csv_rows(netlist_review_csv_path, draft_netlist["review_rows"])
    write_csv_rows(netlist_nets_csv_path, draft_netlist["net_rows"])
    write_csv_rows(functional_blocks_csv_path, functional_blocks["rows"])
    write_csv_rows(block_checklist_csv_path, block_checklist["rows"])

    copied_pdf_relative_path = None
    if request.copy_source_pdf and source_pdf_relative_path:
        source_pdf_path = safe_relative_path(source_pdf_relative_path)
        if source_pdf_path.exists() and source_pdf_path.is_file():
            copied_pdf_name = source_pdf_path.name
            copied_pdf_path = project_dir / copied_pdf_name
            if source_pdf_path.resolve() != copied_pdf_path.resolve():
                shutil.copy2(source_pdf_path, copied_pdf_path)
            copied_pdf_relative_path = str(copied_pdf_path.relative_to(root))

    generated_files = {
        "analysis_json": str(analysis_json_path.relative_to(root)),
        "components_json": str(components_json_path.relative_to(root)),
        "bom_csv": str(components_csv_path.relative_to(root)),
        "signals_json": str(signals_json_path.relative_to(root)),
        "signals_csv": str(signals_csv_path.relative_to(root)),
        "netlist_json": str(netlist_json_path.relative_to(root)),
        "netlist_review_csv": str(netlist_review_csv_path.relative_to(root)),
        "netlist_nets_csv": str(netlist_nets_csv_path.relative_to(root)),
        "functional_blocks_json": str(functional_blocks_json_path.relative_to(root)),
        "functional_blocks_csv": str(functional_blocks_csv_path.relative_to(root)),
        "block_checklist_json": str(block_checklist_json_path.relative_to(root)),
        "block_checklist_csv": str(block_checklist_csv_path.relative_to(root)),
    }
    guide_md_path.write_text(
        build_assisted_mounting_markdown(
            project_name,
            analysis,
            copied_pdf_relative_path or source_pdf_relative_path,
            generated_files,
            functional_blocks["blocks"],
            block_checklist["items"],
        ),
        encoding="utf-8",
    )

    manifest = {
        "project_display_name": project_name,
        "project_folder": str(project_dir.relative_to(root)),
        "analysis_source": {
            "analysis_relative_path": package.get("source_analysis_relative_path"),
            "pdf_relative_path": source_pdf_relative_path,
            "copied_pdf_relative_path": copied_pdf_relative_path,
        },
        "readiness": analysis.get("readiness"),
        "components_count": analysis.get("components_count", 0),
        "signal_candidates_count": analysis.get("signal_candidates_count", 0),
        "generated_files": {
            **generated_files,
            "guide_markdown": str(guide_md_path.relative_to(root)),
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "ok": True,
        "project_folder": str(project_dir.relative_to(root)),
        "project_display_name": project_name,
        "readiness": analysis.get("readiness"),
        "components_count": analysis.get("components_count", 0),
        "signal_candidates_count": analysis.get("signal_candidates_count", 0),
        "generated_files": {
            **manifest["generated_files"],
            "manifest_json": str(manifest_json_path.relative_to(root)),
        },
        "copied_pdf_relative_path": copied_pdf_relative_path,
        "next_step": "Abra MONTAGEM_ASSISTIDA_PROTEUS.md e monte o projeto ISIS manualmente usando o BOM e os sinais detectados.",
    }


def resolve_candidate_manifest_path(source_pdf_path: Path, explicit_manifest_path: Optional[str]) -> Path:
    if explicit_manifest_path:
        manifest_path = safe_relative_path(explicit_manifest_path)
        if not manifest_path.exists() or not manifest_path.is_file():
            raise HTTPException(status_code=404, detail=f"Manifesto de candidatos nao encontrado: {manifest_path}")
        return manifest_path

    root = ensure_root_exists()
    default_manifest_relative = str(source_pdf_path.relative_to(root).with_suffix("")) + "_schematic_candidates/candidate_pages.json"
    manifest_path = safe_relative_path(default_manifest_relative)
    if not manifest_path.exists() or not manifest_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Manifesto de candidatos nao encontrado automaticamente. Execute /pdf/extract-schematic-candidates "
                "ou informe candidate_manifest_path."
            ),
        )
    return manifest_path


def should_keep_focused_page(profile_name: str, page: dict[str, Any], min_score_override: Optional[int]) -> bool:
    page_number = int(page.get("page_number", 0))
    score = int(page.get("score", 0))
    refs = len(page.get("component_references", []))
    signals = len(page.get("signal_candidates", []))
    preview = str(page.get("preview", "")).upper()
    min_score = min_score_override if min_score_override is not None else 0

    if profile_name == "acer_pass3_pure_schematics":
        hard_schematic = "SHEET" in preview and "DATE:" in preview
        return (
            page_number in {211, 246}
            or (score >= max(50, min_score) and hard_schematic)
            or (score >= max(46, min_score) and refs >= 18 and signals >= 2)
            or (score >= max(60, min_score) and refs >= 18)
        )

    if profile_name == "acer_pass4_signal_dense":
        has_bus_keywords = any(token in preview for token in ("DATA", "CLOCK", "ADDR", "BUS", "D[", "A["))
        strong_interconnect = signals >= 5 or has_bus_keywords
        component_dense = refs >= 25
        return (
            ("SHEET" in preview and "DATE:" in preview and strong_interconnect and component_dense and score >= max(48, min_score))
            or page_number in {226, 249, 251, 254}
        )

    raise HTTPException(status_code=400, detail=f"Perfil de foco nao suportado: {profile_name}")


def build_focused_subset_from_candidates(request: BuildFocusedSubsetRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    fitz = import_fitz_module()
    source_pdf_path = safe_relative_path(request.source_pdf_path)
    if source_pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="source_pdf_path deve apontar para um arquivo .pdf")
    if not source_pdf_path.exists() or not source_pdf_path.is_file():
        raise HTTPException(status_code=404, detail=f"PDF nao encontrado: {source_pdf_path}")

    manifest_path = resolve_candidate_manifest_path(source_pdf_path, request.candidate_manifest_path)
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Manifesto de candidatos invalido: {manifest_path}") from exc

    source_pages = manifest_payload.get("selected_pages") or manifest_payload.get("ranked_pages") or []
    if not isinstance(source_pages, list) or not source_pages:
        raise HTTPException(status_code=400, detail="Manifesto nao contem paginas candidatas para processar")

    chosen_pages = []
    for page in source_pages:
        if not isinstance(page, dict):
            continue
        if should_keep_focused_page(request.profile_name, page, request.min_score_override):
            chosen_pages.append(page)

    if not chosen_pages:
        raise HTTPException(status_code=400, detail="Nenhuma pagina foi selecionada pelo perfil de foco informado")

    chosen_pages = sorted(chosen_pages, key=lambda item: int(item.get("page_number", 0)))
    chosen_pages = [page for i, page in enumerate(chosen_pages) if i == 0 or int(page.get("page_number", 0)) != int(chosen_pages[i - 1].get("page_number", 0))]
    selected_page_numbers = [int(page.get("page_number", 0)) for page in chosen_pages]

    default_pdf_relative = str(source_pdf_path.relative_to(root).with_suffix("")) + f"_{request.profile_name}.pdf"
    output_pdf_path = safe_relative_path(request.output_pdf_path or default_pdf_relative)
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    source_document = fitz.open(str(source_pdf_path))
    focused_document = fitz.open()
    for page_number in selected_page_numbers:
        focused_document.insert_pdf(source_document, from_page=page_number - 1, to_page=page_number - 1)
    focused_document.save(str(output_pdf_path))
    focused_document.close()
    source_document.close()

    default_selection_relative = str(source_pdf_path.relative_to(root).with_suffix("")) + f"_{request.profile_name}.selection.json"
    output_selection_path = safe_relative_path(request.output_selection_json_path or default_selection_relative)
    output_selection_path.parent.mkdir(parents=True, exist_ok=True)

    selection_payload = {
        "profile_name": request.profile_name,
        "source_pdf_path": str(source_pdf_path.relative_to(root)),
        "candidate_manifest_path": str(manifest_path.relative_to(root)),
        "output_pdf_path": str(output_pdf_path.relative_to(root)),
        "selected_count": len(selected_page_numbers),
        "selected_pages": selected_page_numbers,
        "selected_page_details": chosen_pages,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    output_selection_path.write_text(json.dumps(selection_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "ok": True,
        "profile_name": request.profile_name,
        "selected_count": len(selected_page_numbers),
        "selected_pages": selected_page_numbers,
        "output_pdf_path": str(output_pdf_path.relative_to(root)),
        "output_selection_json_path": str(output_selection_path.relative_to(root)),
    }


def searchable_text_extensions() -> set[str]:
    return allowed_edit_extensions().union({
        ".log",
        ".ini",
        ".cfg",
        ".yaml",
        ".yml",
        ".toml",
        ".csv",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".svelte",
        ".sql",
    })


def list_workspace_entries(relative_path: str = ".", max_entries: int = 200) -> dict[str, Any]:
    root = ensure_root_exists()
    path = safe_relative_path(relative_path)

    if path.is_file():
        return {
            "ok": True,
            "type": "file",
            "relative_path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
        }

    entries = []
    for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:max_entries]:
        entry = {
            "name": child.name,
            "relative_path": str(child.relative_to(root)),
            "type": "dir" if child.is_dir() else "file",
        }
        if child.is_file():
            entry["size_bytes"] = child.stat().st_size
        entries.append(entry)

    return {
        "ok": True,
        "type": "dir",
        "relative_path": "." if path == root else str(path.relative_to(root)),
        "count": len(entries),
        "entries": entries,
    }


def search_workspace_content(query: str, relative_path: str = ".", is_regex: bool = False, max_results: int = 50) -> dict[str, Any]:
    if not query.strip():
        raise HTTPException(status_code=400, detail="Informe um texto ou regex para busca")

    root = ensure_root_exists()
    base = safe_relative_path(relative_path)
    pattern = re.compile(query, re.IGNORECASE) if is_regex else None
    results = []
    truncated = False

    if base.is_file():
        candidates = [base]
    else:
        candidates = [path for path in base.rglob("*") if path.is_file()]

    for path in candidates:
        if path.suffix.lower() not in searchable_text_extensions():
            continue
        if path.stat().st_size > 2_000_000:
            continue

        try:
            content = read_text_content(path)
        except Exception:
            continue

        for idx, line in enumerate(content.splitlines(), start=1):
            matched = bool(pattern.search(line)) if pattern else query.lower() in line.lower()
            if not matched:
                continue

            results.append(
                {
                    "relative_path": str(path.relative_to(root)),
                    "line_number": idx,
                    "line": line.strip(),
                }
            )
            if len(results) >= max(1, min(max_results, 200)):
                truncated = True
                break

        if truncated:
            break

    return {
        "ok": True,
        "query": query,
        "is_regex": is_regex,
        "count": len(results),
        "truncated": truncated,
        "results": results,
    }


def create_workspace_directory(relative_path: str, exist_ok: bool = True) -> dict[str, Any]:
    root = ensure_root_exists()
    path = safe_relative_path(relative_path)
    already_exists = path.exists()
    if already_exists and not exist_ok:
        raise HTTPException(status_code=409, detail=f"Diretório já existe: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "relative_path": str(path.relative_to(root)),
        "created": not already_exists,
    }


def move_workspace_path(source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, Any]:
    root = ensure_root_exists()
    source = safe_relative_path(source_path)
    destination = safe_relative_path(destination_path)

    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Origem não encontrada: {source}")
    if destination.exists():
        if not overwrite:
            raise HTTPException(status_code=409, detail=f"Destino já existe: {destination}")
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return {
        "ok": True,
        "source": source_path,
        "destination": destination_path,
    }


def delete_workspace_path(relative_path: str, recursive: bool = False) -> dict[str, Any]:
    root = ensure_root_exists()
    path = safe_relative_path(relative_path)

    if path == root:
        raise HTTPException(status_code=400, detail="Não é permitido remover a raiz do workspace")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Caminho não encontrado: {path}")

    if path.is_dir():
        if not recursive:
            raise HTTPException(status_code=400, detail="Diretório requer recursive=true para remoção")
        shutil.rmtree(path)
    else:
        path.unlink()

    return {
        "ok": True,
        "removed": relative_path,
    }


def run_shell_command(command: str, cwd: Optional[str] = None, timeout_seconds: Optional[int] = None) -> dict[str, Any]:
    if not CONFIG.get("allow_shell_commands", False):
        raise HTTPException(status_code=403, detail="Execução de comandos está desabilitada no config")

    root = ensure_root_exists()
    working_dir = safe_relative_path(cwd) if cwd else root
    timeout = int(timeout_seconds or CONFIG.get("shell_timeout_seconds", 60))
    timeout = max(5, min(timeout, 600))
    max_output_chars = int(CONFIG.get("shell_max_output_chars", 12000))

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": -2,
            "cwd": str(working_dir.relative_to(root)) if working_dir != root else ".",
            "command": command,
            "stdout": truncate_output(exc.stdout or "", max_output_chars),
            "stderr": f"Tempo excedido após {timeout}s",
        }

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "cwd": str(working_dir.relative_to(root)) if working_dir != root else ".",
        "command": command,
        "stdout": truncate_output(result.stdout, max_output_chars),
        "stderr": truncate_output(result.stderr, max_output_chars),
    }


def normalize_search_link(link: str) -> str:
    if not link:
        return ""
    if link.startswith("//"):
        link = "https:" + link
    parsed = urlparse(link)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return link


def search_web_online(query: str, max_results: int = 5) -> dict[str, Any]:
    if not query.strip():
        raise HTTPException(status_code=400, detail="Informe uma consulta de busca")

    limit = max(1, min(int(max_results), int(CONFIG.get("web_search_max_results", 8))))
    ddgs_error = None

    try:
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=limit):
                results.append(
                    {
                        "title": item.get("title", ""),
                        "link": item.get("href", ""),
                        "snippet": item.get("body", ""),
                    }
                )
        if results:
            return {"ok": True, "query": query, "count": len(results), "source": "duckduckgo_search", "results": results}
    except Exception as exc:
        ddgs_error = str(exc)

    headers = {"User-Agent": CONFIG.get("web_user_agent", "Mozilla/5.0")}
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        detail = {"message": "Falha na busca web", "error": str(exc)}
        if ddgs_error:
            detail["fallback_error"] = ddgs_error
        raise HTTPException(status_code=502, detail=detail) from exc

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for item in soup.select(".result"):
        link_tag = item.select_one(".result__title a")
        if not link_tag:
            continue
        title = " ".join(link_tag.get_text(" ", strip=True).split())
        link = normalize_search_link(link_tag.get("href", ""))
        snippet_tag = item.select_one(".result__snippet")
        snippet = " ".join(snippet_tag.get_text(" ", strip=True).split()) if snippet_tag else ""
        results.append({"title": title, "link": link, "snippet": snippet})
        if len(results) >= limit:
            break

    return {
        "ok": True,
        "query": query,
        "count": len(results),
        "source": "duckduckgo_html",
        "results": results,
        "note": ddgs_error,
    }


def fetch_web_content(url: str, max_chars: Optional[int] = None) -> dict[str, Any]:
    headers = {"User-Agent": CONFIG.get("web_user_agent", "Mozilla/5.0")}
    limit = int(max_chars or CONFIG.get("web_fetch_max_chars", 12000))
    limit = max(1000, min(limit, 50000))

    try:
        response = requests.get(url, headers=headers, timeout=25)
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao acessar a URL: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else url
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())

    return {
        "ok": True,
        "url": url,
        "title": title,
        "content_length": len(text),
        "content": truncate_output(text, limit),
    }


def check_network_target(target: str, port: Optional[int] = None, url: Optional[str] = None, timeout_seconds: int = 5) -> dict[str, Any]:
    timeout = max(1, min(int(timeout_seconds), 30))
    result: dict[str, Any] = {
        "ok": True,
        "target": target,
        "port": port,
        "url": url,
    }

    try:
        result["resolved_ip"] = socket.gethostbyname(target)
    except Exception as exc:
        result["ok"] = False
        result["resolved_ip"] = None
        result["dns_error"] = str(exc)

    if port is not None:
        try:
            with socket.create_connection((target, int(port)), timeout=timeout):
                result["port_open"] = True
        except Exception as exc:
            result["ok"] = False
            result["port_open"] = False
            result["port_error"] = str(exc)

    if url:
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": CONFIG.get("web_user_agent", "Mozilla/5.0")})
            result["http_ok"] = response.ok
            result["http_status"] = response.status_code
        except Exception as exc:
            result["ok"] = False
            result["http_ok"] = False
            result["http_error"] = str(exc)

    return result


def execute_automation_workflow(request: AutomationRunRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    project_file = resolve_project_path(request.project)
    profile = format_value(get_automation_profile(request.profile), build_workflow_context(project_file, request))
    actions = profile.get("actions", {})
    artifact_cfg = profile.get("artifact", {})
    build_cfg = profile.get("build", {})
    context = build_workflow_context(project_file, request)

    backup_enabled = actions.get("backup", True) if request.backup is None else request.backup
    open_enabled = actions.get("open_project", True) if request.open_project is None else request.open_project
    macro_name = request.run_macro if request.run_macro is not None else actions.get("macro_after_open", "")
    delay_after_open_ms = int(actions.get("delay_after_open_ms", 1200))
    send_keys_sequence = list(actions.get("send_keys", [])) + list(request.send_keys)

    output: dict[str, Any] = {
        "ok": True,
        "message": "Fluxo executado com sucesso" if not request.dry_run else "Pré-visualização gerada com sucesso",
        "profile": request.profile,
        "project": str(project_file.relative_to(root)),
        "steps": [],
    }

    if request.dry_run:
        output["context"] = context

    if backup_enabled:
        if request.dry_run:
            output["steps"].append({"step": "backup", "planned": True})
        else:
            output["steps"].append({"step": "backup", "result": backup_project(BackupProjectRequest(project=request.project))})

    artifact_path_value = request.artifact_path or artifact_cfg.get("path", "")
    artifact_glob_value = artifact_cfg.get("glob", "")
    artifact_target_name = request.artifact_target_name or artifact_cfg.get("target_name") or "firmware.hex"
    artifact_target_name = format_value(artifact_target_name, context)

    if build_cfg.get("enabled", False):
        if not CONFIG.get("allow_shell_commands", False) and not request.dry_run:
            raise HTTPException(status_code=403, detail="Execução de comandos está desabilitada no config")

        command = format_value(build_cfg.get("command", ""), context)
        args = format_value(build_cfg.get("args", []), context)
        cwd = resolve_template_path(build_cfg.get("cwd", "{project_dir}"), context)
        timeout_seconds = int(build_cfg.get("timeout_seconds", 180))

        if request.dry_run:
            output["steps"].append(
                {
                    "step": "build",
                    "planned": True,
                    "cwd": str(cwd),
                    "command": [command, *args],
                    "timeout_seconds": timeout_seconds,
                }
            )
        else:
            build_result = run_command_capture(command, args, cwd, timeout_seconds)
            output["steps"].append({"step": "build", **build_result})
            if not build_result["ok"]:
                output["ok"] = False
                output["message"] = "Fluxo interrompido: build falhou"
                return output

    artifact_source: Optional[Path] = None
    if artifact_path_value:
        artifact_source = resolve_template_path(artifact_path_value, context)
        if not artifact_source.exists():
            artifact_source = None
    if artifact_source is None and artifact_glob_value:
        artifact_source = find_latest_match(artifact_glob_value, context)

    if artifact_path_value or artifact_glob_value or request.artifact_path:
        if artifact_source is None:
            output["ok"] = False
            output["message"] = "Artefato não encontrado após o build"
            output["steps"].append(
                {
                    "step": "artifact_lookup",
                    "ok": False,
                    "path": artifact_path_value,
                    "glob": artifact_glob_value,
                }
            )
            return output

        if request.dry_run:
            output["steps"].append(
                {
                    "step": "import_artifact",
                    "planned": True,
                    "source": str(artifact_source),
                    "target_name": artifact_target_name,
                }
            )
        else:
            import_result = import_artifact(
                ImportArtifactRequest(
                    project=request.project,
                    artifact_path=str(artifact_source),
                    target_name=artifact_target_name,
                    overwrite=bool(artifact_cfg.get("overwrite", True)),
                )
            )
            output["steps"].append({"step": "import_artifact", "result": import_result})

    if open_enabled:
        if request.dry_run:
            output["steps"].append({"step": "open_project", "planned": True, "extra_args": request.extra_args})
        else:
            open_result = open_project(
                OpenProjectRequest(
                    project=request.project,
                    extra_args=request.extra_args,
                    bring_to_front=bool(actions.get("focus", True)),
                )
            )
            output["steps"].append({"step": "open_project", "result": open_result})

            if macro_name:
                time.sleep(max(delay_after_open_ms, 0) / 1000)
                try:
                    macro_result = proteus_run_macro(MacroRequest(name=macro_name, delay_ms=300))
                    output["steps"].append({"step": "run_macro", "result": macro_result})
                except HTTPException as exc:
                    output["ok"] = False
                    output["message"] = "Fluxo executado parcialmente: macro não pôde ser enviada"
                    output["steps"].append({"step": "run_macro", "ok": False, "detail": exc.detail})

            for keys in send_keys_sequence:
                if not keys:
                    continue
                try:
                    key_result = proteus_send_keys(SendKeysRequest(keys=keys))
                    output["steps"].append({"step": "send_keys", "result": key_result})
                except HTTPException as exc:
                    output["ok"] = False
                    output["message"] = "Fluxo executado parcialmente: algumas teclas não puderam ser enviadas"
                    output["steps"].append({"step": "send_keys", "ok": False, "keys": keys, "detail": exc.detail})

    return output


_TASK_CONTRACTS: dict[str, dict[str, Any]] = {
    "diagnose": {
        "required_sections": ["Resumo", "Causa raiz", "Correcao", "Validacao"],
        "verifier_checks": ["service_health", "network_probe", "evidence_present"],
    },
    "workspace": {
        "required_sections": ["Objetivo", "Plano", "Mudancas", "Proximos passos"],
        "verifier_checks": ["workspace_context", "change_safety", "evidence_present"],
    },
    "research": {
        "required_sections": ["Resumo", "Comparativo", "Fontes", "Recomendacao"],
        "verifier_checks": ["web_sources", "source_quality", "evidence_present"],
    },
    "proteus": {
        "required_sections": ["Objetivo", "Plano", "Execucao", "Resultado"],
        "verifier_checks": ["project_context", "automation_steps", "evidence_present"],
    },
    "generic": {
        "required_sections": ["Objetivo", "Plano", "Execucao", "Validacao"],
        "verifier_checks": ["structured_output", "evidence_present"],
    },
}

_MEMORY_DIR = BASE_DIR / "assistant_memory"
_MEMORY_FILE = _MEMORY_DIR / "entries.jsonl"
_CHECKPOINT_FILE = _MEMORY_DIR / "checkpoints.jsonl"
_REPORTS_DIR = BASE_DIR / "assistant_reports"
_REPORTS_SCOPED_DIR = _REPORTS_DIR / "scoped"
_REPORTS_INDEX_FILE = _REPORTS_DIR / "index.json"
_REPORTS_SCHEDULES_FILE = _REPORTS_DIR / "schedules.json"
_REPORTS_POLICIES_FILE = _REPORTS_DIR / "policies.json"
_REPORTS_AUDIT_LOGS_FILE = _REPORTS_DIR / "audit_logs.jsonl"
_REPORTS_INCIDENTS_FILE = _REPORTS_DIR / "incidents.jsonl"

_PHASE4_RISK_POLICY: dict[str, str] = {
    "diagnose": "medium",
    "workspace": "medium",
    "research": "low",
    "proteus": "high",
    "generic": "medium",
}

_PHASE4_HIGH_IMPACT_TERMS = {
    "delete",
    "remove",
    "overwrite",
    "format",
    "reset",
    "force",
    "production",
    "deploy",
    "firmware",
    "proteus",
}

_PHASE5_SLA_WARNING_HOURS = 4
_PHASE5_SLA_CRITICAL_HOURS = 24

_PHASE6_QUEUE_POLICIES: dict[str, dict[str, Any]] = {
    "risk": {
        "eligible_risk_levels": ["high", "critical"],
        "default_action": "manual-review",
        "escalation_levels": {"high": "sev3", "critical": "sev2"},
    },
    "sla": {
        "eligible_states": ["warning", "critical"],
        "default_action": "aging-monitor",
        "escalation_levels": {"warning": "sev3", "critical": "sev2"},
    },
    "compliance": {
        "requires_quality_pass": True,
        "default_action": "contract-remediation",
        "escalation_levels": {"warning": "sev4", "critical": "sev3"},
    },
}

_PHASE7_DEFAULT_DAYS = 7
_PHASE9_DEFAULT_SNAPSHOT_FORMATS = ["markdown", "csv", "json"]
_PHASE10_DEFAULT_ENVIRONMENT = "default"
_PHASE11_DEFAULT_TENANT_ID = "shared"
_PHASE11_DEFAULT_PROJECT_SCOPE = "general"
_PHASE20_SEVERITY_WEIGHTS: dict[str, int] = {
    "sev1": 40,
    "sev2": 20,
    "sev3": 10,
    "sev4": 5,
    "unknown": 1,
}


def _tokenize_text(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9_\-\s]", " ", (text or "").lower())
    return {token for token in normalized.split() if len(token) >= 3}


def _memory_entry_tokens(entry: dict[str, Any]) -> set[str]:
    chunks = [
        str(entry.get("title", "")),
        str(entry.get("content", "")),
        " ".join(str(tag) for tag in entry.get("tags", [])),
        str(entry.get("category", "")),
    ]
    return _tokenize_text(" ".join(chunks))


def _ensure_memory_storage() -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not _MEMORY_FILE.exists():
        _MEMORY_FILE.write_text("", encoding="utf-8")
    if not _CHECKPOINT_FILE.exists():
        _CHECKPOINT_FILE.write_text("", encoding="utf-8")


def _append_memory_entry(entry: dict[str, Any]) -> None:
    _ensure_memory_storage()
    with _MEMORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_memory_entries() -> list[dict[str, Any]]:
    _ensure_memory_storage()
    entries: list[dict[str, Any]] = []
    with _MEMORY_FILE.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    entries.append(payload)
            except Exception:
                continue
    return entries


def _append_checkpoint(entry: dict[str, Any]) -> None:
    _ensure_memory_storage()
    with _CHECKPOINT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_checkpoints() -> list[dict[str, Any]]:
    _ensure_memory_storage()
    checkpoints: list[dict[str, Any]] = []
    with _CHECKPOINT_FILE.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    checkpoints.append(payload)
            except Exception:
                continue
    return checkpoints


def _find_checkpoint(checkpoint_id: str) -> Optional[dict[str, Any]]:
    for checkpoint in reversed(_load_checkpoints()):
        if checkpoint.get("checkpoint_id") == checkpoint_id:
            return checkpoint
    return None


def _record_checkpoint_decision(checkpoint_id: str, decision: str, note: str) -> dict[str, Any]:
    checkpoint = _find_checkpoint(checkpoint_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail=f"checkpoint nao encontrado: {checkpoint_id}")

    decision_entry = {
        "checkpoint_id": checkpoint_id,
        "decision": decision,
        "note": note,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "objective": checkpoint.get("objective"),
        "task_type": checkpoint.get("task_type"),
        "risk": checkpoint.get("risk", {}),
        "status": "approved" if decision == "approve" else "rejected",
    }
    _append_checkpoint(decision_entry)
    return decision_entry


def _to_checkpoint_latest_records() -> list[dict[str, Any]]:
    history: dict[str, list[dict[str, Any]]] = {}
    for item in _load_checkpoints():
        checkpoint_id = str(item.get("checkpoint_id") or "").strip()
        if not checkpoint_id:
            continue
        history.setdefault(checkpoint_id, []).append(item)

    records: list[dict[str, Any]] = []
    for checkpoint_id, items in history.items():
        merged = dict(items[-1])
        for previous in reversed(items[:-1]):
            for key in [
                "objective",
                "task_type",
                "created_at",
                "quality",
                "risk",
                "sla",
                "planner_steps",
                "queue",
                "queue_reason",
                "queue_priority",
                "compliance_state",
            ]:
                current_value = merged.get(key)
                if current_value in (None, "", [], {}):
                    previous_value = previous.get(key)
                    if previous_value not in (None, "", [], {}):
                        merged[key] = previous_value
        merged["checkpoint_id"] = checkpoint_id
        records.append(merged)

    records.sort(key=lambda entry: str(entry.get("updated_at") or entry.get("created_at") or ""), reverse=True)
    return records


def _cleanup_old_checkpoints(retention_days: int) -> dict[str, Any]:
    _ensure_memory_storage()
    cutoff = datetime.now() - timedelta(days=retention_days)
    lines = _CHECKPOINT_FILE.read_text(encoding="utf-8").splitlines()

    kept_lines: list[str] = []
    removed_count = 0
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            kept_lines.append(line)
            continue

        ts_raw = str(payload.get("updated_at") or payload.get("created_at") or "")
        keep_item = True
        if ts_raw:
            try:
                ts_value = datetime.fromisoformat(ts_raw)
                if ts_value < cutoff:
                    keep_item = False
            except Exception:
                keep_item = True

        if keep_item:
            kept_lines.append(line)
        else:
            removed_count += 1

    output = "\n".join(kept_lines)
    if output:
        output += "\n"
    _CHECKPOINT_FILE.write_text(output, encoding="utf-8")

    return {
        "retention_days": retention_days,
        "removed_entries": removed_count,
        "remaining_entries": len(kept_lines),
        "cutoff": cutoff.isoformat(timespec="seconds"),
    }


def _parse_checkpoint_timestamp(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _phase5_enrich_checkpoint_sla(
    checkpoint: dict[str, Any],
    warning_after_hours: int = _PHASE5_SLA_WARNING_HOURS,
    critical_after_hours: int = _PHASE5_SLA_CRITICAL_HOURS,
) -> dict[str, Any]:
    enriched = dict(checkpoint)
    created_at = _parse_checkpoint_timestamp(checkpoint.get("created_at") or checkpoint.get("updated_at"))
    age_hours = 0.0
    if created_at is not None:
        age_hours = round(max((datetime.now() - created_at).total_seconds(), 0.0) / 3600.0, 2)

    status = str(checkpoint.get("status") or "").strip().lower()

    sla_state = "healthy"
    if status == "pending":
        if age_hours >= critical_after_hours:
            sla_state = "critical"
        elif age_hours >= warning_after_hours:
            sla_state = "warning"

    escalation_level = "none"
    if status == "pending":
        if sla_state == "critical":
            escalation_level = "sev2"
        elif sla_state == "warning":
            escalation_level = "sev3"

    enriched["sla"] = {
        "phase": "phase-5-sla",
        "age_hours": age_hours,
        "state": sla_state,
        "warning_after_hours": warning_after_hours,
        "critical_after_hours": critical_after_hours,
        "escalation_level": escalation_level,
    }
    return enriched


def _phase5_sla_summary(
    warning_after_hours: int = _PHASE5_SLA_WARNING_HOURS,
    critical_after_hours: int = _PHASE5_SLA_CRITICAL_HOURS,
    limit: int = 100,
) -> dict[str, Any]:
    records = [_phase5_enrich_checkpoint_sla(item, warning_after_hours, critical_after_hours) for item in _to_checkpoint_latest_records()[:limit]]
    pending = [item for item in records if str(item.get("status") or "").strip().lower() == "pending"]
    warning = [item for item in pending if item.get("sla", {}).get("state") == "warning"]
    critical = [item for item in pending if item.get("sla", {}).get("state") == "critical"]
    escalated = [item for item in pending if item.get("sla", {}).get("escalation_level") in {"sev2", "sev3"}]

    alerts: list[str] = []
    if critical:
        alerts.append(f"{len(critical)} checkpoint(s) pendente(s) em estado critical")
    if warning:
        alerts.append(f"{len(warning)} checkpoint(s) pendente(s) em estado warning")
    if not alerts:
        alerts.append("Nenhum checkpoint pendente fora do SLA")

    return {
        "phase": "phase-5-sla-summary",
        "thresholds": {
            "warning_after_hours": warning_after_hours,
            "critical_after_hours": critical_after_hours,
        },
        "counts": {
            "total": len(records),
            "pending": len(pending),
            "warning": len(warning),
            "critical": len(critical),
            "escalated": len(escalated),
        },
        "alerts": alerts,
        "items": records,
    }


def _phase5_escalate_pending_checkpoints(
    warning_after_hours: int = _PHASE5_SLA_WARNING_HOURS,
    critical_after_hours: int = _PHASE5_SLA_CRITICAL_HOURS,
    limit: int = 50,
) -> dict[str, Any]:
    records = _phase5_sla_summary(warning_after_hours, critical_after_hours, limit=200).get("items", [])
    escalated_items: list[dict[str, Any]] = []

    for item in records:
        if len(escalated_items) >= limit:
            break
        if str(item.get("status") or "").strip().lower() != "pending":
            continue
        escalation_level = str(item.get("sla", {}).get("escalation_level") or "none")
        if escalation_level == "none":
            continue

        note = (
            f"Escalonamento automatico {escalation_level} por SLA {item.get('sla', {}).get('state')} "
            f"com idade {item.get('sla', {}).get('age_hours')}h"
        )
        escalated_entry = {
            "checkpoint_id": item.get("checkpoint_id"),
            "status": "escalated",
            "decision": "escalate",
            "note": note,
            "objective": item.get("objective"),
            "task_type": item.get("task_type"),
            "risk": item.get("risk", {}),
            "sla": item.get("sla", {}),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _append_checkpoint(escalated_entry)
        escalated_items.append(escalated_entry)

    return {
        "phase": "phase-5-sla-escalation",
        "thresholds": {
            "warning_after_hours": warning_after_hours,
            "critical_after_hours": critical_after_hours,
        },
        "count": len(escalated_items),
        "items": escalated_items,
    }


def _phase6_build_operational_queues(
    warning_after_hours: int = _PHASE5_SLA_WARNING_HOURS,
    critical_after_hours: int = _PHASE5_SLA_CRITICAL_HOURS,
    limit: int = 100,
) -> dict[str, Any]:
    records = [_phase5_enrich_checkpoint_sla(item, warning_after_hours, critical_after_hours) for item in _to_checkpoint_latest_records()[:limit]]

    risk_queue: list[dict[str, Any]] = []
    sla_queue: list[dict[str, Any]] = []
    compliance_queue: list[dict[str, Any]] = []

    for item in records:
        status = str(item.get("status") or "").strip().lower()
        risk = item.get("risk") or {}
        quality = item.get("quality") or {}
        risk_level = str(risk.get("risk_level") or "unknown").strip().lower()
        sla_state = str(item.get("sla", {}).get("state") or "healthy").strip().lower()
        quality_pass = bool(quality.get("pass", True))
        missing_sections = list(quality.get("missing_sections") or [])

        if status in {"pending", "escalated"} and risk_level in {"high", "critical"}:
            queue_item = dict(item)
            queue_item["queue"] = "risk"
            queue_item["queue_reason"] = f"risk_level={risk_level}"
            queue_item["queue_priority"] = _PHASE6_QUEUE_POLICIES["risk"]["escalation_levels"].get(risk_level, "sev4")
            risk_queue.append(queue_item)

        if status == "pending" and sla_state in {"warning", "critical"}:
            queue_item = dict(item)
            queue_item["queue"] = "sla"
            queue_item["queue_reason"] = f"sla_state={sla_state}"
            queue_item["queue_priority"] = _PHASE6_QUEUE_POLICIES["sla"]["escalation_levels"].get(sla_state, "sev4")
            sla_queue.append(queue_item)

        if status in {"pending", "escalated", "rejected"} and (not quality_pass or missing_sections):
            compliance_state = "critical" if len(missing_sections) >= 3 else "warning"
            queue_item = dict(item)
            queue_item["queue"] = "compliance"
            queue_item["queue_reason"] = "missing_required_sections" if missing_sections else "quality_gate_failed"
            queue_item["queue_priority"] = _PHASE6_QUEUE_POLICIES["compliance"]["escalation_levels"].get(compliance_state, "sev4")
            queue_item["compliance_state"] = compliance_state
            compliance_queue.append(queue_item)

    alerts: list[str] = []
    if risk_queue:
        alerts.append(f"{len(risk_queue)} item(ns) na fila de risco")
    if sla_queue:
        alerts.append(f"{len(sla_queue)} item(ns) na fila de SLA")
    if compliance_queue:
        alerts.append(f"{len(compliance_queue)} item(ns) na fila de compliance")
    if not alerts:
        alerts.append("Nenhuma fila operacional requer acao")

    return {
        "phase": "phase-6-operations-dashboard",
        "thresholds": {
            "warning_after_hours": warning_after_hours,
            "critical_after_hours": critical_after_hours,
        },
        "policies": _PHASE6_QUEUE_POLICIES,
        "counts": {
            "risk": len(risk_queue),
            "sla": len(sla_queue),
            "compliance": len(compliance_queue),
            "total_records": len(records),
        },
        "alerts": alerts,
        "queues": {
            "risk": risk_queue,
            "sla": sla_queue,
            "compliance": compliance_queue,
        },
    }


def _phase6_escalate_operational_queues(
    queues: list[str],
    warning_after_hours: int = _PHASE5_SLA_WARNING_HOURS,
    critical_after_hours: int = _PHASE5_SLA_CRITICAL_HOURS,
    limit: int = 50,
) -> dict[str, Any]:
    normalized_queues = [str(queue).strip().lower() for queue in queues if str(queue).strip()]
    if not normalized_queues:
        normalized_queues = ["risk", "sla", "compliance"]

    dashboard = _phase6_build_operational_queues(warning_after_hours, critical_after_hours, limit=200)
    escalated_items: list[dict[str, Any]] = []

    for queue_name in normalized_queues:
        queue_items = list((dashboard.get("queues") or {}).get(queue_name, []))
        for item in queue_items:
            if len(escalated_items) >= limit:
                break
            escalation_entry = {
                "checkpoint_id": item.get("checkpoint_id"),
                "status": "escalated",
                "decision": f"escalate-{queue_name}",
                "note": f"Escalonamento fase 6 da fila {queue_name}: {item.get('queue_reason')}",
                "objective": item.get("objective"),
                "task_type": item.get("task_type"),
                "risk": item.get("risk", {}),
                "sla": item.get("sla", {}),
                "queue": queue_name,
                "queue_priority": item.get("queue_priority"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            _append_checkpoint(escalation_entry)
            escalated_items.append(escalation_entry)
        if len(escalated_items) >= limit:
            break

    return {
        "phase": "phase-6-operations-escalation",
        "queues": normalized_queues,
        "thresholds": {
            "warning_after_hours": warning_after_hours,
            "critical_after_hours": critical_after_hours,
        },
        "count": len(escalated_items),
        "items": escalated_items,
    }


def _phase7_checkpoint_event_signature(entry: dict[str, Any]) -> str:
    return "|".join(
        [
            str(entry.get("checkpoint_id") or ""),
            str(entry.get("status") or ""),
            str(entry.get("decision") or ""),
            str(entry.get("queue") or ""),
            str(entry.get("queue_priority") or ""),
            str(entry.get("note") or ""),
        ]
    )


def _phase7_collect_deduplicated_events(limit: int = 5000) -> dict[str, Any]:
    raw_items = _load_checkpoints()[-limit:]
    annotated: list[tuple[str, Optional[datetime], dict[str, Any], str]] = []
    for item in raw_items:
        signature = _phase7_checkpoint_event_signature(item)
        timestamp = _parse_checkpoint_timestamp(item.get("updated_at") or item.get("created_at"))
        timestamp_key = (timestamp.isoformat() if timestamp else "")
        annotated.append((signature, timestamp, item, timestamp_key))

    annotated.sort(key=lambda row: row[3], reverse=True)

    seen: set[str] = set()
    deduplicated: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for signature, _timestamp, item, _key in annotated:
        if signature in seen:
            duplicates.append(item)
            continue
        seen.add(signature)
        deduplicated.append(item)

    deduplicated.reverse()
    duplicates.reverse()
    return {
        "kept": deduplicated,
        "duplicates": duplicates,
        "input_count": len(raw_items),
        "kept_count": len(deduplicated),
        "duplicate_count": len(duplicates),
    }


def _phase7_classify_event_queue(entry: dict[str, Any]) -> str:
    explicit_queue = str(entry.get("queue") or "").strip().lower()
    if explicit_queue in {"risk", "sla", "compliance"}:
        return explicit_queue

    status = str(entry.get("status") or "").strip().lower()
    risk = entry.get("risk") or {}
    quality = entry.get("quality") or {}
    sla = entry.get("sla") or {}
    risk_level = str(risk.get("risk_level") or "").strip().lower()
    sla_state = str(sla.get("state") or "").strip().lower()
    missing_sections = list(quality.get("missing_sections") or [])
    quality_pass = bool(quality.get("pass", True))

    if status in {"pending", "escalated"} and risk_level in {"high", "critical"}:
        return "risk"
    if status == "pending" and sla_state in {"warning", "critical"}:
        return "sla"
    if status in {"pending", "escalated", "rejected"} and (missing_sections or not quality_pass):
        return "compliance"
    return ""


def _phase7_apply_checkpoint_dedup(limit: int = 5000, apply_changes: bool = False) -> dict[str, Any]:
    result = _phase7_collect_deduplicated_events(limit=limit)
    if apply_changes:
        lines = [json.dumps(item, ensure_ascii=False) for item in result["kept"]]
        output = "\n".join(lines)
        if output:
            output += "\n"
        _CHECKPOINT_FILE.write_text(output, encoding="utf-8")
    return {
        "phase": "phase-7-deduplication",
        "applied": apply_changes,
        "input_count": result["input_count"],
        "kept_count": result["kept_count"],
        "duplicate_count": result["duplicate_count"],
        "sample_duplicates": result["duplicates"][:10],
    }


def _phase7_build_executive_dashboard(
    days: int = _PHASE7_DEFAULT_DAYS,
    warning_after_hours: int = _PHASE5_SLA_WARNING_HOURS,
    critical_after_hours: int = _PHASE5_SLA_CRITICAL_HOURS,
    limit: int = 200,
) -> dict[str, Any]:
    if days < 1:
        days = 1
    dashboard = _phase6_build_operational_queues(warning_after_hours, critical_after_hours, limit=limit)
    dedup = _phase7_collect_deduplicated_events(limit=max(limit * 5, 500))
    latest_records = [_phase5_enrich_checkpoint_sla(item, warning_after_hours, critical_after_hours) for item in _to_checkpoint_latest_records()[:limit]]

    cutoff = datetime.now() - timedelta(days=days)
    timeline: dict[str, dict[str, int]] = {}
    queue_history: dict[str, list[dict[str, Any]]] = {"risk": [], "sla": [], "compliance": []}

    dedup_events = list(dedup["kept"])
    for event in dedup_events:
        ts = _parse_checkpoint_timestamp(event.get("updated_at") or event.get("created_at"))
        if ts is None or ts < cutoff:
            continue
        bucket = ts.strftime("%Y-%m-%d")
        day_row = timeline.setdefault(bucket, {"risk": 0, "sla": 0, "compliance": 0, "events": 0})
        queue_name = _phase7_classify_event_queue(event)
        if queue_name in {"risk", "sla", "compliance"}:
            day_row[queue_name] += 1
        day_row["events"] += 1

    sorted_days = sorted(timeline.keys())
    for queue_name in ["risk", "sla", "compliance"]:
        for day in sorted_days:
            row = timeline.get(day, {})
            queue_history[queue_name].append({"date": day, "count": int(row.get(queue_name, 0))})

    open_by_queue = dashboard.get("counts", {})
    summary_metrics = {
        "open_risk": int(open_by_queue.get("risk", 0)),
        "open_sla": int(open_by_queue.get("sla", 0)),
        "open_compliance": int(open_by_queue.get("compliance", 0)),
        "tracked_records": len(latest_records),
        "deduplicated_events": dedup["kept_count"],
        "duplicate_events_removed": dedup["duplicate_count"],
    }

    alerts: list[str] = list(dashboard.get("alerts", []))
    if dedup["duplicate_count"]:
        alerts.append(f"{dedup['duplicate_count']} evento(s) duplicado(s) identificado(s) no historico")

    return {
        "phase": "phase-7-executive-dashboard",
        "window_days": days,
        "summary": summary_metrics,
        "alerts": alerts,
        "timeline": [{"date": day, **timeline[day]} for day in sorted_days],
        "queue_history": queue_history,
        "operations": dashboard,
        "deduplication": {
            "input_count": dedup["input_count"],
            "kept_count": dedup["kept_count"],
            "duplicate_count": dedup["duplicate_count"],
        },
    }


def _ensure_reports_storage() -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not _REPORTS_INDEX_FILE.exists():
        _REPORTS_INDEX_FILE.write_text("[]\n", encoding="utf-8")
    if not _REPORTS_SCHEDULES_FILE.exists():
        _REPORTS_SCHEDULES_FILE.write_text("[]\n", encoding="utf-8")
    if not _REPORTS_POLICIES_FILE.exists():
        _REPORTS_POLICIES_FILE.write_text("[]\n", encoding="utf-8")


def _load_reports_index() -> list[dict[str, Any]]:
    _ensure_reports_storage()
    try:
        payload = json.loads(_REPORTS_INDEX_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    except Exception:
        pass
    return []


def _save_reports_index(entries: list[dict[str, Any]]) -> None:
    _ensure_reports_storage()
    _REPORTS_INDEX_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_report_schedules() -> list[dict[str, Any]]:
    _ensure_reports_storage()
    try:
        payload = json.loads(_REPORTS_SCHEDULES_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    except Exception:
        pass
    return []


def _save_report_schedules(entries: list[dict[str, Any]]) -> None:
    _ensure_reports_storage()
    _REPORTS_SCHEDULES_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_scoped_policies() -> list[dict[str, Any]]:
    _ensure_reports_storage()
    try:
        payload = json.loads(_REPORTS_POLICIES_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    except Exception:
        pass
    return []


def _save_scoped_policies(entries: list[dict[str, Any]]) -> None:
    _ensure_reports_storage()
    _REPORTS_POLICIES_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _phase11_normalize_scope_value(value: Optional[str], default: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip()).strip("._").lower()
    return normalized or default


def _phase11_scope_context(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, str]:
    return {
        "environment": _phase11_normalize_scope_value(environment, _PHASE10_DEFAULT_ENVIRONMENT),
        "tenant_id": _phase11_normalize_scope_value(tenant_id, _PHASE11_DEFAULT_TENANT_ID),
        "project_scope": _phase11_normalize_scope_value(project_scope, _PHASE11_DEFAULT_PROJECT_SCOPE),
    }


def _phase11_entry_scope(entry: dict[str, Any]) -> dict[str, str]:
    return _phase11_scope_context(
        environment=str(entry.get("environment") or _PHASE10_DEFAULT_ENVIRONMENT),
        tenant_id=str(entry.get("tenant_id") or _PHASE11_DEFAULT_TENANT_ID),
        project_scope=str(entry.get("project_scope") or _PHASE11_DEFAULT_PROJECT_SCOPE),
    )


def _phase11_matches_scope(
    entry: dict[str, Any],
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> bool:
    entry_scope = _phase11_entry_scope(entry)
    requested_environment = _phase11_normalize_scope_value(environment, "") if environment is not None else None
    requested_tenant = _phase11_normalize_scope_value(tenant_id, "") if tenant_id is not None else None
    requested_project = _phase11_normalize_scope_value(project_scope, "") if project_scope is not None else None
    if requested_environment is not None and entry_scope["environment"] != requested_environment:
        return False
    if requested_tenant is not None and entry_scope["tenant_id"] != requested_tenant:
        return False
    if requested_project is not None and entry_scope["project_scope"] != requested_project:
        return False
    return True


def _phase11_scope_dir(environment: str, tenant_id: str, project_scope: str) -> Path:
    scope = _phase11_scope_context(environment=environment, tenant_id=tenant_id, project_scope=project_scope)
    return _REPORTS_SCOPED_DIR / scope["tenant_id"] / scope["project_scope"] / scope["environment"]


def _phase11_exports_dir(environment: str, tenant_id: str, project_scope: str) -> Path:
    target = _phase11_scope_dir(environment, tenant_id, project_scope) / "exports"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _phase11_snapshot_dir(snapshot_id: str, environment: str, tenant_id: str, project_scope: str) -> Path:
    target = _phase11_scope_dir(environment, tenant_id, project_scope) / "snapshots" / snapshot_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _phase11_schedule_identity(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    scope = _phase11_entry_scope(entry)
    return (
        str(entry.get("schedule_id") or "").strip(),
        scope["tenant_id"],
        scope["project_scope"],
        scope["environment"],
    )


def _phase13_policy_identity(entry: dict[str, Any]) -> tuple[str, str, str]:
    scope = _phase11_entry_scope(entry)
    return (
        scope["tenant_id"],
        scope["project_scope"],
        scope["environment"],
    )


def _phase13_normalize_policy(payload: dict[str, Any]) -> dict[str, Any]:
    required_formats = []
    for item in list(payload.get("required_formats") or []):
        fmt = str(item).strip().lower()
        if not fmt:
            continue
        _phase8_report_extension(fmt)
        if fmt not in required_formats:
            required_formats.append(fmt)

    return {
        **_phase11_scope_context(
            environment=str(payload.get("environment") or _PHASE10_DEFAULT_ENVIRONMENT),
            tenant_id=str(payload.get("tenant_id") or _PHASE11_DEFAULT_TENANT_ID),
            project_scope=str(payload.get("project_scope") or _PHASE11_DEFAULT_PROJECT_SCOPE),
        ),
        "enabled": bool(payload.get("enabled", True)),
        "min_recent_snapshots": int(payload.get("min_recent_snapshots") or 0),
        "max_snapshot_age_hours": int(payload.get("max_snapshot_age_hours") or 72),
        "max_due_schedules": int(payload.get("max_due_schedules") or 0),
        "required_formats": required_formats,
        "note": (str(payload.get("note") or "").strip() or None),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _phase13_upsert_policy(request: AssistantScopedPolicyRequest) -> dict[str, Any]:
    policy = _phase13_normalize_policy(request.model_dump())
    policies = _load_scoped_policies()
    existing = next((item for item in policies if _phase13_policy_identity(item) == _phase13_policy_identity(policy)), None)
    if existing:
        policy["created_at"] = existing.get("created_at") or datetime.now().isoformat(timespec="seconds")
    else:
        policy["created_at"] = datetime.now().isoformat(timespec="seconds")

    policies = [item for item in policies if _phase13_policy_identity(item) != _phase13_policy_identity(policy)]
    policies.append(policy)
    policies.sort(key=lambda item: "|".join(_phase13_policy_identity(item)))
    _save_scoped_policies(policies)
    return {"phase": "phase-13-scoped-policy", "policy": policy}


def _phase13_list_policies(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, Any]:
    policies = _load_scoped_policies()
    normalized_environment = (environment or "").strip().lower()
    normalized_tenant = (tenant_id or "").strip().lower()
    normalized_project = (project_scope or "").strip().lower()
    policies = [
        item
        for item in policies
        if _phase11_matches_scope(
            item,
            environment=normalized_environment or None,
            tenant_id=normalized_tenant or None,
            project_scope=normalized_project or None,
        )
    ]
    policies.sort(key=lambda item: "|".join(_phase13_policy_identity(item)))
    return {
        "phase": "phase-13-scoped-policy-list",
        "environment": normalized_environment or None,
        "tenant_id": normalized_tenant or None,
        "project_scope": normalized_project or None,
        "count": len(policies),
        "items": policies,
    }


def _phase13_find_policy(scope: dict[str, str]) -> Optional[dict[str, Any]]:
    for item in _load_scoped_policies():
        if _phase13_policy_identity(item) == (scope["tenant_id"], scope["project_scope"], scope["environment"]):
            return item
    return None


def _phase13_evaluate_scoped_policy(policy: Optional[dict[str, Any]], dashboard: dict[str, Any]) -> dict[str, Any]:
    if not policy:
        return {
            "phase": "phase-13-scoped-policy-evaluation",
            "policy_found": False,
            "policy_enabled": False,
            "pass": True,
            "checks": [],
            "recommendations": ["Nenhuma politica cadastrada para o escopo; operacao em modo observabilidade."],
        }

    if not bool(policy.get("enabled", True)):
        return {
            "phase": "phase-13-scoped-policy-evaluation",
            "policy_found": True,
            "policy_enabled": False,
            "pass": True,
            "checks": [],
            "policy": policy,
            "recommendations": ["Politica encontrada, mas desabilitada para este escopo."],
        }

    summary = dashboard.get("summary") or {}
    latest_snapshot_at = _parse_checkpoint_timestamp(dashboard.get("latest_snapshot_at"))
    latest_snapshot_age_hours = None
    if latest_snapshot_at is not None:
        latest_snapshot_age_hours = round(max((datetime.now() - latest_snapshot_at).total_seconds(), 0.0) / 3600.0, 2)

    recent_snapshots = int(summary.get("recent_snapshots", 0))
    due_schedules = int(summary.get("due_schedules", 0))
    required_formats = list(policy.get("required_formats") or [])
    latest_formats = list(((dashboard.get("operations") or {}).get("recent_snapshots") or [{}])[0].get("formats") or [])

    checks = []
    checks.append(
        {
            "check": "min_recent_snapshots",
            "expected": int(policy.get("min_recent_snapshots", 0)),
            "actual": recent_snapshots,
            "pass": recent_snapshots >= int(policy.get("min_recent_snapshots", 0)),
        }
    )
    checks.append(
        {
            "check": "max_due_schedules",
            "expected": int(policy.get("max_due_schedules", 0)),
            "actual": due_schedules,
            "pass": due_schedules <= int(policy.get("max_due_schedules", 0)),
        }
    )

    age_limit = int(policy.get("max_snapshot_age_hours", 72))
    checks.append(
        {
            "check": "max_snapshot_age_hours",
            "expected": age_limit,
            "actual": latest_snapshot_age_hours,
            "pass": latest_snapshot_age_hours is not None and latest_snapshot_age_hours <= age_limit,
        }
    )

    if required_formats:
        checks.append(
            {
                "check": "required_formats",
                "expected": required_formats,
                "actual": latest_formats,
                "pass": set(required_formats).issubset(set(str(item).strip().lower() for item in latest_formats)),
            }
        )

    passed = all(bool(item.get("pass")) for item in checks)
    recommendations: list[str] = []
    if not passed:
        for item in checks:
            if item.get("pass"):
                continue
            if item.get("check") == "min_recent_snapshots":
                recommendations.append("Aumente a frequencia de snapshots ou execute um run forcado para recuperar cobertura.")
            elif item.get("check") == "max_due_schedules":
                recommendations.append("Execute agendas vencidas e ajuste a janela de execucao para reduzir backlog.")
            elif item.get("check") == "max_snapshot_age_hours":
                recommendations.append("Gerar snapshot imediato para reduzir idade do ultimo reporte no escopo.")
            elif item.get("check") == "required_formats":
                recommendations.append("Inclua os formatos obrigatorios na agenda para manter contrato de entrega.")

    if not recommendations:
        recommendations.append("Politica em conformidade para o escopo informado.")

    return {
        "phase": "phase-13-scoped-policy-evaluation",
        "policy_found": True,
        "policy_enabled": True,
        "pass": passed,
        "policy": policy,
        "checks": checks,
        "recommendations": recommendations,
    }


def _load_audit_logs() -> list[dict[str, Any]]:
    if not _REPORTS_AUDIT_LOGS_FILE.exists():
        return []
    try:
        logs = []
        with open(_REPORTS_AUDIT_LOGS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                logs.append(json.loads(line))
        return logs
    except Exception as exc:
        raise RuntimeError(f"Falha ao ler {_REPORTS_AUDIT_LOGS_FILE}: {exc}") from exc


def _save_audit_log(entry: dict[str, Any]) -> None:
    _REPORTS_AUDIT_LOGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_REPORTS_AUDIT_LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        raise RuntimeError(f"Falha ao salvar audit log em {_REPORTS_AUDIT_LOGS_FILE}: {exc}") from exc


def _phase14_record_audit_log(request: AssistantAuditLogRequest, result: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "operation": str(request.operation).strip().lower(),
        "environment": _phase11_normalize_scope_value(request.environment, _PHASE10_DEFAULT_ENVIRONMENT),
        "tenant_id": _phase11_normalize_scope_value(request.tenant_id, _PHASE11_DEFAULT_TENANT_ID),
        "project_scope": _phase11_normalize_scope_value(request.project_scope, _PHASE11_DEFAULT_PROJECT_SCOPE),
        "status": str(request.status).strip().lower(),
        "details": dict(request.details or {}),
        "error_message": (str(request.error_message or "").strip() or None),
        "result_summary": None,
    }
    
    if result:
        entry["result_summary"] = {
            "has_result": True,
            "result_keys": list(result.keys()) if isinstance(result, dict) else None,
        }
    
    _save_audit_log(entry)
    return {
        "phase": "phase-14-audit-log-recorded",
        "timestamp": entry["timestamp"],
        "operation": entry["operation"],
        "scope": {
            "tenant_id": entry["tenant_id"],
            "project_scope": entry["project_scope"],
            "environment": entry["environment"],
        },
        "status": entry["status"],
    }


def _phase14_list_audit_logs(
    operation: Optional[str] = None,
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 7,
    limit: int = 200,
) -> dict[str, Any]:
    logs = _load_audit_logs()
    
    normalized_op = (operation or "").strip().lower() or None
    normalized_env = (environment or "").strip().lower() or None
    normalized_tenant = (tenant_id or "").strip().lower() or None
    normalized_project = (project_scope or "").strip().lower() or None
    normalized_status = (status or "").strip().lower() or None
    
    cutoff_time = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    
    filtered = []
    for entry in logs:
        if entry.get("timestamp", "") < cutoff_time:
            continue
        if normalized_op and entry.get("operation") != normalized_op:
            continue
        if normalized_env and entry.get("environment") != normalized_env:
            continue
        if normalized_tenant and entry.get("tenant_id") != normalized_tenant:
            continue
        if normalized_project and entry.get("project_scope") != normalized_project:
            continue
        if normalized_status and entry.get("status") != normalized_status:
            continue
        filtered.append(entry)
    
    filtered.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    filtered = filtered[:int(limit)]
    
    return {
        "phase": "phase-14-audit-log-list",
        "filters": {
            "operation": normalized_op,
            "environment": normalized_env,
            "tenant_id": normalized_tenant,
            "project_scope": normalized_project,
            "status": normalized_status,
            "days": days,
            "limit": limit,
        },
        "count": len(filtered),
        "items": filtered,
    }


def _phase14_audit_summary_by_operation(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
    days: int = 7,
) -> dict[str, Any]:
    logs = _load_audit_logs()
    
    normalized_env = (environment or "").strip().lower() or None
    normalized_tenant = (tenant_id or "").strip().lower() or None
    normalized_project = (project_scope or "").strip().lower() or None
    
    cutoff_time = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    
    summary_by_op: dict[str, dict[str, int]] = {}
    for entry in logs:
        if entry.get("timestamp", "") < cutoff_time:
            continue
        if normalized_env and entry.get("environment") != normalized_env:
            continue
        if normalized_tenant and entry.get("tenant_id") != normalized_tenant:
            continue
        if normalized_project and entry.get("project_scope") != normalized_project:
            continue
        
        op = entry.get("operation", "unknown")
        status = entry.get("status", "unknown")
        
        if op not in summary_by_op:
            summary_by_op[op] = {"success": 0, "failure": 0, "warning": 0, "total": 0}
        
        summary_by_op[op][status] = summary_by_op[op].get(status, 0) + 1
        summary_by_op[op]["total"] = summary_by_op[op].get("total", 0) + 1
    
    return {
        "phase": "phase-14-audit-summary",
        "scope": {
            "environment": normalized_env,
            "tenant_id": normalized_tenant,
            "project_scope": normalized_project,
        },
        "days": days,
        "summary_by_operation": summary_by_op,
        "total_logs": len(logs),
    }


def _phase15_enforce_scoped_policy(request: AssistantPolicyEnforcementRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")

    scope = _phase11_scope_context(
        environment=request.environment,
        tenant_id=request.tenant_id,
        project_scope=request.project_scope,
    )
    policy = _phase13_find_policy(scope)

    dashboard_before = _phase12_build_scoped_dashboard(
        days=request.days,
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
        environment=scope["environment"],
        tenant_id=scope["tenant_id"],
        project_scope=scope["project_scope"],
    )
    evaluation_before = _phase13_evaluate_scoped_policy(policy, dashboard_before)

    actions: list[dict[str, Any]] = []
    should_enforce = bool(
        evaluation_before.get("policy_found")
        and evaluation_before.get("policy_enabled")
        and not evaluation_before.get("pass")
    )

    if should_enforce and request.run_due_schedules_on_fail:
        if request.dry_run:
            actions.append({"action": "run_due_schedules", "planned": True})
        else:
            runner_result = _phase10_run_due_schedules(
                environment=scope["environment"],
                tenant_id=scope["tenant_id"],
                project_scope=scope["project_scope"],
                force=True,
                limit=1,
            )
            actions.append(
                {
                    "action": "run_due_schedules",
                    "planned": False,
                    "executed_count": int(runner_result.get("count") or 0),
                    "result": runner_result,
                }
            )

    if should_enforce and request.create_snapshot_on_fail:
        if request.dry_run:
            actions.append({"action": "create_snapshot", "planned": True, "formats": request.snapshot_formats})
        else:
            snapshot_result = _phase9_publish_snapshot(
                formats=request.snapshot_formats,
                days=request.days,
                warning_after_hours=request.warning_after_hours,
                critical_after_hours=request.critical_after_hours,
                limit=request.limit,
                snapshot_name=f"policy_enforce_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                environment=scope["environment"],
                tenant_id=scope["tenant_id"],
                project_scope=scope["project_scope"],
                schedule_id="policy_enforce",
            )
            actions.append(
                {
                    "action": "create_snapshot",
                    "planned": False,
                    "formats": request.snapshot_formats,
                    "snapshot_id": ((snapshot_result.get("snapshot") or {}).get("snapshot_id")),
                    "result": snapshot_result,
                }
            )

    dashboard_after = dashboard_before
    evaluation_after = evaluation_before
    if should_enforce and not request.dry_run and actions:
        dashboard_after = _phase12_build_scoped_dashboard(
            days=request.days,
            warning_after_hours=request.warning_after_hours,
            critical_after_hours=request.critical_after_hours,
            limit=request.limit,
            environment=scope["environment"],
            tenant_id=scope["tenant_id"],
            project_scope=scope["project_scope"],
        )
        evaluation_after = _phase13_evaluate_scoped_policy(policy, dashboard_after)

    result = {
        "phase": "phase-15-policy-enforcement",
        "scope": scope,
        "dry_run": bool(request.dry_run),
        "policy": policy,
        "evaluation_before": evaluation_before,
        "actions": actions,
        "evaluation_after": evaluation_after,
        "enforcement_executed": bool(should_enforce and actions),
        "compliance_restored": bool((not evaluation_before.get("pass")) and evaluation_after.get("pass")),
    }

    audit_status = "success" if bool(evaluation_after.get("pass")) else "warning"
    _phase14_record_audit_log(
        AssistantAuditLogRequest(
            operation="policy_enforce",
            environment=scope["environment"],
            tenant_id=scope["tenant_id"],
            project_scope=scope["project_scope"],
            status=audit_status,
            details={
                "dry_run": bool(request.dry_run),
                "actions_count": len(actions),
                "pass_before": bool(evaluation_before.get("pass")),
                "pass_after": bool(evaluation_after.get("pass")),
            },
        ),
        result=result,
    )
    return result


def _load_incidents() -> list[dict[str, Any]]:
    if not _REPORTS_INCIDENTS_FILE.exists():
        return []
    try:
        incidents = []
        with open(_REPORTS_INCIDENTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                incidents.append(json.loads(line))
        return incidents
    except Exception as exc:
        raise RuntimeError(f"Falha ao ler {_REPORTS_INCIDENTS_FILE}: {exc}") from exc


def _save_incident(entry: dict[str, Any]) -> None:
    _REPORTS_INCIDENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_REPORTS_INCIDENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        raise RuntimeError(f"Falha ao salvar incidente em {_REPORTS_INCIDENTS_FILE}: {exc}") from exc


def _save_incidents(entries: list[dict[str, Any]]) -> None:
    _REPORTS_INCIDENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_REPORTS_INCIDENTS_FILE, "w", encoding="utf-8") as f:
            for item in entries:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception as exc:
        raise RuntimeError(f"Falha ao salvar incidentes em {_REPORTS_INCIDENTS_FILE}: {exc}") from exc


def _phase16_open_incident(
    scope: dict[str, str],
    enforcement_result: dict[str, Any],
    severity: str,
    owner: Optional[str],
    channel: str,
) -> dict[str, Any]:
    incident_id = f"inc_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 100000}"
    incident = {
        "incident_id": incident_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "open",
        "severity": severity,
        "channel": (channel or "operations").strip().lower() or "operations",
        "owner": (str(owner or "").strip() or None),
        "scope": scope,
        "phase": "phase-16-policy-escalation",
        "source": "policy_enforcement",
        "policy_pass_before": bool((enforcement_result.get("evaluation_before") or {}).get("pass")),
        "policy_pass_after": bool((enforcement_result.get("evaluation_after") or {}).get("pass")),
        "actions": [str(item.get("action") or "") for item in list(enforcement_result.get("actions") or [])],
        "recommendations": list(((enforcement_result.get("evaluation_after") or {}).get("recommendations") or [])),
        "fingerprint_key": _phase19_incident_fingerprint(
            scope,
            dict(enforcement_result.get("evaluation_after") or {}),
            severity,
        ),
    }
    _save_incident(incident)
    return incident


def _phase19_incident_fingerprint(scope: dict[str, str], evaluation: dict[str, Any], severity: str) -> str:
    failed_checks = [
        str(item.get("check") or "").strip().lower()
        for item in list(evaluation.get("checks") or [])
        if not bool(item.get("pass"))
    ]
    failed_checks = sorted([item for item in failed_checks if item])
    checks_token = ",".join(failed_checks) if failed_checks else "none"
    return "|".join(
        [
            scope.get("tenant_id", ""),
            scope.get("project_scope", ""),
            scope.get("environment", ""),
            str(severity or "sev2").strip().lower(),
            checks_token,
        ]
    )


def _phase19_match_recent_incident(
    scope: dict[str, str],
    fingerprint_key: str,
    window_hours: int,
    status: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    incidents = _load_incidents()
    cutoff = datetime.now() - timedelta(hours=max(int(window_hours), 1))
    target_status = (status or "").strip().lower() or None
    matches: list[dict[str, Any]] = []

    for item in incidents:
        item_scope = dict(item.get("scope") or {})
        if (
            str(item_scope.get("environment") or "").strip().lower() != scope["environment"]
            or str(item_scope.get("tenant_id") or "").strip().lower() != scope["tenant_id"]
            or str(item_scope.get("project_scope") or "").strip().lower() != scope["project_scope"]
        ):
            continue
        if str(item.get("fingerprint_key") or "").strip().lower() != str(fingerprint_key).strip().lower():
            continue
        if target_status and str(item.get("status") or "").strip().lower() != target_status:
            continue
        ts = _parse_checkpoint_timestamp(item.get("reopened_at") or item.get("created_at"))
        if ts is None or ts < cutoff:
            continue
        matches.append(item)

    if not matches:
        return None
    matches.sort(key=lambda item: str(item.get("reopened_at") or item.get("created_at") or ""), reverse=True)
    return matches[0]


def _phase19_deduplicate_incidents(request: AssistantIncidentDedupRequest) -> dict[str, Any]:
    incidents = _load_incidents()
    normalized_env = (request.environment or "").strip().lower() or None
    normalized_tenant = (request.tenant_id or "").strip().lower() or None
    normalized_project = (request.project_scope or "").strip().lower() or None
    cutoff_days = datetime.now() - timedelta(days=int(request.days))
    cutoff_window = datetime.now() - timedelta(hours=int(request.window_hours))

    candidates: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
    for idx, item in enumerate(incidents[: int(request.limit)]):
        if str(item.get("status") or "").strip().lower() != "open":
            continue
        scope = dict(item.get("scope") or {})
        env = str(scope.get("environment") or "").strip().lower()
        ten = str(scope.get("tenant_id") or "").strip().lower()
        proj = str(scope.get("project_scope") or "").strip().lower()
        if normalized_env and env != normalized_env:
            continue
        if normalized_tenant and ten != normalized_tenant:
            continue
        if normalized_project and proj != normalized_project:
            continue
        created_at = _parse_checkpoint_timestamp(item.get("created_at"))
        if created_at is None or created_at < cutoff_days or created_at < cutoff_window:
            continue
        fingerprint = str(item.get("fingerprint_key") or "").strip().lower()
        if not fingerprint:
            continue
        key = (env, ten, proj, fingerprint)
        candidates.setdefault(key, []).append((idx, item))

    duplicates: list[dict[str, Any]] = []
    for group in candidates.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda pair: str((pair[1]).get("reopened_at") or (pair[1]).get("created_at") or ""), reverse=True)
        keeper = group[0][1]
        for idx, item in group[1:]:
            duplicates.append(
                {
                    "index": idx,
                    "incident_id": item.get("incident_id"),
                    "deduplicated_to": keeper.get("incident_id"),
                    "scope": item.get("scope"),
                    "fingerprint_key": item.get("fingerprint_key"),
                }
            )

    if request.apply_changes and duplicates:
        now_iso = datetime.now().isoformat(timespec="seconds")
        for dup in duplicates:
            idx = int(dup["index"])
            incidents[idx]["status"] = "deduplicated"
            incidents[idx]["deduplicated_at"] = now_iso
            incidents[idx]["deduplicated_by"] = "phase-19-incident-dedup"
            incidents[idx]["deduplicated_to"] = dup["deduplicated_to"]
        _save_incidents(incidents)

    return {
        "phase": "phase-19-incident-deduplication",
        "applied": bool(request.apply_changes),
        "filters": {
            "environment": normalized_env,
            "tenant_id": normalized_tenant,
            "project_scope": normalized_project,
            "days": int(request.days),
            "window_hours": int(request.window_hours),
            "limit": int(request.limit),
        },
        "duplicates_count": len(duplicates),
        "duplicates": duplicates[:100],
    }


def _phase18_reopen_latest_closed_incident(
    scope: dict[str, str],
    severity: str,
    owner: Optional[str],
    channel: str,
    reopen_note: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[dict[str, Any]]:
    incidents = _load_incidents()
    latest_idx: Optional[int] = None
    latest_closed_at = ""
    for idx, item in enumerate(incidents):
        item_scope = dict(item.get("scope") or {})
        if (
            str(item_scope.get("environment") or "").strip().lower() != scope["environment"]
            or str(item_scope.get("tenant_id") or "").strip().lower() != scope["tenant_id"]
            or str(item_scope.get("project_scope") or "").strip().lower() != scope["project_scope"]
        ):
            continue
        if str(item.get("status") or "").strip().lower() != "closed":
            continue
        closed_at = str(item.get("closed_at") or item.get("created_at") or "")
        if closed_at >= latest_closed_at:
            latest_closed_at = closed_at
            latest_idx = idx

    if latest_idx is None:
        return None

    target = dict(incidents[latest_idx])
    if dry_run:
        return {
            "incident_id": target.get("incident_id"),
            "status": "open",
            "reopened": True,
            "dry_run": True,
        }

    target["status"] = "open"
    target["reopened_at"] = datetime.now().isoformat(timespec="seconds")
    target["reopened_by"] = "phase-18-regression-reopen"
    target["reopen_note"] = (str(reopen_note or "").strip() or "regressao detectada no escopo")
    target["severity"] = severity
    target["owner"] = (str(owner or "").strip() or target.get("owner") or None)
    target["channel"] = (str(channel or "").strip().lower() or target.get("channel") or "operations")
    target["phase"] = "phase-18-incident-regression-reopen"
    target["last_transition"] = "closed_to_open"
    incidents[latest_idx] = target
    _save_incidents(incidents)
    return target


def _phase18_reopen_on_regression(request: AssistantIncidentReopenRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")

    severity = str(request.severity_on_reopen or "sev2").strip().lower()
    if severity not in {"sev1", "sev2", "sev3", "sev4"}:
        raise HTTPException(status_code=400, detail="severity_on_reopen invalido: use sev1|sev2|sev3|sev4")

    scope = _phase11_scope_context(
        environment=request.environment,
        tenant_id=request.tenant_id,
        project_scope=request.project_scope,
    )
    dashboard = _phase12_build_scoped_dashboard(
        days=request.days,
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
        environment=scope["environment"],
        tenant_id=scope["tenant_id"],
        project_scope=scope["project_scope"],
    )
    policy = _phase13_find_policy(scope)
    evaluation = _phase13_evaluate_scoped_policy(policy, dashboard)
    pass_now = bool(evaluation.get("pass"))
    should_reopen = bool(request.force_reopen or not pass_now)
    fingerprint_key = _phase19_incident_fingerprint(scope, evaluation, severity)

    reopened = None
    deduplicated = False
    if should_reopen:
        if bool(request.dedup_enabled):
            existing_open = _phase19_match_recent_incident(
                scope=scope,
                fingerprint_key=fingerprint_key,
                window_hours=int(request.dedup_window_hours),
                status="open",
            )
            if existing_open is not None:
                reopened = existing_open
                deduplicated = True

        if reopened is None:
            reopened = _phase18_reopen_latest_closed_incident(
                scope=scope,
                severity=severity,
                owner=request.owner,
                channel=request.channel,
                reopen_note=request.reopen_note,
                dry_run=bool(request.dry_run),
            )

    if should_reopen and reopened is None and not request.dry_run:
        reopened = _phase16_open_incident(
            scope=scope,
            enforcement_result={"evaluation_before": evaluation, "evaluation_after": evaluation, "actions": []},
            severity=severity,
            owner=request.owner,
            channel=request.channel,
        )

    _phase14_record_audit_log(
        AssistantAuditLogRequest(
            operation="policy_escalate",
            environment=scope["environment"],
            tenant_id=scope["tenant_id"],
            project_scope=scope["project_scope"],
            status="warning" if should_reopen else "success",
            details={
                "phase": "phase-18",
                "pass_now": pass_now,
                "force_reopen": bool(request.force_reopen),
                "should_reopen": should_reopen,
                "reopened": bool(reopened),
                "deduplicated": deduplicated,
                "severity": severity,
                "fingerprint_key": fingerprint_key,
            },
        ),
        result={"evaluation_phase": evaluation.get("phase"), "incident_id": (reopened or {}).get("incident_id")},
    )

    return {
        "phase": "phase-18-incident-regression-reopen",
        "scope": scope,
        "dry_run": bool(request.dry_run),
        "policy": policy,
        "evaluation": evaluation,
        "pass_now": pass_now,
        "should_reopen": should_reopen,
        "deduplicated": deduplicated,
        "fingerprint_key": fingerprint_key,
        "incident": reopened,
        "incident_reopened": bool(reopened),
    }


def _phase16_enforce_and_escalate(request: AssistantPolicyEscalationRequest) -> dict[str, Any]:
    severity = str(request.severity_on_fail or "sev2").strip().lower()
    if severity not in {"sev1", "sev2", "sev3", "sev4"}:
        raise HTTPException(status_code=400, detail="severity_on_fail invalido: use sev1|sev2|sev3|sev4")

    enforcement = _phase15_enforce_scoped_policy(
        AssistantPolicyEnforcementRequest(
            days=request.days,
            warning_after_hours=request.warning_after_hours,
            critical_after_hours=request.critical_after_hours,
            limit=request.limit,
            environment=request.environment,
            tenant_id=request.tenant_id,
            project_scope=request.project_scope,
            run_due_schedules_on_fail=bool(request.run_due_schedules_on_fail),
            create_snapshot_on_fail=bool(request.create_snapshot_on_fail),
            snapshot_formats=list(request.snapshot_formats or ["markdown", "json"]),
            dry_run=bool(request.dry_run),
        )
    )

    scope = dict(enforcement.get("scope") or {})
    pass_after = bool((enforcement.get("evaluation_after") or {}).get("pass"))
    should_escalate = bool(request.force_incident or not pass_after)
    fingerprint_key = _phase19_incident_fingerprint(scope, dict(enforcement.get("evaluation_after") or {}), severity)

    incident = None
    incident_reopened = False
    incident_created = False
    incident_deduplicated = False
    if should_escalate and not request.dry_run:
        dedup_enabled = bool(request.dedup_enabled)
        if dedup_enabled:
            existing_open = _phase19_match_recent_incident(
                scope=scope,
                fingerprint_key=fingerprint_key,
                window_hours=int(request.dedup_window_hours),
                status="open",
            )
            if existing_open is not None:
                incident = existing_open
                incident_deduplicated = True

        if incident is None:
            incident = _phase18_reopen_latest_closed_incident(
                scope=scope,
                severity=severity,
                owner=request.owner,
                channel=request.channel,
                reopen_note="regressao detectada apos fase de conformidade",
                dry_run=False,
            )
            incident_reopened = bool(incident)
            if incident is not None:
                incident["fingerprint_key"] = fingerprint_key

        if incident is None:
            incident = _phase16_open_incident(
                scope=scope,
                enforcement_result=enforcement,
                severity=severity,
                owner=request.owner,
                channel=request.channel,
            )
            incident_created = bool(incident)

    closure_result = {
        "phase": "phase-17-incident-auto-closure",
        "scope": scope,
        "dry_run": bool(request.dry_run),
        "closed_count": 0,
        "closed_incident_ids": [],
    }
    if pass_after:
        closure_result = _phase17_close_open_incidents_for_scope(
            scope=scope,
            close_note="conformidade restaurada apos enforcement",
            dry_run=bool(request.dry_run),
        )

    _phase14_record_audit_log(
        AssistantAuditLogRequest(
            operation="policy_escalate",
            environment=scope.get("environment") or request.environment,
            tenant_id=scope.get("tenant_id") or request.tenant_id,
            project_scope=scope.get("project_scope") or request.project_scope,
            status="warning" if should_escalate else "success",
            details={
                "dry_run": bool(request.dry_run),
                "pass_after": pass_after,
                "force_incident": bool(request.force_incident),
                "incident_created": incident_created,
                "incident_reopened": incident_reopened,
                "incident_deduplicated": incident_deduplicated,
                "severity": severity,
                "fingerprint_key": fingerprint_key,
                "auto_closed_incidents": int(closure_result.get("closed_count") or 0),
            },
        ),
        result={"enforcement_phase": enforcement.get("phase"), "incident_id": (incident or {}).get("incident_id")},
    )

    return {
        "phase": "phase-16-policy-escalation",
        "scope": scope,
        "dry_run": bool(request.dry_run),
        "severity_on_fail": severity,
        "enforcement": enforcement,
        "escalation_triggered": should_escalate,
        "incident_created": incident_created,
        "incident_reopened": incident_reopened,
        "incident_deduplicated": incident_deduplicated,
        "fingerprint_key": fingerprint_key,
        "incident": incident,
        "auto_closure": closure_result,
    }


def _phase16_list_incidents(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    days: int = 7,
    limit: int = 200,
) -> dict[str, Any]:
    incidents = _load_incidents()
    normalized_env = (environment or "").strip().lower() or None
    normalized_tenant = (tenant_id or "").strip().lower() or None
    normalized_project = (project_scope or "").strip().lower() or None
    normalized_status = (status or "").strip().lower() or None
    normalized_severity = (severity or "").strip().lower() or None
    cutoff_time = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    filtered = []
    for item in incidents:
        if str(item.get("created_at") or "") < cutoff_time:
            continue
        scope = dict(item.get("scope") or {})
        if normalized_env and str(scope.get("environment") or "").strip().lower() != normalized_env:
            continue
        if normalized_tenant and str(scope.get("tenant_id") or "").strip().lower() != normalized_tenant:
            continue
        if normalized_project and str(scope.get("project_scope") or "").strip().lower() != normalized_project:
            continue
        if normalized_status and str(item.get("status") or "").strip().lower() != normalized_status:
            continue
        if normalized_severity and str(item.get("severity") or "").strip().lower() != normalized_severity:
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    filtered = filtered[:int(limit)]
    return {
        "phase": "phase-16-incidents-list",
        "count": len(filtered),
        "filters": {
            "environment": normalized_env,
            "tenant_id": normalized_tenant,
            "project_scope": normalized_project,
            "status": normalized_status,
            "severity": normalized_severity,
            "days": int(days),
            "limit": int(limit),
        },
        "items": filtered,
    }


def _phase16_incidents_summary(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
    days: int = 7,
) -> dict[str, Any]:
    listed = _phase16_list_incidents(
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
        status=None,
        severity=None,
        days=days,
        limit=5000,
    )
    items = list(listed.get("items") or [])
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for item in items:
        sev = str(item.get("severity") or "unknown").strip().lower()
        st = str(item.get("status") or "unknown").strip().lower()
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_status[st] = by_status.get(st, 0) + 1

    return {
        "phase": "phase-16-incidents-summary",
        "scope": {
            "environment": (environment or "").strip().lower() or None,
            "tenant_id": (tenant_id or "").strip().lower() or None,
            "project_scope": (project_scope or "").strip().lower() or None,
        },
        "days": int(days),
        "total": len(items),
        "by_severity": by_severity,
        "by_status": by_status,
    }


def _phase20_impact_tier(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 40:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def _phase20_correlate_incidents(request: AssistantIncidentCorrelationRequest) -> dict[str, Any]:
    listed = _phase16_list_incidents(
        environment=request.environment,
        tenant_id=request.tenant_id,
        project_scope=request.project_scope,
        status=request.status,
        severity=None,
        days=request.days,
        limit=request.limit,
    )
    items = list(listed.get("items") or [])

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        scope = dict(item.get("scope") or {})
        tenant = str(scope.get("tenant_id") or "unknown").strip().lower() or "unknown"
        env = str(scope.get("environment") or "unknown").strip().lower() or "unknown"
        key = (tenant, env)
        group = groups.get(key)
        if group is None:
            group = {
                "tenant_id": tenant,
                "environment": env,
                "incidents_count": 0,
                "severity_breakdown": {},
                "projects": set(),
                "fingerprints": set(),
                "incident_ids": [],
                "latest_at": "",
                "impact_score": 0,
                "impact_tier": "low",
                "top_causes": [],
            }
            groups[key] = group

        sev = str(item.get("severity") or "unknown").strip().lower() or "unknown"
        project = str(scope.get("project_scope") or "unknown").strip().lower() or "unknown"
        fingerprint = str(item.get("fingerprint_key") or "").strip().lower()
        ts = str(item.get("reopened_at") or item.get("created_at") or "")

        group["incidents_count"] = int(group.get("incidents_count", 0)) + 1
        sev_break = dict(group.get("severity_breakdown") or {})
        sev_break[sev] = int(sev_break.get(sev, 0)) + 1
        group["severity_breakdown"] = sev_break
        group["projects"].add(project)
        if fingerprint:
            group["fingerprints"].add(fingerprint)
        group["incident_ids"].append(str(item.get("incident_id") or ""))
        if ts >= str(group.get("latest_at") or ""):
            group["latest_at"] = ts

    output_groups: list[dict[str, Any]] = []
    for group in groups.values():
        sev_break = dict(group.get("severity_breakdown") or {})
        severity_score = 0
        for sev, count in sev_break.items():
            severity_score += int(_PHASE20_SEVERITY_WEIGHTS.get(str(sev), _PHASE20_SEVERITY_WEIGHTS["unknown"])) * int(count)
        scope_spread = len(group.get("projects") or set()) * 3
        cause_spread = len(group.get("fingerprints") or set()) * 2
        impact_score = severity_score + scope_spread + cause_spread
        impact_tier = _phase20_impact_tier(impact_score)

        top_causes = sorted(
            list(group.get("fingerprints") or set()),
            key=lambda item: len(item),
            reverse=True,
        )[:3]

        output_groups.append(
            {
                "tenant_id": group["tenant_id"],
                "environment": group["environment"],
                "incidents_count": group["incidents_count"],
                "severity_breakdown": sev_break,
                "projects_count": len(group.get("projects") or set()),
                "projects": sorted(list(group.get("projects") or set()))[:20],
                "fingerprints_count": len(group.get("fingerprints") or set()),
                "top_causes": top_causes,
                "impact_score": impact_score,
                "impact_tier": impact_tier,
                "latest_at": group.get("latest_at"),
                "incident_ids": list(group.get("incident_ids") or [])[:30],
            }
        )

    output_groups.sort(
        key=lambda item: (int(item.get("impact_score") or 0), int(item.get("incidents_count") or 0), str(item.get("latest_at") or "")),
        reverse=True,
    )

    return {
        "phase": "phase-20-incident-correlation",
        "filters": {
            "environment": (request.environment or "").strip().lower() or None,
            "tenant_id": (request.tenant_id or "").strip().lower() or None,
            "project_scope": (request.project_scope or "").strip().lower() or None,
            "status": (request.status or "").strip().lower() or None,
            "days": int(request.days),
            "limit": int(request.limit),
        },
        "groups_count": len(output_groups),
        "groups": output_groups,
    }


def _phase20_correlation_impact_summary(request: AssistantIncidentCorrelationRequest) -> dict[str, Any]:
    correlated = _phase20_correlate_incidents(request)
    groups = list(correlated.get("groups") or [])
    by_tier: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    total_impact = 0
    total_incidents = 0
    for group in groups:
        tier = str(group.get("impact_tier") or "low")
        by_tier[tier] = int(by_tier.get(tier, 0)) + 1
        total_impact += int(group.get("impact_score") or 0)
        total_incidents += int(group.get("incidents_count") or 0)

    top_groups = groups[:5]
    recommendations: list[str] = []
    if by_tier.get("critical", 0) > 0:
        recommendations.append("Escalone imediatamente os grupos criticos com runbook de contingencia e owner dedicado.")
    if by_tier.get("high", 0) > 0:
        recommendations.append("Priorize os grupos high por impacto agregado e consolide planos por tenant/ambiente.")
    if not recommendations:
        recommendations.append("Sem grupos criticos/high; manter monitoramento continuo e revisao diaria de tendencia.")

    return {
        "phase": "phase-20-correlation-impact-summary",
        "filters": correlated.get("filters"),
        "groups_count": len(groups),
        "total_incidents": total_incidents,
        "total_impact_score": total_impact,
        "impact_tiers": by_tier,
        "top_groups": top_groups,
        "recommendations": recommendations,
    }


def _phase17_close_open_incidents_for_scope(
    scope: dict[str, str],
    close_note: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    incidents = _load_incidents()
    closed_ids: list[str] = []
    updated: list[dict[str, Any]] = []
    now_iso = datetime.now().isoformat(timespec="seconds")
    note = (str(close_note or "").strip() or "scope voltou para conformidade")

    for item in incidents:
        mutable = dict(item)
        item_scope = dict(mutable.get("scope") or {})
        matches_scope = (
            str(item_scope.get("environment") or "").strip().lower() == scope["environment"]
            and str(item_scope.get("tenant_id") or "").strip().lower() == scope["tenant_id"]
            and str(item_scope.get("project_scope") or "").strip().lower() == scope["project_scope"]
        )
        is_open = str(mutable.get("status") or "").strip().lower() == "open"
        if matches_scope and is_open:
            closed_ids.append(str(mutable.get("incident_id") or ""))
            if not dry_run:
                mutable["status"] = "closed"
                mutable["closed_at"] = now_iso
                mutable["closed_by"] = "phase-17-auto-closure"
                mutable["close_note"] = note
        updated.append(mutable)

    if closed_ids and not dry_run:
        _save_incidents(updated)

    return {
        "phase": "phase-17-incident-auto-closure",
        "scope": scope,
        "dry_run": bool(dry_run),
        "closed_count": len(closed_ids),
        "closed_incident_ids": closed_ids,
    }


def _phase17_close_resolved_incidents(request: AssistantIncidentResolveRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")

    scope = _phase11_scope_context(
        environment=request.environment,
        tenant_id=request.tenant_id,
        project_scope=request.project_scope,
    )
    dashboard = _phase12_build_scoped_dashboard(
        days=request.days,
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
        environment=scope["environment"],
        tenant_id=scope["tenant_id"],
        project_scope=scope["project_scope"],
    )
    policy = _phase13_find_policy(scope)
    evaluation = _phase13_evaluate_scoped_policy(policy, dashboard)
    pass_now = bool(evaluation.get("pass"))
    should_close = bool(request.force_close or pass_now)

    closure = {
        "phase": "phase-17-incident-auto-closure",
        "scope": scope,
        "dry_run": bool(request.dry_run),
        "closed_count": 0,
        "closed_incident_ids": [],
    }
    if should_close:
        closure = _phase17_close_open_incidents_for_scope(
            scope=scope,
            close_note=request.close_note,
            dry_run=bool(request.dry_run),
        )

    _phase14_record_audit_log(
        AssistantAuditLogRequest(
            operation="policy_escalate",
            environment=scope["environment"],
            tenant_id=scope["tenant_id"],
            project_scope=scope["project_scope"],
            status="success" if closure.get("closed_count", 0) > 0 else "warning",
            details={
                "phase": "phase-17",
                "pass_now": pass_now,
                "force_close": bool(request.force_close),
                "should_close": should_close,
                "closed_count": int(closure.get("closed_count") or 0),
            },
        ),
        result={"evaluation_phase": evaluation.get("phase"), "closure_phase": closure.get("phase")},
    )

    return {
        "phase": "phase-17-resolved-incident-closure",
        "scope": scope,
        "policy": policy,
        "evaluation": evaluation,
        "pass_now": pass_now,
        "should_close": should_close,
        "closure": closure,
    }


def _phase11_resolve_snapshot_dir(entry: dict[str, Any]) -> Optional[Path]:
    snapshot_dir = str(entry.get("snapshot_dir") or "").strip()
    if snapshot_dir:
        return Path(snapshot_dir)
    snapshot_id = str(entry.get("snapshot_id") or "").strip()
    if not snapshot_id:
        return None
    scope = _phase11_entry_scope(entry)
    scoped_candidate = _phase11_scope_dir(scope["environment"], scope["tenant_id"], scope["project_scope"]) / "snapshots" / snapshot_id
    if scoped_candidate.exists():
        return scoped_candidate
    legacy_candidate = _REPORTS_DIR / snapshot_id
    if legacy_candidate.exists():
        return legacy_candidate
    return scoped_candidate


def _phase12_build_scoped_dashboard(
    days: int,
    warning_after_hours: int,
    critical_after_hours: int,
    limit: int,
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, Any]:
    if days < 1:
        days = 1

    normalized_environment = (environment or "").strip().lower() or None
    normalized_tenant = (tenant_id or "").strip().lower() or None
    normalized_project = (project_scope or "").strip().lower() or None

    snapshots = [
        entry
        for entry in _load_reports_index()
        if _phase11_matches_scope(
            entry,
            environment=normalized_environment,
            tenant_id=normalized_tenant,
            project_scope=normalized_project,
        )
    ]
    schedules = [
        entry
        for entry in _load_report_schedules()
        if _phase11_matches_scope(
            entry,
            environment=normalized_environment,
            tenant_id=normalized_tenant,
            project_scope=normalized_project,
        )
    ]

    snapshots.sort(key=lambda item: str(item.get("generated_at") or ""), reverse=True)
    schedules.sort(key=lambda item: str(item.get("schedule_id") or ""))

    now = datetime.now()
    cutoff = now - timedelta(days=days)
    timeline: dict[str, dict[str, int]] = {}
    recent_snapshots = 0
    for entry in snapshots:
        ts = _parse_checkpoint_timestamp(entry.get("generated_at"))
        if ts is None or ts < cutoff:
            continue
        recent_snapshots += 1
        bucket = ts.strftime("%Y-%m-%d")
        row = timeline.setdefault(bucket, {"risk": 0, "sla": 0, "compliance": 0, "events": 0})
        row["events"] += 1

    due_schedules = 0
    enabled_schedules = 0
    for schedule in schedules:
        if bool(schedule.get("enabled", True)):
            enabled_schedules += 1
        next_run = _phase10_compute_next_run(schedule, now=now)
        if bool(schedule.get("enabled", True)) and next_run and next_run <= now:
            due_schedules += 1

    alerts: list[str] = []
    if not snapshots:
        alerts.append("Nenhum snapshot encontrado para o escopo informado")
    if enabled_schedules and due_schedules:
        alerts.append(f"{due_schedules} agenda(s) vencida(s) no escopo")
    if not alerts:
        alerts.append("Escopo monitorado sem alertas criticos")

    unique_tenants = {str(item.get("tenant_id") or "").strip().lower() for item in snapshots + schedules if str(item.get("tenant_id") or "").strip()}
    unique_projects = {str(item.get("project_scope") or "").strip().lower() for item in snapshots + schedules if str(item.get("project_scope") or "").strip()}
    unique_environments = {str(item.get("environment") or "").strip().lower() for item in snapshots + schedules if str(item.get("environment") or "").strip()}

    latest_snapshot_at = None
    if snapshots:
        latest_snapshot_at = snapshots[0].get("generated_at")

    summary_metrics = {
        "open_risk": 0,
        "open_sla": 0,
        "open_compliance": 0,
        "tracked_records": len(snapshots),
        "deduplicated_events": 0,
        "duplicate_events_removed": 0,
        "snapshot_count": len(snapshots),
        "recent_snapshots": recent_snapshots,
        "schedule_count": len(schedules),
        "enabled_schedules": enabled_schedules,
        "due_schedules": due_schedules,
    }

    scope_info = {
        "environment": normalized_environment,
        "tenant_id": normalized_tenant,
        "project_scope": normalized_project,
        "coverage": {
            "tenants": len(unique_tenants),
            "projects": len(unique_projects),
            "environments": len(unique_environments),
        },
    }

    sorted_days = sorted(timeline.keys())
    queue_history = {
        "risk": [{"date": day, "count": int(timeline[day].get("risk", 0))} for day in sorted_days],
        "sla": [{"date": day, "count": int(timeline[day].get("sla", 0))} for day in sorted_days],
        "compliance": [{"date": day, "count": int(timeline[day].get("compliance", 0))} for day in sorted_days],
    }

    return {
        "phase": "phase-12-scoped-executive-dashboard",
        "window_days": days,
        "scope": scope_info,
        "summary": summary_metrics,
        "latest_snapshot_at": latest_snapshot_at,
        "alerts": alerts,
        "timeline": [{"date": day, **timeline[day]} for day in sorted_days],
        "queue_history": queue_history,
        "operations": {
            "phase": "phase-12-scoped-operations",
            "thresholds": {
                "warning_after_hours": warning_after_hours,
                "critical_after_hours": critical_after_hours,
            },
            "counts": {
                "risk": 0,
                "sla": 0,
                "compliance": 0,
                "total_records": len(snapshots),
                "schedules": len(schedules),
                "due_schedules": due_schedules,
            },
            "queues": {"risk": [], "sla": [], "compliance": []},
            "recent_snapshots": snapshots[: max(1, min(limit, 50))],
            "schedules": schedules[: max(1, min(limit, 50))],
            "alerts": alerts,
        },
        "deduplication": {
            "input_count": len(snapshots),
            "kept_count": len(snapshots),
            "duplicate_count": 0,
        },
    }


def _phase8_recommendations(executive: dict[str, Any]) -> list[str]:
    summary = executive.get("summary") or {}
    operations = executive.get("operations") or {}
    alerts = list(executive.get("alerts") or [])

    recommendations: list[str] = []
    if int(summary.get("open_risk", 0)) > 0:
        recommendations.append("Priorize revisao manual dos itens de risco alto antes de novas automacoes sensiveis.")
    if int(summary.get("open_compliance", 0)) > 0:
        recommendations.append("Corrija contratos e secoes obrigatorias ausentes para reduzir reincidencia na fila de compliance.")
    if int(summary.get("open_sla", 0)) > 0:
        recommendations.append("Ataque primeiro os itens fora do SLA para evitar envelhecimento operacional adicional.")
    if int((executive.get("deduplication") or {}).get("duplicate_count", 0)) > 0:
        recommendations.append("Aplique deduplicacao do historico antes de consolidar relatorios executivos oficiais.")
    if not recommendations and alerts:
        recommendations.append("Ambiente operacional estavel; mantenha monitoramento e revise apenas os alertas ativos.")
    if not recommendations:
        recommendations.append("Nenhuma acao imediata identificada; siga monitorando filas e tendencias historicas.")
    if int((operations.get("counts") or {}).get("risk", 0)) > int((operations.get("counts") or {}).get("compliance", 0)):
        recommendations.append("Risco domina o backlog atual; concentre aprovacoes e controles em tarefas de impacto alto.")
    return recommendations[:5]


def _phase8_render_markdown_report(payload: dict[str, Any]) -> str:
    executive = payload.get("executive_dashboard") or {}
    summary = executive.get("summary") or {}
    operations = executive.get("operations") or {}
    timeline = list(executive.get("timeline") or [])
    recommendations = list(payload.get("recommendations") or [])
    alerts = list(executive.get("alerts") or [])

    lines = [
        "# Relatorio Executivo Operacional",
        "",
        f"Janela analisada: {executive.get('window_days', 0)} dia(s)",
        "",
        "## Resumo",
        "",
        f"- Risco aberto: {summary.get('open_risk', 0)}",
        f"- SLA aberto: {summary.get('open_sla', 0)}",
        f"- Compliance aberto: {summary.get('open_compliance', 0)}",
        f"- Registros rastreados: {summary.get('tracked_records', 0)}",
        f"- Eventos deduplicados: {summary.get('deduplicated_events', 0)}",
        f"- Duplicatas removiveis: {summary.get('duplicate_events_removed', 0)}",
        "",
        "## Alertas",
        "",
    ]
    for alert in alerts or ["Nenhum alerta ativo"]:
        lines.append(f"- {alert}")

    lines.extend(["", "## Recomendacoes", ""])
    for item in recommendations:
        lines.append(f"- {item}")

    lines.extend(["", "## Filas", ""])
    counts = operations.get("counts") or {}
    lines.append(f"- risk: {counts.get('risk', 0)}")
    lines.append(f"- sla: {counts.get('sla', 0)}")
    lines.append(f"- compliance: {counts.get('compliance', 0)}")

    lines.extend(["", "## Tendencias", "", "| Data | Risk | SLA | Compliance | Events |", "|---|---:|---:|---:|---:|"])
    for row in timeline:
        lines.append(
            f"| {row.get('date', '')} | {row.get('risk', 0)} | {row.get('sla', 0)} | {row.get('compliance', 0)} | {row.get('events', 0)} |"
        )

    return "\n".join(lines).strip() + "\n"


def _phase8_render_csv_report(payload: dict[str, Any]) -> str:
    executive = payload.get("executive_dashboard") or {}
    summary = executive.get("summary") or {}
    operations = executive.get("operations") or {}
    timeline = list(executive.get("timeline") or [])
    recommendations = list(payload.get("recommendations") or [])
    alerts = list(executive.get("alerts") or [])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "key", "value", "extra"])
    for key, value in summary.items():
        writer.writerow(["summary", key, value, ""])
    for key, value in (operations.get("counts") or {}).items():
        writer.writerow(["queue_counts", key, value, ""])
    for item in alerts:
        writer.writerow(["alerts", "alert", item, ""])
    for item in recommendations:
        writer.writerow(["recommendations", "item", item, ""])
    for row in timeline:
        writer.writerow(["timeline", row.get("date", ""), row.get("events", 0), json.dumps(row, ensure_ascii=False)])
    return buffer.getvalue()


def _phase8_render_report_content(payload: dict[str, Any], report_format: str) -> str:
    normalized = report_format.strip().lower()
    if normalized == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if normalized == "csv":
        return _phase8_render_csv_report(payload)
    if normalized == "markdown":
        return _phase8_render_markdown_report(payload)
    raise HTTPException(status_code=400, detail="report_format invalido: use json, csv ou markdown")


def _phase8_build_report_payload(
    days: int,
    warning_after_hours: int,
    critical_after_hours: int,
    limit: int,
    report_format: str,
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, Any]:
    use_scoped_dashboard = bool((environment or "").strip() or (tenant_id or "").strip() or (project_scope or "").strip())
    if use_scoped_dashboard:
        executive = _phase12_build_scoped_dashboard(
            days=days,
            warning_after_hours=warning_after_hours,
            critical_after_hours=critical_after_hours,
            limit=limit,
            environment=environment,
            tenant_id=tenant_id,
            project_scope=project_scope,
        )
    else:
        executive = _phase7_build_executive_dashboard(days, warning_after_hours, critical_after_hours, limit)
    return {
        "phase": "phase-8-executive-report",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "report_format": report_format.strip().lower(),
        "executive_dashboard": executive,
        "recommendations": _phase8_recommendations(executive),
    }


def _phase8_report_extension(report_format: str) -> str:
    extension_map = {"json": "json", "csv": "csv", "markdown": "md"}
    normalized = report_format.strip().lower()
    if normalized not in extension_map:
        raise HTTPException(status_code=400, detail="report_format invalido: use json, csv ou markdown")
    return extension_map[normalized]


def _phase8_safe_report_name(base_name: str, extension: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", (base_name or "").strip()).strip("._") or "executive_report"
    if not safe_name.lower().endswith(f".{extension}"):
        safe_name = f"{safe_name}.{extension}"
    return safe_name


def _phase8_export_executive_report(
    report_format: str,
    days: int,
    warning_after_hours: int,
    critical_after_hours: int,
    limit: int,
    persist: bool,
    output_name: Optional[str] = None,
    environment: str = _PHASE10_DEFAULT_ENVIRONMENT,
    tenant_id: str = _PHASE11_DEFAULT_TENANT_ID,
    project_scope: str = _PHASE11_DEFAULT_PROJECT_SCOPE,
) -> dict[str, Any]:
    payload = _phase8_build_report_payload(
        days,
        warning_after_hours,
        critical_after_hours,
        limit,
        report_format,
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
    )
    content = _phase8_render_report_content(payload, payload["report_format"])
    scope = _phase11_scope_context(environment=environment, tenant_id=tenant_id, project_scope=project_scope)

    file_path = None
    if persist:
        _ensure_reports_storage()
        extension = _phase8_report_extension(payload["report_format"])
        base_name = (output_name or f"executive_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}").strip()
        safe_name = _phase8_safe_report_name(base_name, extension)
        target = _phase11_exports_dir(scope["environment"], scope["tenant_id"], scope["project_scope"]) / safe_name
        target.write_text(content, encoding="utf-8")
        file_path = str(target)

    return {
        "phase": "phase-8-executive-report",
        "format": payload["report_format"],
        "persisted": persist,
        "file_path": file_path,
        "scope": scope,
        "content": content,
        "recommendations": payload["recommendations"],
        "summary": (payload.get("executive_dashboard") or {}).get("summary", {}),
    }


def _phase9_publish_snapshot(
    formats: list[str],
    days: int,
    warning_after_hours: int,
    critical_after_hours: int,
    limit: int,
    snapshot_name: Optional[str] = None,
    environment: str = _PHASE10_DEFAULT_ENVIRONMENT,
    tenant_id: str = _PHASE11_DEFAULT_TENANT_ID,
    project_scope: str = _PHASE11_DEFAULT_PROJECT_SCOPE,
    schedule_id: Optional[str] = None,
) -> dict[str, Any]:
    normalized_formats = []
    for report_format in formats or _PHASE9_DEFAULT_SNAPSHOT_FORMATS:
        fmt = str(report_format).strip().lower()
        if fmt and fmt not in normalized_formats:
            normalized_formats.append(fmt)
    if not normalized_formats:
        normalized_formats = list(_PHASE9_DEFAULT_SNAPSHOT_FORMATS)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", (snapshot_name or f"snapshot_{timestamp}").strip()).strip("._") or f"snapshot_{timestamp}"
    _ensure_reports_storage()
    scope = _phase11_scope_context(environment=environment, tenant_id=tenant_id, project_scope=project_scope)
    snapshot_dir = _phase11_snapshot_dir(snapshot_id, scope["environment"], scope["tenant_id"], scope["project_scope"])

    exported_files: list[dict[str, Any]] = []
    for report_format in normalized_formats:
        payload = _phase8_build_report_payload(
            days,
            warning_after_hours,
            critical_after_hours,
            limit,
            report_format,
            environment=scope["environment"],
            tenant_id=scope["tenant_id"],
            project_scope=scope["project_scope"],
        )
        content = _phase8_render_report_content(payload, report_format)
        extension = _phase8_report_extension(report_format)
        file_name = _phase8_safe_report_name(f"{snapshot_id}_{report_format}", extension)
        target = snapshot_dir / file_name
        target.write_text(content, encoding="utf-8")
        exported_files.append(
            {
                "format": report_format,
                "path": str(target),
                "size_bytes": len(content.encode("utf-8")),
            }
        )

    manifest = {
        "snapshot_id": snapshot_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "environment": scope["environment"],
        "tenant_id": scope["tenant_id"],
        "project_scope": scope["project_scope"],
        "schedule_id": (schedule_id or "").strip() or None,
        "window_days": days,
        "formats": normalized_formats,
        "snapshot_dir": str(snapshot_dir),
        "files": exported_files,
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    index_entries = _load_reports_index()
    index_entries = [
        entry
        for entry in index_entries
        if not (
            str(entry.get("snapshot_id") or "") == snapshot_id
            and _phase11_matches_scope(
                entry,
                environment=scope["environment"],
                tenant_id=scope["tenant_id"],
                project_scope=scope["project_scope"],
            )
        )
    ]
    index_entries.append(manifest)
    index_entries.sort(key=lambda entry: str(entry.get("generated_at") or ""), reverse=True)
    _save_reports_index(index_entries)

    return {
        "phase": "phase-9-report-snapshot",
        "snapshot": manifest,
        "index_count": len(index_entries),
    }


def _phase9_list_snapshots(
    limit: int = 20,
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, Any]:
    entries = _load_reports_index()
    normalized_environment = (environment or "").strip().lower()
    normalized_tenant = (tenant_id or "").strip().lower()
    normalized_project = (project_scope or "").strip().lower()
    entries = [
        entry
        for entry in entries
        if _phase11_matches_scope(
            entry,
            environment=normalized_environment or None,
            tenant_id=normalized_tenant or None,
            project_scope=normalized_project or None,
        )
    ]
    entries.sort(key=lambda entry: str(entry.get("generated_at") or ""), reverse=True)
    return {
        "phase": "phase-9-report-index",
        "environment": normalized_environment or None,
        "tenant_id": normalized_tenant or None,
        "project_scope": normalized_project or None,
        "count": min(len(entries), limit),
        "items": entries[:limit],
    }


def _phase9_cleanup_snapshots(
    retention_days: int,
    apply_changes: bool,
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, Any]:
    entries = _load_reports_index()
    cutoff = datetime.now() - timedelta(days=retention_days)
    kept_entries: list[dict[str, Any]] = []
    removed_entries: list[dict[str, Any]] = []
    normalized_environment = (environment or "").strip().lower()
    normalized_tenant = (tenant_id or "").strip().lower()
    normalized_project = (project_scope or "").strip().lower()

    for entry in entries:
        ts = _parse_checkpoint_timestamp(entry.get("generated_at"))
        matches_scope = _phase11_matches_scope(
            entry,
            environment=normalized_environment or None,
            tenant_id=normalized_tenant or None,
            project_scope=normalized_project or None,
        )
        if matches_scope and ts is not None and ts < cutoff:
            removed_entries.append(entry)
        else:
            kept_entries.append(entry)

    if apply_changes:
        for entry in removed_entries:
            target = _phase11_resolve_snapshot_dir(entry)
            if target and target.exists() and target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        _save_reports_index(kept_entries)

    return {
        "phase": "phase-9-report-cleanup",
        "applied": apply_changes,
        "environment": normalized_environment or None,
        "tenant_id": normalized_tenant or None,
        "project_scope": normalized_project or None,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "removed_count": len(removed_entries),
        "kept_count": len(kept_entries),
        "removed_snapshots": removed_entries[:20],
    }


def _phase10_normalize_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    schedule_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(payload.get("schedule_id") or "").strip()).strip("._")
    if not schedule_id:
        raise HTTPException(status_code=400, detail="schedule_id invalido")

    cadence = str(payload.get("cadence") or "daily").strip().lower()
    if cadence not in {"hourly", "daily"}:
        raise HTTPException(status_code=400, detail="cadence invalida: use hourly ou daily")

    warning_after_hours = int(payload.get("warning_after_hours") or 4)
    critical_after_hours = int(payload.get("critical_after_hours") or 24)
    if critical_after_hours < warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")

    formats = []
    for item in list(payload.get("formats") or []):
        fmt = str(item).strip().lower()
        if fmt and fmt not in formats:
            _phase8_report_extension(fmt)
            formats.append(fmt)
    if not formats:
        formats = ["markdown", "json"]

    return {
        "schedule_id": schedule_id,
        **_phase11_scope_context(
            environment=str(payload.get("environment") or _PHASE10_DEFAULT_ENVIRONMENT),
            tenant_id=str(payload.get("tenant_id") or _PHASE11_DEFAULT_TENANT_ID),
            project_scope=str(payload.get("project_scope") or _PHASE11_DEFAULT_PROJECT_SCOPE),
        ),
        "enabled": bool(payload.get("enabled", True)),
        "cadence": cadence,
        "interval_hours": int(payload.get("interval_hours") or 24),
        "hour_of_day": int(payload.get("hour_of_day") or 8),
        "formats": formats,
        "retention_days": int(payload.get("retention_days") or 30),
        "days": int(payload.get("days") or 7),
        "warning_after_hours": warning_after_hours,
        "critical_after_hours": critical_after_hours,
        "limit": int(payload.get("limit") or 200),
        "snapshot_prefix": (str(payload.get("snapshot_prefix") or "").strip() or None),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _phase10_compute_next_run(schedule: dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    if not bool(schedule.get("enabled", True)):
        return None
    now = now or datetime.now()
    cadence = str(schedule.get("cadence") or "daily").strip().lower()
    last_run = _parse_checkpoint_timestamp(schedule.get("last_run_at"))
    created_at = _parse_checkpoint_timestamp(schedule.get("created_at")) or now

    if cadence == "hourly":
        interval_hours = max(int(schedule.get("interval_hours") or 24), 1)
        anchor = last_run or created_at
        return anchor + timedelta(hours=interval_hours)

    hour_of_day = int(schedule.get("hour_of_day") or 8)
    base_date = (last_run or now).date()
    candidate = datetime.combine(base_date, datetime.min.time()).replace(hour=hour_of_day)
    if last_run is None:
        if candidate < created_at:
            candidate += timedelta(days=1)
    else:
        if candidate <= last_run:
            candidate += timedelta(days=1)
    return candidate


def _phase10_upsert_schedule(request: AssistantReportScheduleRequest) -> dict[str, Any]:
    schedule = _phase10_normalize_schedule(request.model_dump())
    schedules = _load_report_schedules()
    existing = next((item for item in schedules if _phase11_schedule_identity(item) == _phase11_schedule_identity(schedule)), None)
    if existing:
        schedule["created_at"] = existing.get("created_at") or datetime.now().isoformat(timespec="seconds")
        schedule["last_run_at"] = existing.get("last_run_at")
    else:
        schedule["created_at"] = datetime.now().isoformat(timespec="seconds")
        schedule["last_run_at"] = None

    next_run = _phase10_compute_next_run(schedule)
    schedule["next_run_at"] = next_run.isoformat(timespec="seconds") if next_run else None

    schedules = [item for item in schedules if _phase11_schedule_identity(item) != _phase11_schedule_identity(schedule)]
    schedules.append(schedule)
    schedules.sort(key=lambda item: str(item.get("schedule_id") or ""))
    _save_report_schedules(schedules)
    return {"phase": "phase-10-report-schedule", "schedule": schedule}


def _phase10_list_schedules(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
) -> dict[str, Any]:
    schedules = _load_report_schedules()
    normalized_environment = (environment or "").strip().lower()
    normalized_tenant = (tenant_id or "").strip().lower()
    normalized_project = (project_scope or "").strip().lower()
    schedules = [
        item
        for item in schedules
        if _phase11_matches_scope(
            item,
            environment=normalized_environment or None,
            tenant_id=normalized_tenant or None,
            project_scope=normalized_project or None,
        )
    ]

    now = datetime.now()
    enriched = []
    for item in schedules:
        next_run = _phase10_compute_next_run(item, now=now)
        enriched_item = dict(item)
        enriched_item["next_run_at"] = next_run.isoformat(timespec="seconds") if next_run else None
        enriched_item["due_now"] = bool(next_run and next_run <= now and enriched_item.get("enabled", True))
        enriched.append(enriched_item)

    enriched.sort(key=lambda item: str(item.get("schedule_id") or ""))
    return {
        "phase": "phase-10-report-schedules",
        "environment": normalized_environment or None,
        "tenant_id": normalized_tenant or None,
        "project_scope": normalized_project or None,
        "count": len(enriched),
        "items": enriched,
    }


def _phase10_run_due_schedules(
    environment: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_scope: Optional[str] = None,
    force: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    all_schedules = _load_report_schedules()
    normalized_environment = (environment or "").strip().lower()
    normalized_tenant = (tenant_id or "").strip().lower()
    normalized_project = (project_scope or "").strip().lower()
    schedules = [
        item
        for item in all_schedules
        if _phase11_matches_scope(
            item,
            environment=normalized_environment or None,
            tenant_id=normalized_tenant or None,
            project_scope=normalized_project or None,
        )
    ]

    now = datetime.now()
    executed: list[dict[str, Any]] = []
    updated_schedules: list[dict[str, Any]] = []

    for schedule in schedules:
        next_run = _phase10_compute_next_run(schedule, now=now)
        due_now = bool(force or (next_run and next_run <= now))
        if bool(schedule.get("enabled", True)) and due_now and len(executed) < limit:
            snapshot_prefix = str(schedule.get("snapshot_prefix") or schedule.get("schedule_id") or "snapshot").strip()
            snapshot_name = f"{snapshot_prefix}_{now.strftime('%Y%m%d_%H%M%S')}"
            snapshot_result = _phase9_publish_snapshot(
                formats=list(schedule.get("formats") or _PHASE9_DEFAULT_SNAPSHOT_FORMATS),
                days=int(schedule.get("days") or 7),
                warning_after_hours=int(schedule.get("warning_after_hours") or 4),
                critical_after_hours=int(schedule.get("critical_after_hours") or 24),
                limit=int(schedule.get("limit") or 200),
                snapshot_name=snapshot_name,
                environment=str(schedule.get("environment") or _PHASE10_DEFAULT_ENVIRONMENT),
                tenant_id=str(schedule.get("tenant_id") or _PHASE11_DEFAULT_TENANT_ID),
                project_scope=str(schedule.get("project_scope") or _PHASE11_DEFAULT_PROJECT_SCOPE),
                schedule_id=str(schedule.get("schedule_id") or ""),
            )
            cleanup_result = _phase9_cleanup_snapshots(
                retention_days=int(schedule.get("retention_days") or 30),
                apply_changes=True,
                environment=str(schedule.get("environment") or _PHASE10_DEFAULT_ENVIRONMENT),
                tenant_id=str(schedule.get("tenant_id") or _PHASE11_DEFAULT_TENANT_ID),
                project_scope=str(schedule.get("project_scope") or _PHASE11_DEFAULT_PROJECT_SCOPE),
            )
            schedule["last_run_at"] = now.isoformat(timespec="seconds")
            next_future = _phase10_compute_next_run(schedule, now=now)
            schedule["next_run_at"] = next_future.isoformat(timespec="seconds") if next_future else None
            schedule["updated_at"] = now.isoformat(timespec="seconds")
            executed.append(
                {
                    "schedule_id": schedule.get("schedule_id"),
                    "environment": schedule.get("environment"),
                    "tenant_id": schedule.get("tenant_id"),
                    "project_scope": schedule.get("project_scope"),
                    "snapshot": snapshot_result.get("snapshot"),
                    "cleanup": {
                        "removed_count": cleanup_result.get("removed_count"),
                        "kept_count": cleanup_result.get("kept_count"),
                    },
                }
            )

        updated_schedules.append(schedule)

    processed_keys = {_phase11_schedule_identity(item) for item in schedules}
    untouched = [item for item in all_schedules if _phase11_schedule_identity(item) not in processed_keys]
    updated_schedules = untouched + updated_schedules

    updated_schedules.sort(key=lambda item: str(item.get("schedule_id") or ""))
    _save_report_schedules(updated_schedules)

    return {
        "phase": "phase-10-report-runner",
        "environment": normalized_environment or None,
        "tenant_id": normalized_tenant or None,
        "project_scope": normalized_project or None,
        "force": force,
        "count": len(executed),
        "items": executed,
    }


def _phase4_classify_risk(task_type: str, objective: str, quality_score: float, require_approval: bool) -> dict[str, Any]:
    base_level = _PHASE4_RISK_POLICY.get(task_type, "medium")
    risk_level = base_level

    objective_tokens = _tokenize_text(objective)
    if objective_tokens.intersection(_PHASE4_HIGH_IMPACT_TERMS):
        risk_level = "high"

    if quality_score < 70:
        if risk_level == "low":
            risk_level = "medium"
        elif risk_level == "medium":
            risk_level = "high"
        else:
            risk_level = "critical"

    action = "auto-approve"
    if require_approval:
        action = "require-approval"
    elif risk_level in {"high", "critical"}:
        action = "require-approval"

    if risk_level == "critical":
        action = "block-until-approval"

    return {
        "phase": "phase-4-risk-policy",
        "risk_level": risk_level,
        "base_level": base_level,
        "quality_score": quality_score,
        "action": action,
        "high_impact_detected": bool(objective_tokens.intersection(_PHASE4_HIGH_IMPACT_TERMS)),
    }


def _build_phase3_roles(task_type: str, objective: str) -> dict[str, Any]:
    role_planner = {
        "role": "planner",
        "responsibility": "Definir plano por etapas e riscos",
        "steps": _build_assistant_plan(task_type, objective),
    }
    role_executor = {
        "role": "executor",
        "responsibility": "Executar checagens seguras e coletar evidencias",
    }
    role_verifier = {
        "role": "verifier",
        "responsibility": "Validar contrato, qualidade e confianca",
    }
    return {"planner": role_planner, "executor": role_executor, "verifier": role_verifier}


def _score_memory_entry(query_tokens: set[str], entry: dict[str, Any]) -> float:
    if not query_tokens:
        return 0.0
    entry_tokens = _memory_entry_tokens(entry)
    overlap = len(query_tokens.intersection(entry_tokens))
    if overlap == 0:
        return 0.0
    coverage = overlap / max(len(query_tokens), 1)
    density = overlap / max(len(entry_tokens), 1)
    return round((coverage * 0.75) + (density * 0.25), 4)


def _memory_search(query: str, category: Optional[str] = None, tags: Optional[list[str]] = None, top_k: int = 5) -> dict[str, Any]:
    tags = tags or []
    tag_set = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
    q_tokens = _tokenize_text(query)
    entries = _load_memory_entries()

    scored: list[dict[str, Any]] = []
    for entry in entries:
        if category and str(entry.get("category", "")).lower() != category.lower():
            continue
        entry_tags = {str(tag).strip().lower() for tag in entry.get("tags", [])}
        if tag_set and not tag_set.issubset(entry_tags):
            continue
        score = _score_memory_entry(q_tokens, entry)
        if score <= 0:
            continue
        scored.append({"score": score, "entry": entry})

    scored.sort(key=lambda item: item["score"], reverse=True)
    limited = scored[: max(1, min(int(top_k), 20))]
    return {
        "ok": True,
        "query": query,
        "count": len(limited),
        "results": [
            {
                "score": item["score"],
                "id": item["entry"].get("id"),
                "category": item["entry"].get("category"),
                "title": item["entry"].get("title"),
                "content": item["entry"].get("content"),
                "tags": item["entry"].get("tags", []),
                "source": item["entry"].get("source"),
                "created_at": item["entry"].get("created_at"),
            }
            for item in limited
        ],
    }


def _normalize_task_type(task_type: str) -> str:
    normalized = (task_type or "generic").strip().lower()
    return normalized if normalized in _TASK_CONTRACTS else "generic"


def _get_task_contract(task_type: str) -> dict[str, Any]:
    key = _normalize_task_type(task_type)
    contract = dict(_TASK_CONTRACTS.get(key, _TASK_CONTRACTS["generic"]))
    contract["task_type"] = key
    return contract


def _build_assistant_plan(task_type: str, objective: str) -> list[dict[str, Any]]:
    common_steps = [
        "Mapear contexto e restricoes da tarefa",
        "Executar checagens com ferramentas locais",
        "Consolidar evidencia verificavel",
        "Propor correcao/acao com validacao",
    ]
    if task_type == "research":
        common_steps[1] = "Coletar fontes na web e comparar resultados"
    if task_type == "workspace":
        common_steps[1] = "Inspecionar estrutura/arquivos relevantes no workspace"
    if task_type == "proteus":
        common_steps[1] = "Executar checagens do bridge e do fluxo de automacao Proteus"

    steps = []
    for idx, step in enumerate(common_steps, start=1):
        steps.append({"id": idx, "step": step, "objective": objective if idx == 1 else None})
    return steps


def _evaluate_sections(response_text: str, required_sections: list[str]) -> tuple[list[str], list[str]]:
    text_lower = (response_text or "").lower()
    present: list[str] = []
    missing: list[str] = []
    for section in required_sections:
        token = section.lower()
        if token in text_lower:
            present.append(section)
        else:
            missing.append(section)
    return present, missing


def _score_confidence(
    required_count: int,
    present_count: int,
    evidence_count: int,
    tool_count: int,
    has_root_cause: bool,
    has_validation: bool,
) -> dict[str, Any]:
    ratio = (present_count / required_count) if required_count else 1.0
    score = 25.0
    score += 40.0 * ratio
    score += min(max(evidence_count, 0), 4) * 5.0
    score += min(max(tool_count, 0), 3) * 5.0
    if has_root_cause:
        score += 10.0
    if has_validation:
        score += 10.0
    score = round(max(0.0, min(score, 100.0)), 2)

    if score >= 85:
        level = "high"
    elif score >= 65:
        level = "medium"
    else:
        level = "low"

    return {"score": score, "level": level}


def _run_phase1_executor(request: AssistantPipelineRequest) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    task_type = _normalize_task_type(request.task_type)

    try:
        memory_hits = _memory_search(request.objective, top_k=max(3, request.max_results))
        outputs.append({"check": "memory_retrieval", "ok": True, "data": memory_hits})
    except Exception as exc:
        outputs.append({"check": "memory_retrieval", "ok": False, "error": str(exc)})

    # Always capture a lightweight workspace context snapshot for traceability.
    try:
        workspace_preview = list_workspace_entries(request.workspace_path, max_entries=max(5, request.max_results))
        outputs.append({"check": "workspace_preview", "ok": bool(workspace_preview.get("ok")), "data": workspace_preview})
    except Exception as exc:
        outputs.append({"check": "workspace_preview", "ok": False, "error": str(exc)})

    if task_type in {"diagnose", "generic", "proteus"}:
        try:
            network_probe = check_network_target(
                target=request.network_target,
                port=request.network_port,
                url=request.network_url,
                timeout_seconds=5,
            )
            outputs.append({"check": "network_probe", "ok": bool(network_probe.get("ok")), "data": network_probe})
        except Exception as exc:
            outputs.append({"check": "network_probe", "ok": False, "error": str(exc)})

    if task_type == "research" and request.web_query:
        try:
            web_results = search_web_online(request.web_query, max_results=request.max_results)
            outputs.append({"check": "web_research", "ok": bool(web_results.get("ok")), "data": web_results})
        except Exception as exc:
            outputs.append({"check": "web_research", "ok": False, "error": str(exc)})

    all_ok = all(bool(item.get("ok")) for item in outputs) if outputs else False
    return {"executed": True, "ok": all_ok, "outputs": outputs}


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "projects_root": str(project_root()),
        "proteus_exe": CONFIG.get("proteus_exe"),
        "capabilities": [
            "proteus_automation",
            "workspace_listing",
            "workspace_search",
            "workspace_management",
            "shell_execution",
            "web_search",
            "web_fetch",
            "network_diagnostics",
        ],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    root = project_root()
    exe = Path(CONFIG.get("proteus_exe", "")).expanduser()
    token_enabled = bool(_bridge_token())
    return {
        "ok": True,
        "time": datetime.now().isoformat(timespec="seconds"),
        "projects_root_exists": root.exists(),
        "proteus_exe_exists": exe.exists(),
        "projects_root": str(root),
        "proteus_exe": str(exe),
        "security": {
            "bridge_token_enabled": token_enabled,
            "allow_write": bool(CONFIG.get("allow_write", False)),
            "allow_shell_commands": bool(CONFIG.get("allow_shell_commands", False)),
        },
        "running_processes": get_proteus_processes(),
    }


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    with _METRICS_LOCK:
        return dict(_BRIDGE_METRICS)


@app.get("/projects")
def list_projects() -> dict[str, Any]:
    root = ensure_root_exists()
    items = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in allowed_project_extensions():
            stat = path.stat()
            items.append(
                {
                    "name": path.name,
                    "relative_path": str(path.relative_to(root)),
                    "folder": str(path.parent.relative_to(root)),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "size_bytes": stat.st_size,
                }
            )
    return {"count": len(items), "projects": items}


@app.get("/projects/files")
def list_project_files(project: str = Query(..., description="Projeto por nome ou caminho relativo")) -> dict[str, Any]:
    root = ensure_root_exists()
    project_file = resolve_project_path(project)
    base = get_project_dir(project_file)
    files = []
    for path in sorted(base.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "relative_path": str(path.relative_to(root)),
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                }
            )
    return {
        "project": str(project_file.relative_to(root)),
        "folder": str(base.relative_to(root)),
        "file_count": len(files),
        "files": files,
    }


@app.post("/projects/open")
def open_project(request: OpenProjectRequest) -> dict[str, Any]:
    project_file = resolve_project_path(request.project)
    proteus_exe = Path(CONFIG.get("proteus_exe", "")).expanduser()
    if not proteus_exe.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Executável do Proteus não encontrado. Ajuste 'proteus_exe' em {CONFIG_PATH}",
        )

    command = [str(proteus_exe), *CONFIG.get("default_launch_args", []), *request.extra_args, str(project_file)]
    process = subprocess.Popen(command, cwd=str(project_file.parent))
    time.sleep(1)

    response = {
        "ok": True,
        "pid": process.pid,
        "command": command,
        "project": str(project_file),
    }
    if request.bring_to_front:
        try:
            response["focus"] = send_keys_to_window("", None, 100)
        except HTTPException:
            response["focus"] = {"ok": False, "message": "Proteus abriu, mas a janela não foi trazida ao foco automaticamente."}
    return response


@app.post("/projects/backup")
def backup_project(request: BackupProjectRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    project_file = resolve_project_path(request.project)
    project_dir = get_project_dir(project_file)

    destination_dir = Path(request.destination_dir).expanduser() if request.destination_dir else (BASE_DIR / "proteus_backups")
    destination_dir.mkdir(parents=True, exist_ok=True)

    archive_name = destination_dir / f"{project_file.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    zip_path = shutil.make_archive(str(archive_name), "zip", root_dir=str(project_dir))
    return {
        "ok": True,
        "project": str(project_file.relative_to(root)),
        "backup_zip": zip_path,
    }


@app.post("/projects/import-artifact")
def import_artifact(request: ImportArtifactRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    project_file = resolve_project_path(request.project)
    project_dir = get_project_dir(project_file)

    source = Path(request.artifact_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=404, detail=f"Artefato não encontrado: {source}")

    destination_name = request.target_name or source.name
    destination = (project_dir / destination_name).resolve()

    if destination.exists() and not request.overwrite:
        raise HTTPException(status_code=409, detail=f"Arquivo já existe: {destination}")

    shutil.copy2(source, destination)
    return {
        "ok": True,
        "project": str(project_file.relative_to(root)),
        "source": str(source),
        "destination": str(destination),
    }


@app.get("/files/read")
def read_file(relative_path: str = Query(..., description="Caminho relativo dentro do projects_root")) -> dict[str, Any]:
    root = ensure_root_exists()
    path = safe_relative_path(relative_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")

    return {
        "ok": True,
        "relative_path": str(path.relative_to(root)),
        "size_bytes": path.stat().st_size,
        "content": content,
    }


@app.post("/files/write")
def write_file(request: FileWriteRequest) -> dict[str, Any]:
    if not CONFIG.get("allow_write", False):
        raise HTTPException(status_code=403, detail="Escrita desabilitada no config")

    root = ensure_root_exists()
    path = safe_relative_path(request.relative_path)

    if path.suffix.lower() not in allowed_edit_extensions():
        raise HTTPException(status_code=400, detail=f"Extensão não permitida para edição: {path.suffix}")

    if path.exists() and not request.overwrite:
        raise HTTPException(status_code=409, detail=f"Arquivo já existe: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(request.content, encoding="utf-8")
    return {
        "ok": True,
        "relative_path": str(path.relative_to(root)),
        "size_bytes": path.stat().st_size,
    }


@app.post("/build/run")
def run_build(request: BuildRequest) -> dict[str, Any]:
    if not CONFIG.get("allow_shell_commands", False):
        raise HTTPException(status_code=403, detail="Execução de comandos está desabilitada no config")

    root = ensure_root_exists()
    project_file = resolve_project_path(request.project)
    cwd = get_project_dir(project_file)
    result = run_command_capture(request.command, request.args, cwd, request.timeout_seconds)
    result["cwd"] = str(cwd.relative_to(root))
    return result


@app.get("/automation/profiles")
def list_automation_profiles() -> dict[str, Any]:
    profiles = CONFIG.get("automation_profiles", {})
    return {"count": len(profiles), "profiles": profiles}


@app.post("/automation/run")
def automation_run(request: AutomationRunRequest) -> dict[str, Any]:
    return execute_automation_workflow(request)


@app.post("/pdf/analyze-schematic")
def pdf_analyze_schematic(request: AnalyzeSchematicPdfRequest) -> dict[str, Any]:
    return analyze_schematic_pdf(request)


@app.post("/pdf/extract-schematic-candidates")
def pdf_extract_schematic_candidates(request: ExtractSchematicCandidatesRequest) -> dict[str, Any]:
    return extract_schematic_candidate_pages(request)


@app.post("/pdf/build-focused-subset")
def pdf_build_focused_subset(request: BuildFocusedSubsetRequest) -> dict[str, Any]:
    return build_focused_subset_from_candidates(request)


@app.post("/proteus/prepare-assisted-project")
def proteus_prepare_assisted_project(request: PrepareProteusAssistedProjectRequest) -> dict[str, Any]:
    return prepare_proteus_assisted_project(request)


@app.post("/assistant/pipeline")
def assistant_pipeline(request: AssistantPipelineRequest) -> dict[str, Any]:
    task_type = _normalize_task_type(request.task_type)
    contract = _get_task_contract(task_type)
    planner_steps = _build_assistant_plan(task_type, request.objective)

    executor: dict[str, Any] = {"executed": False, "ok": True, "outputs": []}
    if request.execute:
        executor = _run_phase1_executor(request)

    response_text = request.response_text or ""
    present_sections, missing_sections = _evaluate_sections(response_text, contract.get("required_sections", []))
    verifier = {
        "required_sections": contract.get("required_sections", []),
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "executor_ok": bool(executor.get("ok", True)),
        "objective_present": bool(request.objective.strip()),
    }
    verifier["pass"] = (
        bool(verifier["objective_present"])
        and bool(verifier["executor_ok"])
        and (len(verifier["missing_sections"]) == 0 if response_text else True)
    )

    evidence_count = len(executor.get("outputs", [])) if request.execute else 0
    tool_count = sum(1 for item in executor.get("outputs", []) if item.get("check")) if request.execute else 0
    confidence = _score_confidence(
        required_count=len(contract.get("required_sections", [])),
        present_count=len(present_sections),
        evidence_count=evidence_count,
        tool_count=tool_count,
        has_root_cause=("causa raiz" in response_text.lower()) if response_text else False,
        has_validation=("valida" in response_text.lower()) if response_text else bool(request.execute),
    )

    return {
        "ok": True,
        "phase": "phase-1-planner-executor-verifier",
        "task_type": task_type,
        "objective": request.objective,
        "planner": {"steps": planner_steps},
        "executor": executor,
        "verifier": verifier,
        "confidence": confidence,
    }


@app.post("/assistant/quality-gate")
def assistant_quality_gate(request: AssistantQualityGateRequest) -> dict[str, Any]:
    task_type = _normalize_task_type(request.task_type)
    contract = _get_task_contract(task_type)
    present_sections, missing_sections = _evaluate_sections(
        request.response_text,
        contract.get("required_sections", []),
    )

    confidence = _score_confidence(
        required_count=len(contract.get("required_sections", [])),
        present_count=len(present_sections),
        evidence_count=len(request.evidence_items),
        tool_count=len(request.tool_calls),
        has_root_cause=request.has_root_cause,
        has_validation=request.has_validation,
    )

    return {
        "ok": True,
        "phase": "phase-1-quality-gate",
        "task_type": task_type,
        "contract": contract,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "confidence": confidence,
        "pass": len(missing_sections) == 0 and confidence["score"] >= 70,
        "recommendations": [
            "Inclua as secoes ausentes do contrato" if missing_sections else "Contrato completo",
            "Aumente evidencias verificaveis (saidas/links/logs)" if len(request.evidence_items) < 2 else "Evidencias suficientes",
            "Inclua validacao objetiva no fim da resposta" if not request.has_validation else "Validacao presente",
        ],
    }


@app.post("/assistant/memory/add")
def assistant_memory_add(request: AssistantMemoryAddRequest) -> dict[str, Any]:
    entry = {
        "id": f"mem_{int(time.time() * 1000)}",
        "category": (request.category or "general").strip().lower(),
        "title": request.title.strip(),
        "content": request.content.strip(),
        "tags": [str(tag).strip().lower() for tag in request.tags if str(tag).strip()],
        "source": request.source.strip() or "manual",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not entry["title"] or not entry["content"]:
        raise HTTPException(status_code=400, detail="title e content sao obrigatorios")
    _append_memory_entry(entry)
    return {"ok": True, "entry": entry}


@app.post("/assistant/memory/search")
def assistant_memory_search(request: AssistantMemorySearchRequest) -> dict[str, Any]:
    if not (request.query or "").strip():
        raise HTTPException(status_code=400, detail="query obrigatoria")
    return _memory_search(
        query=request.query,
        category=request.category,
        tags=request.tags,
        top_k=request.top_k,
    )


@app.post("/assistant/orchestrate")
def assistant_orchestrate(request: AssistantOrchestrateRequest) -> dict[str, Any]:
    objective = (request.objective or "").strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective obrigatorio")

    task_type = _normalize_task_type(request.task_type)
    roles = _build_phase3_roles(task_type, objective)

    pipeline = assistant_pipeline(
        AssistantPipelineRequest(
            task_type=task_type,
            objective=objective,
            execute=bool(request.auto_execute),
            workspace_path=request.workspace_path,
            web_query=request.web_query,
            max_results=request.max_results,
        )
    )

    quality = assistant_quality_gate(
        AssistantQualityGateRequest(
            task_type=task_type,
            response_text=(pipeline.get("executor", {}).get("summary") or objective),
            evidence_items=[item.get("check", "") for item in (pipeline.get("executor", {}).get("outputs", [])) if item.get("ok")],
            tool_calls=[item.get("check", "") for item in (pipeline.get("executor", {}).get("outputs", []))],
            has_root_cause=True,
            has_validation=bool(pipeline.get("verifier", {}).get("pass")),
        )
    )

    quality_score = float(quality.get("confidence", {}).get("score", 0))
    risk = _phase4_classify_risk(
        task_type=task_type,
        objective=objective,
        quality_score=quality_score,
        require_approval=bool(request.require_approval),
    )

    needs_approval = risk["action"] in {"require-approval", "block-until-approval"}
    execution_blocked = risk["action"] == "block-until-approval"
    checkpoint_id: Optional[str] = None
    checkpoint_status = "not-required"
    if needs_approval:
        checkpoint_id = f"cp_{int(time.time() * 1000)}"
        checkpoint_status = "pending"
        _append_checkpoint(
            {
                "checkpoint_id": checkpoint_id,
                "status": checkpoint_status,
                "objective": objective,
                "task_type": task_type,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "quality": quality,
                "risk": risk,
                "planner_steps": roles["planner"]["steps"],
            }
        )

    return {
        "ok": True,
        "phase": "phase-3-orchestration",
        "task_type": task_type,
        "objective": objective,
        "roles": roles,
        "pipeline": pipeline,
        "quality_gate": quality,
        "risk": risk,
        "checkpoint": {
            "required": needs_approval,
            "status": checkpoint_status,
            "checkpoint_id": checkpoint_id,
            "execution_blocked": execution_blocked,
        },
    }


@app.post("/assistant/checkpoint/decision")
def assistant_checkpoint_decision(request: AssistantCheckpointDecisionRequest) -> dict[str, Any]:
    checkpoint_id = (request.checkpoint_id or "").strip()
    if not checkpoint_id:
        raise HTTPException(status_code=400, detail="checkpoint_id obrigatorio")

    decision = (request.decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="decision invalida: use approve ou reject")

    note = (request.note or "").strip()
    entry = _record_checkpoint_decision(checkpoint_id=checkpoint_id, decision=decision, note=note)
    return {"ok": True, "checkpoint": entry}


@app.get("/assistant/checkpoints")
def assistant_checkpoints(status: Optional[str] = Query(default=None), limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    normalized_status = (status or "").strip().lower() or None
    records = _to_checkpoint_latest_records()
    if normalized_status:
        records = [entry for entry in records if str(entry.get("status") or "").strip().lower() == normalized_status]
    return {
        "ok": True,
        "phase": "phase-4-checkpoint-audit",
        "status_filter": normalized_status,
        "count": min(len(records), limit),
        "items": records[:limit],
    }


@app.post("/assistant/checkpoints/cleanup")
def assistant_checkpoints_cleanup(request: AssistantCheckpointCleanupRequest) -> dict[str, Any]:
    result = _cleanup_old_checkpoints(request.retention_days)
    return {"ok": True, "phase": "phase-4-checkpoint-cleanup", "result": result}


@app.get("/assistant/sla/summary")
def assistant_sla_summary(
    warning_after_hours: int = Query(default=_PHASE5_SLA_WARNING_HOURS, ge=1, le=720),
    critical_after_hours: int = Query(default=_PHASE5_SLA_CRITICAL_HOURS, ge=1, le=1440),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if critical_after_hours < warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    return {"ok": True, "result": _phase5_sla_summary(warning_after_hours, critical_after_hours, limit)}


@app.post("/assistant/checkpoints/escalate")
def assistant_checkpoints_escalate(request: AssistantCheckpointEscalationRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    result = _phase5_escalate_pending_checkpoints(
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
    )
    return {"ok": True, "result": result}


@app.get("/assistant/operations/dashboard")
def assistant_operations_dashboard(
    warning_after_hours: int = Query(default=_PHASE5_SLA_WARNING_HOURS, ge=1, le=720),
    critical_after_hours: int = Query(default=_PHASE5_SLA_CRITICAL_HOURS, ge=1, le=1440),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if critical_after_hours < warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    return {"ok": True, "result": _phase6_build_operational_queues(warning_after_hours, critical_after_hours, limit)}


@app.get("/assistant/operations/policies")
def assistant_operations_policies() -> dict[str, Any]:
    return {"ok": True, "phase": "phase-6-operations-policies", "policies": _PHASE6_QUEUE_POLICIES}


@app.post("/assistant/operations/escalate")
def assistant_operations_escalate(request: AssistantOperationsEscalationRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    result = _phase6_escalate_operational_queues(
        queues=request.queues,
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
    )
    return {"ok": True, "result": result}


@app.get("/assistant/executive/dashboard")
def assistant_executive_dashboard(
    days: int = Query(default=_PHASE7_DEFAULT_DAYS, ge=1, le=90),
    warning_after_hours: int = Query(default=_PHASE5_SLA_WARNING_HOURS, ge=1, le=720),
    critical_after_hours: int = Query(default=_PHASE5_SLA_CRITICAL_HOURS, ge=1, le=1440),
    limit: int = Query(default=200, ge=10, le=2000),
) -> dict[str, Any]:
    if critical_after_hours < warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    return {"ok": True, "result": _phase7_build_executive_dashboard(days, warning_after_hours, critical_after_hours, limit)}


@app.get("/assistant/executive/dashboard/scoped")
def assistant_executive_dashboard_scoped(
    days: int = Query(default=_PHASE7_DEFAULT_DAYS, ge=1, le=90),
    warning_after_hours: int = Query(default=_PHASE5_SLA_WARNING_HOURS, ge=1, le=720),
    critical_after_hours: int = Query(default=_PHASE5_SLA_CRITICAL_HOURS, ge=1, le=1440),
    limit: int = Query(default=200, ge=10, le=2000),
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    if critical_after_hours < warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    return {
        "ok": True,
        "result": _phase12_build_scoped_dashboard(
            days=days,
            warning_after_hours=warning_after_hours,
            critical_after_hours=critical_after_hours,
            limit=limit,
            environment=environment,
            tenant_id=tenant_id,
            project_scope=project_scope,
        ),
    }


@app.post("/assistant/executive/policy")
def assistant_executive_policy(request: AssistantScopedPolicyRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase13_upsert_policy(request)}


@app.get("/assistant/executive/policy")
def assistant_executive_policy_list(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase13_list_policies(
            environment=environment,
            tenant_id=tenant_id,
            project_scope=project_scope,
        ),
    }


@app.get("/assistant/executive/dashboard/scoped/policy")
def assistant_executive_dashboard_scoped_policy(
    days: int = Query(default=_PHASE7_DEFAULT_DAYS, ge=1, le=90),
    warning_after_hours: int = Query(default=_PHASE5_SLA_WARNING_HOURS, ge=1, le=720),
    critical_after_hours: int = Query(default=_PHASE5_SLA_CRITICAL_HOURS, ge=1, le=1440),
    limit: int = Query(default=200, ge=10, le=2000),
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    if critical_after_hours < warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")

    dashboard = _phase12_build_scoped_dashboard(
        days=days,
        warning_after_hours=warning_after_hours,
        critical_after_hours=critical_after_hours,
        limit=limit,
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
    )
    scope = _phase11_scope_context(
        environment=environment or _PHASE10_DEFAULT_ENVIRONMENT,
        tenant_id=tenant_id or _PHASE11_DEFAULT_TENANT_ID,
        project_scope=project_scope or _PHASE11_DEFAULT_PROJECT_SCOPE,
    )
    policy = _phase13_find_policy(scope)
    evaluation = _phase13_evaluate_scoped_policy(policy, dashboard)

    return {
        "ok": True,
        "result": {
            "phase": "phase-13-scoped-policy-dashboard",
            "scope": scope,
            "dashboard": dashboard,
            "evaluation": evaluation,
        },
    }


@app.post("/assistant/executive/policy/enforce")
def assistant_executive_policy_enforce(request: AssistantPolicyEnforcementRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase15_enforce_scoped_policy(request)}


@app.post("/assistant/executive/policy/enforce/escalate")
def assistant_executive_policy_enforce_escalate(request: AssistantPolicyEscalationRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase16_enforce_and_escalate(request)}


@app.post("/assistant/audit-log")
def assistant_audit_log(request: AssistantAuditLogRequest) -> dict[str, Any]:
    result = _phase14_record_audit_log(request)
    return {"ok": True, "result": result}


@app.get("/assistant/audit-logs")
def assistant_audit_logs(
    operation: Optional[str] = Query(default=None),
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=200, ge=10, le=2000),
) -> dict[str, Any]:
    result = _phase14_list_audit_logs(
        operation=operation,
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
        status=status,
        days=days,
        limit=limit,
    )
    return {"ok": True, "result": result}


@app.get("/assistant/audit-summary")
def assistant_audit_summary(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    result = _phase14_audit_summary_by_operation(
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
        days=days,
    )
    return {"ok": True, "result": result}


@app.get("/assistant/incidents")
def assistant_incidents(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=200, ge=10, le=2000),
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase16_list_incidents(
            environment=environment,
            tenant_id=tenant_id,
            project_scope=project_scope,
            status=status,
            severity=severity,
            days=days,
            limit=limit,
        ),
    }


@app.get("/assistant/incidents/summary")
def assistant_incidents_summary(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase16_incidents_summary(
            environment=environment,
            tenant_id=tenant_id,
            project_scope=project_scope,
            days=days,
        ),
    }


@app.post("/assistant/incidents/close-resolved")
def assistant_incidents_close_resolved(request: AssistantIncidentResolveRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase17_close_resolved_incidents(request)}


@app.post("/assistant/incidents/reopen-regressed")
def assistant_incidents_reopen_regressed(request: AssistantIncidentReopenRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase18_reopen_on_regression(request)}


@app.post("/assistant/incidents/deduplicate")
def assistant_incidents_deduplicate(request: AssistantIncidentDedupRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase19_deduplicate_incidents(request)}


@app.get("/assistant/incidents/correlation")
def assistant_incidents_correlation(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
    status: str = Query(default="open"),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=5000, ge=100, le=50000),
) -> dict[str, Any]:
    request = AssistantIncidentCorrelationRequest(
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
        status=status,
        days=days,
        limit=limit,
    )
    return {"ok": True, "result": _phase20_correlate_incidents(request)}


@app.get("/assistant/incidents/correlation/impact")
def assistant_incidents_correlation_impact(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
    status: str = Query(default="open"),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=5000, ge=100, le=50000),
) -> dict[str, Any]:
    request = AssistantIncidentCorrelationRequest(
        environment=environment,
        tenant_id=tenant_id,
        project_scope=project_scope,
        status=status,
        days=days,
        limit=limit,
    )
    return {"ok": True, "result": _phase20_correlation_impact_summary(request)}


@app.post("/assistant/checkpoints/deduplicate")
def assistant_checkpoints_deduplicate(request: AssistantCheckpointDedupRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase7_apply_checkpoint_dedup(limit=request.limit, apply_changes=request.apply_changes)}


@app.post("/assistant/executive/report")
def assistant_executive_report(request: AssistantExecutiveReportRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    result = _phase8_export_executive_report(
        report_format=request.report_format,
        days=request.days,
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
        persist=bool(request.persist),
        output_name=request.output_name,
        environment=request.environment,
        tenant_id=request.tenant_id,
        project_scope=request.project_scope,
    )
    return {"ok": True, "result": result}


@app.post("/assistant/executive/report/snapshot")
def assistant_executive_report_snapshot(request: AssistantExecutiveSnapshotRequest) -> dict[str, Any]:
    if request.critical_after_hours < request.warning_after_hours:
        raise HTTPException(status_code=400, detail="critical_after_hours deve ser maior ou igual a warning_after_hours")
    result = _phase9_publish_snapshot(
        formats=request.formats,
        days=request.days,
        warning_after_hours=request.warning_after_hours,
        critical_after_hours=request.critical_after_hours,
        limit=request.limit,
        snapshot_name=request.snapshot_name,
        environment=request.environment,
        tenant_id=request.tenant_id,
        project_scope=request.project_scope,
    )
    return {"ok": True, "result": result}


@app.get("/assistant/executive/report/index")
def assistant_executive_report_index(
    limit: int = Query(default=20, ge=1, le=500),
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase9_list_snapshots(limit, environment, tenant_id, project_scope),
    }


@app.post("/assistant/executive/report/cleanup")
def assistant_executive_report_cleanup(request: AssistantReportCleanupRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase9_cleanup_snapshots(
            request.retention_days,
            request.apply_changes,
            request.environment,
            request.tenant_id,
            request.project_scope,
        ),
    }


@app.post("/assistant/executive/report/schedule")
def assistant_executive_report_schedule(request: AssistantReportScheduleRequest) -> dict[str, Any]:
    return {"ok": True, "result": _phase10_upsert_schedule(request)}


@app.get("/assistant/executive/report/schedule")
def assistant_executive_report_schedule_list(
    environment: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    project_scope: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase10_list_schedules(environment, tenant_id, project_scope),
    }


@app.post("/assistant/executive/report/schedule/run")
def assistant_executive_report_schedule_run(request: AssistantReportScheduleRunRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "result": _phase10_run_due_schedules(
            environment=request.environment,
            tenant_id=request.tenant_id,
            project_scope=request.project_scope,
            force=bool(request.force),
            limit=request.limit,
        ),
    }


@app.get("/assistant/capabilities")
def assistant_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "service": app.title,
        "capabilities": {
            "proteus": True,
            "schematic_pdf_analysis": True,
            "schematic_pdf_candidate_extraction": True,
            "schematic_pdf_focus_profiles": ["acer_pass3_pure_schematics", "acer_pass4_signal_dense"],
            "proteus_assisted_project_packaging": True,
            "workspace_files": True,
            "workspace_search": True,
            "workspace_management": True,
            "shell": bool(CONFIG.get("allow_shell_commands", False)),
            "web_search": True,
            "web_fetch": True,
            "network_diagnostics": True,
            "assistant_phase1_pipeline": True,
            "assistant_quality_gate": True,
            "assistant_phase2_memory": True,
            "assistant_phase3_orchestration": True,
            "assistant_phase3_checkpoints": True,
            "assistant_phase4_risk_policy": True,
            "assistant_phase4_checkpoint_audit": True,
            "assistant_phase5_sla": True,
            "assistant_phase5_escalation": True,
            "assistant_phase6_operations_dashboard": True,
            "assistant_phase6_operations_policies": True,
            "assistant_phase6_operations_escalation": True,
            "assistant_phase7_executive_dashboard": True,
            "assistant_phase7_deduplication": True,
            "assistant_phase8_executive_report": True,
            "assistant_phase9_report_snapshots": True,
            "assistant_phase9_report_retention": True,
            "assistant_phase10_report_schedules": True,
            "assistant_phase10_report_runner": True,
            "assistant_phase11_multi_tenant_reports": True,
            "assistant_phase12_scoped_dashboard": True,
            "assistant_phase13_scoped_policy": True,
            "assistant_phase14_audit_logs": True,
            "assistant_phase15_policy_enforcement": True,
            "assistant_phase16_incident_escalation": True,
            "assistant_phase17_auto_incident_closure": True,
            "assistant_phase18_incident_regression_reopen": True,
            "assistant_phase19_incident_deduplication": True,
            "assistant_phase20_multi_scope_correlation": True,
        },
        "projects_root": str(project_root()),
    }


@app.get("/workspace/list")
def workspace_list(
    relative_path: str = Query(".", description="Pasta relativa dentro do workspace"),
    max_entries: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    return list_workspace_entries(relative_path, max_entries)


@app.post("/workspace/search")
def workspace_search(request: WorkspaceSearchRequest) -> dict[str, Any]:
    return search_workspace_content(
        query=request.query,
        relative_path=request.relative_path,
        is_regex=request.is_regex,
        max_results=request.max_results,
    )


@app.post("/workspace/mkdir")
def workspace_mkdir(request: WorkspaceMkdirRequest) -> dict[str, Any]:
    return create_workspace_directory(request.relative_path, request.exist_ok)


@app.post("/workspace/move")
def workspace_move(request: WorkspaceMoveRequest) -> dict[str, Any]:
    return move_workspace_path(request.source_path, request.destination_path, request.overwrite)


@app.post("/workspace/delete")
def workspace_delete(request: WorkspaceDeleteRequest) -> dict[str, Any]:
    return delete_workspace_path(request.relative_path, request.recursive)


@app.post("/shell/run")
def shell_run(request: ShellCommandRequest) -> dict[str, Any]:
    return run_shell_command(request.command, request.cwd, request.timeout_seconds)


@app.post("/web/search")
def web_search(request: WebSearchRequest) -> dict[str, Any]:
    return search_web_online(request.query, request.max_results)


@app.post("/web/fetch")
def web_fetch(request: WebFetchRequest) -> dict[str, Any]:
    return fetch_web_content(request.url, request.max_chars)


@app.post("/network/check")
def network_check(request: NetworkCheckRequest) -> dict[str, Any]:
    return check_network_target(request.target, request.port, request.url, request.timeout_seconds)


@app.get("/proteus/processes")
def proteus_processes() -> dict[str, Any]:
    processes = get_proteus_processes()
    return {"count": len(processes), "processes": processes}


@app.post("/proteus/focus")
def focus_proteus() -> dict[str, Any]:
    return send_keys_to_window("", None, 100)


@app.post("/proteus/send-keys")
def proteus_send_keys(request: SendKeysRequest) -> dict[str, Any]:
    return send_keys_to_window(request.keys, request.window_title, request.delay_ms)


@app.post("/proteus/run-macro")
def proteus_run_macro(request: MacroRequest) -> dict[str, Any]:
    macros = CONFIG.get("ui_macros", {})
    if request.name not in macros or not macros.get(request.name):
        raise HTTPException(
            status_code=404,
            detail=f"Macro '{request.name}' não configurada em {CONFIG_PATH}",
        )
    return send_keys_to_window(macros[request.name], None, request.delay_ms)


@app.post("/proteus/close")
def close_proteus(request: CloseRequest) -> dict[str, Any]:
    closed = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "pds.exe" in name or "proteus" in name:
                if request.force:
                    proc.kill()
                    action = "killed"
                else:
                    proc.terminate()
                    action = "terminated"
                closed.append({"pid": proc.info.get("pid"), "name": proc.info.get("name"), "action": action})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"ok": True, "count": len(closed), "closed": closed}


@app.get("/config")
def read_config() -> dict[str, Any]:
    return CONFIG


@app.post("/config/update")
def update_config(request: ConfigUpdateRequest) -> dict[str, Any]:
    current = load_config()
    payload = request.model_dump(exclude_none=True)
    updated = deep_update(current, payload)
    CONFIG_PATH.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    CONFIG.clear()
    CONFIG.update(updated)
    return {"ok": True, "config": CONFIG}


def is_bridge_already_running(host: str, port: int) -> bool:
    base_url = f"http://{host}:{port}"
    try:
        session = requests.Session()
        session.trust_env = False
        session.proxies.update({"http": None, "https": None})
        response = session.get(f"{base_url}/health", timeout=2)
        response.raise_for_status()
        payload = response.json()
        return bool(payload.get("ok"))
    except Exception:
        return False


if __name__ == "__main__":
    host = str(CONFIG.get("host", "127.0.0.1"))
    port = int(CONFIG.get("port", 8001))
    if is_bridge_already_running(host, port):
        print(f"Proteus bridge already running at http://{host}:{port}")
        raise SystemExit(0)
    uvicorn.run(app, host=host, port=port)
