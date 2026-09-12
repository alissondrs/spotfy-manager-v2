"""File & Import Service — upload, validação e parsing de Markdown/CSV/JSON."""

from __future__ import annotations

import json
import os
import uuid

from fastapi import Depends, File, Header, UploadFile
from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.files import parse_import_content, validate_upload
from spotfy_contracts.schemas import TrackInput, utcnow_iso
from spotfy_contracts.service import build_app
from spotfy_contracts.store import SqliteStore

SERVICE_NAME = "fileimport"
app = build_app(SERVICE_NAME)
cfg = app.state.config
uploads_dir = os.path.join(cfg.data_dir, "uploads")
os.makedirs(uploads_dir, exist_ok=True)
store = SqliteStore(os.path.join(cfg.data_dir, f"{SERVICE_NAME}_db.sqlite"))
store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS imports (
        import_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
])


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


@app.post("/import")
async def import_playlist(
    file: UploadFile = File(...),
    username: str = Depends(_require_user),
) -> dict:
    raw = await file.read()
    validate_upload(file.filename or "", len(raw))
    name, tracks = parse_import_content(file.filename or "", raw)
    if not tracks:
        raise_error("PARSED_TRACKS_EMPTY")
    import_id = str(uuid.uuid4())
    created_at = utcnow_iso()
    entry = {
        "import_id": import_id,
        "filename": file.filename,
        "name": name,
        "track_count": len(tracks),
        "created_at": created_at,
        "owner": username,
        "tracks": [t.model_dump(mode="json") for t in tracks],
    }
    store.execute(
        "INSERT OR REPLACE INTO imports (import_id, owner, payload, created_at) VALUES (?, ?, ?, ?)",
        (import_id, username, json.dumps(entry, ensure_ascii=False), created_at),
    )
    app.state.logger.info("importação: %s (%d faixas) por %s", file.filename, len(tracks), username)
    return {
        "import_id": import_id,
        "filename": file.filename,
        "name": name or file.filename,
        "track_count": len(tracks),
        "preview": [t.model_dump(mode="json") for t in tracks[:5]],
    }


@app.get("/import/{import_id}", response_model=dict)
def get_import(import_id: str, username: str = Depends(_require_user)) -> dict:
    row = store.query_one("SELECT owner, payload FROM imports WHERE import_id = ?", (import_id,))
    if not row:
        raise_error("NOT_FOUND", "Importação não encontrada.")
    if row["owner"] != username:
        raise_error("FORBIDDEN")
    entry = json.loads(row["payload"])
    return entry | {"tracks": [TrackInput(**t).model_dump(mode="json") for t in entry["tracks"]]}


@app.get("/imports")
def list_imports(username: str = Depends(_require_user)) -> dict:
    rows = store.query("SELECT payload FROM imports WHERE owner = ? ORDER BY created_at DESC", (username,))
    mine = [json.loads(r["payload"]) for r in rows]
    return {"imports": [{k: v for k, v in e.items() if k not in ("tracks", "owner")} for e in mine]}


@app.post("/validate")
async def validate_file(file: UploadFile = File(...), _username: str = Depends(_require_user)) -> dict:
    raw = await file.read()
    validate_upload(file.filename or "", len(raw))
    name, tracks = parse_import_content(file.filename or "", raw)
    return {"valid": True, "filename": file.filename, "name": name, "track_count": len(tracks)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)