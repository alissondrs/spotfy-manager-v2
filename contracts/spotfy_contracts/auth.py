"""Autenticação compartilhada: hash de senha, JWT e dependência FastAPI."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from .errors import raise_error

try:
    import jwt as _jwt
except Exception:  # pragma: no cover
    _jwt = None

PBKDF2_ITERATIONS = 240_000
MIN_JWT_SECRET_LENGTH = 32
_KNOWN_WEAK_JWT_SECRETS = {
    "",
    "dev-secret-change-me",
    "change-me",
    "changeme",
    "secret",
    "jwt-secret",
    "test-secret",
}


def _new_salt() -> bytes:
    return os.urandom(16)


def hash_password(plain: str) -> str:
    """Hash de senha com PBKDF2-HMAC-SHA256 (nunca guardar senha em claro)."""
    salt = _new_salt()
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(plain: str, stored: str) -> bool:
    try:
        _algo, _iterations, _salt_b64, _digest_b64 = stored.split("$")
        iterations = int(_iterations)
        salt = base64.b64decode(_salt_b64)
        expected = base64.b64decode(_digest_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def create_token(subject: str, secret: str, ttl_minutes: int, extra: Optional[dict] = None) -> tuple[str, int]:
    """Gera JWT HS256. Retorna (token, expires_in_segundos)."""
    if _jwt is None:
        raise RuntimeError("PyJWT não instalado")
    now = int(time.time())
    payload = {"sub": subject, "iat": now, "exp": now + ttl_minutes * 60, "jti": str(uuid4())}
    if extra:
        payload.update(extra)
    token = _jwt.encode(payload, secret, algorithm="HS256")
    return token, ttl_minutes * 60


def decode_token(token: str, secret: str) -> dict:
    """Valida JWT e retorna o payload. Falha com DomainError UNAUTHORIZED."""
    if not token:
        raise_error("UNAUTHORIZED")
    if _jwt is None:
        raise_error("INTERNAL", "PyJWT não instalado")
    try:
        payload = _jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        raise_error("UNAUTHORIZED", "Token inválido ou expirado.")
    return payload


def current_username_from_header(secret: str, authorization: Optional[str] = None) -> str:
    """Extrai o usuário autenticado do header Authorization: Bearer <token>."""
    if not authorization:
        raise_error("UNAUTHORIZED")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise_error("UNAUTHORIZED")
    payload = decode_token(value.strip(), secret)
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise_error("UNAUTHORIZED", "Token sem subject.")
    return sub


def new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_jwt_secret(secret: str, app_env: str, allow_insecure_dev: bool = False) -> None:
    """Valida JWT_SECRET e falha cedo em configuração insegura."""
    normalized_env = (app_env or "development").strip().lower()
    value = (secret or "").strip()
    insecure_secret = value.lower() in _KNOWN_WEAK_JWT_SECRETS
    too_short = len(value) < MIN_JWT_SECRET_LENGTH
    is_dev_like = normalized_env in {"development", "dev", "test"}

    if not value:
        raise RuntimeError("JWT_SECRET não configurado.")
    if insecure_secret or too_short:
        if is_dev_like and allow_insecure_dev:
            return
        raise RuntimeError(
            "JWT_SECRET inseguro: use no mínimo 32 caracteres e evite valores padrão."
        )