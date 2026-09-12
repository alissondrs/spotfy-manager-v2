"""BPM Match Service — cálculo de compatibilidade, tolerâncias, ranking,
confiança, explicabilidade e rejeição segura de dados insuficientes."""

from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import Depends, Header
from pydantic import BaseModel, Field
from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.ids import artist_matches, detect_version, parse_source_reference, title_matches
from spotfy_contracts.schemas import (
    Analysis,
    Decision,
    JobStatus,
    MatchResult,
    ReviewState,
    TrackCandidate,
    TrackInput,
    utcnow_iso,
)
from spotfy_contracts.service import build_app
from spotfy_contracts.store import SqliteStore

SERVICE_NAME = "bpm-match"
app = build_app(SERVICE_NAME)
cfg = app.state.config
logger = app.state.logger

store = SqliteStore(os.path.join(cfg.data_dir, f"{SERVICE_NAME}_db.sqlite"))
store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS analyses (
        analysis_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL,
        status TEXT NOT NULL,
        target_bpm REAL NOT NULL,
        tolerance_bpm REAL NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
])

if not store.query_one("SELECT 1 FROM pragma_table_info('analyses') WHERE name = 'owner'"):
    store.execute("ALTER TABLE analyses ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
if not store.query_one("SELECT 1 FROM pragma_table_info('analyses') WHERE name = 'reference'"):
    store.execute("ALTER TABLE analyses ADD COLUMN reference TEXT")


class AnalyzeReq(BaseModel):
    """Base de uma análise BPM Match: faixas + BPM alvo."""

    name: str
    target_bpm: float
    tolerance_bpm: float = 3.0
    reference: Optional[str] = None
    force: bool = False
    tracks: list[TrackInput] = Field(default_factory=list)


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


def _catalog_url() -> str:
    return cfg.external_hifi_url and f"{cfg.external_hifi_url}" or cfg.catalog_url


def _fetch_candidates(track: TrackInput, authorization: str, limit: int = 10) -> tuple[list[TrackCandidate], Optional[str]]:
    """Retorna (candidatos, dependency_error). dependency_error só é preenchido
    quando a indisponibilidade da dependência impediu a busca (não é ausência
    real de candidatos)."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{_catalog_url()}/candidates",
                headers={"Authorization": authorization},
                json={
                    "name": track.name,
                    "artists": track.artists,
                    "album": track.album,
                    "tidal_id_hint": getattr(track, "extra_tidal_id", None),
                    "limit": limit,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return [TrackCandidate(**c) for c in data.get("candidates", [])], None
            logger.warning("catalog /candidates %s: %s", resp.status_code, resp.text[:200])
            return [], "catalog_unavailable"
    except httpx.RequestError as exc:
        logger.warning("catalog unreachable: %s", exc)
        return [], "catalog_unavailable"


def _resolve_tracks(reference: str, authorization: str) -> list[TrackInput]:
    """Resolve faixas de uma referência estável de origem no serviço de origem.

    Formatos: ``spotify:<playlist_id>`` e ``file:<import_id>``.
    """
    parsed = parse_source_reference(reference)
    if not parsed:
        raise_error("INVALID_PAYLOAD", f"Referência inválida: '{reference}'. Use spotify:<id> ou file:<import_id>.")
    kind, value = parsed
    if kind == "spotify":
        url = f"{cfg.playlist_url}/synced/{value}"
    else:
        url = f"{cfg.fileimport_url}/import/{value}"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers={"Authorization": authorization})
        if resp.status_code in (204, 205):
            return []
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("tracks") or []
            tracks = []
            for t in raw:
                item = TrackInput(**t)
                if item.source and item.source != "unknown" and kind == "spotify":
                    item.source = "spotify"
                tracks.append(item)
            return tracks
        if resp.status_code == 403:
            raise_error("FORBIDDEN")
        raise_error("NOT_FOUND", f"Não foi possível resolver a referência '{reference}' ({resp.status_code}).")
    except httpx.RequestError:
        raise_error("NOT_FOUND", f"Serviço de origem indisponível ao resolver '{reference}'.")


def _find_reuse(username: str, reference: str, target_bpm: float) -> Optional[Analysis]:
    """Retorna a última análise concluída da mesma playlist/referência do usuário."""
    row = store.query_one(
        "SELECT payload FROM analyses WHERE owner = ? AND reference = ? AND target_bpm = ? "
        "AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
        (username, reference, target_bpm),
    )
    if not row:
        return None
    try:
        analysis = Analysis(**_load_json(row["payload"]))
    except Exception:
        return None
    analysis.reused = True
    return analysis


def _bpm_score(target: float, tolerance: float, candidate_bpm: Optional[float]) -> Optional[float]:
    """Score 0..1 simétrico em torno do alvo, com tolerância suave. None se sem BPM."""
    if candidate_bpm is None or candidate_bpm <= 0:
        return None
    diff = abs(candidate_bpm - target)
    if diff <= tolerance:
        return 1.0
    # pena gradual: de 1.0 até ~0.2 no dobro da tolerância
    excess = diff - tolerance
    decay = tolerance
    if decay <= 0:
        decay = 1.0
    return max(0.2, 1.0 - (excess / decay) * 0.8)


def _aggregate_confidence(scores: list[Optional[float]],
                          candidate_count: int,
                          title_conf: float,
                          artist_conf: float,
                          has_bpm_any: bool,
                          conflict: bool) -> float:
    """Confiança final combinando: match de título/artista, disponibilidade de
    dados, coerência entre candidatos e presença de conflito."""
    bpm_values = [s for s in scores if s is not None]
    avg_bpm = sum(bpm_values) / len(bpm_values) if bpm_values else 0.0

    data_availability = 0.25 if has_bpm_any else (0.1 if candidate_count > 0 else 0.0)
    title_term = 0.30 * title_conf
    artist_term = 0.25 * artist_conf
    bpm_term = 0.20 * avg_bpm if bpm_values else 0.0

    if conflict:
        data_availability -= 0.15
        bpm_term *= 0.5
    return round(min(1.0, max(0.0, title_term + artist_term + bpm_term + data_availability)), 3)


def _decide(track: TrackInput,
            candidates: list[TrackCandidate],
            target_bpm: float,
            tolerance_bpm: float) -> tuple[Decision, MatchResult]:
    result = MatchResult(
        ordinal=0, track=track, candidates=candidates, candidate_count=len(candidates),
    )
    if not candidates:
        result.decision = Decision.insufficient
        result.confidence = 0.0
        result.reasons.append("Nenhum candidato encontrado no catálogo.")
        return result.decision, result

    scored = []
    for cand in candidates:
        t = title_matches(track.name, cand.title)
        a = artist_matches(", ".join(track.artists), cand.artists)
        if t <= 0.85 or a <= 0.5:
            # candidato fraco: registra mas não confia
            cand.match_confidence = round(min(t, a), 3)
            scored.append((t, a, None, cand))
            continue
        s = _bpm_score(target_bpm, tolerance_bpm, cand.bpm)
        conf = _aggregate_confidence([s], 1, t, a, s is not None, conflict=False)
        cand.match_confidence = conf
        scored.append((t, a, s, cand))

    bpm_present = [x for x in scored if x[2] is not None]

    divergences = _find_divergences(track, candidates)
    result.divergences = divergences

    # conflito: candidatos fortes com BPM muito distantes entre si
    conflict = _has_conflict(bpm_present, target_bpm)

    if not bpm_present:
        result.reasons.append("Nenhum candidato com BPM disponível — impossível validar compatibilidade.")
        result.decision = Decision.insufficient
        result.confidence = _aggregate_confidence([], len(candidates), 0.9, 0.9, False, conflict)
        if divergences:
            result.decision = Decision.conflict
            result.reasons.extend(divergences)
        return result.decision, result

    best_entry = max(bpm_present, key=lambda x: x[2])
    best = best_entry[3]
    best_score = best_entry[2]
    if best_score is None or best_score < 0.5:
        result.reasons.append(f"Nenhum candidato compatível dentro da tolerância (±{tolerance_bpm} BPM).")
        result.decision = Decision.rejected
        result.confidence = max(0.0, best_score or 0.0)
        result.chosen = best
        return result.decision, result

    t_best, a_best, _, _ = best_entry
    result.confidence = _aggregate_confidence(
        [s for (_, _, s, _) in bpm_present], len(candidates),
        t_best, a_best, True, conflict,
    )
    result.chosen = best
    result.fields_used = ["título", "artista", "BPM"]
    if best.bpm is not None:
        result.fields_used.append("chave musical")

    reason_parts = [
        f"Título mais próximo: '{best.title}' (confiança {t_best:.0%}).",
        f"BPM {best.bpm} a {abs(best.bpm - target_bpm)} do alvo {target_bpm:.0f} (±{tolerance_bpm}).",
    ]
    if best.explicit:
        reason_parts.append("Versão explícita.")
    if best.version:
        reason_parts.append(f"Versão marcada como '{best.version}'.")
    if conflict:
        result.decision = Decision.conflict
        reason_parts.append("Candidatos com BPM conflitantes entre si — revise antes de aprovar.")
    else:
        result.decision = Decision.recommended
    result.reasons.extend(reason_parts)
    if divergences:
        result.reasons.extend(divergences)
    return result.decision, result


def _find_divergences(track: TrackInput, candidates: list[TrackCandidate]) -> list[str]:
    divergences: list[str] = []
    versions = {detect_version(c.title) for c in candidates if c.title and detect_version(c.title)}
    distinct_versions = [v for v in versions if v]
    if len(distinct_versions) > 1:
        versions_text = ", ".join(distinct_versions)
        divergences.append(f"Candidatos com versões diferentes detectadas: {versions_text}.")
    explicit_any = any(c.explicit for c in candidates)
    if explicit_any:
        divergences.append("Há candidatos explícitos e não explícitos entre os resultados.")
    return divergences


def _has_conflict(bpm_present: list, target_bpm: float) -> bool:
    if len(bpm_present) < 2:
        return False
    bpm_values = sorted(c.bpm for (_, _, _, c) in bpm_present if c.bpm is not None)
    if len(bpm_values) < 2:
        return False
    scope = bpm_values[-1] - bpm_values[0]
    # conflito: candidatos fortes com BPM muito distantes entre si (ambiguidade real)
    return scope > 12.0


@app.get("/tolerance")
def defaults(_username: str = Depends(_require_user)) -> dict:
    return {"default_target_bpm": cfg.default_target_bpm, "default_tolerance_bpm": cfg.default_tolerance_bpm}


@app.post("/analyze")
def analyze(
    payload: AnalyzeReq,
    username: str = Depends(_require_user),
    authorization: str | None = Header(default=None),
) -> Analysis:
    """Executa o BPM Match para uma lista de faixas (fluxo síncrono para o MVP)."""
    if not (40 <= payload.target_bpm <= 240):
        raise_error("TARGET_BPM_INVALID")
    if payload.tolerance_bpm <= 0:
        raise_error("INVALID_PAYLOAD", "Tolerance BPM deve ser maior que zero.")

    tracks = list(payload.tracks)
    reference = (payload.reference or "").strip() or None
    if not tracks and reference:
        tracks = _resolve_tracks(reference, authorization or "")
    if not tracks:
        raise_error("PARSED_TRACKS_EMPTY")

    # Reuso: mesma playlist + mesmo alvo já analisados pelo usuário.
    if reference and not payload.force:
        previous = _find_reuse(username, reference, payload.target_bpm)
        if previous:
            logger.info("análise %s reutilizada para ref=%s (usuário %s)", previous.analysis_id, reference, username)
            return previous

    analysis_id = f"analysis_{os.urandom(4).hex()}"
    now = utcnow_iso()
    items: list[MatchResult] = []
    auth_header = authorization or ""
    catalog_unavailable = False
    for i, track in enumerate(tracks, start=1):
        candidates, dep_error = _fetch_candidates(track, auth_header)
        if dep_error:
            catalog_unavailable = True
        ranking = _rank(candidates)
        decision, result = _decide(track, ranking, payload.target_bpm, payload.tolerance_bpm)
        result.ordinal = i
        result.decision = decision
        if dep_error:
            result.dependency_error = dep_error
            result.reasons.append("Catálogo indisponível — não foi possível buscar candidatos (não é ausência de dados).")
        items.append(result)
    recommended = sum(1 for r in items if r.decision == Decision.recommended)
    rejected = sum(1 for r in items if r.decision == Decision.rejected)
    insufficient = sum(1 for r in items if r.decision == Decision.insufficient)
    conflict = sum(1 for r in items if r.decision == Decision.conflict)
    avg_conf = round(sum(r.confidence for r in items) / len(items), 3) if items else 0.0

    analysis = Analysis(
        analysis_id=analysis_id,
        name=payload.name,
        status=JobStatus.completed,
        target_bpm=payload.target_bpm,
        tolerance_bpm=payload.tolerance_bpm,
        created_at=now,
        updated_at=now,
        reference=reference,
        items=items,
        catalog_unavailable=catalog_unavailable,
        summary={
            "total": len(items),
            "recommended": recommended,
            "rejected": rejected,
            "insufficient": insufficient,
            "conflict": conflict,
            "avg_confidence": avg_conf,
            "catalog_unavailable": catalog_unavailable,
            "dependency_errors": sum(1 for r in items if r.dependency_error),
        },
    )
    store.execute(
        "INSERT OR REPLACE INTO analyses (analysis_id, owner, reference, payload, status, target_bpm, tolerance_bpm, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (analysis_id, username, reference, analysis.model_dump_json(), JobStatus.completed.value,
         payload.target_bpm, payload.tolerance_bpm, now, now),
    )
    logger.info("análise %s: %d faixas (rec=%d, rej=%d, insuf=%d, conflict=%d)",
                analysis_id, len(items), recommended, rejected, insufficient, conflict)
    return analysis


def _rank(candidates: list[TrackCandidate]) -> list[TrackCandidate]:
    # explicit original > clean original > versioned; ordem estável pela confiança de match
    return sorted(candidates, key=lambda c: (c.match_confidence, c.explicit), reverse=True)


@app.get("/analyses")
def list_analyses(username: str = Depends(_require_user)) -> dict:
    rows = store.query(
        "SELECT analysis_id, status, target_bpm, tolerance_bpm, created_at, updated_at FROM analyses WHERE owner = ? ORDER BY created_at DESC",
        (username,),
    )
    for r in rows:
        payload = store.query_one("SELECT payload FROM analyses WHERE analysis_id = ?", (r["analysis_id"],))
        p = _load_json(payload["payload"])
        r["summary"] = (p or {}).get("summary", {})
        r["name"] = (p or {}).get("name", r["analysis_id"])
    return {"analyses": rows}


@app.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, username: str = Depends(_require_user)) -> Analysis:
    row = store.query_one("SELECT payload FROM analyses WHERE analysis_id = ? AND owner = ?", (analysis_id, username))
    if not row:
        exists = store.query_one("SELECT 1 FROM analyses WHERE analysis_id = ?", (analysis_id,))
        if exists:
            raise_error("FORBIDDEN")
        raise_error("ANALYSIS_NOT_FOUND")
    p = _load_json(row["payload"])
    return Analysis(**p)


@app.post("/analyses/{analysis_id}/review")
def review(analysis_id: str, item_index: int, decision: str, comment: str | None = None, username: str = Depends(_require_user)) -> dict:
    row = store.query_one("SELECT payload FROM analyses WHERE analysis_id = ? AND owner = ?", (analysis_id, username))
    if not row:
        exists = store.query_one("SELECT 1 FROM analyses WHERE analysis_id = ?", (analysis_id,))
        if exists:
            raise_error("FORBIDDEN")
        raise_error("ANALYSIS_NOT_FOUND")
    payload = _load_json(row["payload"])
    items = payload["items"]
    if item_index < 0 or item_index >= len(items):
        raise_error("INVALID_PAYLOAD", f"item fora do intervalo (0..{len(items) - 1}).")
    item = items[item_index]
    if decision not in ("approved", "rejected"):
        raise_error("INVALID_PAYLOAD", "Decisão deve ser 'approved' ou 'rejected'.")
    item["review"] = ReviewState.approved.value if decision == "approved" else ReviewState.rejected.value
    item["review_comment"] = comment
    payload["items"] = items
    payload["updated_at"] = utcnow_iso()
    store.execute("UPDATE analyses SET payload = ?, updated_at = ? WHERE analysis_id = ?",
                  (json_dumps(payload), payload["updated_at"], analysis_id))
    return {"status": "ok", "analysis_id": analysis_id, "item_index": item_index,
            "review_state": item["review"]}


def _load_json(raw: str) -> dict:
    import json
    return json.loads(raw)


def json_dumps(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)


@app.get("/health/deps")
def health_deps() -> dict:
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{_catalog_url()}/health")
            return {"catalog": {"status": "ok" if resp.status_code == 200 else "error"}}
    except httpx.RequestError:
        return {"catalog": {"status": "unreachable"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)