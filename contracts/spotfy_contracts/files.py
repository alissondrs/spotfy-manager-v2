"""Validação, upload e parsing de arquivos de playlist (Markdown/CSV/JSON)."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Optional

from .errors import raise_error
from .schemas import TrackInput

ALLOWED_EXTENSIONS = {".md", ".markdown", ".csv", ".json"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB

_MD_TABLE_LINE = re.compile(r"^\s*\|")
_MD_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")


def validate_upload(filename: str, size_bytes: int) -> None:
    """Valida nome, extensão e tamanho do upload. Nunca confiar no input sem validar."""
    if not filename:
        raise_error("INVALID_PAYLOAD", "Arquivo sem nome.")
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise_error("FILE_TYPE_NOT_SUPPORTED",
                    f"Extensão '{ext}' não suportada. Permitidas: {sorted(ALLOWED_EXTENSIONS)}")
    if size_bytes <= 0:
        raise_error("FILE_EMPTY")
    if size_bytes > MAX_UPLOAD_BYTES:
        raise_error("FILE_TOO_LARGE", f"Limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB excedido.")


def _extension(filename: str) -> str:
    name = filename.lower()
    for ext in ALLOWED_EXTENSIONS:
        if name.endswith(ext):
            return ext
    return "." + (filename.rsplit(".", 1)[-1] if "." in filename else "")


def _parse_md_cell(raw: str) -> str:
    return raw.strip().replace("|", "-")


def parse_playlist_markdown(text: str) -> tuple[Optional[str], list[TrackInput]]:
    """Interpreta o Markdown de playlist no formato legado:
    `| # | Nome | Artistas | Álbum | [URL_TIDAL] |`. Retorna (titulo, faixas)."""
    title: Optional[str] = None
    tracks: list[TrackInput] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("# ") and title is None:
            title = line[2:].strip()
            continue
        if not _MD_TABLE_LINE.match(line):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 4:
            continue
        if parts[0] == "#":
            continue
        if all(re.match(r"^-+$", p) for p in parts) or re.match(r"^-{3,}$", parts[0]):
            continue
        num_raw, nome, artistas, album = parts[0], parts[1], parts[2], parts[3]
        if not re.match(r"^\d+$", num_raw):
            continue
        url_tidal = parts[4] if len(parts) > 4 else ""
        artists = [a.strip() for a in artistas.split(",") if a.strip()]
        tid = None
        m = re.search(r"/track/(\d+)", url_tidal)
        if m:
            tid = int(m.group(1))
        tracks.append(TrackInput(
            name=_parse_md_cell(nome) or "N/A",
            artists=artists or ["N/A"],
            album=_parse_md_cell(album) or None,
            source="markdown",
            uri=url_tidal or None,
        ))
        if tid:
            tracks[-1].extra_tidal_id = tid  # type: ignore[attr-defined]

    # Fallback para listas simples: "1. Nome - Artista1, Artista2" ou "- Nome - Artista".
    if not tracks:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m = _MD_BULLET.match(line)
            if not m:
                continue
            content = m.group(1).strip()
            if " - " not in content:
                continue
            nome, artistas_raw = content.split(" - ", 1)
            artists = [a.strip() for a in re.split(r"[,;]", artistas_raw) if a.strip()] or ["N/A"]
            if title is None:
                title = nome + " (importado)"
            tracks.append(TrackInput(
                name=_parse_md_cell(nome) or "N/A",
                artists=artists,
                source="markdown",
            ))
    return title, tracks


def parse_csv_playlist(text: str) -> tuple[Optional[str], list[TrackInput]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    tracks: list[TrackInput] = []
    for row in rows:
        nome = _first(row, ["nome", "name", "track", "title"])
        artistas_raw = _first(row, ["artistas", "artist", "artists"])
        album = _first(row, ["album"]) or None
        if not nome:
            continue
        artists = [a.strip() for a in re.split(r"[,;]", str(artistas_raw)) if a.strip()] or ["N/A"]
        tracks.append(TrackInput(name=nome, artists=artists, album=album, source="csv"))
    return None, tracks


def parse_json_playlist(text: str) -> tuple[Optional[str], list[TrackInput]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise_error("FILE_PARSE_ERROR", "JSON inválido.")
    name = None
    raw_tracks = []
    if isinstance(payload, dict):
        if "playlist" in payload:
            playlist = payload["playlist"]
            if not isinstance(playlist, dict):
                raise_error("FILE_PARSE_ERROR", "Campo 'playlist' deve ser um objeto.")
            name = playlist.get("name") or name
            raw_tracks = playlist.get("tracks", [])
        elif "tracks" in payload:
            name = payload.get("name")
            raw_tracks = payload["tracks"]
        elif "name" in payload:
            name = payload.get("name")
    elif isinstance(payload, list):
        raw_tracks = payload
    if not isinstance(raw_tracks, list):
        raise_error("FILE_PARSE_ERROR", "Campo 'tracks' deve ser uma lista.")
    tracks: list[TrackInput] = []
    for item in raw_tracks:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        nome = item.get("name") or item.get("nome") or item.get("title") or item.get("track")
        if not nome:
            continue
        artistas_raw = item.get("artists") or item.get("artist") or item.get("artistas") or "N/A"
        if isinstance(artistas_raw, list):
            artists = [str(a.get("name") if isinstance(a, dict) else a) for a in artistas_raw]
            artists = [a for a in artists if a.strip()] or ["N/A"]
        else:
            artists = [a.strip() for a in str(artistas_raw).split(",") if a.strip()] or ["N/A"]
        album = item.get("album")
        if isinstance(album, dict):
            album = album.get("name")
        tracks.append(TrackInput(
            name=str(nome), artists=artists, album=str(album) if album else None,
            spotify_id=item.get("spotify_id") or item.get("id") or None,
            isrc=item.get("isrc") or None,
            duration_ms=item.get("duration_ms") or item.get("duration") or None,
            source="json",
        ))
    return name, tracks


def _first(row: dict, keys: list[str]) -> Optional[str]:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k]).strip()
    return None


def parse_import_content(filename: str, raw: bytes) -> tuple[Optional[str], list[TrackInput]]:
    """Dispatcher: valida e interpreta qualquer arquivo suportado."""
    validate_upload(filename, len(raw))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise_error("FILE_PARSE_ERROR", "Arquivo deve ser UTF-8.")
    ext = _extension(filename)
    if ext in (".md", ".markdown"):
        return parse_playlist_markdown(text)
    if ext == ".csv":
        return parse_csv_playlist(text)
    return parse_json_playlist(text)