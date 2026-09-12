"""Contratos e biblioteca compartilhada do spotfy-manager-v2.

Pacote com modelos, erros, normalização, parsing de arquivos, autenticação e
bootstrap de serviço usados por todos os microsserviços. Não contém regra de
domínio específica (matching, download, relatórios ficam nos serviços).
"""

from .errors import DomainError, ERROR_CATALOG, raise_error, to_error_dict
from .auth import (
    create_token,
    current_username_from_header,
    decode_token,
    hash_password,
    new_id,
    now_iso,
    verify_password,
)
from .ids import (
    artist_matches,
    detect_reference_source,
    detect_version,
    duration_label,
    fuzzy_ratio,
    normalize_stream_key,
    normalize_text,
    parse_spotify_reference,
    sanitize_filename,
    strip_parenthesized,
    title_matches,
)
from .files import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    parse_import_content,
    parse_playlist_markdown,
    parse_csv_playlist,
    parse_json_playlist,
    validate_upload,
)
from .store import JsonStore, SqliteStore
from .schemas import (
    Analysis,
    Decision,
    DownloadReportItem,
    DownloadSelection,
    Job,
    JobStatus,
    LibraryFile,
    MatchResult,
    ReportFile,
    ReviewState,
    TokenResponse,
    TrackCandidate,
    TrackInput,
    UserCreate,
    UserLogin,
    UserPublic,
    utcnow_iso,
)
from .service import BaseConfig, build_app, get_config, setup_logging

__all__ = [
    "DomainError", "ERROR_CATALOG", "raise_error", "to_error_dict",
    "create_token", "current_username_from_header", "decode_token",
    "hash_password", "new_id", "now_iso", "verify_password",
    "artist_matches", "detect_reference_source", "detect_version", "duration_label",
    "fuzzy_ratio", "normalize_stream_key", "normalize_text",
    "parse_spotify_reference", "sanitize_filename", "strip_parenthesized", "title_matches",
    "ALLOWED_EXTENSIONS", "MAX_UPLOAD_BYTES", "parse_import_content",
    "parse_playlist_markdown", "parse_csv_playlist", "parse_json_playlist", "validate_upload",
    "JsonStore", "SqliteStore",
    "Analysis", "Decision", "DownloadReportItem", "DownloadSelection", "Job",
    "JobStatus", "LibraryFile", "MatchResult", "ReportFile", "ReviewState",
    "TokenResponse", "TrackCandidate", "TrackInput", "UserCreate", "UserLogin",
    "UserPublic", "utcnow_iso",
    "BaseConfig", "build_app", "get_config", "setup_logging",
]

__version__ = "0.1.0"