"""Fluxo ponta a ponta (BPM Match completo) contra serviços reais em processo."""

from __future__ import annotations

import time

import httpx
from mutagen.easyid3 import EasyID3FileType

USER = "alice"
PASS = "senha12345"


def _url(stack, name: str, path: str) -> str:
    return f"http://127.0.0.1:{stack['ports'][name]}{path}"


def _auth_token(stack, username: str = USER, password: str = PASS) -> str:
    c = httpx.Client(timeout=15.0)
    c.post(_url(stack, "identity", "/_debug/bootstrap"))
    c.post(_url(stack, "identity", "/auth/register"), json={"username": username, "password": password})
    r = c.post(_url(stack, "identity", "/auth/login"), json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.close()
    return r.json()["token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _web_session(c: httpx.Client, stack) -> dict:
    """Cria a sessão anônima via BFF e retorna o token CSRF válido."""
    r = c.get(_url(stack, "web", "/api/session"))
    assert r.status_code == 200, r.text
    return r.json()


def _web_headers(session: dict) -> dict:
    return {"X-CSRF-Token": session["session"]["csrf_token"]}


def _wait_job(stack, c: httpx.Client, headers: dict, job_id: str, service: str = "library", timeout: float = 30.0) -> dict:
    base = _url(stack, service, "/jobs") if service == "library" else _url(stack, "web", "/api/library/jobs")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"{base}/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == "completed":
            return body
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} não concluiu em {timeout}s")


def test_identity_register_and_login(stack):
    token = _auth_token(stack)
    assert token


def test_fileimport_markdown(stack):
    token = _auth_token(stack)
    headers = _auth_headers(token)
    md = (
        b"# Meu Set (125 BPM)\n\n"
        b"1. One More Time - Daft Punk\n"
        b"2. Get Lucky - Daft Punk, Pharrell Williams\n"
        b"3. Faixa Inexistente - Artista Desconhecido\n"
    )
    c = httpx.Client(timeout=15.0)
    r = c.post(
        _url(stack, "fileimport", "/import"),
        files={"file": ("set.md", md, "text/markdown")},
        headers=headers,
    )
    c.close()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["track_count"] == 3
    assert body["name"] == "Meu Set (125 BPM)"


def _analyze(stack, c: httpx.Client, token: str, name="Meu Set", target=120, tolerance=3):
    tracks = [
        {"name": "One More Time", "artists": ["Daft Punk"], "album": "Discovery"},
        {"name": "Get Lucky", "artists": ["Daft Punk", "Pharrell Williams"], "album": "Random Access Memories"},
        {"name": "Faixa Inexistente", "artists": ["Artista Desconhecido"]},
    ]
    r = c.post(
        _url(stack, "bpm-match", "/analyze"),
        json={"name": name, "target_bpm": target, "tolerance_bpm": tolerance, "tracks": tracks},
        headers=_auth_headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_analyze_decisions(stack):
    token = _auth_token(stack)
    c = httpx.Client(timeout=30.0)
    body = _analyze(stack, c, token)
    c.close()
    assert body["summary"]["recommended"] == 2
    assert body["summary"]["insufficient"] == 1
    decisions = [i["decision"] for i in body["items"]]
    assert decisions[0] == "recommended"
    assert decisions[2] == "insufficient"


def test_review_and_report(stack):
    token = _auth_token(stack)
    headers = _auth_headers(token)
    c = httpx.Client(timeout=30.0)
    analysis = _analyze(stack, c, token)
    analysis_id = analysis["analysis_id"]

    r = c.post(
        _url(stack, "bpm-match", f"/analyses/{analysis_id}/review"),
        params={"item_index": 0, "decision": "approved"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["review_state"] == "approved"

    r = c.post(
        _url(stack, "report", "/reports"),
        json={"analysis_id": analysis_id, "format": "markdown"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    report = r.json()

    r = c.get(_url(stack, "report", f"/reports/{report['report_id']}/download"), headers=headers)
    c.close()
    assert r.status_code == 200
    assert "Meu Set" in r.text
    assert "One More Time" in r.text


def test_library_compare_dryrun_download(stack):
    token = _auth_token(stack)
    headers = _auth_headers(token)
    c = httpx.Client(timeout=60.0)
    r = c.get(_url(stack, "library", "/library"), headers=headers)
    assert r.json()["count"] == 0

    track = {"name": "One More Time", "artists": ["Daft Punk"], "album": "Discovery"}
    r = c.post(_url(stack, "library", "/compare"), json={"tracks": [track]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["missing_count"] == 1

    r = c.post(_url(stack, "library", "/dryrun"), json={"tracks": [track, track]}, headers=headers)
    assert r.status_code == 200, r.text
    dryrun = r.json()
    selections = dryrun["selections"]
    assert dryrun["dry_run_id"].startswith("dryrun_")
    assert selections[0]["status"] == "found"
    assert selections[0]["manifest_url"].startswith(f"http://127.0.0.1:{stack['ports']['catalog']}")
    assert selections[0]["extension"] == "mp3"

    r = c.post(_url(stack, "library", "/download"), json={"dry_run_id": dryrun["dry_run_id"]}, headers=headers)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["job_id"].startswith("download_")
    assert job["status"] == "running"

    state = _wait_job(stack, c, headers, job["job_id"])
    assert state["completed"] == 2 and state["total"] == 2
    assert all(it["status"] == "downloaded" for it in state["items"])

    r = c.get(_url(stack, "library", "/library"), headers=headers)
    assert r.json()["count"] == 1

    path = stack["base"] / "downloads" / state["items"][0]["filename"]
    assert path.exists()
    audio = EasyID3FileType(str(path))
    assert audio["title"] == ["One More Time"]
    assert audio["artist"] == ["Daft Punk"]
    c.close()


def test_web_download_confirmation_and_import_lookup(stack):
    c = httpx.Client(timeout=60.0)
    session = _web_session(c, stack)
    headers = _web_headers(session)
    md = b"# Set\n\n1. One More Time - Daft Punk\n"
    r = c.post(
        _url(stack, "web", "/api/import/file"),
        files={"file": ("set.md", md, "text/markdown")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    import_id = r.json()["import_id"]

    r = c.get(_url(stack, "web", f"/api/import/{import_id}"))
    assert r.status_code == 200, r.text
    assert r.json()["import_id"] == import_id
    assert r.json()["tracks"][0]["name"] == "One More Time"

    track = {"name": "One More Time", "artists": ["Daft Punk"], "album": "Discovery"}
    r = c.post(_url(stack, "web", "/api/library/dryrun"), json={"tracks": [track]}, headers=headers)
    assert r.status_code == 200, r.text
    dryrun = r.json()
    selection = dryrun["selections"][0]
    assert selection["status"] == "found"

    r = c.post(_url(stack, "web", "/api/library/download"), json={"dry_run_id": dryrun["dry_run_id"]}, headers=headers)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["job_id"].startswith("download_")
    state = _wait_job(stack, c, headers, job["job_id"], service="web")
    c.close()
    assert state["items"][0]["status"] == "downloaded"


def test_web_bff_and_ui(stack):
    c = httpx.Client(timeout=30.0)
    session = _web_session(c, stack)
    headers = _web_headers(session)
    r = c.get(_url(stack, "web", "/health"))
    assert r.status_code == 200 and r.json()["service"] == "web"
    r = c.get(_url(stack, "web", "/"))
    assert r.status_code == 200 and "BPM Match" in r.text
    r = c.get(_url(stack, "web", "/static/app.js"))
    assert r.status_code == 200
    r = c.get(_url(stack, "web", "/api/me"))
    assert r.json()["anonymous"] is True
    assert r.json()["session"]["owner"].startswith("anon_")

    md = b"# Set\n\n1. One More Time - Daft Punk\n2. Get Lucky - Daft Punk, Pharrell\n"
    r = c.post(
        _url(stack, "web", "/api/import/file"),
        files={"file": ("set.md", md, "text/markdown")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    import_id = r.json()["import_id"]
    r = c.post(
        _url(stack, "web", "/api/analyze"),
        json={"name": "Set", "target_bpm": 120, "tolerance_bpm": 3,
              "tracks": [], "reference": f"file:{import_id}"},
        headers=headers,
    )
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json()["analysis_id"]
    r = c.get(_url(stack, "web", "/api/analyses"))
    c.close()
    assert r.status_code == 200 and r.json()["analyses"]


def test_metrics_exposed(stack):
    c = httpx.Client(timeout=15.0)
    for service in ("catalog", "bpm-match", "web"):
        r = c.get(_url(stack, service, "/metrics"))
        assert r.status_code == 200
        assert "http_requests_total" in r.text
    r = c.get(_url(stack, "catalog", "/mock/audio.mp3"))
    c.close()
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"


def test_domain_endpoints_require_auth(stack):
    c = httpx.Client(timeout=30.0)
    checks = [
        ("GET", _url(stack, "fileimport", "/imports")),
        ("GET", _url(stack, "playlist", "/synced")),
        ("GET", _url(stack, "bpm-match", "/analyses")),
        ("GET", _url(stack, "library", "/library")),
        ("GET", _url(stack, "report", "/reports")),
        ("GET", _url(stack, "catalog", "/search?s=one")),
    ]
    for method, url in checks:
        r = c.request(method, url)
        assert r.status_code == 401, (url, r.status_code, r.text)
    c.close()


def test_analysis_reuse_by_reference(stack):
    token = _auth_token(stack)
    headers = _auth_headers(token)
    c = httpx.Client(timeout=30.0)
    md = b"# Set\n\n1. One More Time - Daft Punk\n2. Get Lucky - Daft Punk, Pharrell\n"
    r = c.post(
        _url(stack, "fileimport", "/import"),
        files={"file": ("set.md", md, "text/markdown")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    import_id = r.json()["import_id"]
    reference = f"file:{import_id}"

    body = {"name": "Set", "target_bpm": 120, "tolerance_bpm": 3, "tracks": [], "reference": reference}
    a1 = c.post(_url(stack, "bpm-match", "/analyze"), json=body, headers=headers)
    assert a1.status_code == 200, a1.text
    first = a1.json()
    assert first["items"][0]["track"]["name"] == "One More Time"

    a2 = c.post(_url(stack, "bpm-match", "/analyze"), json=body, headers=headers)
    assert a2.status_code == 200, a2.text
    second = a2.json()
    assert second["reused"] is True
    assert second["analysis_id"] == first["analysis_id"]

    a3 = c.post(_url(stack, "bpm-match", "/analyze"), json={**body, "force": True}, headers=headers)
    assert a3.status_code == 200, a3.text
    third = a3.json()
    assert third.get("reused", False) is False
    assert third["analysis_id"] != first["analysis_id"]
    c.close()


def test_download_requires_bound_dryrun(stack):
    import sys

    lib = sys.modules["svc_library"]
    token = _auth_token(stack)
    headers = _auth_headers(token)
    c = httpx.Client(timeout=30.0)

    r = c.post(_url(stack, "library", "/download"), json={}, headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "DOWNLOAD_JOB_CONFIRM_REQUIRED"

    r = c.post(_url(stack, "library", "/download"), json={"dry_run_id": "dryrun_naoexiste"}, headers=headers)
    assert r.status_code == 404, r.text
    assert r.json()["error"] == "DRYRUN_NOT_FOUND"

    track = {"name": "One More Time", "artists": ["Daft Punk"], "album": "Discovery"}
    dr = c.post(_url(stack, "library", "/dryrun"), json={"tracks": [track]}, headers=headers).json()
    dry_run_id = dr["dry_run_id"]
    lib.store.execute("UPDATE dryruns SET expires_at = '1' WHERE dry_run_id = ?", (dry_run_id,))
    r = c.post(_url(stack, "library", "/download"), json={"dry_run_id": dry_run_id}, headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "DRYRUN_EXPIRED"

    owner_alice = _auth_token(stack, "alice_dryrun", PASS)
    dr2 = c.post(_url(stack, "library", "/dryrun"), json={"tracks": [track]}, headers=_auth_headers(owner_alice)).json()
    r = c.post(_url(stack, "library", "/download"), json={"dry_run_id": dr2["dry_run_id"]}, headers=headers)
    assert r.status_code == 403, r.text
    assert r.json()["error"] == "FORBIDDEN"
    c.close()


def test_ownership_isolation_across_services(stack):
    token_alice = _auth_token(stack, "alice_owner", PASS)
    token_bob = _auth_token(stack, "bob_owner", PASS)
    headers_alice = _auth_headers(token_alice)
    headers_bob = _auth_headers(token_bob)
    c = httpx.Client(timeout=60.0)

    md = b"# Set Alice\n\n1. One More Time - Daft Punk\n"
    r = c.post(
        _url(stack, "fileimport", "/import"),
        files={"file": ("set.md", md, "text/markdown")},
        headers=headers_alice,
    )
    assert r.status_code == 200
    import_id = r.json()["import_id"]
    assert c.get(_url(stack, "fileimport", f"/import/{import_id}"), headers=headers_bob).status_code == 403

    track = {"name": "One More Time", "artists": ["Daft Punk"], "album": "Discovery"}
    dryrun = c.post(_url(stack, "library", "/dryrun"), json={"tracks": [track]}, headers=headers_alice).json()
    download = c.post(
        _url(stack, "library", "/download"),
        json={"dry_run_id": dryrun["dry_run_id"]},
        headers=headers_alice,
    ).json()
    assert c.get(_url(stack, "library", f"/jobs/{download['job_id']}"), headers=headers_bob).status_code == 403

    analysis = c.post(
        _url(stack, "bpm-match", "/analyze"),
        json={"name": "Alice", "target_bpm": 120, "tolerance_bpm": 3, "tracks": [track]},
        headers=headers_alice,
    ).json()
    assert c.get(_url(stack, "bpm-match", f"/analyses/{analysis['analysis_id']}"), headers=headers_bob).status_code == 403

    report = c.post(
        _url(stack, "report", "/reports"),
        json={"analysis_id": analysis["analysis_id"], "format": "markdown"},
        headers=headers_alice,
    ).json()
    assert c.get(_url(stack, "report", f"/reports/{report['report_id']}/download"), headers=headers_bob).status_code == 403
    c.close()
