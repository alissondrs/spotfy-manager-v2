"""Bootstrap padrão dos serviços: config, logging, health, métricas e handlers."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .auth import validate_jwt_secret
from .errors import DomainError

# ---------------------------------------------------------------------------
# Configuração base
# ---------------------------------------------------------------------------

_SERVICE_DEFAULTS = {
    "identity": 8101,
    "fileimport": 8102,
    "playlist": 8103,
    "catalog": 8104,
    "bpm-match": 8105,
    "library": 8106,
    "report": 8107,
    "web": 8000,
}


class BaseConfig:
    """Config compartilhada por todos os serviços (env-driven)."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self.port = int(os.getenv("SERVICE_PORT", str(_SERVICE_DEFAULTS.get(service_name, 8100))))
        self.data_dir = os.getenv("DATA_DIR", "./data")
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.jwt_secret = os.getenv("JWT_SECRET", "dev-secret-change-me")
        self.jwt_ttl_minutes = int(os.getenv("JWT_TTL_MINUTES", "480"))
        self.app_env = os.getenv("APP_ENV", "development")
        self.jwt_allow_insecure_dev = os.getenv("JWT_ALLOW_INSECURE_FOR_DEV", "0") in ("1", "true", "yes")
        self.version = os.getenv("SERVICE_VERSION", "0.1.0")
        self.spotify_access_token = os.getenv("SPOTIFY_ACCESS_TOKEN", "")
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
        self.default_target_bpm = float(os.getenv("DEFAULT_TARGET_BPM", "120"))
        self.default_tolerance_bpm = float(os.getenv("DEFAULT_TOLERANCE_BPM", "3"))
        self.dryrun_ttl_minutes = int(os.getenv("DRYRUN_TTL_MINUTES", "15"))
        self.download_dir = os.getenv("DOWNLOAD_DIR", "")
        self.tidal_token_file = os.getenv("TIDAL_TOKEN_FILE", "")
        self.tidal_client_id = os.getenv("TIDAL_CLIENT_ID", "")
        self.tidal_client_secret = os.getenv("TIDAL_CLIENT_SECRET", "")
        self.tidal_refresh_token = os.getenv("TIDAL_REFRESH_TOKEN", "")
        self.tidal_country_code = os.getenv("TIDAL_COUNTRY_CODE", "US")
        self.external_hifi_url = os.getenv("HIFI_API_URL", "").rstrip("/")
        validate_jwt_secret(self.jwt_secret, self.app_env, allow_insecure_dev=self.jwt_allow_insecure_dev)

    # URLs dos demais serviços (defaults na rede docker compose)
    def url(self, name: str) -> str:
        port = _SERVICE_DEFAULTS.get(name, 8100)
        return os.getenv(f"{name.upper().replace('-', '_')}_URL", f"http://{name}:{port}").rstrip("/")

    @property
    def identity_url(self) -> str:
        return self.url("identity")

    @property
    def fileimport_url(self) -> str:
        return self.url("fileimport")

    @property
    def playlist_url(self) -> str:
        return self.url("playlist")

    @property
    def catalog_url(self) -> str:
        return self.url("catalog")

    @property
    def bpm_url(self) -> str:
        return self.url("bpm-match")

    @property
    def library_url(self) -> str:
        return self.url("library")

    @property
    def report_url(self) -> str:
        return self.url("report")


_ACTIVE_SERVICE = "web"


def get_config(name: Optional[str] = None) -> BaseConfig:
    return BaseConfig(name or _ACTIVE_SERVICE)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(service_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            f"%(asctime)s {service_name} %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


class _CurrentService:
    _name = "web"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_START_TIME = time.time()


def build_app(service_name: str, version: str = "0.1.0") -> FastAPI:
    """Cria o FastAPI padrão com /health, /metrics, logging de requests e erros."""
    global _ACTIVE_SERVICE
    _ACTIVE_SERVICE = service_name
    cfg = BaseConfig(service_name)
    logger = setup_logging(service_name, cfg.log_level)

    app = FastAPI(title=f"spotfy-manager-v2 · {service_name}", version=version)
    request_count = {"count": 0}
    request_duration = {"sum_ms": 0.0, "count": 0}
    response_statuses: dict[int, int] = {}

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        start = time.time()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request failed: %s %s", request.method, request.url.path)
            raise
        duration_ms = round((time.time() - start) * 1000, 1)
        request_count["count"] += 1
        request_duration["sum_ms"] += duration_ms
        request_duration["count"] += 1
        response_statuses[response.status_code] = response_statuses.get(response.status_code, 0) + 1
        logger.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, duration_ms)
        return response

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})

    @app.exception_handler(Exception)
    async def _unexpected_handler(_request: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": "INTERNAL", "detail": str(exc)})

    @app.get("/health")
    async def health():
        return {
            "service": service_name,
            "status": "ok",
            "version": version,
            "uptime_s": int(time.time() - _START_TIME),
            "time": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/metrics")
    async def metrics():
        body = (
            "# HELP http_requests_total Total de requisições HTTP.\n"
            "# TYPE http_requests_total counter\n"
            f'http_requests_total{{service="{service_name}"}} {request_count["count"]}\n'
            "# HELP http_request_duration_ms_sum Total acumulado da duração das requisições em milissegundos.\n"
            "# TYPE http_request_duration_ms_sum counter\n"
            f'http_request_duration_ms_sum{{service="{service_name}"}} {request_duration["sum_ms"]:.1f}\n'
            "# HELP http_request_duration_ms_count Número de requisições observadas para a duração.\n"
            "# TYPE http_request_duration_ms_count counter\n"
            f'http_request_duration_ms_count{{service="{service_name}"}} {request_duration["count"]}\n'
            "# HELP http_responses_total Total de respostas HTTP por status.\n"
            "# TYPE http_responses_total counter\n"
            + "".join(
                f'http_responses_total{{service="{service_name}",status="{status}"}} {count}\n'
                for status, count in sorted(response_statuses.items())
            )
            +
            f'process_uptime_seconds{{service="{service_name}"}} {int(time.time() - _START_TIME)}\n'
        )
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    app.state.config = cfg
    app.state.logger = logger
    return app