"""Fake Spotify — simula Authorization Code + PKCE (S256), refresh com rotação
e endpoints de API com paginação. Controlável via POST /__control para cenários
de falha (refresh revogado, uploads 429, etc.). Nunca usa rede externa."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="fake-spotify")

_CLIENT_ID = "fake-client-id"
_SCOPE = "user-library-read playlist-read-private playlist-read-collaborative"

_PLAYLISTS = [
    {
        "id": "pl_main",
        "name": "Minha Playlist",
        "owner": {"id": "fakeuser", "display_name": "Fake User"},
        "tracks": {"total": 3, "href": "/v1/playlists/pl_main/tracks"},
    },
    {
        "id": "pl_second",
        "name": "Segunda Playlist",
        "owner": {"id": "fakeuser", "display_name": "Fake User"},
        "tracks": {"total": 2, "href": "/v1/playlists/pl_second/tracks"},
    },
]

_TRACK_DATA = {
    "t1": {"id": "t1", "name": "One More Time", "artists": [{"name": "Daft Punk"}],
           "album": {"name": "Discovery"}, "duration_ms": 318000,
           "uri": "spotify:track:t1"},
    "t2": {"id": "t2", "name": "Get Lucky", "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}],
           "album": {"name": "Random Access Memories"}, "duration_ms": 369000,
           "uri": "spotify:track:t2"},
    "t3": {"id": "t3", "name": "Faixa Inexistente", "artists": [{"name": "Artista Desconhecido"}],
           "album": {"name": "Desconhecido"}, "duration_ms": 180000,
           "uri": "spotify:track:t3"},
    "t4": {"id": "t4", "name": "Instant Crush", "artists": [{"name": "Daft Punk"}, {"name": "Julian Casablancas"}],
           "album": {"name": "Random Access Memories"}, "duration_ms": 337000,
           "uri": "spotify:track:t4"},
    "t5": {"id": "t5", "name": "Doin' it Right", "artists": [{"name": "Daft Punk"}, {"name": "Panda Bear"}],
           "album": {"name": "Random Access Memories"}, "duration_ms": 254000,
           "uri": "spotify:track:t5"},
}

_PLAYLIST_TRACKS = {
    "pl_main": [
        {"added_at": "2026-09-01T00:00:00Z", "track": _TRACK_DATA["t1"]},
        {"added_at": "2026-09-01T00:00:00Z", "track": _TRACK_DATA["t2"]},
        {"added_at": "2026-09-01T00:00:00Z", "track": _TRACK_DATA["t3"]},
    ],
    "pl_second": [
        {"added_at": "2026-09-02T00:00:00Z", "track": _TRACK_DATA["t4"]},
        {"added_at": "2026-09-02T00:00:00Z", "track": _TRACK_DATA["t5"]},
    ],
}

_SAVED_TRACKS = [
    {"added_at": "2026-08-20T00:00:00Z", "track": _TRACK_DATA["t1"]},
    {"added_at": "2026-08-20T00:00:00Z", "track": _TRACK_DATA["t4"]},
]

# ---- estado do fake (por processo de teste) ---------------------------------
_store = {
    "codes": {},            # code -> {challenge, challenge_method, redirect_uri, client_id, used, at}
    "refresh": {},          # refresh_token -> {access_token, expires_at, scope}
    "control": {},          # cenários de falha
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _token_value(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _token_expires_in() -> int:
    return int(_store["control"].get("token_expires_in", 3600))


def _pkce_ok(verifier: str | None, rec: dict) -> bool:
    method = (rec.get("challenge_method") or "S256").upper()
    given = rec.get("challenge") or ""
    if method == "S256":
        expected = _b64url(hashlib.sha256((verifier or "").encode("ascii")).digest())
    else:
        expected = verifier or ""
    return bool(verifier) and hmac.compare_digest(expected, given)


@app.get("/health")
def health() -> dict:
    return {"service": "fake-spotify", "status": "ok"}


@app.post("/__control")
def control(payload: dict) -> dict:
    """Cenários de teste: {'token_expires_in': 0, 'refresh_token_revoked': True,
    'api_status': 429, 'reset': True}."""
    if payload.get("reset"):
        _store["control"] = {}
        _store["codes"].clear()
        _store["refresh"].clear()
    else:
        _store["control"].update(payload)
    return {"ok": True, "control": _store["control"]}


@app.get("/authorize")
def authorize(request: Request):
    q = request.query_params
    if q.get("response_type") != "code":
        return JSONResponse(status_code=400, content={"error": "unsupported_response_type"})
    if not q.get("client_id") or not q.get("redirect_uri") or not q.get("state"):
        return JSONResponse(status_code=400, content={"error": "invalid_request"})
    code = _token_value("code")
    _store["codes"][code] = {
        "challenge": q.get("code_challenge"),
        "challenge_method": q.get("code_challenge_method"),
        "redirect_uri": q.get("redirect_uri"),
        "client_id": q.get("client_id"),
        "used": False,
        "at": time.time(),
    }
    target = q["redirect_uri"] + "?" + urlencode({"state": q["state"], "code": code})
    if q.get("auto") == "1":
        return RedirectResponse(target)
    return HTMLResponse(
        "<p>Autorize o BPM Match?</p>"
        f'<p><a href="{target}">Sim, autorizar</a></p>'
    )


@app.post("/api/token")
async def token(request: Request):
    form = await request.form()
    grant = form.get("grant_type")
    if grant == "authorization_code":
        code = form.get("code")
        rec = _store["codes"].get(code or "")
        if not rec or rec["used"]:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if rec["client_id"] != form.get("client_id"):
            return JSONResponse(status_code=400, content={"error": "invalid_client"})
        if rec["redirect_uri"] != form.get("redirect_uri"):
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if not _pkce_ok(form.get("code_verifier"), rec):
            return JSONResponse(status_code=400, content={"error": "invalid_grant", "error_description": "PKCE falhou"})
        rec["used"] = True
        return _token_response(rec["client_id"])
    if grant == "refresh_token":
        rt = form.get("refresh_token")
        rec = _store["refresh"].get(rt or "")
        if not rec:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if _store["control"].get("refresh_token_revoked"):
            _store["refresh"].pop(rt, None)
            return JSONResponse(status_code=400, content={"error": "invalid_grant", "error_description": "refresh revogado"})
        if form.get("client_id") != rec["client_id"]:
            return JSONResponse(status_code=400, content={"error": "invalid_client"})
        _store["refresh"].pop(rt, None)
        return _token_response(rec["client_id"], access=rec["access_token"])
    return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})


def _token_response(client_id: str, access: str | None = None) -> dict:
    access_token = access or _token_value("access")
    refresh_token = _token_value("refresh")
    _store["refresh"][refresh_token] = {
        "access_token": access_token,
        "client_id": client_id,
        "expires_at": time.time() + _token_expires_in(),
    }
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": _token_expires_in(),
        "refresh_token": refresh_token,
        "scope": _SCOPE,
    }


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _control_status(request: Request, bucket: str) -> int | None:
    status = _store["control"].get(bucket)
    if status:
        return status
    return None


@app.get("/v1/me")
def me(request: Request):
    control = _store["control"]
    token = _bearer(request)
    if not token or not any(r["access_token"] == token for r in _store["refresh"].values()):
        if control.get("api_status"):
            return JSONResponse(status_code=control["api_status"], content={"error": "rate_limited"})
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return {"id": "fakeuser", "display_name": "Fake User", "product": "premium"}


def _client_id_for(token: str) -> str | None:
    for rec in _store["refresh"].values():
        if rec["access_token"] == token:
            return rec["client_id"]
    return None


def _authed(request, status_bucket: str):
    token = _bearer(request)
    if not token:
        return None, None
    client_id = _client_id_for(token)
    if client_id is None:
        return None, None
    status = _store["control"].get(status_bucket)
    return (token, client_id) if not status else (None, None)


@app.get("/v1/me/playlists")
def me_playlists(request: Request):
    status = _store["control"].get("playlist_status")
    token = _bearer(request)
    if status:
        return JSONResponse(status_code=status, content={"error": "rate_limited", "message": "Too Many Requests"})
    if not token or _client_id_for(token) is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return {"items": [dict(p) for p in _PLAYLISTS], "total": len(_PLAYLISTS), "next": None}


@app.get("/v1/me/tracks")
def me_tracks(request: Request):
    token = _bearer(request)
    if not token or _client_id_for(token) is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return {"items": [dict(t) for t in _SAVED_TRACKS], "total": len(_SAVED_TRACKS), "next": None}


@app.get("/v1/playlists/{playlist_id}")
def playlist(playlist_id: str, request: Request):
    token = _bearer(request)
    if not token or _client_id_for(token) is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    found = next((p for p in _PLAYLISTS if p["id"] == playlist_id), None)
    if _store["control"].get("playlist_status"):
        return JSONResponse(status_code=_store["control"]["playlist_status"], content={"error": "rate_limited"})
    if not found:
        return JSONResponse(status_code=404, content={"error": "playlist_not_found"})
    return dict(found)


@app.get("/v1/playlists/{playlist_id}/tracks")
def playlist_tracks(playlist_id: str, request: Request):
    token = _bearer(request)
    if not token or _client_id_for(token) is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    tracks = _PLAYLIST_TRACKS.get(playlist_id)
    if tracks is None:
        return JSONResponse(status_code=404, content={"error": "playlist_not_found"})
    offset = int(request.query_params.get("offset", 0))
    limit = int(request.query_params.get("limit", 50))
    page = tracks[offset:offset + limit]
    base = str(request.base_url).rstrip("/")
    next_url = None
    if offset + limit < len(tracks):
        next_url = f"{base}/v1/playlists/{playlist_id}/tracks?offset={offset + limit}&limit={limit}"
    return {
        "items": [dict(t) for t in page],
        "total": len(tracks),
        "offset": offset,
        "next": next_url,
    }


@app.get("/metrics")
def metrics() -> str:
    return "# HELP http_requests_total Total de requisições.\n# TYPE http_requests_total counter\nhttp_requests_total 0\n"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("fake_spotify:app", host="127.0.0.1", port=8901, log_level="warning")