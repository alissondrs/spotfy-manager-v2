"""Identificadores, normalização e matching de títulos/artistas."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

_SPOTIFY_URI_RE = re.compile(r"^spotify:(playlist|track|album|artist):([A-Za-z0-9]{22})$")
_SPOTIFY_URL_RE = re.compile(r"^https?://open\.spotify\.com/(playlist|track|album|artist)/([A-Za-z0-9]{22})")
_RAW_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")

_VERSION_TOKENS = ("remix", "live", "radio edit", "extended", "instrumental",
                   "acoustic", "original mix", "edit", "version", "ao vivo",
                   "versão", "reprise", "mashup", "bootleg")


def _compile_escaped(token: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(token)}\b")


_VERSION_RES = [_compile_escaped(_tok) for _tok in _VERSION_TOKENS]

TITLE_SPLIT_CHARS = re.compile(r"[^a-z0-9]+")


def detect_reference_source(query: str) -> str:
    """'favorites' | 'uri' | 'url' | 'id' | 'name'."""
    q = (query or "").strip()
    if not q:
        return "name"
    lower = q.lower()
    if lower == "favoritas" or lower == "favorites":
        return "favorites"
    if lower.startswith("spotify:"):
        return "uri"
    if "open.spotify.com/" in lower:
        return "url"
    if _RAW_ID_RE.match(q):
        return "id"
    return "name"


def parse_spotify_reference(query: str) -> Optional[tuple[str, str]]:
    """Retorna (kind, id) para URI/URL/ID puro; None se não for reconhecível."""
    q = (query or "").strip()
    m = _SPOTIFY_URI_RE.match(q) or _SPOTIFY_URL_RE.match(q)
    if m:
        return (m.group(1), m.group(2))
    if _RAW_ID_RE.match(q):
        return ("unknown", q)
    return None


def parse_source_reference(reference: Optional[str]) -> Optional[tuple[str, str]]:
    """Interpreta uma referência estável de origem de faixas.

    Formatos aceitos:
    - ``spotify:<playlist_id>`` -> (``spotify``, id)
    - ``file:<import_id>``  -> (``file``, import_id)

    Retorna None para referências não reconhecidas.
    """
    if not reference or not isinstance(reference, str):
        return None
    ref = reference.strip()
    prefix, sep, value = ref.partition(":")
    if not sep or not value.strip():
        return None
    kind = prefix.strip().lower()
    if kind == "spotify":
        return ("spotify", value.strip())
    if kind == "file":
        return ("file", value.strip())
    return None


def sanitize_filename(value: str, fallback: str = "playlist") -> str:
    """Limpa um nome para uso seguro como nome de arquivo."""
    if not value:
        return fallback
    normalized = unicodedata.normalize("NFKD", value)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def normalize_text(text: str, remove_accents: bool = False) -> str:
    """Normaliza texto para comparação: lowercase, espaços colapsados, acentos opcionais."""
    if text is None:
        return ""
    t = unicodedata.normalize("NFD", str(text))
    if remove_accents:
        t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.lower()
    return " ".join(t.split())


def normalize_stream_key(title: str, artist: str) -> str:
    """Chave canônica para deduplicação de faixas (título + artista)."""
    return f"{normalize_text(title, remove_accents=True)}::{normalize_text(artist, remove_accents=True)}"


def strip_parenthesized(text: str) -> str:
    """Remove trechos entre parênteses (ex: '(King Kami Remix)'), como o projeto legado."""
    return " ".join(re.sub(r"\s*\([^)]*\)", "", text or "").split()).strip()


def fuzzy_ratio(a: str, b: str, remove_accents: bool = True) -> float:
    return SequenceMatcher(None, normalize_text(a, remove_accents=remove_accents),
                           normalize_text(b, remove_accents=remove_accents)).ratio()


def title_matches(expected: str, candidate: str, threshold: float = 0.0) -> float:
    """Verifica se candidato contém/é contido no título esperado; retorna confiança 0..1."""
    exp = normalize_text(expected, remove_accents=True)
    cand = normalize_text(candidate, remove_accents=True)
    if not exp or not cand:
        return 0.0
    if exp == cand:
        return 1.0
    if exp in cand or cand in exp:
        return 0.9
    ratio = fuzzy_ratio(exp, cand)
    if ratio >= threshold:
        return ratio
    return 0.0


def artist_matches(expected: str, candidate_artists: list[str], threshold: float = 0.8) -> float:
    """Compara lista esperada de artistas com os artistas do candidato (conjunto)."""
    expected_set = {normalize_text(a, remove_accents=True) for a in _split_artists(expected)}
    cand_set = {normalize_text(a, remove_accents=True) for a in candidate_artists or []}
    if not expected_set or not cand_set:
        return 0.0
    if expected_set == cand_set:
        return 1.0
    inter = expected_set & cand_set
    if inter:
        return 0.6 + 0.4 * (len(inter) / max(len(expected_set), len(cand_set)))
    # tenta razão de similaridade nos pares
    best = max((fuzzy_ratio(e, c) for e in expected_set for c in cand_set), default=0.0)
    return best if best >= threshold else 0.0


def _split_artists(raw: str | list[str]) -> list[str]:
    if isinstance(raw, list):
        return [str(a) for a in raw if str(a).strip()]
    return [a.strip() for a in str(raw).split(",") if a.strip()]


def detect_version(title: str) -> Optional[str]:
    """Retorna o marcador de versão (remix, live, etc.) ou None se for original."""
    t = normalize_text(title or "")
    for res in _VERSION_RES:
        if res.search(t):
            return res
    for token in _VERSION_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", t):
            return token
    return None


def duration_label(ms: Optional[int]) -> str:
    if not ms:
        return "—"
    total_s = ms // 1000
    return f"{total_s // 60}:{total_s % 60:02d}"