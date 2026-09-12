"""Playlist Service — integração Spotify, extração de playlists e normalização."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, Header
from pydantic import BaseModel
from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.ids import (
    detect_reference_source,
    parse_spotify_reference,
)
from spotfy_contracts.schemas import TrackInput, utcnow_iso
from spotfy_contracts.service import build_app
from spotfy_contracts.store import JsonStore

SERVICE_NAME = "playlist"
app = build_app(SERVICE_NAME)
cfg = app.state.config

SCOPES = "user-library-read playlist-read-private playlist-read-collaborative"
root_sync = JsonStore(os.path.join(cfg.data_dir, "synced_playlists.json"))

# Token do Spotify recebido do web service (via header X-Spotify-Token)
_external_token: str | None = None


def _sp_client(token_override: str | None = None):
    try:
        import spotipy
    except Exception:
        raise_error("SPOTIFY_AUTH_FAILED", "Spotipy não instalado no serviço.")
    # Token explícito (via header ou config)
    token = token_override or _external_token or cfg.spotify_access_token
    if token:
        return spotipy.Spotify(auth=token)
    # Client credentials (não funciona para playlists do usuário)
    if cfg.spotify_client_id and cfg.spotify_client_secret:
        from spotipy.oauth2 import SpotifyClientCredentials
        auth = SpotifyClientCredentials(
            client_id=cfg.spotify_client_id, client_secret=cfg.spotify_client_secret)
        return spotipy.Spotify(auth_manager=auth)
    raise_error("SPOTIFY_AUTH_EXPIRED", "Faça login no Spotify via UI (ícone de playlist).")


class PlaylistReq(BaseModel):
    reference: str


def _resolve_playlist(sp, reference: str) -> dict:
    kind = detect_reference_source(reference)
    if kind == "favorites":
        return {"source": "favorites", "name": "Favoritas (Músicas Salvas)", "id": "favorites_saved"}
    if kind in ("uri", "url", "id"):
        parsed = parse_spotify_reference(reference)
        if not parsed:
            raise_error("INVALID_REFERENCE_TYPE")
        pkind, pid = parsed
        if pkind not in ("playlist", "unknown"):
            raise_error("INVALID_REFERENCE_TYPE", f"Tipo '{pkind}' não suportado. Use uma playlist ou favoritas.")
        try:
            p = sp.playlist(pid)
        except Exception as exc:
            _map_spotify_exc(exc)
            raise_error("PLAYLIST_NOT_FOUND")
        return {"source": "playlist", "name": p.get("name") or pid, "id": pid,
                "owner": (p.get("owner") or {}).get("display_name")}
    # nome
    playlists = []
    result = sp.current_user_playlists(limit=50)
    while result:
        playlists.extend(result.get("items", []))
        result = sp.next(result) if result.get("next") else None
    found = next((p for p in playlists if p.get("name", "").lower() == reference.lower()), None)
    if not found:
        raise_error("PLAYLIST_NOT_FOUND", f"Playlist '{reference}' não encontrada.")
    return {"source": "playlist", "name": found["name"], "id": found["id"],
            "owner": (found.get("owner") or {}).get("display_name")}


def _fetch_tracks(sp, meta: dict) -> list[TrackInput]:
    tracks: list[TrackInput] = []
    if meta["source"] == "favorites":
        result = sp.current_user_saved_tracks(limit=50)
        while result:
            for item in result.get("items", []):
                trk = item.get("track")
                if not trk:
                    continue
                tracks.append(_to_input(trk, item.get("added_at")))
            result = sp.next(result) if result.get("next") else None
    else:
        result = sp.playlist_items(meta["id"], limit=100)
        while result:
            for item in result.get("items", []):
                trk = item.get("track") or item.get("item")
                if not trk:
                    continue
                tracks.append(_to_input(trk, item.get("added_at")))
            result = sp.next(result) if result.get("next") else None
    return tracks


def _to_input(trk: dict, added_at: Optional[str]) -> TrackInput:
    name = trk.get("name") or "N/A"
    artists = [a.get("name") for a in trk.get("artists") or [] if a.get("name")] or ["N/A"]
    return TrackInput(
        name=name,
        artists=artists,
        album=(trk.get("album") or {}).get("name") or None,
        duration_ms=trk.get("duration_ms"),
        spotify_id=trk.get("id"),
        uri=trk.get("uri"),
        added_at=added_at,
        source="spotify",
    )


def _map_spotify_exc(exc: Exception) -> None:
    try:
        status = getattr(exc, "http_status", None) or getattr(exc, "status", None)
    except Exception:
        status = None
    if status == 401:
        raise_error("SPOTIFY_AUTH_EXPIRED")
    if status == 403:
        raise_error("PLAYLIST_INACCESSIBLE")
    if status == 404:
        raise_error("PLAYLIST_NOT_FOUND")
    raise_error("SPOTIFY_API_ERROR", str(exc)[:200])


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


@app.get("/playlists")
def list_playlists(
    username: str = Depends(_require_user),
    spotify_token: str | None = Header(default=None, alias="X-Spotify-Token"),
) -> dict:
    sp = _sp_client(token_override=spotify_token)
    playlists = []
    result = sp.current_user_playlists(limit=50)
    while result:
        for p in result.get("items", []):
            playlists.append({
                "id": p.get("id"), "name": p.get("name"),
                "owner": (p.get("owner") or {}).get("display_name"),
                "tracks_total": (p.get("tracks") or {}).get("total"),
            })
        result = sp.next(result) if result.get("next") else None
    return {"playlists": playlists}


@app.post("/import")
def import_playlist(
    payload: PlaylistReq,
    username: str = Depends(_require_user),
    spotify_token: str | None = Header(default=None, alias="X-Spotify-Token"),
) -> dict:
    sp = _sp_client(token_override=spotify_token)
    meta = _resolve_playlist(sp, payload.reference)
    tracks = _fetch_tracks(sp, meta)
    if not tracks:
        raise_error("PARSED_TRACKS_EMPTY", f"Playlist '{meta['name']}' sem faixas.")
    key = meta["id"]
    storage_key = f"{username}:{key}"
    existing: dict = root_sync.get(storage_key) or {}
    existing["name"] = meta["name"]
    existing["source"] = meta["source"]
    existing["synced_at"] = utcnow_iso()
    existing["track_count"] = len(tracks)
    existing["owner"] = username
    existing["id"] = key
    existing["playlist_id"] = key
    existing["tracks"] = [t.model_dump(mode="json") for t in tracks]
    root_sync.set(storage_key, existing)
    app.state.logger.info("playlist importada: %s (%d faixas)", meta["name"], len(tracks))
    return {"status": "success", "playlist_id": key, "playlist_name": meta["name"],
            "source": meta["source"], "track_count": len(tracks),
            "preview": [t.model_dump(mode="json") for t in tracks[:5]]}


@app.get("/synced")
def synced_playlists(username: str = Depends(_require_user)) -> dict:
    out = []
    for storage_key in root_sync.keys():
        d = root_sync.get(storage_key)
        if not d or d.get("owner") != username:
            continue
        out.append({k: v for k, v in d.items() if k != "tracks"})
    return {"synced": out}


@app.get("/synced/{key}")
def get_synced(key: str, username: str = Depends(_require_user)) -> dict:
    d = root_sync.get(f"{username}:{key}")
    if not d:
        for storage_key in root_sync.keys():
            candidate = root_sync.get(storage_key)
            if candidate and candidate.get("playlist_id") == key:
                raise_error("FORBIDDEN")
        raise_error("NOT_FOUND", "Playlist sincronizada não encontrada.")
    return d | {"tracks": [TrackInput(**t) for t in d["tracks"]]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)