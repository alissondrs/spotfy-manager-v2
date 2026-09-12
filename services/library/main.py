"""Library & Download Service — comparação com arquivos locais, dry-run,
seleção de qualidade, download com tagging e status/erros rastreáveis."""

from __future__ import annotations

import os
import re
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import httpx
from fastapi import Depends, Header
from pydantic import BaseModel

from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.ids import normalize_text, sanitize_filename, strip_parenthesized
from spotfy_contracts.schemas import (
    DownloadSelection,
    LibraryFile,
    TrackCandidate,
    TrackInput,
    utcnow_iso,
)
from spotfy_contracts.service import build_app
from spotfy_contracts.store import SqliteStore

SERVICE_NAME = "library"
app = build_app(SERVICE_NAME)
cfg = app.state.config
logger = app.state.logger

DOWNLOAD_DIR = cfg.download_dir or os.path.join(cfg.data_dir, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

store = SqliteStore(os.path.join(cfg.data_dir, f"{SERVICE_NAME}_db.sqlite"))
store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL,
        status TEXT NOT NULL,
        progress REAL NOT NULL,
        message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        result TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dryruns (
        dry_run_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_items (
        job_id TEXT NOT NULL,
        owner TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        track TEXT NOT NULL,
        status TEXT NOT NULL,
        filename TEXT,
        size_bytes INTEGER,
        error TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (job_id, ordinal)
    )
    """,
])
if not store.query_one("SELECT 1 FROM pragma_table_info('jobs') WHERE name = 'owner'"):
    store.execute("ALTER TABLE jobs ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
if not store.query_one("SELECT 1 FROM pragma_table_info('dryruns') WHERE name = 'consumed'"):
    store.execute("ALTER TABLE dryruns ADD COLUMN consumed INTEGER NOT NULL DEFAULT 0")

MATCH_THRESHOLD = 0.85
ACCEPTED_EXTENSIONS = {".flac", ".m4a", ".mp3", ".alac", ".wav", ".aiff", ".opus", ".ogg"}
ALLOWED_DOWNLOAD = os.getenv("ALLOW_DOWNLOADS", "0") == "1"


class CompareReq(BaseModel):
    tracks: list[TrackInput]


class DryRunReq(BaseModel):
    tracks: list[TrackInput]


class DownloadReq(BaseModel):
    dry_run_id: str = ""


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


def _scan_library(directory: str) -> list[LibraryFile]:
    files: list[LibraryFile] = []
    if not os.path.isdir(directory):
        return files
    for root, _dirs, names in os.walk(directory):
        for name in names:
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in ACCEPTED_EXTENSIONS:
                continue
            stem = name[: -len(ext)] if ext else name
            track_name = stem.split(" - ", 1)[0] if " - " in stem else stem
            files.append(LibraryFile(
                filename=name, extension=ext, track_name=track_name,
                normalized=normalize_text(strip_parenthesized(track_name)),
                size_bytes=os.path.getsize(os.path.join(root, name)),
            ))
    return files


def compare_tracks(tracks: list[TrackInput]) -> dict:
    library = _scan_library(DOWNLOAD_DIR)
    known = {f.normalized for f in library}
    matched = 0
    missing: list[TrackInput] = []
    for track in tracks:
        cand = normalize_text(strip_parenthesized(track.name))
        if cand in known:
            matched += 1
            continue
        if any(SequenceMatcher(None, cand, f.normalized).ratio() > MATCH_THRESHOLD for f in library):
            matched += 1
            continue
        missing.append(track)
    logger.info("comparação: %d faixas, %d já na biblioteca, %d faltando", len(tracks), matched, len(missing))
    return {
        "library_file_count": len(library),
        "matched_count": matched,
        "missing_count": len(missing),
        "missing": [t.model_dump(mode="json") for t in missing],
    }


def _search_candidates(track: TrackInput, authorization: str) -> list[dict]:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{cfg.catalog_url}/candidates",
                headers={"Authorization": authorization},
                json={
                    "name": track.name,
                    "artists": track.artists,
                    "album": track.album,
                    "tidal_id_hint": getattr(track, "extra_tidal_id", None),
                    "limit": 10,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("candidates", [])
            logger.warning("candidates -> %s: %s", resp.status_code, resp.text[:200])
    except httpx.RequestError as exc:
        logger.warning("catálogo indisponível: %s", exc)
    return []


def _fetch_manifest(tidal_id: int, authorization: str) -> dict | None:
    for quality in ("HI_RES_LOSSLESS", "LOSSLESS"):
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(
                    f"{cfg.catalog_url}/track/{tidal_id}/playback",
                    headers={"Authorization": authorization},
                    params={"quality": quality},
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 503, 429):
                    logger.warning("manifest %s %s: %s", tidal_id, quality, resp.status_code)
        except httpx.RequestError as exc:
            logger.warning("manifest %s falhou: %s", tidal_id, exc)
    return None


def dryrun(tracks: list[TrackInput], authorization: str) -> list[DownloadSelection]:
    selections: list[DownloadSelection] = []
    for idx, track in enumerate(tracks, start=1):
        sel = DownloadSelection(track=track)
        candidates = _search_candidates(track, authorization)
        # candidato com hint primeiro (alinhado ao fluxo legado)
        if getattr(track, "extra_tidal_id", None):
            hint_id = track.extra_tidal_id
            hint = next((c for c in candidates if c.get("tidal_id") == hint_id), None)
            if hint:
                candidates = [hint] + [c for c in candidates if c.get("tidal_id") != hint_id]
        manifest = None
        chosen = None
        for cand in candidates:
            manifest = _fetch_manifest(cand["tidal_id"], authorization)
            if manifest:
                chosen = cand
                break
        if chosen and manifest:
            sel.status = "found"
            sel.tidal_id = int(chosen["tidal_id"])
            sel.quality = manifest.get("quality")
            sel.candidates = [TrackCandidate(**c) for c in candidates[:5]]
            sel.manifest_url = manifest.get("url") or ""
            sel.codecs = manifest.get("codecs") or ""
            sel.extension = manifest.get("extension") or "m4a"
            sel.reason = f"Candidato Tidal {sel.tidal_id} com qualidade {sel.quality}."
        else:
            sel.status = "not_found"
            sel.reason = "Nenhum candidato com qualidade aceitável (HI_RES_LOSSLESS/LOSSLESS)."
            sel.candidates = [TrackCandidate(**c) for c in candidates[:5]]
        logger.info("dry-run [%d/%d] %s -> %s", idx, len(tracks), track.name, sel.status)
        selections.append(sel)
    return selections


def _ext_to_tag_mapping(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    return {"ext": ext}


def set_audio_tags(path: str, title: str, artist: str, album: str | None) -> bool:
    """Aplica tags (título/artista/álbum) conforme a extensão do arquivo."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(path)
            audio["title"] = title
            audio["artist"] = artist
            if album:
                audio["album"] = album
            audio.save()
            return True
        if ext == ".m4a" or ext == ".mp4":
            from mutagen.mp4 import MP4
            audio = MP4(path)
            audio["\xa9nam"] = [title]
            audio["\xa9ART"] = [artist]
            if album:
                audio["\xa9alb"] = [album]
            audio.save()
            return True
        if ext == ".mp3":
            try:
                from mutagen.easyid3 import EasyID3FileType as Mp3Tag
            except ImportError:  # mutagen < 1.48
                from mutagen.easyid3 import EasyMP3 as Mp3Tag
            audio = Mp3Tag(path)
            audio["title"] = title
            audio["artist"] = artist
            if album:
                audio["album"] = album
            audio.save()
            return True
    except Exception as exc:
        logger.warning("tagging falhou para %s: %s", path, exc)
        return False
    return False


@app.get("/library")
def get_library(_username: str = Depends(_require_user)) -> dict:
    files = _scan_library(DOWNLOAD_DIR)
    return {"directory": DOWNLOAD_DIR, "count": len(files),
            "files": [f.model_dump(mode="json") for f in files]}


@app.post("/compare")
def compare(payload: CompareReq, _username: str = Depends(_require_user)) -> dict:
    return compare_tracks(payload.tracks)


@app.post("/dryrun")
def run_dryrun(
    payload: DryRunReq,
    username: str = Depends(_require_user),
    authorization: str | None = Header(default=None),
) -> dict:
    if not payload.tracks:
        raise_error("PARSED_TRACKS_EMPTY")
    selections = dryrun(payload.tracks, authorization or "")
    dry_run_id = f"dryrun_{os.urandom(4).hex()}"
    now = time.time()
    expires_at = now + cfg.dryrun_ttl_minutes * 60
    created_iso = utcnow_iso()
    store.execute(
        "INSERT OR REPLACE INTO dryruns (dry_run_id, owner, payload, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (dry_run_id, username, json.dumps([s.model_dump(mode="json") for s in selections], ensure_ascii=False),
         created_iso, f"{expires_at:.0f}"),
    )
    logger.info("dry-run %s: %d seleções para %s", dry_run_id, len(selections), username)
    return {
        "dry_run_id": dry_run_id,
        "created_at": created_iso,
        "expires_at": f"{expires_at:.0f}",
        "expires_in": cfg.dryrun_ttl_minutes * 60,
        "selections": [s.model_dump(mode="json") for s in selections],
    }


def _claim_dryrun(dry_run_id: str, username: str) -> list[DownloadSelection]:
    """Valida owner + expiração e consome o dry-run **atomicamente**.

    Cada dry-run só pode confirmar um único download (CA-14). A atualização
    guardada por ``consumed = 0`` garante que duas requisições concorrentes não
    criem dois jobs para o mesmo lote.
    """
    row = store.query_one("SELECT payload, owner, expires_at FROM dryruns WHERE dry_run_id = ?", (dry_run_id,))
    if not row:
        raise_error("DRYRUN_NOT_FOUND")
    if row["owner"] != username:
        raise_error("FORBIDDEN")
    try:
        expires_at = float(row["expires_at"])
    except (TypeError, ValueError):
        expires_at = 0.0
    if time.time() > expires_at:
        raise_error("DRYRUN_EXPIRED")
    claimed = store.execute_rowcount(
        "UPDATE dryruns SET consumed = 1 WHERE dry_run_id = ? AND consumed = 0", (dry_run_id,))
    if claimed == 0:
        raise_error("DRYRUN_NOT_FOUND",
                    "Este dry-run já foi confirmado. Execute novo dry-run para repetir.", status=409)
    payload = row["payload"]
    try:
        raw = json.loads(payload)
    except ValueError:
        raise_error("DRYRUN_NOT_FOUND", "Dry-run corrompido. Execute novo dry-run.")
    return [DownloadSelection(**s) for s in raw]


def _download_one(sel: DownloadSelection) -> dict:
    """Baixa uma seleção confirmada e aplica tags. Retorna o estado do item."""
    filename = sanitize_filename(f"{sel.track.name} - {sel.track.artist_string}") + f".{sel.extension or 'm4a'}"
    path = os.path.join(DOWNLOAD_DIR, filename)
    item = {"filename": filename}
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            resp = client.get(sel.manifest_url)
            resp.raise_for_status()
            with open(path, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    fh.write(chunk)
        item["size_bytes"] = os.path.getsize(path)
        if not set_audio_tags(path, sel.track.name, sel.track.artist_string, sel.track.album):
            item["error"] = "arquivo baixado sem tagging (formato não suportado)"
        item["status"] = "downloaded"
    except Exception as exc:
        item["status"] = "failed"
        item["error"] = str(exc)[:200]
    return item


def _update_item(job_id: str, ordinal: int, **fields) -> None:
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    store.execute(
        f"UPDATE job_items SET {set_clause}, updated_at = ? WHERE job_id = ? AND ordinal = ?",
        (*fields.values(), utcnow_iso(), job_id, ordinal),
    )


def _run_download_job(job_id: str, owner: str, entries: list[tuple[int, DownloadSelection]]) -> None:
    total = len(entries)
    done = 0
    lock = threading.Lock()

    def worker(entry):
        nonlocal done
        ordinal, sel = entry
        _update_item(job_id, ordinal, status="downloading")
        result = _download_one(sel)
        _update_item(job_id, ordinal, status=result.get("status", "failed"),
                     filename=result.get("filename"), size_bytes=result.get("size_bytes"),
                     error=result.get("error"))
        with lock:
            done += 1
            progress = round(done / total, 3)
        _update_job(job_id, progress, f"{done}/{total} concluídas")

    with ThreadPoolExecutor(max_workers=min(3, total)) as pool:
        futures = [pool.submit(worker, e) for e in entries]
        for _ in as_completed(futures):
            pass

    rows = store.query("SELECT status FROM job_items WHERE job_id = ?", (job_id,))
    ok = sum(1 for r in rows if r["status"] == "downloaded")
    failed = total - ok
    message = f"{ok} downloaded / {failed} failed"
    if failed:
        not_found_path = os.path.join(DOWNLOAD_DIR, "not_found.txt")
        try:
            with open(not_found_path, "w", encoding="utf-8") as fh:
                fh.write("RELATÓRIO DE DOWNLOADS FALHOS\n")
                fh.write("=" * 80 + "\n")
                for _ordinal, sel in entries:
                    row = store.query_one("SELECT error FROM job_items WHERE job_id = ? AND ordinal = ?",
                                          (job_id, _ordinal))
                    if row and row["error"]:
                        fh.write(f"Nome: {sel.track.name}\nArtistas: {sel.track.artist_string}\n"
                                 f"Álbum: {sel.track.album or '—'}\nErro: {row['error'] or 'desconhecido'}\n"
                                 + "-" * 78 + "\n")
        except OSError:
            logger.warning("não foi possível gravar relatório de falhas do job %s", job_id)
    _update_job(job_id, 1.0, message, status="completed")
    logger.info("download batch %s: %d ok, %d falhas", job_id, ok, failed)


def _update_job(job_id: str, progress: float, message: str, status: str = "running") -> None:
    store.execute(
        "UPDATE jobs SET progress = ?, message = ?, status = ?, updated_at = ? WHERE job_id = ?",
        (progress, message, status, utcnow_iso(), job_id),
    )


@app.post("/download")
def download(payload: DownloadReq, username: str = Depends(_require_user)) -> dict:
    """Executa download + tagging de um lote confirmado (vínculo com o dry-run)."""
    if not payload.dry_run_id:
        raise_error("DOWNLOAD_JOB_CONFIRM_REQUIRED",
                    "Confirmação exige um dry-run válido. Execute o dry-run e confirme o lote.")
    if not ALLOWED_DOWNLOAD:
        raise_error("DOWNLOAD_JOB_CONFIRM_REQUIRED",
                    "Downloads reais desativados: defina ALLOW_DOWNLOADS=1 para executar.")

    selections = _claim_dryrun(payload.dry_run_id, username)
    confirmed = [(i, s) for i, s in enumerate(selections, start=1) if s.status == "found" and s.manifest_url]
    if not confirmed:
        raise_error("DOWNLOAD_JOB_CONFIRM_REQUIRED",
                    "Nenhuma seleção confirmada com manifest. Execute o dry-run e confirme o lote.")

    total = len(confirmed)
    job_id = f"download_{os.urandom(4).hex()}"
    now = utcnow_iso()
    store.execute(
        "INSERT OR REPLACE INTO jobs (job_id, owner, type, status, progress, message, created_at, updated_at, result) "
        "VALUES (?, ?, 'download', 'running', 0.0, 'iniciando', ?, ?, NULL)",
        (job_id, username, now, now),
    )
    for ordinal, sel in confirmed:
        store.execute(
            "INSERT OR REPLACE INTO job_items (job_id, owner, ordinal, track, status, updated_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (job_id, username, ordinal, json.dumps(sel.track.model_dump(mode="json"), ensure_ascii=False), now),
        )

    thread = threading.Thread(target=_run_download_job, args=(job_id, username, confirmed), daemon=True)
    thread.start()
    logger.info("job de download %s iniciado: %d itens para %s", job_id, total, username)
    return {"job_id": job_id, "status": "running", "owner": username,
            "downloaded": 0, "failed_count": 0, "tracked": total}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, username: str = Depends(_require_user)) -> dict:
    """Status do job de download com progresso e itens por faixa (polling da UI)."""
    job = store.query_one("SELECT job_id, owner, type, status, progress, message, created_at, updated_at FROM jobs WHERE job_id = ?", (job_id,))
    if not job:
        raise_error("JOB_NOT_FOUND")
    if job["owner"] != username:
        raise_error("FORBIDDEN")
    if job["type"] != "download":
        raise_error("JOB_NOT_FOUND", "Tipo de job não suportado para consulta.")
    items = store.query(
        "SELECT ordinal, track, status, filename, size_bytes, error, updated_at FROM job_items WHERE job_id = ? ORDER BY ordinal",
        (job_id,),
    )
    for it in items:
        try:
            it["track"] = json.loads(it["track"])
        except ValueError:
            it["track"] = {"name": "(indisponível)", "artists": []}
    done = sum(1 for it in items if it["status"] in ("downloaded", "failed"))
    return {
        "job_id": job["job_id"],
        "owner": job["owner"],
        "type": job["type"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "completed": done,
        "total": len(items),
        "items": items,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)