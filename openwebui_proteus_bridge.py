from __future__ import annotations

import csv
import glob
import json
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import psutil
import requests
import uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "proteus_bridge_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "proteus_exe": r"C:/Users/rober/AppData/Local/Programs/Labcenter Electronics/Proteus 9 Professional/Bin/PDS.exe",
    "projects_root": str(BASE_DIR),
    "host": "127.0.0.1",
    "port": 8001,
    "allow_write": True,
    "allow_shell_commands": True,
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

app = FastAPI(
    title="Professional Workspace & Proteus Bridge",
    version="1.2.0",
    description="Ponte local para o Open WebUI com automação do ISIS Proteus, busca web, leitura do workspace e execução controlada de comandos.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class PrepareProteusAssistedProjectRequest(BaseModel):
    project_folder: str = Field(..., description="Pasta relativa dentro do projects_root para gerar o pacote assistido")
    analysis_relative_path: Optional[str] = Field(default=None, description="JSON gerado por /pdf/analyze-schematic")
    pdf_path: Optional[str] = Field(default=None, description="PDF original dentro do projects_root, usado se não houver JSON")
    project_display_name: Optional[str] = None
    max_pages: int = Field(default=12, ge=1, le=100)
    overwrite: bool = False
    copy_source_pdf: bool = True


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


def build_schematic_pdf_analysis(pdf_path: Path, max_pages: int) -> dict[str, Any]:
    PdfReader = import_pdf_reader()
    reader = PdfReader(str(pdf_path))
    pages = list(reader.pages[:max_pages])
    text_by_page: list[dict[str, Any]] = []
    component_map: dict[str, dict[str, Any]] = {}
    signal_map: dict[str, dict[str, Any]] = {}
    extracted_chars = 0

    for index, page in enumerate(pages, start=1):
        text = (page.extract_text() or "").strip()
        text_by_page.append(
            {
                "page_number": index,
                "characters": len(text),
                "preview": text[:500],
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
    warnings = []

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

    return {
        "analysis_type": "schematic_pdf_preflight",
        "pdf_name": pdf_path.name,
        "page_count_total": len(reader.pages),
        "pages_processed": len(pages),
        "extracted_text_characters": extracted_chars,
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


def analyze_schematic_pdf(request: AnalyzeSchematicPdfRequest) -> dict[str, Any]:
    root = ensure_root_exists()
    pdf_path = safe_relative_path(request.pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Informe um arquivo .pdf dentro do projects_root")
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail=f"PDF não encontrado: {pdf_path}")

    analysis = build_schematic_pdf_analysis(pdf_path, request.max_pages)
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


def build_block_checklist(functional_blocks: list[dict[str, Any]]) -> dict[str, Any]:
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

    for block in ordered_blocks:
        block_name = str(block.get("block", "misc_control"))
        references = ", ".join(block.get("references", [])[:20])
        priority = int(block.get("priority", block_priority(block_name)))
        item = {
            "order": step_number,
            "priority": priority,
            "block": block_name,
            "component_count": block.get("component_count", 0),
            "avg_confidence_score": block.get("avg_confidence_score", 0),
            "confidence_label": block.get("confidence_label", "low"),
            "mount_action": action_map.get(block_name, "Montar bloco e revisar componentes associados"),
            "verification": verification_map.get(block_name, "Revisar ligacoes e coerencia do bloco no ISIS"),
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
    package = load_schematic_analysis_payload(request.analysis_relative_path, request.pdf_path, request.max_pages)
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
    block_checklist = build_block_checklist(functional_blocks["blocks"])

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
    return {
        "ok": True,
        "time": datetime.now().isoformat(timespec="seconds"),
        "projects_root_exists": root.exists(),
        "proteus_exe_exists": exe.exists(),
        "projects_root": str(root),
        "proteus_exe": str(exe),
        "running_processes": get_proteus_processes(),
    }


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


@app.post("/proteus/prepare-assisted-project")
def proteus_prepare_assisted_project(request: PrepareProteusAssistedProjectRequest) -> dict[str, Any]:
    return prepare_proteus_assisted_project(request)


@app.get("/assistant/capabilities")
def assistant_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "service": app.title,
        "capabilities": {
            "proteus": True,
            "schematic_pdf_analysis": True,
            "proteus_assisted_project_packaging": True,
            "workspace_files": True,
            "workspace_search": True,
            "workspace_management": True,
            "shell": bool(CONFIG.get("allow_shell_commands", False)),
            "web_search": True,
            "web_fetch": True,
            "network_diagnostics": True,
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
