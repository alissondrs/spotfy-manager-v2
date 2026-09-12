"""Identity Service — usuários locais, hash de senha, tokens JWT e autorização."""

from __future__ import annotations

import os

from fastapi import Depends, Header
from spotfy_contracts.auth import (
    create_token,
    current_username_from_header,
    hash_password,
    now_iso,
    verify_password,
)
from spotfy_contracts.errors import raise_error
from spotfy_contracts.schemas import TokenResponse, UserCreate, UserLogin, UserPublic
from spotfy_contracts.service import build_app
from spotfy_contracts.store import SqliteStore

SERVICE_NAME = "identity"
app = build_app(SERVICE_NAME)
cfg = app.state.config
store = SqliteStore(f"{cfg.data_dir}/{SERVICE_NAME}_db.sqlite")
store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
])

_REDACTED = {"password", "password_hash", "token"}
_BOOTSTRAP_ENABLED = (
    cfg.app_env.lower() == "development"
    and os.getenv("IDENTITY_ENABLE_BOOTSTRAP", "0") in ("1", "true", "yes")
)


def _safe(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in _REDACTED}


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


@app.post("/auth/register")
def register(payload: UserCreate) -> UserPublic:
    if store.query_one("SELECT 1 FROM users WHERE username = ?", (payload.username,)):
        raise_error("CONFLICT", f"Usuário '{payload.username}' já existe.")
    created = now_iso()
    store.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (payload.username, hash_password(payload.password), created),
    )
    app.state.logger.info("usuário registrado: %s", payload.username)
    return UserPublic(username=payload.username, created_at=created)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: UserLogin) -> TokenResponse:
    row = store.query_one("SELECT * FROM users WHERE username = ?", (payload.username,))
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise_error("UNAUTHORIZED", "Usuário ou senha inválidos.")
    token, expires_in = create_token(
        payload.username, cfg.jwt_secret, cfg.jwt_ttl_minutes,
        extra={"role": "admin"},
    )
    app.state.logger.info("login: %s", payload.username)
    return TokenResponse(token=token, expires_in=expires_in, username=payload.username)


@app.get("/auth/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    username = current_username_from_header(cfg.jwt_secret, authorization)
    row = store.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not row:
        raise_error("UNAUTHORIZED")
    return {"username": username, "created_at": row["created_at"], "authenticated": True}


@app.get("/auth/users")
def list_users(_username: str = Depends(_require_user)) -> dict:
    rows = store.query("SELECT username, created_at FROM users")
    return {"users": [_safe(r) for r in rows]}


@app.get("/users/{username}")
def get_user(username: str, _authenticated: str = Depends(_require_user)) -> UserPublic:
    row = store.query_one("SELECT username, created_at FROM users WHERE username = ?", (username,))
    if not row:
        raise_error("NOT_FOUND", f"Usuário '{username}' não encontrado.")
    return UserPublic(username=row["username"], created_at=row["created_at"])


@app.get("/_debug/bootstrap")
def bootstrap() -> dict:
    """Cria um usuário administrador padrão (apenas em desenvolvimento)."""
    if not _BOOTSTRAP_ENABLED:
        raise_error("NOT_FOUND")
    if store.query_one("SELECT 1 FROM users WHERE username = ?", ("admin",)):
        return {"status": "exists"}
    return register(UserCreate(username="admin", password="admin12345")).model_dump()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)