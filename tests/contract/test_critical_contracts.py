"""Critical contract and persistence regressions derived from the documented safety rules."""

from __future__ import annotations

import json

import jwt
import pytest
from spotfy_contracts import auth, errors
from spotfy_contracts.files import MAX_UPLOAD_BYTES, parse_import_content, validate_upload
from spotfy_contracts.ids import (
    artist_matches,
    parse_source_reference,
    title_matches,
)
from spotfy_contracts.store import JsonStore, SqliteStore

STRONG_SECRET = "qa-secret-long-enough-1234567890-abcdefghijklmnopqrstuvwxyz"


def test_source_reference_accepts_only_documented_kinds():
    assert parse_source_reference(" spotify:playlist-123 ") == ("spotify", "playlist-123")
    assert parse_source_reference("FILE:import-456") == ("file", "import-456")
    for invalid in (None, "", "spotify:", ":id", "http:playlist", "tidal:123"):
        assert parse_source_reference(invalid) is None


def test_matching_is_accent_insensitive_and_conservative_for_unrelated_tracks():
    assert title_matches("Coração", "Coracao") == 1.0
    assert artist_matches("Beyoncé", ["Beyonce"]) == 1.0
    assert title_matches("One More Time", "Completely Different", threshold=0.8) == 0.0
    assert artist_matches("Daft Punk", ["The Beatles"], threshold=0.8) == 0.0


def test_upload_size_boundary_and_case_insensitive_extension():
    validate_upload("SET.JSON", MAX_UPLOAD_BYTES)
    with pytest.raises(errors.DomainError) as exc:
        validate_upload("set.json", MAX_UPLOAD_BYTES + 1)
    assert exc.value.code == "FILE_TOO_LARGE"

    with pytest.raises(errors.DomainError) as exc:
        validate_upload("set.csv", 0)
    assert exc.value.code == "FILE_EMPTY"


def test_non_utf8_upload_has_stable_parse_error():
    with pytest.raises(errors.DomainError) as exc:
        parse_import_content("set.md", b"\xff\xfe")
    assert exc.value.code == "FILE_PARSE_ERROR"


@pytest.mark.parametrize(
    "payload",
    [
        {"playlist": []},
        {"tracks": {"name": "not-a-track-list"}},
    ],
)
def test_malformed_json_container_is_rejected_as_domain_error(payload):
    """Malformed JSON must not crash or silently fabricate tracks."""
    with pytest.raises(errors.DomainError) as exc:
        parse_import_content("set.json", json.dumps(payload).encode())
    assert exc.value.code == "FILE_PARSE_ERROR"


def test_expired_jwt_is_rejected():
    token, _ = auth.create_token("alice", STRONG_SECRET, 0)
    with pytest.raises(errors.DomainError) as exc:
        auth.decode_token(token, STRONG_SECRET)
    assert exc.value.code == "UNAUTHORIZED"


def test_jwt_without_subject_is_rejected():
    token = jwt.encode({"iat": 1, "exp": 4_102_444_800}, STRONG_SECRET, algorithm="HS256")
    with pytest.raises(errors.DomainError) as exc:
        auth.current_username_from_header(STRONG_SECRET, f"Bearer {token}")
    assert exc.value.code == "UNAUTHORIZED"


def test_sqlite_persists_across_reopen(tmp_path):
    path = tmp_path / "state.sqlite"
    first = SqliteStore(str(path))
    first.init_schema(["CREATE TABLE state (id TEXT PRIMARY KEY, value TEXT NOT NULL)"])
    first.execute("INSERT INTO state (id, value) VALUES (?, ?)", ("analysis", "completed"))
    first.close()

    second = SqliteStore(str(path))
    assert second.query_one("SELECT value FROM state WHERE id = ?", ("analysis",)) == {
        "value": "completed"
    }
    second.close()


def test_json_store_atomic_persistence_and_delete(tmp_path):
    path = tmp_path / "state.json"
    store = JsonStore(str(path))
    store.set("playlist", {"tracks": ["one", "two"]})
    assert not (tmp_path / "state.json.tmp").exists()
    assert JsonStore(str(path)).get("playlist") == {"tracks": ["one", "two"]}

    store.delete("playlist")
    assert JsonStore(str(path)).get("playlist") is None
