"""Playlist Service — integração Spotify (URLs configuráveis por ambiente),
extração de playlists e normalização estável (referências `spotify:<id>`)."""

from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import Depends, Header
from pydantic import BaseModel
from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.ids import (
    detect_reference_source,
    parse_source_reference,
    parse_spotify_reference,
)
from spotfy_contracts.schemas import TrackInput, utcnow_iso
from spotfy_contracts.service import build_app
from spotfy_contracts.store import JsonStore

SERVICE_NAME = "playlist"
app = build_app(SERVICE_NAME)
cfg = app.state.config

root_sync = JsonStore(os.path.join(cfg.data_dir, "synced_playlists.json"))


def _token(token_override: str | None) -> str:
    token = (token_override or "").strip() or cfg.spotify_access_token
    if not token:
        raise_error("SPOTIFY_AUTH_EXPIRED", "Conecte o Spotify para listar as suas playlists.")
    return token


def _raise_for_status(resp: httpx.Response, context: str = "") -> None:
    if resp.status_code in (200, 204):
        return
    detail = f"{context}: " if context else f"{context}"
    if resp.status_code == 401:
        raise_error("SPOTIFY_AUTH_EXPIRED")
    if resp.status_code == 403:
        raise_error("PLAYLIST_INACCESSIBLE")
    if resp.status_code == 404:
        raise_error("PLAYLIST_NOT_FOUND")
    if resp.status_code == 429:
        raise_error("SPOTIFY_API_ERROR", "Spotify limitou as requisições (429). Tente novamente em instantes.")
    raise_error("SPOTIFY_API_ERROR", f"{detail}HTTP {resp.status_code}".strip())


def _api_get(token: str, url: str) -> dict:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError:
        raise_error("SPOTIFY_API_ERROR", "Spotify indisponível ou sem resposta.")
    _raise_for_status(resp, "Spotify")
    return resp.json()


def _paginate(token: str, first_url: str) -> list[dict]:
    out: list[dict] = []
    url: str | None = first_url
    while url:
        data = _api_get(token, url)
        out.extend(data.get("items") or [])
        url = data.get("next")
    return out


def _resolve_playlist(token: str, reference: str) -> dict:
    ref = (reference or "").strip()
    # Formato estável do fluxo: spotify:<playlist_id>
    parsed_ref = parse_source_reference(ref)
    if parsed_ref and parsed_ref[0] == "spotify":
        pid = parsed_ref[1]
        meta = {"source": "playlist", "name": pid, "id": pid, "owner": None}
        return _fill_playlist_meta(token, meta)
    kind = detect_reference_source(ref)
    if kind == "favorites":
        return {"source": "favorites", "name": "Favoritas (Músicas Salvas)", "id": "favorites_saved"}
    if kind in ("uri", "url", "id"):
        parsed = parse_spotify_reference(ref)
        if not parsed:
            raise_error("INVALID_REFERENCE_TYPE")
        pkind, pid = parsed
        if pkind not in ("playlist", "unknown"):
            raise_error("INVALID_REFERENCE_TYPE", f"Tipo '{pkind}' não suportado. Use uma playlist ou favoritas.")
        meta = {"source": "playlist", "name": pid, "id": pid, "owner": None}
        return _fill_playlist_meta(token, meta)
    # nome: busca nas playlists do usuário
    playlists = _paginate(token, f"{cfg.spotify_api_url}/v1/me/playlists")
    found = next((p for p in playlists if p.get("name", "").lower() == ref.lower()), None)
    if not found:
        raise_error("PLAYLIST_NOT_FOUND", f"Playlist '{ref}' não encontrada.")
    return {"source": "playlist", "name": found["name"], "id": found["id"],
            "owner": (found.get("owner") or {}).get("display_name")}


def _fill_playlist_meta(token: str, meta: dict) -> dict:
    data = _api_get(token, f"{cfg.spotify_api_url}/v1/playlists/{meta['id']}")
    meta["name"] = (data.get("name") or meta["id"])
    meta["owner"] = ((data.get("owner") or {}).get("display_name")) if data.get("owner") else None
    return meta


def _fetch_tracks(token: str, meta: dict) -> list[TrackInput]:
    tracks: list[TrackInput] = []
    if meta["source"] == "favorites":
        items = _paginate(token, f"{cfg.spotify_api_url}/v1/me/tracks")
        for item in items:
            trk = item.get("track")
            if not trk:
                continue
            tracks.append(_to_input(trk, item.get("added_at")))
    else:
        items = _paginate(token, f"{cfg.spotify_api_url}/v1/playlists/{meta['id']}/tracks")
        for item in items:
            trk = item.get("track") or item.get("item")
            if not trk:
                continue
            tracks.append(_to_input(trk, item.get("added_at")))
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


class PlaylistReq(BaseModel):
    reference: str


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


@app.get("/playlists")
def list_playlists(
    username: str = Depends(_require_user),
    spotify_token: str | None = Header(default=None, alias="X-Spotify-Token"),
) -> dict:
    token = _token(spotify_token)
    playlists = _paginate(token, f"{cfg.spotify_api_url}/v1/me/playlists")
    items = []
    for p in playlists:
        if not p or not p.get("id"):
            continue
        items.append({
            "id": p.get("id"), "name": p.get("name"),
            "owner": (p.get("owner") or {}).get("display_name"),
            "tracks_total": (p.get("tracks") or {}).get("total"),
        })
    return {"playlists": items}


@app.post("/import")
def import_playlist(
    payload: PlaylistReq,
    username: str = Depends(_require_user),
    spotify_token: str | None = Header(default=None, alias="X-Spotify-Token"),
) -> dict:
    token = _token(spotify_token)
    meta = _resolve_playlist(token, payload.reference)
    tracks = _fetch_tracks(token, meta)
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