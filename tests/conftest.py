"""Fixture de integração: sobe todos os serviços localmente (uvicorn em threads)
com dados isolados em diretório temporário, catálogo em modo mock e portas
efêmeras (sem conflitar com o ambiente de dev)."""

from __future__ import annotations

import base64
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
E2E = Path(__file__).resolve().parent / "e2e"

ORDER = ["identity", "fileimport", "playlist", "catalog", "bpm-match", "library", "report", "web"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_app(name: str, path: Path | None = None):
    # nome de módulo estável (sem hífens) para a resolução de modelos pydantic
    modname = f"svc_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(
        modname, path or (SERVICES / name / "main.py"))
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
    spotify_port = _free_port()
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
        # Fake Spotify (Authorization Code + PKCE) para os testes de sessão/flow
        SPOTIFY_CLIENT_ID="fake-client-id",
        SPOTIFY_CLIENT_SECRET="fake-client-secret",
        SPOTIFY_REDIRECT_URI=f"http://127.0.0.1:{ports['web']}/api/auth/spotify/callback",
        SPOTIFY_SCOPES="user-library-read playlist-read-private playlist-read-collaborative",
        SPOTIFY_AUTH_URL=f"http://127.0.0.1:{spotify_port}/authorize",
        SPOTIFY_TOKEN_URL=f"http://127.0.0.1:{spotify_port}/api/token",
        SPOTIFY_API_URL=f"http://127.0.0.1:{spotify_port}",
        # Sessão anônima + criptografia + configurações do BFF
        WEB_TOKEN_ENCRYPTION_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
        WEB_SESSION_COOKIE_SECURE="0",
        WEB_SESSION_COOKIE_SAMESITE="lax",
        WEB_SESSION_IDLE_TTL_SECONDS="3600",
        WEB_SESSION_ABSOLUTE_TTL_SECONDS="86400",
        WEB_OAUTH_STATE_TTL_SECONDS="600",
        WEB_INTERNAL_JWT_TTL_MINUTES="5",
    )
    servers, threads = [], []

    def _run(name, app, port):
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        servers.append((name, server, port))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        threads.append(thread)

    fake_spotify = _load_app("fake-spotify", E2E / "fake_spotify.py")
    _run("fake-spotify", fake_spotify, spotify_port)
    for name in ORDER:
        _run(name, _load_app(name), ports[name])
    try:
        for name, _server, port in servers:
            _wait_health(port, name)
    except Exception:
        for _name, server, _port in servers:
            server.should_exit = True
        raise
    ports["spotify"] = spotify_port
    yield {"base": base, "ports": ports}
    for _name, server, _port in servers:
        server.should_exit = True
    for thread in threads:
        thread.join(timeout=10)