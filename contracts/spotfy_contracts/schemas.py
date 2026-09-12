"""Modelos compartilhados (pydantic) dos contratos entre serviços."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Decision(str, Enum):
    recommended = "recommended"
    rejected = "rejected"
    insufficient = "insufficient"
    conflict = "conflict"


class ReviewState(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class TrackInput(BaseModel):
    """Faixa como entregue pela importação (origem Spotify/arquivo)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    name: str
    artists: list[str] = Field(default_factory=list)
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    spotify_id: Optional[str] = None
    isrc: Optional[str] = None
    uri: Optional[str] = None
    added_at: Optional[str] = None
    source: str = "playlist"

    @property
    def artist_string(self) -> str:
        return ", ".join(self.artists)


class TrackCandidate(BaseModel):
    """Candidato vindo do catálogo (origem Tidal)."""

    model_config = ConfigDict(extra="allow")
    tidal_id: int
    title: str
    artists: list[str] = Field(default_factory=list)
    album: Optional[str] = None
    explicit: bool = False
    version: Optional[str] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    key_scale: Optional[str] = None
    duration: Optional[int] = None
    isrc: Optional[str] = None
    strategy: Optional[str] = None
    match_confidence: float = 0.0

    @property
    def artist_string(self) -> str:
        return ", ".join(self.artists)


class Job(BaseModel):
    model_config = ConfigDict(extra="allow")
    job_id: str
    type: str
    status: JobStatus = JobStatus.pending
    progress: float = 0.0
    message: str = ""
    created_at: str = ""
    updated_at: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None


class MatchResult(BaseModel):
    """Resultado de uma faixa avaliada pelo motor BPM Match."""

    model_config = ConfigDict(extra="allow")
    ordinal: int
    track: TrackInput
    candidates: list[TrackCandidate] = Field(default_factory=list)
    candidate_count: int = 0
    decision: Decision = Decision.insufficient
    score: float = 0.0
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    fields_used: list[str] = Field(default_factory=list)
    divergences: list[str] = Field(default_factory=list)
    chosen: Optional[TrackCandidate] = None
    review: ReviewState = ReviewState.pending
    review_comment: Optional[str] = None
    dependency_error: Optional[str] = None


class Analysis(BaseModel):
    model_config = ConfigDict(extra="allow")
    analysis_id: str
    name: str
    status: JobStatus = JobStatus.running
    target_bpm: float
    tolerance_bpm: float = 3.0
    created_at: str = ""
    updated_at: str = ""
    reference: Optional[str] = None
    summary: dict[str, Any] = Field(default_factory=dict)
    items: list[MatchResult] = Field(default_factory=list)
    error: Optional[str] = None
    reused: bool = False
    catalog_unavailable: bool = False


class LibraryFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    filename: str
    extension: str
    track_name: str
    normalized: str
    size_bytes: int = 0


class DownloadSelection(BaseModel):
    """Resultado de dry-run da biblioteca para uma faixa faltante."""

    model_config = ConfigDict(extra="allow")
    track: TrackInput
    status: str = "not_found"  # found | not_found | error
    quality: Optional[str] = None
    extension: Optional[str] = None
    tidal_id: Optional[int] = None
    reason: Optional[str] = None
    candidates: list[TrackCandidate] = Field(default_factory=list)
    manifest_url: str = ""
    codecs: str = ""


class DownloadReportItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    track: TrackInput
    filename: Optional[str] = None
    status: str = "skipped"  # downloaded | failed | skipped
    size_bytes: Optional[int] = None
    error: Optional[str] = None


class ReportFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    report_id: str
    format: str
    filename: str
    path: str
    created_at: str = ""
    content: str = ""


class UserPublic(BaseModel):
    username: str
    created_at: str = ""


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    username: str