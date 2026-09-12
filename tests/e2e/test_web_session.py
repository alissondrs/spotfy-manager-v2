"""Fluxo de sessão anônima (cookie) + OAuth Spotify com PKCE contra o BFF real
e o fake Spotify (cenários de estado, CSRF, refresh e falhas de API)."""

from __future__ import annotations

import httpx


def _url(stack, name: str, path: str) -> str:
    return f"http://127.0.0.1:{stack['ports'][name]}{path}"


def _session(c: httpx.Client, stack) -> dict:
    r = c.get(_url(stack, "web", "/api/session"))
    assert r.status_code == 200, r.text
    return r.json()


def _csrf(session: dict) -> str:
    return session["session"]["csrf_token"]


def test_anonymous_session_and_cookie(stack):
    c = httpx.Client(timeout=30.0)
    try:
        body = _session(c, stack)
        assert body["anonymous"] is True
        assert body["session"]["owner"].startswith("anon_")
        assert body["session"]["csrf_token"]
        assert body["session"]["absolute_ttl_seconds"] > 0
        cookie = c.cookies.get("bpm_session")
        assert cookie, "cookie de sessão não foi definido"

        me = c.get(_url(stack, "web", "/api/me"))
        assert me.status_code == 200
        assert me.json()["session"]["owner"] == body["session"]["owner"]

        # mesmo cookie, nova chamada: mesmo owner, CSRF rotacionado
        again = _session(c, stack)
        assert again["session"]["owner"] == body["session"]["owner"]
        assert again["session"]["csrf_token"] != _csrf(body)

        # Authorization do navegador é ignorado: a sessão continua anônima
        authed = c.get(_url(stack, "web", "/api/me"), headers={"Authorization": "Bearer whatever"})
        assert authed.json()["anonymous"] is True
    finally:
        c.close()


def test_csrf_required_and_rotated(stack):
    c = httpx.Client(timeout=30.0)
    try:
        body = _session(c, stack)
        csrf1 = _csrf(body)

        # mutação sem CSRF -> 403
        r = c.post(_url(stack, "web", "/api/auth/logout"), json={})
        assert r.status_code == 403 and r.json()["error"] == "CSRF_INVALID"

        # CSRF antigo após rotação -> 403
        _session(c, stack)
        r = c.post(_url(stack, "web", "/api/auth/logout"),
                   json={}, headers={"X-CSRF-Token": csrf1})
        assert r.status_code == 403 and r.json()["error"] == "CSRF_INVALID"

        # CSRF atual -> ok
        body2 = _session(c, stack)
        r = c.post(_url(stack, "web", "/api/auth/logout"),
                   json={}, headers={"X-CSRF-Token": _csrf(body2)})
        assert r.status_code == 200 and r.json()["ok"] is True

        # sessão revogada: API protegida nega
        me = c.get(_url(stack, "web", "/api/me"))
        assert me.status_code == 401 and me.json()["error"] == "SESSION_INVALID"
    finally:
        c.close()


def test_login_and_register_are_disabled(stack):
    c = httpx.Client(timeout=30.0)
    try:
        r = c.post(_url(stack, "web", "/api/auth/login"), json={"username": "alice", "password": "x"})
        assert r.status_code == 410 and r.json()["error"] == "LOCAL_AUTH_DISABLED"
        r = c.post(_url(stack, "web", "/api/auth/register"), json={"username": "alice", "password": "x"})
        assert r.status_code == 410 and r.json()["error"] == "LOCAL_AUTH_DISABLED"
    finally:
        c.close()


def _connect_spotify(c: httpx.Client, stack) -> dict:
    """Passa pelo fluxo popup (authorize auto -> callback) e valida o estado."""
    r = c.get(_url(stack, "web", "/api/auth/spotify"))
    assert r.status_code == 200, r.text
    data = r.json()
    url = data["url"]
    assert "code_challenge_method=S256" in url and "code_challenge=" in url
    assert url.startswith(f"http://127.0.0.1:{stack['ports']['spotify']}/authorize")

    cb = c.get(url + "&auto=1", follow_redirects=True)
    assert cb.status_code == 200, cb.text
    assert "bpm-spotify-oauth" in cb.text and "Spotify conectado" in cb.text
    status = c.get(_url(stack, "web", "/api/auth/spotify/status")).json()
    assert status["connected"] is True
    return data


def test_spotify_pkce_connect_import_and_analyze(stack):
    c = httpx.Client(timeout=60.0)
    try:
        session = _session(c, stack)
        csrf = _csrf(session)
        _connect_spotify(c, stack)

        playlists = c.get(_url(stack, "web", "/api/playlists"))
        assert playlists.status_code == 200, playlists.text
        ids = [p["id"] for p in playlists.json()["playlists"]]
        assert "pl_main" in ids and "pl_second" in ids

        imp = c.post(_url(stack, "web", "/api/import/spotify"),
                     json={"reference": "spotify:pl_main"}, headers={"X-CSRF-Token": csrf})
        assert imp.status_code == 200, imp.text
        body = imp.json()
        assert body["playlist_id"] == "pl_main"
        assert body["track_count"] == 3

        synced = c.get(_url(stack, "web", "/api/synced")).json()
        assert any(p["id"] == "pl_main" for p in synced["synced"])

        an = c.post(_url(stack, "web", "/api/analyze"),
                    json={"name": "Meu Set", "target_bpm": 120, "tolerance_bpm": 3,
                          "reference": "spotify:pl_main", "tracks": []},
                    headers={"X-CSRF-Token": csrf})
        assert an.status_code == 200, an.text
        analysis = an.json()
        assert analysis["analysis_id"]
        assert analysis["items"][0]["track"]["name"] == "One More Time"

        analyses = c.get(_url(stack, "web", "/api/analyses")).json()
        assert len(analyses["analyses"]) >= 1
    finally:
        c.close()


def test_oauth_state_is_bound_to_the_session(stack):
    """O state do OAuth não pode ser consumido por outra sessão."""
    a = httpx.Client(timeout=30.0)
    b = httpx.Client(timeout=30.0)
    try:
        _session(a, stack)
        _session(b, stack)
        url = a.get(_url(stack, "web", "/api/auth/spotify")).json()["url"]

        # sessão B tenta concluir o fluxo iniciado na sessão A
        cb = b.get(url + "&auto=1", follow_redirects=True)
        assert cb.status_code == 200
        assert "state_invalid" in cb.text or "session_missing" in cb.text
        assert a.get(_url(stack, "web", "/api/auth/spotify/status")).json()["connected"] is False
    finally:
        a.close()
        b.close()


def test_oauth_state_is_single_use(stack):
    c = httpx.Client(timeout=30.0)
    try:
        _session(c, stack)
        url = c.get(_url(stack, "web", "/api/auth/spotify")).json()["url"]

        ok = c.get(url + "&auto=1", follow_redirects=True)
        assert "Spotify conectado" in ok.text

        # reutilizar o mesmo state (novo code gerado pelo fake) é bloqueado
        again = c.get(url + "&auto=1", follow_redirects=True)
        assert "state_reused" in again.text
    finally:
        c.close()


def test_spotify_token_rotation_and_refresh(stack):
    c = httpx.Client(timeout=60.0)
    try:
        session = _session(c, stack)
        csrf = _csrf(session)
        c.post(_url(stack, "spotify", "/__control"), json={"token_expires_in": 0})
        try:
            _connect_spotify(c, stack)  # access_token expira imediatamente

            # próxima chamada dispara refresh com rotação no fake
            playlists = c.get(_url(stack, "web", "/api/playlists"))
            assert playlists.status_code == 200, playlists.text
            assert playlists.json()["playlists"]
            assert c.get(_url(stack, "web", "/api/auth/spotify/status")).json()["connected"] is True
        finally:
            c.post(_url(stack, "spotify", "/__control"), json={"reset": True})
    finally:
        c.close()


def test_spotify_refresh_failure_revokes_connection(stack):
    c = httpx.Client(timeout=60.0)
    try:
        session = _session(c, stack)
        csrf = _csrf(session)
        c.post(_url(stack, "spotify", "/__control"), json={"token_expires_in": 0})
        _connect_spotify(c, stack)

        c.post(_url(stack, "spotify", "/__control"), json={"refresh_token_revoked": True, "token_expires_in": 0})
        try:
            playlists = c.get(_url(stack, "web", "/api/playlists"))
            assert playlists.status_code == 401, playlists.text
            assert playlists.json()["error"] == "SPOTIFY_AUTH_EXPIRED"
            assert c.get(_url(stack, "web", "/api/auth/spotify/status")).json()["connected"] is False
        finally:
            c.post(_url(stack, "spotify", "/__control"), json={"reset": True})
    finally:
        c.close()


def test_spotify_rate_limit_surfaces_as_error(stack):
    c = httpx.Client(timeout=60.0)
    try:
        session = _session(c, stack)
        csrf = _csrf(session)
        _connect_spotify(c, stack)

        c.post(_url(stack, "spotify", "/__control"), json={"playlist_status": 429})
        try:
            playlists = c.get(_url(stack, "web", "/api/playlists"))
            assert playlists.status_code == 502, playlists.text
            assert playlists.json()["error"] == "SPOTIFY_API_ERROR"
        finally:
            c.post(_url(stack, "spotify", "/__control"), json={"reset": True})
    finally:
        c.close()


def test_spotify_disconnect_stays_within_session(stack):
    c = httpx.Client(timeout=30.0)
    try:
        session = _session(c, stack)
        _connect_spotify(c, stack)
        r = c.post(_url(stack, "web", "/api/auth/spotify/disconnect"),
                   json={}, headers={"X-CSRF-Token": _csrf(session)})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert c.get(_url(stack, "web", "/api/auth/spotify/status")).json()["connected"] is False
    finally:
        c.close()


def test_no_tokens_leak_in_api_responses(stack):
    c = httpx.Client(timeout=30.0)
    try:
        session = _session(c, stack)
        csrf = _csrf(session)
        _connect_spotify(c, stack)

        def assert_no_tokens(payload, where):
            text = repr(payload).lower()
            assert "access_token" not in text, where
            assert "refresh_token" not in text, where
            assert "code_verifier" not in text, where

        assert_no_tokens(session, "session")
        st = c.get(_url(stack, "web", "/api/auth/spotify/status")).json()
        assert_no_tokens(st, "status")
        pl = c.get(_url(stack, "web", "/api/playlists")).json()
        assert_no_tokens(pl, "playlists")
        me = c.get(_url(stack, "web", "/api/me")).json()
        assert_no_tokens(me, "me")
    finally:
        c.close()