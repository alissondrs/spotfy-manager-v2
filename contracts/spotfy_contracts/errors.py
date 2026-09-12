"""Catálogo de erros e exceção de domínio compartilhada entre os serviços."""

from __future__ import annotations

from typing import Optional

# Código -> (status HTTP, mensagem padrão)
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    # Genéricos
    "INTERNAL": (500, "Erro interno inesperado."),
    "INVALID_PAYLOAD": (400, "Payload inválido."),
    "UNAUTHORIZED": (401, "Autenticação necessária."),
    "FORBIDDEN": (403, "Sem permissão para acessar o recurso."),
    "NOT_FOUND": (404, "Recurso não encontrado."),
    "CONFLICT": (409, "Conflito com o estado atual."),
    "NOT_IMPLEMENTED": (501, "Funcionalidade não implementada."),

    # Spotify / Playlist
    "SPOTIFY_AUTH_EXPIRED": (401, "Credenciais Spotify ausentes ou expiradas. Configure SPOTIFY_ACCESS_TOKEN ou complete o OAuth."),
    "SPOTIFY_AUTH_FAILED": (401, "Falha de autenticação com o Spotify."),
    "SPOTIFY_NOT_CONNECTED": (401, "Conecte o Spotify para listar as suas playlists."),
    "SPOTIFY_TOKEN_EXPIRED": (401, "Token do Spotify expirado. Conecte novamente."),
    "SPOTIFY_API_ERROR": (502, "Erro ao consultar a API do Spotify."),
    "PLAYLIST_NOT_FOUND": (404, "Playlist não encontrada no Spotify."),
    "PLAYLIST_INACCESSIBLE": (403, "Playlist inacessível com as credenciais atuais."),
    "INVALID_REFERENCE_TYPE": (400, "Referência do Spotify com tipo não suportado."),

    # Arquivos / importação
    "FILE_TYPE_NOT_SUPPORTED": (415, "Tipo de arquivo não suportado. Use .md, .csv ou .json."),
    "FILE_TOO_LARGE": (413, "Arquivo maior que o limite permitido."),
    "FILE_EMPTY": (400, "Arquivo vazio."),
    "FILE_PARSE_ERROR": (422, "Falha ao interpretar o conteúdo do arquivo."),
    "PARSED_TRACKS_EMPTY": (422, "Nenhuma faixa válida encontrada no arquivo."),

    # Matching
    "TRACK_DATA_INSUFFICIENT": (422, "Dados insuficientes para recomendar a faixa."),
    "TRACK_DATA_CONFLICT": (409, "Dados conflitantes entre as fontes para a faixa."),
    "ANALYSIS_NOT_FOUND": (404, "Análise BPM Match não encontrada."),
    "TARGET_BPM_INVALID": (422, "BPM alvo deve estar entre 40 e 240."),

    # Catálogo / Tidal
    "TIDAL_AUTH_FAILED": (401, "Falha de autenticação com o Tidal."),
    "TIDAL_UPSTREAM_ERROR": (502, "Erro ao consultar o catálogo Tidal."),
    "TIDAL_RATE_LIMITED": (429, "Catálogo Tidal limitou as requisições. Aguarde e tente novamente."),
    "CATALOG_UNREACHABLE": (503, "Serviço de catálogo indisponível."),

    # Download / biblioteca
    "NO_CANDIDATES": (404, "Nenhum candidato encontrado no catálogo."),
    "NO_ACCEPTABLE_QUALITY": (404, "Nenhum candidato com qualidade aceitável (HI_RES_LOSSLESS/LOSSLESS)."),
    "DOWNLOAD_FAILED": (502, "Falha ao baixar o arquivo de áudio."),
    "DOWNLOAD_DIR_NOT_CONFIGURED": (500, "Diretório de downloads não configurado."),
    "DOWNLOAD_JOB_CONFIRM_REQUIRED": (409, "Confirmação explícita é necessária para executar downloads."),
    "PARTIAL_DOWNLOAD_FAILED": (502, "Alguns itens do lote falharam ao baixar."),
    "DRYRUN_NOT_FOUND": (404, "Dry-run não encontrado. Execute novo dry-run."),
    "DRYRUN_EXPIRED": (409, "Dry-run expirado. Execute novamente o dry-run antes de baixar."),
    "JOB_NOT_FOUND": (404, "Job não encontrado."),

    # Relatórios
    "REPORT_NOT_FOUND": (404, "Relatório não encontrado."),
    "REPORT_FORMAT_UNSUPPORTED": (415, "Formato de relatório não suportado. Use md, csv ou html."),

    # Sessão anônima / CSRF
    "SESSION_INVALID": (401, "Sessão inválida ou expirada."),
    "CSRF_INVALID": (403, "Token CSRF inválido ou ausente."),
    "LOCAL_AUTH_DISABLED": (410, "Cadastro e login local foram desativados. A sessão agora é anônima por navegador."),

    # OAuth Spotify (PKCE)
    "OAUTH_STATE_INVALID": (400, "Parâmetro de autorização ausente, inválido, expirado ou reutilizado."),
    "OAUTH_CODE_EXCHANGE_FAILED": (502, "Falha ao trocar o código de autorização com o Spotify."),
    "SPOTIFY_REFRESH_FAILED": (502, "Falha ao renovar o token do Spotify."),
    "SPOTIFY_REVOKED": (401, "A autorização do Spotify foi revogada. Conecte novamente."),
}


class DomainError(Exception):
    """Erro de domínio com código estável e mensagem legível."""

    def __init__(self, code: str, message: Optional[str] = None, status: Optional[int] = None):
        default_status, default_message = ERROR_CATALOG.get(code, ERROR_CATALOG["INTERNAL"])
        self.code = code
        self.message = message or default_message
        self.status = status or default_status
        super().__init__(self.message)


def raise_error(code: str, message: Optional[str] = None, status: Optional[int] = None) -> None:
    raise DomainError(code, message=message, status=status)


def to_error_dict(code: str, message: Optional[str] = None) -> dict:
    status, default = ERROR_CATALOG.get(code, ERROR_CATALOG["INTERNAL"])
    return {"error": code, "status": status, "detail": message or default}