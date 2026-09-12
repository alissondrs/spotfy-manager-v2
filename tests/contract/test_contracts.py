"""Testes unitários dos contratos compartilhados (sem serviços)."""

from __future__ import annotations

import pytest

from spotfy_contracts import auth, errors
from spotfy_contracts.files import parse_import_content, validate_upload
from spotfy_contracts.ids import (
    artist_matches,
    detect_version,
    normalize_text,
    sanitize_filename,
    strip_parenthesized,
    title_matches,
)
from spotfy_contracts.store import SqliteStore


STRONG_SECRET = "test-secret-long-enough-1234567890-abcdefghijklmnopqrstuvwxyz"
OTHER_STRONG_SECRET = "other-secret-long-enough-0987654321-abcdefghijklmnopqrstuvwxyz"


def test_normalize_text():
    assert normalize_text("  Get  Lucky  (feat. Pharrell) ") == "get lucky (feat. pharrell)"
    assert normalize_text("Coração de Maria", remove_accents=True) == "coracao de maria"


def test_title_matches():
    assert title_matches("One More Time", "One More Time") == 1.0
    assert title_matches("One More Time", "One More Time (Radio Edit)") > 0.85
    assert title_matches("Get Lucky", "Something Else") < 0.5


def test_artist_matches():
    assert artist_matches("Daft Punk", ["Daft Punk"]) >= 0.9
    assert artist_matches("Daft Punk", ["Daft Punk", "Pharrell Williams"]) >= 0.6
    assert artist_matches("Daft Punk", ["The Beatles"], threshold=0.6) < 0.3


def test_detect_version():
    assert detect_version("One More Time") is None
    assert detect_version("One More Time (Radio Edit)") is not None
    assert detect_version("Get Lucky (Live)") is not None
    assert detect_version("Harder Better Faster Stronger") is None


def test_strip_and_sanitize():
    assert strip_parenthesized("Get Lucky (Radio Edit)") == "Get Lucky"
    assert "/" not in sanitize_filename("One More Time - Daft Punk")
    assert sanitize_filename("A/B*C") == "A B C"


def test_password_roundtrip():
    hashed = auth.hash_password("senha12345")
    assert hashed != "senha12345"
    assert auth.verify_password("senha12345", hashed)
    assert not auth.verify_password("errada", hashed)


def test_jwt_roundtrip():
    token, ttl = auth.create_token("alice", "test-secret-long-enough-1234567890", 60, extra={"role": "admin"})
    payload = auth.decode_token(token, "test-secret-long-enough-1234567890")
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"
    assert ttl == 3600
    assert payload["exp"] > payload["iat"]


def test_jwt_wrong_secret():
    token, _ = auth.create_token("alice", OTHER_STRONG_SECRET, 60)
    with pytest.raises(errors.DomainError):
        auth.decode_token(token, STRONG_SECRET)


def test_current_username_from_header():
    token, _ = auth.create_token("bob", STRONG_SECRET, 60)
    assert auth.current_username_from_header(STRONG_SECRET, f"Bearer {token}") == "bob"
    with pytest.raises(errors.DomainError):
        auth.current_username_from_header(STRONG_SECRET, "")


def test_validate_jwt_secret():
    auth.validate_jwt_secret("strong-secret-1234567890-abcdefghijklmnopqrstuvwxyz", "production")
    with pytest.raises(RuntimeError):
        auth.validate_jwt_secret("dev-secret-change-me", "production")
    with pytest.raises(RuntimeError):
        auth.validate_jwt_secret("short-secret", "production")


def test_parse_markdown():
    raw = b"# Meu Set\n\n1. One More Time - Daft Punk\n2. Get Lucky - Daft Punk, Pharrell\n"
    name, tracks = parse_import_content("set.md", raw)
    assert name == "Meu Set"
    assert len(tracks) == 2
    assert tracks[0].name == "One More Time"
    assert tracks[0].artists == ["Daft Punk"]
    assert tracks[1].artists == ["Daft Punk", "Pharrell"]


def test_parse_csv():
    raw = b"name,artists\nInstant Crush,Daft Punk;Julian Casablancas\n"
    _name, tracks = parse_import_content("set.csv", raw)
    assert len(tracks) == 1
    assert tracks[0].artists == ["Daft Punk", "Julian Casablancas"]


def test_parse_json():
    raw = b'{"tracks": [{"name": "Voyager", "artists": ["Daft Punk"]}]}'
    _name, tracks = parse_import_content("set.json", raw)
    assert tracks[0].name == "Voyager"


def test_validate_upload():
    with pytest.raises(errors.DomainError):
        validate_upload("set.txt", 20)
    validate_upload("set.md", 20)


def test_domain_errors():
    with pytest.raises(errors.DomainError) as exc:
        errors.raise_error("INVALID_PAYLOAD", "mensagem")
    assert exc.value.code == "INVALID_PAYLOAD"
    assert exc.value.message == "mensagem"


def test_sqlite_store():
    store = SqliteStore(":memory:")
    store.init_schema(["CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY, v TEXT)"])
    store.execute("INSERT OR REPLACE INTO t (id, v) VALUES (?, ?)", ("a", "1"))
    row = store.query_one("SELECT v FROM t WHERE id = ?", ("a",))
    assert row["v"] == "1"
    assert len(store.query("SELECT * FROM t")) == 1