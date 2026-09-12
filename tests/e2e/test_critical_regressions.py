"""Critical service regressions for BPM safety and the download confirmation lifecycle."""

from __future__ import annotations

import sys

import httpx
import pytest

from spotfy_contracts.schemas import Decision, TrackCandidate, TrackInput

from test_flow import PASS, _auth_headers, _auth_token, _url, _wait_job


def test_bpm_score_boundaries_are_deterministic(stack):
    bpm = sys.modules["svc_bpm_match"]
    assert bpm._bpm_score(120, 3, None) is None
    assert bpm._bpm_score(120, 3, 117) == 1.0
    assert bpm._bpm_score(120, 3, 123) == 1.0
    assert bpm._bpm_score(120, 3, 126) == pytest.approx(0.2)
    assert bpm._bpm_score(120, 3, 200) == pytest.approx(0.2)


def test_divergent_candidate_bpms_are_never_recommended(stack):
    """The documented conservative rule requires conflict for strong divergent candidates."""
    bpm = sys.modules["svc_bpm_match"]
    track = TrackInput(name="One More Time", artists=["Daft Punk"])
    candidates = [
        TrackCandidate(tidal_id=1, title=track.name, artists=track.artists, bpm=120),
        TrackCandidate(tidal_id=2, title=track.name, artists=track.artists, bpm=140),
    ]

    decision, result = bpm._decide(track, candidates, target_bpm=120, tolerance_bpm=3)

    assert decision == Decision.conflict
    assert result.chosen is not None
    assert any("conflitantes" in reason for reason in result.reasons)


def test_compatible_candidate_is_not_hidden_by_an_out_of_range_candidate(stack):
    """Candidate ordering must not reject a track when a strong candidate is in tolerance."""
    bpm = sys.modules["svc_bpm_match"]
    track = TrackInput(name="Voyager", artists=["Daft Punk"])
    candidates = [
        TrackCandidate(tidal_id=10, title=track.name, artists=track.artists, bpm=130),
        TrackCandidate(tidal_id=11, title=track.name, artists=track.artists, bpm=120),
    ]

    decision, result = bpm._decide(track, candidates, target_bpm=120, tolerance_bpm=3)

    assert decision == Decision.recommended
    assert result.chosen is not None and result.chosen.tidal_id == 11


def test_catalog_unavailable_is_distinct_from_empty_catalog(stack, monkeypatch):
    bpm = sys.modules["svc_bpm_match"]
    monkeypatch.setattr(bpm, "_catalog_url", lambda: "http://127.0.0.1:1")
    token = _auth_token(stack, "catalog_unavailable_qa", PASS)
    headers = _auth_headers(token)

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            _url(stack, "bpm-match", "/analyze"),
            headers=headers,
            json={
                "name": "Dependency failure",
                "target_bpm": 120,
                "tolerance_bpm": 3,
                "tracks": [{"name": "One More Time", "artists": ["Daft Punk"]}],
            },
        )

    assert response.status_code == 200, response.text
    analysis = response.json()
    assert analysis["catalog_unavailable"] is True
    assert analysis["summary"]["catalog_unavailable"] is True
    assert analysis["summary"]["dependency_errors"] == 1
    assert analysis["items"][0]["decision"] == "insufficient"
    assert analysis["items"][0]["dependency_error"] == "catalog_unavailable"
    assert "não é ausência de dados" in " ".join(analysis["items"][0]["reasons"])


def test_analysis_reuse_is_scoped_by_owner(stack):
    first_token = _auth_token(stack, "reuse_owner_a", PASS)
    second_token = _auth_token(stack, "reuse_owner_b", PASS)
    body = {
        "name": "Owner-scoped reuse",
        "target_bpm": 123,
        "tolerance_bpm": 3,
        "reference": "file:shared-looking-reference",
        "tracks": [{"name": "One More Time", "artists": ["Daft Punk"]}],
    }

    with httpx.Client(timeout=30.0) as client:
        first = client.post(
            _url(stack, "bpm-match", "/analyze"), json=body, headers=_auth_headers(first_token)
        )
        second = client.post(
            _url(stack, "bpm-match", "/analyze"), json=body, headers=_auth_headers(second_token)
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["analysis_id"] != second.json()["analysis_id"]
    assert second.json()["reused"] is False


def test_dryrun_confirmation_is_single_use(stack):
    """CA-14 requires repeated confirmation of one dry-run to be blocked."""
    token = _auth_token(stack, "single_use_dryrun_qa", PASS)
    headers = _auth_headers(token)
    track = {"name": "One More Time", "artists": ["Daft Punk"], "album": "Discovery"}
    download_dir = stack["base"] / "downloads"
    files_before = set(download_dir.iterdir()) if download_dir.exists() else set()

    try:
        with httpx.Client(timeout=60.0) as client:
            dryrun = client.post(
                _url(stack, "library", "/dryrun"), json={"tracks": [track]}, headers=headers
            )
            assert dryrun.status_code == 200, dryrun.text
            dry_run_id = dryrun.json()["dry_run_id"]

            first = client.post(
                _url(stack, "library", "/download"), json={"dry_run_id": dry_run_id}, headers=headers
            )
            assert first.status_code == 200, first.text
            _wait_job(stack, client, headers, first.json()["job_id"])

            repeated = client.post(
                _url(stack, "library", "/download"), json={"dry_run_id": dry_run_id}, headers=headers
            )
            if repeated.status_code == 200:
                _wait_job(stack, client, headers, repeated.json()["job_id"])
    finally:
        if download_dir.exists():
            for path in set(download_dir.iterdir()) - files_before:
                path.unlink()

    assert repeated.status_code == 409, repeated.text
    assert repeated.json()["error"] in {"DRYRUN_NOT_FOUND", "DOWNLOAD_JOB_CONFIRM_REQUIRED"}
