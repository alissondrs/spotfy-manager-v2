"""Fixture de integração: sobe todos os serviços localmente (uvicorn em threads)
com dados isolados em diretório temporário, catálogo em modo mock e portas
efêmeras (sem conflitar com o ambiente de dev)."""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"

ORDER = ["identity", "fileimport", "playlist", "catalog", "bpm-match", "library", "report", "web"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_app(name: str):
    # nome de módulo estável (sem hífens) para a resolução de modelos pydantic
    modname = f"svc_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(
        modname, SERVICES / name / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module.app


def _wait_health(port: int, name: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.RequestError:
            time.sleep(0.15)
    raise RuntimeError(f"serviço {name} não subiu na porta {port}")


@pytest.fixture(scope="session")
def stack():
    base = Path(tempfile.mkdtemp(prefix="bpm-e2e-"))
    ports = {name: _free_port() for name in ORDER}
    os.environ.update(
        JWT_SECRET="test-secret-e2e-1234567890-abcdefghijklmnopqrstuvwxyz",
        JWT_TTL_MINUTES="60",
        IDENTITY_ENABLE_BOOTSTRAP="1",
        DATA_DIR=str(base / "data"),
        LOG_LEVEL="WARNING",
        TIDAL_MOCK="1",
        ALLOW_DOWNLOADS="1",
        DOWNLOAD_DIR=str(base / "downloads"),
        IDENTITY_URL=f"http://127.0.0.1:{ports['identity']}",
        FILEIMPORT_URL=f"http://127.0.0.1:{ports['fileimport']}",
        PLAYLIST_URL=f"http://127.0.0.1:{ports['playlist']}",
        CATALOG_URL=f"http://127.0.0.1:{ports['catalog']}",
        BPM_MATCH_URL=f"http://127.0.0.1:{ports['bpm-match']}",
        LIBRARY_URL=f"http://127.0.0.1:{ports['library']}",
        REPORT_URL=f"http://127.0.0.1:{ports['report']}",
        WEB_URL=f"http://127.0.0.1:{ports['web']}",
    )
    servers, threads = [], []
    for name in ORDER:
        app = _load_app(name)
        config = uvicorn.Config(app, host="127.0.0.1", port=ports[name], log_level="warning")
        server = uvicorn.Server(config)
        servers.append((name, server, ports[name]))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        threads.append(thread)
    try:
        for name, _server, port in servers:
            _wait_health(port, name)
    except Exception:
        for _name, server, _port in servers:
            server.should_exit = True
        raise
    yield {"base": base, "ports": ports}
    for _name, server, _port in servers:
        server.should_exit = True
    for thread in threads:
        thread.join(timeout=10)