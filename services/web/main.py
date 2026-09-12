"""Web Service / BFF — autentica sessão, serve a UI estática e compõe
as respostas dos serviços internos para o frontend."""

from __future__ import annotations

import os
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.service import _ACTIVE_SERVICE, BaseConfig, setup_logging  # noqa: F401
from spotfy_contracts.store import SqliteStore

cfg = BaseConfig("web")
logger = setup_logging("web", cfg.log_level)

app = FastAPI(title="spotfy-manager-v2 · web", version="0.1.0")

# --- Persistência local do BFF: tokens Spotify por usuário ------------------
_web_store = SqliteStore(os.path.join(cfg.data_dir, "web_spotify.sqlite"))
_web_store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS spotify_tokens (
        owner TEXT PRIMARY KEY,
        access_token TEXT NOT NULL,
        expires_at REAL NOT NULL,
        pending INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
])

_start = time.time()
_request_count = {"count": 0}


@app.middleware("http")
async def _count_requests(request: Request, call_next):
    response = await call_next(request)
    _request_count["count"] += 1
    return response


@app.get("/health")
def health() -> dict:
    return {"service": "web", "status": "ok"}


@app.get("/metrics")
def metrics() -> str:
    body = (
        "# HELP http_requests_total Total de requisições HTTP.\n"
        "# TYPE http_requests_total counter\n"
        f'http_requests_total{{service="web"}} {_request_count["count"]}\n'
        f'process_uptime_seconds{{service="web"}} {int(time.time() - _start)}\n'
    )
    return body


# --- Spotify OAuth (simplificado, token por usuário) -------------------------

_SPOTIFY_SCOPES = "user-library-read playlist-read-private playlist-read-collaborative"
_PENDING_OWNER = "__pending__"


def _current_user(request: Request) -> str:
    return current_username_from_header(cfg.jwt_secret, request.headers.get("Authorization", ""))


def _get_spotify_token(owner: str) -> dict | None:
    row = _web_store.query_one(
        "SELECT access_token, expires_at FROM spotify_tokens WHERE owner = ? AND pending = 0", (owner,))
    if not row:
        return None
    if row["expires_at"] <= time.time():
        _web_store.execute("DELETE FROM spotify_tokens WHERE owner = ? AND pending = 0", (owner,))
        return None
    return {"access_token": row["access_token"], "expires_at": float(row["expires_at"])}


def _save_spotify_token(owner: str, token: str, expires_in: float, pending: int = 0) -> None:
    _web_store.execute(
        "INSERT OR REPLACE INTO spotify_tokens (owner, access_token, expires_at, pending, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (owner, token, time.time() + expires_in, pending),
    )


@app.get("/api/auth/spotify")
def spotify_auth_url() -> dict:
    """Gera a URL de autorização do Spotify. O usuário visita esta URL."""
    client_id = cfg.spotify_client_id
    if not client_id:
        raise_error("SPOTIFY_NOT_CONNECTED", "SPOTIFY_CLIENT_ID não configurado no .env")
    redirect_uri = "http://localhost:8000/api/auth/spotify/callback"
    url = (
        f"https://accounts.spotify.com/authorize?"
        f"client_id={client_id}&response_type=code&redirect_uri={redirect_uri}"
        f"&scope={_SPOTIFY_SCOPES.replace(' ', '%20')}&show_dialog=true"
    )
    return {"url": url, "redirect_uri": redirect_uri}


@app.get("/api/auth/spotify/callback")
async def spotify_callback(code: str = "", error: str = ""):
    """Callback do Spotify: troca o code por token e guarda como pendente."""
    if error:
        return HTMLResponse(f"<h3>Erro: {error}</h3><p><a href='/'>Voltar</a></p>")
    if not code:
        return HTMLResponse("<h3>Código não recebido</h3><p><a href='/'>Voltar</a></p>")
    token_data = await _exchange_spotify_code(code)
    if token_data:
        _save_spotify_token(_PENDING_OWNER, token_data["access_token"], token_data["expires_in"], pending=1)
        return HTMLResponse(
            "<h3>Spotify conectado!</h3>"
            "<p>Token salvo. Pode fechar esta janela e usar as playlists.</p>"
            "<script>setTimeout(function(){ window.close(); }, 1500);</script>"
        )
    return HTMLResponse("<h3>Falha ao obter token</h3><p><a href='/'>Voltar</a></p>")


async def _exchange_spotify_code(code: str) -> dict | None:
    """Troca o code de autorização por um access_token via POST ao Spotify."""
    client_id = cfg.spotify_client_id
    client_secret = cfg.spotify_client_secret
    if not client_id or not client_secret:
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://localhost:8000/api/auth/spotify/callback",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    if resp.status_code == 200:
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "expires_in": float(data.get("expires_in", 3600)),
        }
    return None


@app.get("/api/auth/spotify/status")
def spotify_status(request: Request) -> dict:
    """Estado do token do Spotify para o usuário autenticado (por usuário)."""
    owner = _current_user(request)
    token = _get_spotify_token(owner)
    if token:
        return {"connected": True, "expires_in": int(token["expires_at"] - time.time())}
    return {"connected": False}


@app.post("/api/auth/spotify/token")
def set_spotify_token(payload: dict, request: Request) -> dict:
    """Salva um token de acesso manual para o usuário autenticado."""
    token = (payload.get("token") or "").strip()
    if not token:
        raise_error("INVALID_PAYLOAD", "Token não pode ser vazio.")
    owner = _current_user(request)
    _save_spotify_token(owner, token, 3600)
    logger.info("token Spotify salvo por usuário %s", owner)
    return {"ok": True, "expires_in": 3600}


@app.post("/api/auth/spotify/claim")
def claim_spotify_token(request: Request) -> dict:
    """Adota o token pendente do OAuth para o usuário autenticado."""
    owner = _current_user(request)
    row = _web_store.query_one(
        "SELECT access_token, expires_at FROM spotify_tokens WHERE owner = ? AND pending = 1", (_PENDING_OWNER,))
    if not row:
        raise_error("NOT_FOUND", "Nenhum token Spotify pendente de autorização.")
    _save_spotify_token(owner, row["access_token"], max(0.0, row["expires_at"] - time.time()))
    _web_store.execute("DELETE FROM spotify_tokens WHERE owner = ? AND pending = 1", (_PENDING_OWNER,))
    logger.info("token Spotify vinculado ao usuário %s", owner)
    return {"ok": True}


def _service_root(name: str) -> str:
    return cfg.url(name)


async def _proxy(request: Request, service: str, path: str, params: dict | None = None, extra_headers: dict | None = None):
    params = dict(request.query_params) if params is None else params
    headers = {"Authorization": request.headers.get("Authorization", ""),
               "X-Request-Id": request.headers.get("X-Request-Id") or str(uuid.uuid4())}
    if extra_headers:
        headers.update(extra_headers)
    content_type = request.headers.get("Content-Type", "application/json")
    url = f"{_service_root(service)}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        if request.method == "POST":
            body = await request.body()
            resp = await client.request(
                request.method, url, content=body if body else None,
                headers={**headers, "Content-Type": content_type},
                params=params)
        else:
            resp = await client.request(request.method, url, headers=headers, params=params)
    ct = resp.headers.get("content-type", "application/json")
    if "application/json" in ct:
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return JSONResponse(status_code=resp.status_code, content=data)
    extra: dict = {}
    if resp.headers.get("content-disposition"):
        extra["content-disposition"] = resp.headers["content-disposition"]
    return Response(content=resp.content, status_code=resp.status_code, media_type=ct, headers=extra)


@app.post("/api/auth/login")
async def login(request: Request):
    return await _proxy(request, "identity", "/auth/login", params={})


@app.post("/api/auth/register")
async def register(request: Request):
    return await _proxy(request, "identity", "/auth/register", params={})


@app.get("/api/auth/me")
async def me(request: Request):
    return await _proxy(request, "identity", "/auth/me")


@app.get("/api/me")
async def whoami(request: Request):
    auth = request.headers.get("Authorization", "")
    try:
        username = current_username_from_header(cfg.jwt_secret, auth)
    except Exception as exc:
        return JSONResponse(status_code=401, content={"error": "UNAUTHORIZED", "detail": str(exc)})
    return {"username": username}


# --- Importação ------------------------------------------------------------

@app.post("/api/import/file")
async def import_file(request: Request):
    # encaminha multipart para o fileimport
    headers = {"Authorization": request.headers.get("Authorization", "")}
    url = f"{_service_root('fileimport')}/import"
    async with httpx.AsyncClient(timeout=60.0) as client:
        body = await request.body()
        content_type = request.headers.get("Content-Type", "multipart/form-data")
        resp = await client.post(url, content=body, headers={
            "Authorization": headers["Authorization"], "Content-Type": content_type})
    try:
        data = resp.json()
    except Exception:
        data = resp.text
    return JSONResponse(status_code=resp.status_code, content=data)


@app.post("/api/import/spotify")
async def import_spotify(request: Request):
    return await _proxy(request, "playlist", "/import", extra_headers=_spotify_headers(request))


@app.get("/api/playlists")
async def playlists(request: Request):
    return await _proxy(request, "playlist", "/playlists", extra_headers=_spotify_headers(request))


def _spotify_headers(request: Request) -> dict:
    """Injeta o token do Spotify do usuário no header para o playlist service."""
    token = _get_spotify_token(_current_user(request))
    if token:
        return {"X-Spotify-Token": token["access_token"]}
    return {}


@app.get("/api/synced")
async def synced(request: Request):
    return await _proxy(request, "playlist", "/synced")


@app.get("/api/synced/{key}")
async def synced_one(key: str, request: Request):
    return await _proxy(request, "playlist", f"/synced/{key}")


@app.get("/api/imports")
async def import_imports(request: Request):
    return await _proxy(request, "fileimport", "/imports")


@app.get("/api/import/{import_id}")
async def import_one(import_id: str, request: Request):
    return await _proxy(request, "fileimport", f"/import/{import_id}")


# --- BPM Match -------------------------------------------------------------

@app.get("/api/tolerance")
async def tolerance(request: Request):
    return await _proxy(request, "bpm-match", "/tolerance")


@app.post("/api/analyze")
async def analyze(request: Request):
    return await _proxy(request, "bpm-match", "/analyze")


@app.get("/api/analyses")
async def analyses(request: Request):
    return await _proxy(request, "bpm-match", "/analyses")


@app.get("/api/analyses/{analysis_id}")
async def analysis_one(analysis_id: str, request: Request):
    return await _proxy(request, "bpm-match", f"/analyses/{analysis_id}")


@app.post("/api/analyses/{analysis_id}/review")
async def analysis_review(analysis_id: str, request: Request):
    return await _proxy(request, "bpm-match", f"/analyses/{analysis_id}/review")


# --- Biblioteca / Downloads ------------------------------------------------

@app.get("/api/library")
async def library(request: Request):
    return await _proxy(request, "library", "/library")


@app.post("/api/library/compare")
async def library_compare(request: Request):
    return await _proxy(request, "library", "/compare")


@app.post("/api/library/dryrun")
async def library_dryrun(request: Request):
    return await _proxy(request, "library", "/dryrun")


@app.post("/api/library/download")
async def library_download(request: Request):
    return await _proxy(request, "library", "/download")


@app.get("/api/library/jobs/{job_id}")
async def library_job(job_id: str, request: Request):
    return await _proxy(request, "library", f"/jobs/{job_id}")


# --- Relatórios ------------------------------------------------------------

@app.post("/api/reports")
async def reports_create(request: Request):
    return await _proxy(request, "report", "/reports")


@app.get("/api/reports")
async def reports_list(request: Request):
    return await _proxy(request, "report", "/reports")


@app.get("/api/reports/{report_id}/download")
async def reports_download(report_id: str, request: Request):
    params = dict(request.query_params)
    url = f"{_service_root('report')}/reports/{report_id}/download"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers={"Authorization": request.headers.get("Authorization", "")}, params=params)
    extra: dict = {}
    if resp.headers.get("content-disposition"):
        extra["content-disposition"] = resp.headers["content-disposition"]
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers=extra,
    )


# --- UI estática -----------------------------------------------------------

_base = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_base, exist_ok=True)
app.mount("/static", StaticFiles(directory=_base), name="static")
# arquivos dedicados priorizados, fallback para o index (SPA simples)
app.mount("/", StaticFiles(directory=_base, html=True), name="root")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)