"""Catalog / HiFi Service — encapsula o hifi-api (Tidal), busca de candidatos
e detalhes de faixas. Não expõe detalhes de integração aos demais serviços."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Optional

import httpx
from fastapi import Depends, Header
from fastapi.responses import Response

from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.ids import (
    artist_matches,
    detect_version,
    normalize_text,
    title_matches,
)
from spotfy_contracts.schemas import TrackCandidate
from spotfy_contracts.service import build_app
from spotfy_contracts.store import SqliteStore

SERVICE_NAME = "catalog"
app = build_app(SERVICE_NAME)
cfg = app.state.config
logger = app.state.logger

# Candidatos favoritos preferidos: explicit original > clean original > versioned
PREFERRED_QUALITIES = ["HI_RES_LOSSLESS", "LOSSLESS"]

TIDAL_MOCK = os.getenv("TIDAL_MOCK", "0") in ("1", "true", "yes")
_UPSTREAM = cfg.external_hifi_url or (cfg.url("catalog") if not TIDAL_MOCK else "")
_CACHE = SqliteStore(os.path.join(cfg.data_dir, f"{SERVICE_NAME}_cache.sqlite"))
_CACHE.init_schema([
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        key TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
])

# ---------------------------------------------------------------------------
# Modo mock (demo/docs sem credenciais Tidal) — dados determinísticos.
# ---------------------------------------------------------------------------

_MOCK_TRACKS = [
    {"id": 1001, "title": "One More Time", "artists": [{"name": "Daft Punk"}], "album": {"title": "Discovery"},
     "explicit": False, "version": None, "bpm": 123, "key": "A", "keyScale": "minor", "duration": 320000, "isrc": "US1"},
    {"id": 1002, "title": "Around the World", "artists": [{"name": "Daft Punk"}], "album": {"title": "Homework"},
     "explicit": False, "version": None, "bpm": 121, "key": "G", "keyScale": "major", "duration": 404000, "isrc": "US2"},
    {"id": 1003, "title": "Instant Crush", "artists": [{"name": "Daft Punk"}, {"name": "Julian Casablancas"}], "album": {"title": "Random Access Memories"},
     "explicit": False, "version": None, "bpm": 102, "key": "C", "keyScale": "major", "duration": 337000, "isrc": "US3"},
    {"id": 1004, "title": "Get Lucky", "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}], "album": {"title": "Random Access Memories"},
     "explicit": False, "version": None, "bpm": 116, "key": "D", "keyScale": "minor", "duration": 369000, "isrc": "US4"},
    {"id": 1005, "title": "Voyager", "artists": [{"name": "Daft Punk"}], "album": {"title": "Discovery"},
     "explicit": False, "version": None, "bpm": 117, "key": "E", "keyScale": "major", "duration": 287000, "isrc": "US5"},
    {"id": 1006, "title": "Da Funk", "artists": [{"name": "Daft Punk"}], "album": {"title": "Homework"},
     "explicit": False, "version": None, "bpm": 126, "key": "F", "keyScale": "minor", "duration": 338000, "isrc": "US6"},
    {"id": 1007, "title": "Harder, Better, Faster, Stronger", "artists": [{"name": "Daft Punk"}], "album": {"title": "Discovery"},
     "explicit": False, "version": None, "bpm": 123, "key": "A", "keyScale": "major", "duration": 224000, "isrc": "US7"},
    {"id": 1008, "title": "One More Time", "artists": [{"name": "Daft Punk"}], "album": {"title": "Alive 2007"},
     "explicit": False, "version": "Live", "bpm": 126, "key": "A", "keyScale": "major", "duration": 249000, "isrc": "US8"},
    {"id": 1009, "title": "Get Lucky", "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}], "album": {"title": "Random Access Memories"},
     "explicit": True, "version": "Radio Edit", "bpm": 116, "key": "D", "keyScale": "minor", "duration": 252000, "isrc": "US9"},
]


def _to_candidate(item: dict, strategy: Optional[str] = None) -> TrackCandidate:
    return TrackCandidate(
        tidal_id=int(item["id"]),
        title=item["title"],
        artists=[a["name"] for a in item.get("artists", [])],
        album=(item.get("album") or {}).get("title"),
        explicit=bool(item.get("explicit")),
        version=item.get("version"),
        bpm=item.get("bpm"),
        key=item.get("key"),
        key_scale=item.get("keyScale"),
        duration=item.get("duration"),
        isrc=item.get("isrc"),
        strategy=strategy,
    )


def _mock_search(query: str, limit: int = 10) -> list[TrackCandidate]:
    q = normalize_text(query, remove_accents=True).lower()
    terms = [t for t in q.split() if t]
    out: list[TrackCandidate] = []
    for item in _MOCK_TRACKS:
        hay = normalize_text(item["title"], remove_accents=True) + " " + " ".join(a["name"] for a in item.get("artists", []))
        if all(t in hay for t in terms):
            out.append(_to_candidate(item))
        if len(out) >= limit:
            break
    return out


def _fixture_audio_bytes() -> bytes:
    # MP3 mínimo (ID3v2.4 vazio + 4 frames MPEG silenciosos) — taggável via mutagen.
    frame = b"\xff\xfb\x90\x64" + bytes(413)
    return b"ID3\x04\x00\x00" + b"\x00\x00\x00\x00" + frame * 4


@app.get("/mock/audio.mp3")
def mock_audio():
    return Response(content=_fixture_audio_bytes(), media_type="audio/mpeg")


def _call_upstream(method: str, path: str, params: dict | None = None, timeout: float = 12.0) -> dict:
    if TIDAL_MOCK:
        return {"version": "mock", "data": []}
    url = f"{_UPSTREAM}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, params=params)
        if resp.status_code in (401,):
            raise_error("TIDAL_AUTH_FAILED", "hifi-api não autenticado no Tidal. Verifique o token.")
        if resp.status_code == 429:
            raise_error("TIDAL_RATE_LIMITED")
        if resp.status_code >= 500:
            raise_error("TIDAL_UPSTREAM_ERROR", f"hifi-api retornou {resp.status_code}.")
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        raise_error("CATALOG_UNREACHABLE", "Timeout ao consultar o catálogo.")
    except httpx.RequestError as exc:
        raise_error("CATALOG_UNREACHABLE", f"Sem conexão com hifi-api: {exc}")


def _cached_search(key: str) -> Optional[list]:
    row = _CACHE.query_one("SELECT payload FROM search_cache WHERE key = ?", (key,))
    if row:
        try:
            return json.loads(row["payload"])
        except ValueError:
            return None
    return None


def _save_cache(key: str, items: list) -> None:
    _CACHE.execute(
        "INSERT OR REPLACE INTO search_cache (key, payload, created_at) VALUES (?, ?, datetime('now'))",
        (key, json.dumps(items)),
    )


def _normalize_candidate(item: dict, strategy: Optional[str] = None) -> Optional[TrackCandidate]:
    if not item:
        return None
    title = item.get("title")
    artists_raw = [a.get("name") for a in item.get("artists") or [] if a.get("name")]
    album_raw = (item.get("album") or {}).get("title") or item.get("albumTitle")
    if not title:
        return None
    return TrackCandidate(
        tidal_id=int(item.get("id")),
        title=title,
        artists=artists_raw or [],
        album=album_raw or None,
        explicit=bool(item.get("explicit")),
        version=item.get("version"),
        bpm=item.get("bpm"),
        key=item.get("key"),
        key_scale=item.get("keyScale"),
        duration=item.get("duration") or item.get("duration_ms"),
        isrc=item.get("isrc"),
        strategy=strategy,
    )


def search_tracks(query: str, limit: int = 10) -> list[TrackCandidate]:
    """Busca faixas no catálogo (Tidal via hifi-api) com cache."""
    if TIDAL_MOCK:
        return _mock_search(query, limit=limit)
    cache_key = f"search:{limit}:{normalize_text(query)}"
    cached = _cached_search(cache_key)
    if cached is not None:
        return [TrackCandidate(**c) for c in cached]
    data = _call_upstream("GET", "/search/", {"s": query, "limit": max(1, min(limit, 50))})
    raw_items = data.get("data") or data.get("items") or []
    items = [_normalize_candidate(i) for i in raw_items if _normalize_candidate(i)]
    _save_cache(cache_key, [i.model_dump(mode="json") for i in items])
    logger.info("busca '%s' -> %d candidatos", query, len(items))
    return items


def get_track_info(tidal_id: int) -> dict:
    if TIDAL_MOCK:
        item = next((t for t in _MOCK_TRACKS if int(t["id"]) == int(tidal_id)), None)
        if item is None:
            return {"tidal_id": int(tidal_id), "explicit": False, "version": None, "duration": None}
        return {
            "tidal_id": int(item["id"]),
            "explicit": bool(item["explicit"]),
            "version": item.get("version"),
            "duration": item.get("duration"),
            "title": item["title"],
        }
    try:
        data = _call_upstream("GET", "/info/", {"id": tidal_id})
        item = data.get("data") or data.get("track") or data
        return {
            "tidal_id": int(item.get("id") or tidal_id),
            "explicit": bool(item.get("explicit", False)),
            "version": item.get("version"),
            "duration": item.get("duration"),
            "title": item.get("title"),
        }
    except Exception as exc:  # trata como dados ausentes, não falha a análise
        logger.warning("info %s falhou: %s", tidal_id, exc)
        return {"tidal_id": tidal_id, "explicit": False, "version": None, "duration": None}


def _strategies(name: str, artists: str, album: Optional[str], principal: str) -> list[str]:
    queries = [q for q in [
        f"{name} {principal} {album}".strip() if album else None,
        f"{name} {principal}",
        f"{name} {artists}" if artists else None,
        f"{name} {album}" if album else None,
        name,
    ] if q]
    seen, out = set(), []
    for q in queries:
        key = normalize_text(q)
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


def find_candidates(track_name: str, artists: list[str], album: Optional[str],
                    tidal_id_hint: Optional[int] = None,
                    limit: int = 10) -> list[TrackCandidate]:
    """Busca candidatos com as 5 estratégias em cascata e ordena por prioridade."""
    principal = artists[0] if artists else ""
    artist_joined = ", ".join(artists)
    candidates: list[TrackCandidate] = []

    if tidal_id_hint:
        info = get_track_info(tidal_id_hint)
        hint = TrackCandidate(
            tidal_id=tidal_id_hint, title=info.get("title") or track_name,
            artists=artists, explicit=info.get("explicit", False),
            version=info.get("version"), strategy="hint",
        )
        candidates.append(hint)

    for query in _strategies(track_name, artist_joined, album, principal):
        for cand in search_tracks(query, limit=limit):
            if not _is_plausible_match(track_name, artists, cand):
                continue
            candidates.append(cand)

    return _dedupe_candidates(candidates)


def _is_plausible_match(name: str, artists: list[str], cand: TrackCandidate, threshold: float = 0.78) -> bool:
    t = title_matches(name, cand.title, threshold=threshold)
    a = artist_matches(", ".join(artists), cand.artists, threshold=0.6)
    # título + algum artista
    return t >= 0.85 and a >= 0.6


def _dedupe_candidates(candidates: list[TrackCandidate]) -> list[TrackCandidate]:
    seen: set[int] = set()
    out: list[TrackCandidate] = []
    for c in candidates:
        if c.tidal_id in seen:
            continue
        seen.add(c.tidal_id)
        out.append(c)
    return out


def _candidate_priority(cand: TrackCandidate) -> tuple:
    is_explicit = cand.explicit
    has_version = cand.version is not None or detect_version(cand.title) is not None
    return (has_version, not is_explicit)


def rank_candidates(candidates: list[TrackCandidate]) -> list[TrackCandidate]:
    """Prioriza explicit original > clean original > versioned (com criador de empatar)."""
    return sorted(candidates, key=_candidate_priority)


def track_manifest(tidal_id: int, quality: str = "LOSSLESS") -> Optional[dict]:
    if TIDAL_MOCK:
        item = next((t for t in _MOCK_TRACKS if int(t["id"]) == int(tidal_id)), None)
        if item is None:
            return None
        base = cfg.url("catalog")
        return {"url": f"{base}/mock/audio.mp3", "quality": quality, "codecs": "mp3", "tidal_id": int(tidal_id)}
    for q in PREFERRED_QUALITIES:
        for playbackmode in ("OFFLINE", "STREAM"):
            try:
                data = _call_upstream("GET", "/track/", {
                    "id": tidal_id, "quality": q, "playbackmode": playbackmode}, timeout=8.0)
                inner = data.get("data") or data.get("track") or data
                manifest_b64 = inner.get("manifest")
                mime = inner.get("manifestMimeType")
                if not manifest_b64 or mime != "application/vnd.tidal.bts":
                    continue
                decoded = json.loads(base64.b64decode(manifest_b64).decode("utf-8"))
                urls = decoded.get("urls") or []
                if urls:
                    return {"url": urls[0], "quality": q, "codecs": decoded.get("codecs", ""), "tidal_id": tidal_id}
            except Exception as exc:
                logger.warning("manifest %s/%s falhou: %s", tidal_id, q, exc)
    return None


def _codec_to_ext(codec: str) -> str:
    c = (codec or "").lower()
    if "flac" in c:
        return "flac"
    if "mp3" in c or "mpga" in c or "mp2" in c:
        return "mp3"
    if "alac" in c:
        return "m4a"
    if "mp4a" in c or "aac" in c or "he-aac" in c:
        return "m4a"
    return "m4a"


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


@app.get("/search")
def api_search(s: str, limit: int = 10, _username: str = Depends(_require_user)) -> dict:
    return {"candidates": [c.model_dump(mode="json") for c in search_tracks(s, limit)]}


@app.post("/candidates")
def api_candidates(payload: dict, _username: str = Depends(_require_user)) -> dict:
    name = payload.get("name")
    artists = payload.get("artists") or []
    album = payload.get("album")
    hint = payload.get("tidal_id_hint")
    limit = int(payload.get("limit", 10))
    if not name:
        raise_error("INVALID_PAYLOAD")
    candidates = find_candidates(str(name), artists, album, hint, limit=limit)
    ranked = rank_candidates(candidates)
    return {"candidates": [c.model_dump(mode="json") for c in ranked]}


@app.get("/info/{tidal_id}")
def api_info(tidal_id: int, _username: str = Depends(_require_user)) -> dict:
    return get_track_info(tidal_id)


@app.get("/track/{tidal_id}/playback")
def api_playback(
    tidal_id: int,
    quality: str | None = None,
    playbackmode: str = "OFFLINE",
    _username: str = Depends(_require_user),
) -> dict:
    """Manifest de reprodução direta (bts) para a biblioteca baixar o áudio."""
    manifest = track_manifest(tidal_id, quality or "LOSSLESS")
    if not manifest:
        raise_error("NO_ACCEPTABLE_QUALITY", f"Sem manifest aceitável para {tidal_id} (HI_RES_LOSSLESS/LOSSLESS).")
    manifest["extension"] = _codec_to_ext(manifest.get("codecs", ""))
    return manifest


@app.get("/health/upstream")
def health_upstream() -> dict:
    try:
        data = _call_upstream("GET", "/", timeout=5.0)
        return {"upstream": "ok", "version": data.get("version")}
    except Exception as exc:
        return {"upstream": "error", "detail": str(exc.args[0]) if exc.args else str(exc)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)