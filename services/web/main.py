"""Web Service / BFF — sessões anônimas por navegador (cookie HttpOnly),
OAuth Spotify Authorization Code + PKCE (S256), refresh automático e
composição das respostas dos serviços internos para o frontend.

O BFF não aceita mais `Authorization` vindo do navegador: para cada chamada
downstream ele emite um JWT interno curto com `sub = owner_claim` da sessão,
mantendo compatibilidade com `current_username_from_header()` dos serviços.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from spotfy_contracts.auth import create_token
from spotfy_contracts.errors import ERROR_CATALOG, DomainError, raise_error
from spotfy_contracts.service import BaseConfig, setup_logging
from spotfy_contracts.store import SqliteStore

cfg = BaseConfig("web")
logger = setup_logging("web", cfg.log_level)

app = FastAPI(title="spotfy-manager-v2 · web", version="0.1.0")

# --- Persistência do BFF: sessões, transações OAuth e conexões Spotify -------

_web_store = SqliteStore(os.path.join(cfg.data_dir, "web_spotify.sqlite"))
_web_store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        csrf_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_transactions (
        state TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        code_verifier_enc TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS spotify_connections (
        owner TEXT PRIMARY KEY,
        access_token_enc TEXT NOT NULL,
        refresh_token_enc TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL,
        scopes TEXT NOT NULL DEFAULT '',
        spotify_user_id TEXT,
        revoked INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    # Tabela legada mantida APENAS para compatibilidade/rollback (sem novas gravações).
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
if not _web_store.query_one("SELECT version FROM schema_meta LIMIT 1"):
    _web_store.execute("INSERT INTO schema_meta (version) VALUES (1)")

_start = time.time()
_request_count = {"count": 0}


# --- Criptografia em repouso (tokens e code_verifier) -----------------------

def _encryption_key() -> str:
    """Retorna a chave Fernet em base64url (a string que o Fernet espera)."""
    raw = (cfg.web_token_encryption_key or "").strip()
    if raw:
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        except Exception:
            raise RuntimeError(
                "WEB_TOKEN_ENCRYPTION_KEY inválida: esperado base64url (32 bytes Fernet).")
        if len(decoded) != 32:
            raise RuntimeError(
                "WEB_TOKEN_ENCRYPTION_KEY inválida: esperado base64url (32 bytes Fernet).")
        return raw
    env = (cfg.app_env or "development").lower()
    if env not in ("development", "dev", "test"):
        raise RuntimeError("WEB_TOKEN_ENCRYPTION_KEY é obrigatória em produção.")
    key_path = os.path.join(cfg.data_dir, "web_encryption.key")
    key = ""
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="ascii") as fh:
            key = fh.read().strip()
    if not key:
        key = Fernet.generate_key().decode("ascii")
        os.makedirs(cfg.data_dir, exist_ok=True)
        with open(key_path, "w", encoding="ascii") as fh:
            fh.write(key)
        os.chmod(key_path, 0o600)
        logger.warning("chave de criptografia local gerada em %s (apenas dev)", key_path)
    return key


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_encryption_key().encode("ascii"))
    return _fernet


def _encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _cipher().encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(blob: str) -> str | None:
    if not blob:
        return None
    try:
        return _cipher().decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None


# --- Sessão anônima ----------------------------------------------------------

_SESSION_COOKIE_SECURE_NAME = "__Host-bpm_session"
_SESSION_COOKIE_NAME = "bpm_session"


def _cookie_name() -> str:
    # `__Host-` exige Secure; em dev (http) usamos o nome simples para o
    # navegador aceitar o cookie.
    return _SESSION_COOKIE_SECURE_NAME if cfg.session_cookie_secure else _SESSION_COOKIE_NAME


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).digest().hex()


def _new_session_token() -> str:
    return secrets.token_urlsafe(32)  # ≥ 256 bits


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_cookie_name(),
        value=token,
        max_age=cfg.session_absolute_ttl_seconds,
        httponly=True,
        secure=cfg.session_cookie_secure,
        samesite=cfg.session_cookie_samesite,
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(key=_cookie_name(), path="/", secure=cfg.session_cookie_secure)


def _persist_session(token: str, owner: str, csrf: str, now: float) -> dict:
    row = {
        "token_hash": _sha256(token),
        "owner": owner,
        "csrf_hash": _sha256(csrf),
        "created_at": now,
        "last_seen_at": now,
        "expires_at": now + cfg.session_absolute_ttl_seconds,
        "revoked": 0,
    }
    _web_store.execute(
        "INSERT INTO sessions (token_hash, owner, csrf_hash, created_at, last_seen_at, expires_at, revoked) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row["token_hash"], row["owner"], row["csrf_hash"], row["created_at"],
         row["last_seen_at"], row["expires_at"], row["revoked"]),
    )
    return row


def _session_valid(row: dict) -> bool:
    if row["revoked"]:
        return False
    now = time.time()
    if now >= float(row["expires_at"]):
        return False
    if now - float(row["last_seen_at"]) > cfg.session_idle_ttl_seconds:
        return False
    return True


def _read_session(request: Request) -> tuple[str, dict] | None:
    """Retorna (token, row) se o cookie da sessão for válido; senão None."""
    token = request.cookies.get(_cookie_name())
    if not token:
        return None
    row = _web_store.query_one("SELECT * FROM sessions WHERE token_hash = ?", (_sha256(token),))
    if not row or not _session_valid(row):
        if row:
            _web_store.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    _web_store.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                       (time.time(), row["token_hash"]))
    return token, row


def _attach_session(request: Request, token: str, row: dict) -> None:
    request.state.session_token = token
    request.state.session_row = row
    request.state.owner = row["owner"]


def _valid_csrf(row: dict, header: str) -> bool:
    if not header:
        return False
    return hmac.compare_digest(_sha256(header), row["csrf_hash"])


def _msg(code: str) -> str:
    _status, message = ERROR_CATALOG.get(code, ERROR_CATALOG["INTERNAL"])
    return message or "Erro."


@app.middleware("http")
async def _guard_api(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    method = request.method
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        if path in ("/api/auth/login", "/api/auth/register"):
            return await call_next(request)  # rotas retornam 410 sempre
        session = _read_session(request)
        if not session:
            return JSONResponse(status_code=401, content={"error": "SESSION_INVALID", "detail": _msg("SESSION_INVALID")})
        token, row = session
        _attach_session(request, token, row)
        if not _valid_csrf(row, request.headers.get("X-CSRF-Token", "")):
            return JSONResponse(status_code=403, content={"error": "CSRF_INVALID", "detail": _msg("CSRF_INVALID")})
        return await call_next(request)
    # GET parecidos que não dependem de sessão pré-existente
    if path in ("/api/session", "/api/auth/spotify", "/api/auth/spotify/callback"):
        return await call_next(request)
    session = _read_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "SESSION_INVALID", "detail": _msg("SESSION_INVALID")})
    _attach_session(request, *session)
    return await call_next(request)


@app.exception_handler(DomainError)
async def _domain_error_handler(_request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


@app.exception_handler(Exception)
async def _unexpected_handler(_request: Request, exc: Exception):
    logger.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": "INTERNAL", "detail": str(exc)})


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


def _session_body(row: dict, csrf: str | None = None, created: bool = False) -> dict:
    now = time.time()
    return {
        "anonymous": True,
        "created": created,
        "session": {
            "owner": row["owner"],
            "created_at": int(float(row["created_at"])),
            "expires_in": max(0, int(float(row["expires_at"]) - now)),
            "idle_ttl_seconds": cfg.session_idle_ttl_seconds,
            "absolute_ttl_seconds": cfg.session_absolute_ttl_seconds,
            "csrf_token": csrf or "",
        },
    }


@app.get("/api/session")
def session_v1(request: Request, response: Response) -> JSONResponse:
    """Cria ou recupera a sessão anônima e gera/rotaciona o token CSRF."""
    session = _read_session(request)
    token, row = session if session else (None, None)
    created = False
    if not row:
        owner = f"anon_{uuid.uuid4().hex}"
        token = _new_session_token()
        row = _persist_session(token, owner, secrets.token_urlsafe(32), time.time())
        created = True
        request.state.owner = owner
    else:
        request.state.owner = row["owner"]
    csrf = secrets.token_urlsafe(32)
    _web_store.execute("UPDATE sessions SET csrf_hash = ? WHERE token_hash = ?",
                       (_sha256(csrf), row["token_hash"]))
    resp = JSONResponse(content=_session_body(row, csrf=csrf, created=created))
    _set_cookie(resp, token)
    return resp


@app.get("/api/me")
def me(request: Request):
    return _session_body(request.state.session_row)


@app.get("/api/auth/me")
def auth_me(request: Request):
    return _session_body(request.state.session_row)


@app.post("/api/auth/logout")
def logout(request: Request):
    row = request.state.session_row
    _web_store.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (row["token_hash"],))
    resp = JSONResponse(content={"ok": True})
    _clear_cookie(resp)
    return resp


@app.post("/api/auth/login")
async def login():
    raise_error("LOCAL_AUTH_DISABLED")


@app.post("/api/auth/register")
async def register():
    raise_error("LOCAL_AUTH_DISABLED")


# --- OAuth Spotify (Authorization Code + PKCE) --------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _ensure_session(request: Request, response: Response) -> tuple[str, str]:
    """Garante uma sessão para as rotas públicas que precisam de owner."""
    session = _read_session(request)
    if session:
        token, row = session
        request.state.owner = row["owner"]
        return token, row["owner"]
    token = _new_session_token()
    owner = f"anon_{uuid.uuid4().hex}"
    row = _persist_session(token, owner, secrets.token_urlsafe(32), time.time())
    request.state.owner = owner
    _set_cookie(response, token)
    return token, owner


@app.get("/api/auth/spotify")
def spotify_auth_url(request: Request, response: Response) -> dict:
    """Gera a URL de autorização do Spotify com PKCE S256, vinculada à sessão."""
    client_id = cfg.spotify_client_id
    if not client_id:
        raise_error("SPOTIFY_NOT_CONNECTED", "SPOTIFY_CLIENT_ID não configurado no .env")
    _token, owner = _ensure_session(request, response)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(43)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    now = time.time()
    _web_store.execute(
        "INSERT INTO oauth_transactions (state, owner, code_verifier_enc, created_at, expires_at, used) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (state, owner, _encrypt(verifier), now, now + cfg.oauth_state_ttl_seconds),
    )
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": cfg.spotify_redirect_uri,
        "scope": cfg.spotify_scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "show_dialog": "true",
    }
    url = f"{cfg.spotify_auth_url}?{urlencode(params)}"
    return {"url": url, "redirect_uri": cfg.spotify_redirect_uri}


async def _exchange_code(code: str, verifier: str) -> dict | None:
    """Troca o code de autorização usando code_verifier (sem client_secret)."""
    client_id = cfg.spotify_client_id
    if not client_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(cfg.spotify_token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": cfg.spotify_redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            })
    except httpx.RequestError:
        logger.warning("troca de código Spotify falhou (rede)")
        return None
    if resp.status_code != 200:
        logger.warning("troca de código Spotify falhou: %s", resp.status_code)
        return None
    data = resp.json()
    if not data.get("access_token"):
        return None
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or "",
        "expires_in": float(data.get("expires_in", 3600)),
        "scope": data.get("scope") or "",
    }


async def _fetch_me(access_token: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{cfg.spotify_api_url}/v1/me",
                                    headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 200:
            return (resp.json() or {}).get("id") or None
    except httpx.RequestError:
        pass
    return None


def _save_connection(owner: str, access_token: str, refresh_token: str, expires_in: float,
                     scopes: str, user_id: str | None) -> None:
    existing = _web_store.query_one(
        "SELECT refresh_token_enc FROM spotify_connections WHERE owner = ?", (owner,))
    refresh_enc = _encrypt(refresh_token) if refresh_token else (existing or {}).get("refresh_token_enc", "")
    _web_store.execute(
        "INSERT OR REPLACE INTO spotify_connections "
        "(owner, access_token_enc, refresh_token_enc, expires_at, scopes, spotify_user_id, revoked, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now'))",
        (owner, _encrypt(access_token), refresh_enc, time.time() + expires_in, scopes, user_id),
    )


def _get_connection(owner: str) -> dict | None:
    return _web_store.query_one(
        "SELECT * FROM spotify_connections WHERE owner = ?", (owner,))


def _revoke_connection(owner: str) -> None:
    _web_store.execute(
        "UPDATE spotify_connections SET revoked = 1, expires_at = 0, access_token_enc = '', "
        "refresh_token_enc = '' WHERE owner = ?",
        (owner,),
    )


_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()


def _lock_for(owner: str) -> threading.Lock:
    with _refresh_locks_guard:
        lock = _refresh_locks.get(owner)
        if lock is None:
            lock = threading.Lock()
            _refresh_locks[owner] = lock
        return lock


async def _refresh_spotify_token(owner: str) -> bool:
    """Renova o token antes da expiração com trava por sessão (sem refresh concorrente)."""
    lock = _lock_for(owner)
    with lock:
        row = _get_connection(owner)
        if not row:
            return False
        if float(row["expires_at"]) > time.time():
            return True
        refresh = _decrypt(row.get("refresh_token_enc") or "")
        if not refresh or not cfg.spotify_client_id:
            _revoke_connection(owner)
            return False
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(cfg.spotify_token_url, data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": cfg.spotify_client_id,
                })
        except httpx.RequestError:
            logger.warning("refresh do Spotify falhou (rede) para %s", owner)
            return False
        if resp.status_code == 200:
            data = resp.json()
            access = data.get("access_token")
            if not access:
                return False
            scopes = data.get("scope") or (row.get("scopes") or "")
            # preserva o refresh token anterior quando o Spotify não rotacionar
            _save_connection(owner, access, data.get("refresh_token") or "",
                             float(data.get("expires_in", 3600)), scopes, row.get("spotify_user_id"))
            return True
        err = ""
        try:
            err = (resp.json() or {}).get("error", "")
        except Exception:
            pass
        if resp.status_code in (400, 401) and err == "invalid_grant":
            logger.warning("refresh do Spotify invalid_grant: revogando conexão %s", owner)
            _revoke_connection(owner)
            return False
        logger.warning("refresh do Spotify falhou (%s) para %s", resp.status_code, owner)
        return False


async def _get_spotify_access_token(owner: str) -> str | None:
    row = _get_connection(owner)
    if not row or row["revoked"]:
        return None
    if float(row["expires_at"]) <= time.time():
        if not await _refresh_spotify_token(owner):
            return None
        row = _get_connection(owner)
        if not row:
            return None
    return _decrypt(row.get("access_token_enc") or "")


@app.get("/api/auth/spotify/callback")
async def spotify_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Callback do Spotify: valida state da sessão e troca o code (PKCE)."""
    session = _read_session(request)
    if not session:
        return _oauth_html(ok=False, error="session_missing")
    _token, row = session
    owner = row["owner"]
    if error:
        return _oauth_html(ok=False, error=error)
    if not code or not state:
        return _oauth_html(ok=False, error="missing_params")
    tx = _web_store.query_one("SELECT * FROM oauth_transactions WHERE state = ?", (state,))
    if not tx or tx["owner"] != owner:
        return _oauth_html(ok=False, error="state_invalid")
    if float(tx["expires_at"]) < time.time():
        return _oauth_html(ok=False, error="state_expired")
    if tx["used"]:
        return _oauth_html(ok=False, error="state_reused")
    _web_store.execute("UPDATE oauth_transactions SET used = 1 WHERE state = ?", (state,))
    verifier = _decrypt(tx["code_verifier_enc"])
    if verifier is None:
        return _oauth_html(ok=False, error="state_invalid")
    tok = await _exchange_code(code, verifier)
    if tok is None:
        return _oauth_html(ok=False, error="exchange_failed")
    user_id = await _fetch_me(tok["access_token"])
    _save_connection(owner, tok["access_token"], tok["refresh_token"], tok["expires_in"],
                     tok["scope"], user_id)
    logger.info("spotify conectado para a sessão %s", owner)
    return _oauth_html(ok=True)


@app.get("/api/auth/spotify/status")
def spotify_status(request: Request) -> dict:
    """Estado seguro da conexão Spotify da sessão (nunca expõe tokens)."""
    owner = request.state.owner
    row = _get_connection(owner)
    if row and not row["revoked"] and _decrypt(row.get("access_token_enc") or ""):
        return {"connected": True, "expires_in": max(0, int(float(row["expires_at"]) - time.time()))}
    return {"connected": False}


@app.post("/api/auth/spotify/disconnect")
def spotify_disconnect(request: Request) -> dict:
    _revoke_connection(request.state.owner)
    logger.info("spotify desconectado para a sessão %s", request.state.owner)
    return {"ok": True}


def _oauth_html(ok: bool, error: str = "") -> HTMLResponse:
    if ok:
        status_text = "Spotify conectado! Pode fechar esta janela."
    else:
        status_text = f"Falha ao conectar o Spotify ({error or 'desconhecido'})."
    payload = json.dumps({"type": "bpm-spotify-oauth", "ok": ok, "error": error}).replace("</", "<\\/")
    return HTMLResponse(
        "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
        "<title>Conectar Spotify</title></head><body><p>"
        + status_text
        + "</p><p><a href=\"/\">Voltar ao BPM Match</a></p>"
        + "<script>(function(){var payload=" + payload + ";"
        + "function notify(){if(window.opener){try{window.opener.postMessage(payload,window.location.origin);}catch(e){}}}"
        + "notify();setTimeout(function(){notify();try{window.close();}catch(e){}},600);})();</script></body></html>"
    )


# --- Proxy para os serviços internos -----------------------------------------

def _service_root(name: str) -> str:
    return cfg.url(name)


def _internal_headers(request: Request) -> dict:
    """JWT interno curto com sub=owner_claim para os microsserviços."""
    owner = request.state.owner
    token, _ = create_token(owner, cfg.jwt_secret, cfg.web_internal_jwt_ttl_minutes)
    return {"Authorization": f"Bearer {token}", "X-Request-Id": str(uuid.uuid4())}


async def _spotify_headers(request: Request) -> dict:
    """Injeta o token do Spotify gerenciado pelo BFF (nunca vindo do cliente)."""
    token = await _get_spotify_access_token(request.state.owner)
    if token:
        return {"X-Spotify-Token": token}
    return {}


async def _proxy(request: Request, service: str, path: str, params: dict | None = None,
                 extra_headers: dict | None = None):
    params = dict(request.query_params) if params is None else params
    headers = _internal_headers(request)
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


# --- Importação ----------------------------------------------------------------

@app.post("/api/import/file")
async def import_file(request: Request):
    headers = _internal_headers(request)
    url = f"{_service_root('fileimport')}/import"
    async with httpx.AsyncClient(timeout=60.0) as client:
        body = await request.body()
        content_type = request.headers.get("Content-Type", "multipart/form-data")
        resp = await client.post(url, content=body, headers={
            "Authorization": headers["Authorization"],
            "X-Request-Id": headers["X-Request-Id"],
            "Content-Type": content_type})
    try:
        data = resp.json()
    except Exception:
        data = resp.text
    return JSONResponse(status_code=resp.status_code, content=data)


@app.post("/api/import/spotify")
async def import_spotify(request: Request):
    return await _proxy(request, "playlist", "/import", extra_headers=await _spotify_headers(request))


@app.get("/api/playlists")
async def playlists(request: Request):
    return await _proxy(request, "playlist", "/playlists", extra_headers=await _spotify_headers(request))


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


# --- BPM Match ----------------------------------------------------------------

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


# --- Biblioteca / Downloads ----------------------------------------------------

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


# --- Relatórios ----------------------------------------------------------------

@app.post("/api/reports")
async def reports_create(request: Request):
    return await _proxy(request, "report", "/reports")


@app.get("/api/reports")
async def reports_list(request: Request):
    return await _proxy(request, "report", "/reports")


@app.get("/api/reports/{report_id}/download")
async def reports_download(report_id: str, request: Request):
    params = dict(request.query_params)
    headers = _internal_headers(request)
    url = f"{_service_root('report')}/reports/{report_id}/download"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=headers, params=params)
    extra: dict = {}
    if resp.headers.get("content-disposition"):
        extra["content-disposition"] = resp.headers["content-disposition"]
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers=extra,
    )


# --- UI estática ---------------------------------------------------------------

_base = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_base, exist_ok=True)
app.mount("/static", StaticFiles(directory=_base), name="static")
app.mount("/", StaticFiles(directory=_base, html=True), name="root")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)