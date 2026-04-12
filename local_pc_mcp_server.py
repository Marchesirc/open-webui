from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

import psutil
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

HOST = os.environ.get("LOCAL_PC_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("LOCAL_PC_MCP_PORT", "8765"))
WORKSPACE_ROOT = Path(os.environ.get("LOCAL_PC_MCP_WORKSPACE", r"D:/Projetos")).expanduser().resolve()
AUTOMATION_ROOT = WORKSPACE_ROOT / "OpenWebUI_Proteus" if (WORKSPACE_ROOT / "OpenWebUI_Proteus").exists() else WORKSPACE_ROOT
DEFAULT_PORTS = [8080, 8001, 11434, PORT]
MAX_OUTPUT_CHARS = 6000

ALLOWED_AUTOMATIONS = {
    "openwebui_restart": {
        "path": AUTOMATION_ROOT / "restart_openwebui_with_proteus_tool.ps1",
        "description": "Reinicia o Open WebUI e reaplica a integração com Proteus/MCP.",
        "default_args": ["-ForceRestart"],
    },
    "proteus_bridge_start": {
        "path": AUTOMATION_ROOT / "start_proteus_bridge.ps1",
        "description": "Inicia o bridge do Proteus na porta 8001.",
        "default_args": [],
    },
    "ollama_background_start": {
        "path": AUTOMATION_ROOT / "start_ollama_background.ps1",
        "description": "Inicia o Ollama em background para os modelos locais.",
        "default_args": [],
    },
    "full_stack_start": {
        "path": AUTOMATION_ROOT / "start_openwebui_professional_stack.ps1",
        "description": "Sobe o stack profissional completo: Ollama, MCP local, bridge e Open WebUI.",
        "default_args": [],
    },
}

mcp = FastMCP(
    name="Local PC MCP",
    instructions=(
        "Servidor MCP local deste PC Windows para diagnóstico do sistema, "
        "serviços do Open WebUI, Ollama, bridge do Proteus e leitura controlada do workspace."
    ),
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    log_level="INFO",
)


def _resolve_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    return candidate.resolve()


def _bytes_to_gb(value: int | float) -> float:
    return round(float(value) / (1024 ** 3), 2)


def _truncate_text(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    return text[:max_chars] if len(text) > max_chars else text


def _listening_ports(ports: list[int]) -> list[dict]:
    listeners: dict[int, int | None] = {}
    for conn in psutil.net_connections(kind="inet"):
        if conn.status == psutil.CONN_LISTEN and conn.laddr:
            listeners[conn.laddr.port] = conn.pid

    results = []
    for port in ports:
        pid = listeners.get(port)
        process_name = None
        if pid:
            try:
                process_name = psutil.Process(pid).name()
            except Exception:
                process_name = None

        results.append(
            {
                "port": int(port),
                "listening": pid is not None,
                "pid": pid,
                "process": process_name,
            }
        )
    return results


@mcp.tool(description="Resumo objetivo do PC local, recursos do sistema e serviços relevantes do ambiente.")
def get_pc_summary() -> dict:
    vm = psutil.virtual_memory()
    disks = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            disks.append(
                {
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "total_gb": _bytes_to_gb(usage.total),
                    "free_gb": _bytes_to_gb(usage.free),
                    "used_percent": round(usage.percent, 1),
                }
            )
        except Exception:
            continue

    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "workspace_root": str(WORKSPACE_ROOT),
        "cpu_percent": round(psutil.cpu_percent(interval=0.2), 1),
        "memory": {
            "total_gb": _bytes_to_gb(vm.total),
            "available_gb": _bytes_to_gb(vm.available),
            "used_percent": round(vm.percent, 1),
        },
        "services": _listening_ports(DEFAULT_PORTS),
        "disks": disks,
    }


@mcp.tool(description="Verifica portas e serviços locais do Open WebUI, Ollama, Proteus Bridge e do MCP local.")
def check_local_services(ports: Optional[list[int]] = None) -> dict:
    checked_ports = [int(p) for p in (ports or DEFAULT_PORTS)]
    return {
        "count": len(checked_ports),
        "services": _listening_ports(checked_ports),
    }


@mcp.tool(description="Lista arquivos e pastas do workspace ou de uma pasta local segura, com limite de resultados.")
def list_directory(path: str = str(WORKSPACE_ROOT), pattern: str = "*", max_results: int = 100) -> dict:
    root = _resolve_path(path)
    if not root.exists():
        return {"ok": False, "error": "path_not_found", "path": str(root)}

    max_results = max(1, min(int(max_results), 300))
    entries = sorted(root.glob(pattern), key=lambda item: (not item.is_dir(), item.name.lower()))

    items = []
    for entry in entries[:max_results]:
        item = {
            "name": entry.name + ("/" if entry.is_dir() else ""),
            "path": str(entry),
            "is_dir": entry.is_dir(),
        }
        if entry.is_file():
            try:
                item["size_bytes"] = entry.stat().st_size
            except Exception:
                item["size_bytes"] = None
        items.append(item)

    return {
        "ok": True,
        "path": str(root),
        "pattern": pattern,
        "count": len(items),
        "items": items,
    }


@mcp.tool(description="Lê um arquivo de texto local com limite de tamanho para inspeção rápida.")
def read_text_file(path: str, max_chars: int = 4000) -> dict:
    file_path = _resolve_path(path)
    if not file_path.exists() or not file_path.is_file():
        return {"ok": False, "error": "file_not_found", "path": str(file_path)}

    max_chars = max(200, min(int(max_chars), 20000))
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "ok": True,
        "path": str(file_path),
        "truncated": len(content) > max_chars,
        "content": content[:max_chars],
    }


@mcp.tool(description="Lista processos em execução, opcionalmente filtrando por parte do nome.")
def list_processes(name_filter: str = "", max_results: int = 50) -> dict:
    name_filter = (name_filter or "").strip().lower()
    max_results = max(1, min(int(max_results), 200))

    processes = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            info = proc.info
            name = (info.get("name") or "")
            if name_filter and name_filter not in name.lower():
                continue

            memory_info = info.get("memory_info")
            memory_mb = round((memory_info.rss / (1024 ** 2)), 1) if memory_info else None
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": name,
                    "username": info.get("username"),
                    "memory_mb": memory_mb,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes.sort(key=lambda item: ((item.get("memory_mb") or 0), item.get("name") or ""), reverse=True)
    return {"count": len(processes[:max_results]), "processes": processes[:max_results]}


@mcp.tool(description="Lista as automações locais permitidas neste PC, com seus scripts e finalidade.")
def list_available_automations() -> dict:
    items = []
    for automation_id, meta in ALLOWED_AUTOMATIONS.items():
        path = Path(meta["path"])
        items.append(
            {
                "id": automation_id,
                "description": meta["description"],
                "path": str(path),
                "exists": path.exists(),
                "default_args": meta.get("default_args", []),
            }
        )
    return {"count": len(items), "automations": items}


@mcp.tool(description="Executa uma automação local permitida do Windows/Open WebUI com segurança. Use os ids listados em list_available_automations.")
def run_automation(script_id: str, wait: bool = False, timeout_seconds: int = 30) -> dict:
    meta = ALLOWED_AUTOMATIONS.get(script_id)
    if not meta:
        return {
            "ok": False,
            "error": "automation_not_allowed",
            "allowed_ids": sorted(ALLOWED_AUTOMATIONS.keys()),
        }

    script_path = Path(meta["path"])
    if not script_path.exists():
        return {"ok": False, "error": "script_not_found", "path": str(script_path)}

    command = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        *meta.get("default_args", []),
    ]

    if wait:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=max(5, min(int(timeout_seconds), 180)),
                cwd=str(AUTOMATION_ROOT),
                check=False,
            )
            output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
            return {
                "ok": completed.returncode == 0,
                "script_id": script_id,
                "returncode": completed.returncode,
                "output": _truncate_text(output.strip()),
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "script_id": script_id,
                "error": "timeout",
                "timeout_seconds": int(timeout_seconds),
            }

    process = subprocess.Popen(command, cwd=str(AUTOMATION_ROOT))
    return {
        "ok": True,
        "script_id": script_id,
        "pid": process.pid,
        "started": True,
    }


@mcp.tool(description="Abre uma pasta, arquivo ou URL neste PC Windows para acelerar o fluxo local.")
def open_target(target: str) -> dict:
    target = (target or "").strip()
    if not target:
        return {"ok": False, "error": "empty_target"}

    if target.lower().startswith(("http://", "https://")):
        webbrowser.open(target)
        return {"ok": True, "target": target, "type": "url"}

    resolved = _resolve_path(target)
    if not resolved.exists():
        return {"ok": False, "error": "path_not_found", "path": str(resolved)}

    os.startfile(str(resolved))
    return {"ok": True, "target": str(resolved), "type": "path"}


@mcp.resource("pc://summary", name="PC Summary", mime_type="application/json")
def pc_summary_resource() -> str:
    return json.dumps(get_pc_summary(), indent=2, ensure_ascii=False)


@mcp.resource("pc://workspace-root", name="Workspace Root", mime_type="text/plain")
def workspace_root_resource() -> str:
    return str(WORKSPACE_ROOT)


@mcp.custom_route("/health", methods=["GET"], name="health")
async def health(_request):
    return JSONResponse(
        {
            "ok": True,
            "name": "Local PC MCP",
            "host": HOST,
            "port": PORT,
            "mcp_path": "/mcp",
            "workspace_root": str(WORKSPACE_ROOT),
        }
    )


if __name__ == "__main__":
    print(f"Local PC MCP ativo em http://{HOST}:{PORT}/mcp")
    mcp.run(transport="streamable-http")
