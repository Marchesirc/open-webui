from __future__ import annotations

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


@app.get("/assistant/capabilities")
def assistant_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "service": app.title,
        "capabilities": {
            "proteus": True,
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
