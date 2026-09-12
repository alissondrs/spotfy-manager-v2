"""Report Service — exportação de análises BPM Match em Markdown/CSV/HTML
e histórico mínimo de execuções. Compatível com os `bpm_report_*` legados."""

from __future__ import annotations

import csv
import html
import io
import os
from typing import Optional

import httpx
from fastapi import Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from spotfy_contracts.auth import current_username_from_header
from spotfy_contracts.errors import raise_error
from spotfy_contracts.ids import sanitize_filename
from spotfy_contracts.schemas import Decision, utcnow_iso
from spotfy_contracts.service import build_app
from spotfy_contracts.store import SqliteStore

SERVICE_NAME = "report"
app = build_app(SERVICE_NAME)
cfg = app.state.config
logger = app.state.logger

REPORTS_DIR = os.path.join(cfg.data_dir, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

store = SqliteStore(os.path.join(cfg.data_dir, f"{SERVICE_NAME}_db.sqlite"))
store.init_schema([
    """
    CREATE TABLE IF NOT EXISTS reports (
        report_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL DEFAULT '',
        analysis_id TEXT NOT NULL,
        format TEXT NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        bytes INTEGER NOT NULL
    )
    """,
])
if not store.query_one("SELECT 1 FROM pragma_table_info('reports') WHERE name = 'owner'"):
    store.execute("ALTER TABLE reports ADD COLUMN owner TEXT NOT NULL DEFAULT ''")


class ReportReq(BaseModel):
    analysis_id: str
    format: str = "markdown"  # markdown | csv | html


def _require_user(authorization: str | None = Header(default=None)) -> str:
    return current_username_from_header(cfg.jwt_secret, authorization)


def _fetch_analysis(analysis_id: str, token: str) -> dict:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"{cfg.bpm_url}/analyses/{analysis_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 403:
            raise_error("FORBIDDEN")
        raise_error("ANALYSIS_NOT_FOUND", f"Serviço BPM Match retornou {resp.status_code}.")
    except httpx.RequestError:
        raise_error("ANALYSIS_NOT_FOUND", "Serviço BPM Match indisponível.")


def _fmt(value, default: str = "—") -> str:
    return default if value in (None, "") else str(value)


def _bpm_of(result: dict, use: str = "chosen") -> Optional[float]:
    if use == "chosen" and result.get("chosen"):
        b = result["chosen"].get("bpm")
        if b:
            return float(b)
    cands = result.get("candidates") or []
    for c in cands:
        if c.get("bpm"):
            return float(c["bpm"])
    return None


def _render_markdown(analysis: dict) -> str:
    name = analysis.get("name") or analysis.get("analysis_id")
    total = len(analysis.get("items", []))
    summary = analysis.get("summary", {})
    lines = [
        f"# BPM Match Report — {name}",
        "",
        f"_Alvo: {analysis['target_bpm']:.0f} BPM · Tolerância: ±{analysis['tolerance_bpm']:.1f} BPM_",
        f"_Total: {total} faixas · Recomendadas: {summary.get('recommended', 0)} · "
        f"Rejeitadas: {summary.get('rejected', 0)} · Insuficientes: {summary.get('insufficient', 0)} · "
        f"Conflito: {summary.get('conflict', 0)}_",
        "",
        "| # | Faixa | Artistas | Álbum | BPM | Confiança | Decisão | Como",
        "|---|-------|----------|-------|-----|-----------|---------|-----|",
    ]
    decision_label = {
        Decision.recommended.value: "RECOMENDADA",
        Decision.rejected.value: "REJEITADA",
        Decision.insufficient.value: "INSUFICIENTE",
        Decision.conflict.value: "CONFLITO",
    }
    for item in analysis.get("items", []):
        track = item["track"]
        bpm = _bpm_of(item)
        conf = f"{item.get('confidence', 0) * 100:.0f}%"
        reasons = "; ".join(item.get("reasons", []) or []) + (
            (f" [rev: {item['review']}]" if item.get("review") != "pending" else ""))
        lines.append(
            f"| {item.get('ordinal', 0)} | {_fmt(track.get('name'))} | "
            f"{_fmt(', '.join(track.get('artists', [])))} | {_fmt(track.get('album'))} | "
            f"{_fmt(bpm, '—')} | {conf} | {decision_label.get(item.get('decision'), item.get('decision'))} | "
            f"{reasons} |"
        )
    return "\n".join(lines) + "\n"


def _render_csv(analysis: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["#","Track","Artist","Album","BPM","Decision","Confidence","Reasons"])
    decision_label = {Decision.recommended.value: "RECOMENDADA",
                      Decision.rejected.value: "REJEITADA",
                      Decision.insufficient.value: "INSUFICIENTE",
                      Decision.conflict.value: "CONFLITO"}
    for item in analysis.get("items", []):
        track = item["track"]
        writer.writerow([
            item.get("ordinal", 0),
            track.get("name"),
            ", ".join(track.get("artists", [])),
            track.get("album") or "",
            _bpm_of(item) or "",
            decision_label.get(item.get("decision"), item.get("decision")),
            item.get("confidence", 0),
            "; ".join(item.get("reasons", []) or []),
        ])
    return buffer.getvalue()


def _render_html(analysis: dict) -> str:
    name = html.escape(analysis.get("name") or analysis.get("analysis_id"))
    target = analysis["target_bpm"]
    rows_html = []
    decision_badge = {
        Decision.recommended.value: ("badge-ok", "RECOMENDADA"),
        Decision.rejected.value: ("badge-no", "REJEITADA"),
        Decision.insufficient.value: ("badge-mid", "INSUFICIENTE"),
        Decision.conflict.value: ("badge-no", "CONFLITO"),
    }
    for item in analysis.get("items", []):
        track = item["track"]
        badge, label = decision_badge.get(item.get("decision"), ("badge-mid", item.get("decision")))
        bpm = _bpm_of(item)
        reasons = "; ".join(item.get("reasons", []) or [])
        conf = item.get("confidence", 0)
        rows_html.append(
            f"<tr><td>{item.get('ordinal', 0)}</td>"
            f"<td>{html.escape(track.get('name', ''))}</td>"
            f"<td>{html.escape(', '.join(track.get('artists', [])))}</td>"
            f"<td>{html.escape(track.get('album', '') or '—')}</td>"
            f"<td>{bpm if bpm else '—'}</td>"
            f"<td>{conf * 100:.0f}%</td>"
            f"<td><span class='{badge}'>{label}</span></td>"
            f"<td>{html.escape(reasons)}</td></tr>"
        )
    summary = analysis.get("summary", {})
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BPM Match Report — {name}</title>
<style>
:root {{ color-scheme: dark; }}
body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e8e8e8; margin: 0; padding: 24px; }}
h1 {{ color: #58c6b8; }}
.meta {{ opacity: .8; margin: 8px 0 20px; }}
table {{ border-collapse: collapse; width: 100%; background: #171a21; }}
th, td {{ border: 1px solid #2a2e39; padding: 8px 10px; text-align: left; font-size: 13px; }}
th {{ background: #1d2129; }}
.badge-ok {{ color: #58c6b8; font-weight: 600; }}
.badge-no {{ color: #ff7b72; font-weight: 600; }}
.badge-mid {{ color: #d29922; font-weight: 600; }}
</style></head><body>
<h1>BPM Match Report</h1>
<div class="meta">{name} · alvo {target:.0f} BPM ±{analysis['tolerance_bpm']:.1f} ·
recomendadas {summary.get('recommended', 0)} · rejeitadas {summary.get('rejected', 0)} ·
insuficientes {summary.get('insufficient', 0)} · conflito {summary.get('conflict', 0)}</div>
<table><thead><tr><th>#</th><th>Faixa</th><th>Artistas</th><th>Álbum</th><th>BPM</th>
<th>Confiança</th><th>Decisão</th><th>Como</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table>
</body></html>
"""


_RENDERERS = {
    "markdown": ("md", _render_markdown),
    "csv": ("csv", _render_csv),
    "html": ("html", _render_html),
}


@app.post("/reports")
def create_report(payload: ReportReq, username: str = Depends(_require_user), authorization: str | None = Header(default=None)) -> dict:
    fmt = (payload.format or "markdown").lower()
    if fmt not in _RENDERERS:
        raise_error("REPORT_FORMAT_UNSUPPORTED",
                    f"Formato '{fmt}' inválido. Use markdown, csv ou html.")
    _scheme, _sep, token = (authorization or "").partition(" ")
    token = token.strip()
    analysis = _fetch_analysis(payload.analysis_id, token=token)
    ext, renderer = _RENDERERS[fmt]
    content = renderer(analysis)
    base = sanitize_filename(analysis.get("name") or payload.analysis_id)
    filename = f"bpm_report_{base}.{ext}"
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    report_id = f"report_{os.urandom(4).hex()}"
    created = utcnow_iso()
    store.execute(
        "INSERT OR REPLACE INTO reports (report_id, owner, analysis_id, format, filename, path, created_at, bytes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (report_id, username, payload.analysis_id, fmt, filename, path, created, len(content.encode("utf-8"))),
    )
    logger.info("relatório %s (%s) para análise %s", report_id, fmt, payload.analysis_id)
    return {"report_id": report_id, "analysis_id": payload.analysis_id, "format": fmt,
            "filename": filename, "bytes": len(content.encode("utf-8"))}


@app.get("/reports")
def list_reports(username: str = Depends(_require_user)) -> dict:
    rows = store.query("SELECT * FROM reports WHERE owner = ? ORDER BY created_at DESC", (username,))
    return {"reports": rows}


@app.get("/reports/{report_id}/download")
def download_report(report_id: str, username: str = Depends(_require_user)):
    row = store.query_one("SELECT * FROM reports WHERE report_id = ? AND owner = ?", (report_id, username))
    if not row:
        exists = store.query_one("SELECT 1 FROM reports WHERE report_id = ?", (report_id,))
        if exists:
            raise_error("FORBIDDEN")
        raise_error("REPORT_NOT_FOUND")
    return FileResponse(row["path"], filename=row["filename"], media_type="application/octet-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=False)