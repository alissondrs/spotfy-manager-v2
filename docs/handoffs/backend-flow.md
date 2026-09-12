# Handoff de Backend — Fluxo Spotify → BPM Match → Download

Leia junto com `context.md`, `AGENTS.md` e `docs/features/playlist-bpm-download-flow.md`
(PRD). Este documento é o contrato de implementação do backend para a iteração.

## 1. Endpoints e payloads

### Auth / sessão (BFF — anônima por navegador)
| Método | Rota (web BFF) | Notas |
| ------ | -------------- | ----- |
| GET | `/api/session` | cria/recupera a sessão anônima; rotaciona o CSRF; define o cookie HttpOnly |
| GET | `/api/me`, `/api/auth/me` | identidade da sessão (anonimizada) |
| POST | `/api/auth/logout` | revoga a sessão e limpa o cookie |
| POST | `/api/auth/login`, `/api/auth/register` | **410 `LOCAL_AUTH_DISABLED`** (desativado) |
| GET | `/api/auth/spotify` | gera `state` + `code_verifier` (PKCE S256) e devolve `{url, redirect_uri}` |
| GET | `/api/auth/spotify/callback` | valida state da sessão (owner/expiração/uso único) e troca o code |
| GET | `/api/auth/spotify/status` | `{"connected": bool, "expires_in": int|0}` (nunca expõe tokens) |
| POST | `/api/auth/spotify/disconnect` | revoga a conexão Spotify da sessão |

**Contrato:**
- `GET /api/session` → `{anonymous: true, created: bool, session: {owner, created_at, expires_in, idle_ttl_seconds, absolute_ttl_seconds, csrf_token}}` + cookie `bpm_session` (dev) ou `__Host-bpm_session` (produção/HTTPS).
- Operações mutáveis exigem header `X-CSRF-Token` (rotacionado a cada `/api/session`).
- `GET /api/auth/spotify` → `{"url": "<accounts.spotify.com/authorize?...PKCE...>", "redirect_uri": "..."}`.
- O navegador **nunca** envia `Authorization`; o BFF injeta JWT interno curto
  (`sub=owner_claim`, `WEB_INTERNAL_JWT_TTL_MINUTES`) nas chamadas downstream.

### Playlists / fontes
| Método | Rota | Notas |
| ------ | ---- | ----- |
| GET | web `/api/playlists` → playlist `/playlists` | sem mudança |
| POST | web `/api/import/spotify` → playlist `/import` | sem mudança |
| GET | web `/api/synced` → playlist `/synced` | sem mudança (retorna `id` como chave estável) |
| GET | web `/api/imports` → fileimport `/imports` | sem mudança (retorna `import_id`) |
| GET | web `/api/import/{import_id}` | sem mudança |

**Referência estável para o BPM Match:**
- `spotify:<playlist_id>` — resolve em playlist `/synced/{playlist_id}`.
- `file:<import_id>` — resolve em fileimport `/import/{import_id}`.
- Ambos retornam `{tracks: [...]}` no formato `TrackInput`.

### BPM Match
| Método | Rota | Mudança |
| ------ | ---- | ------- |
| POST | `/analyze` | body ganha `reference` (opcional) e `force` (bool); se `tracks` vazio e `reference` presente, resolve faixas nos serviços; reuso por chave `owner+reference+target_bpm` quando `force=false` |
| GET | `/analyses/{id}` | retorna também `reused` e `catalog_unavailable` (compatíveis com schema) |
| GET | `/analyses` | sem mudança |

**Contrato `POST /analyze`:**
```json
{
  "name": "Tech House 125",
  "target_bpm": 125,
  "tolerance_bpm": 3,
  "reference": "spotify:37i9dQZF1DX4PP3DA4V0yg",
  "force": false,
  "tracks": []
}
```
Resposta (análise): igual à atual + `"reused": false` e `"catalog_unavailable": bool`.
Quando reusada: `"reused": true` e a análise existente completa.

**Resolution de faixas (server-side):**
- `spotify:<id>` → `GET {playlist_url}/synced/{id}` com `Authorization` do usuário.
- `file:<import_id>` → `GET {fileimport_url}/import/{import_id}` com `Authorization`.
- Erro de resolução propaga o código original (`FORBIDDEN`, `NOT_FOUND`, etc.).
- Se `reference` presente e `tracks` vazio e resolução falhar → `INVALID_PAYLOAD` com detalhe.

**Decisões e dependências (política):**
- `MatchResult` ganha o campo opcional `dependency_error: str|null`.
- Quando `_fetch_candidates` falha por indisponibilidade (RequestError/timeout/não-200 da
  dependência), todos os itens daquela análise recebem `dependency_error`; a análise marca
  `catalog_unavailable=true` no summary. Decisão permanece `insufficient`, mas `reasons`
  passa a dizer explicitamente "Catálogo indisponível — não é ausência de dados ...".
- A UI/consumidores distinguem `insufficient` (sem dados) de `dependency_error` (sem
  dependência) pela presença do campo.

**Reuso:**
```sql
SELECT analysis_id FROM analyses
WHERE owner=? AND reference=? AND target_bpm=? AND status='completed'
ORDER BY created_at DESC LIMIT 1
```
- `reference` deve ser armazenado em coluna própria no `analyses` (hoje só no payload).
- `force=true` ignora reuso e cria análise nova.

### Library / download
| Método | Rota | Mudança |
| ------ | ---- | ------- |
| POST | `/dryrun` | retorna `dry_run_id` + `expires_at` + `selections` (persiste no SQLite) |
| POST | `/download` | body `{"dry_run_id": "..."}`; valida owner + não expirado; cria job e processa em segundo plano; retorna `{job_id, status:"running"}` |
| GET | `/jobs/{job_id}` | retorna job + `items[]` com status por item (polling da UI) |

**Schema DB library:**
```sql
CREATE TABLE IF NOT EXISTS dryruns (
    dry_run_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    payload TEXT NOT NULL,      -- selections JSON
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_items (
    job_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    track TEXT NOT NULL,        -- JSON TrackInput
    status TEXT NOT NULL,       -- pending | downloading | downloaded | failed
    filename TEXT,
    size_bytes INTEGER,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, ordinal)
);
```

**Fluxo `POST /download`:**
1. `dry_run_id` obrigatório → `DRYRUN_NOT_FOUND` (404) se não existe ou `DRYRUN_EXPIRED`
   (409) se `expires_at < now` — ambos com ownership (403 se de outro usuário).
2. Carrega seleções persistidas; filtra `status == "found"` com `manifest_url`.
3. Sem itens → `DOWNLOAD_JOB_CONFIRM_REQUIRED`.
4. Cria job (`running`, progress 0) e itens `pending`.
5. Dispara thread (ThreadPoolExecutor, max_workers=min(3,total)) que para cada item:
   atualiza `job_items.status='downloading'` → baixa → `downloaded`/`failed` com erro;
   atualiza `jobs.progress` e `jobs.result` parcial a cada conclusão.
6. Ao final, `jobs.status='completed'` com `message="{ok} downloaded / {fail} failed"`.
7. Retorna `{job_id, status: "running"}` imediatamente (processamento assíncrono).

**Contrato `GET /jobs/{job_id}`:**
```json
{
  "job_id": "download_x",
  "owner": "alice", "type": "download", "status": "running",
  "progress": 0.66, "message": "2/3 concluídas",
  "items": [
    {"ordinal":1,"track":{...},"status":"downloaded","filename":"...","error":null},
    {"ordinal":2,"track":{...},"status":"downloading"},
    {"ordinal":3,"track":{...},"status":"failed","error":"HTTP 403"}
  ]
}
```

## 2. Schemas (contracts/spotfy_contracts/schemas.py)

- `MatchResult`: adicionar `dependency_error: Optional[str] = None`.
- `Analysis`: adicionar `reused: bool = False` e `catalog_unavailable: bool = False`.
- Manter `Analysis.model_config = ConfigDict(extra="allow")` para compat.

## 3. Códigos de erro novos (contracts/spotfy_contracts/errors.py)

| Código | HTTP | Mensagem padrão |
| ------ | ---- | --------------- |
| `DRYRUN_NOT_FOUND` | 404 | Dry-run não encontrado. Execute novo dry-run. |
| `DRYRUN_EXPIRED` | 409 | Dry-run expirado. Execute novamente o dry-run antes de baixar. |
| `SPOTIFY_NOT_CONNECTED` | 401 | Conecte o Spotify para listar as suas playlists. |
| `SPOTIFY_REFRESH_FAILED` | 502 | Falha ao renovar a conexão com o Spotify. |
| `SPOTIFY_REVOKED` | 401 | Conexão com o Spotify revogada. Conecte novamente. |
| `SESSION_INVALID` | 401 | Sessão inválida ou expirada. |
| `CSRF_INVALID` | 403 | Token CSRF ausente ou inválido. Recarregue a página. |
| `LOCAL_AUTH_DISABLED` | 410 | Login/registro local desativados. Sessão anônima por navegador. |
| `OAUTH_STATE_INVALID` | 400 | Estado de autorização inválido. |
| `OAUTH_CODE_EXCHANGE_FAILED` | 502 | Falha ao trocar o código de autorização. |
| `ANALYSIS_REUSE_FAILED` | 400 | Não foi possível reutilizar a análise anterior. |

## 4. BFF / proxy

- Extrair cliente interno compartilhado (`_internal_client()` com timeout de leitura).
- Injeta `X-Request-Id` (uuid) em todas as chamadas internas; **não repassa
  `Authorization` vindo do navegador** — emite JWT interno com `sub=owner_claim`.
- Injeta `X-Spotify-Token` (token gerenciado/refreshed pelo BFF) nas chamadas ao
  playlist service; tokens nunca vêm do cliente e nunca vazam em respostas.
- Propagar `status`, `content-type` e `content-disposition` em respostas upstream
  (incluindo download de relatório).
- Futuro: correlation ID em logs (mínimo nesta iteração: header presente).

## 5. Ownership e autorização

- Já vigente: todos os endpoints de domínio com `_require_user` — agora recebem o
  JWT interno do BFF com `sub = owner_claim` (ex. `anon_<uuid>`).
- Sessões: só o **hash** do cookie é persistido; `owner` é a chave de isolamento;
  TTL idle/absoluto (`WEB_SESSION_*`) e revogação no logout.
- OAuth: `state` + `code_verifier` atrelados ao `owner` da sessão e cifrados (Fernet);
  state é uso-único e expira (`WEB_OAUTH_STATE_TTL_SECONDS`).
- `spotify_connections`: por `owner`, cifrado em repouso (`WEB_TOKEN_ENCRYPTION_KEY`);
  refresh com trava por owner e revogação local em `invalid_grant`.
- `dryruns`, `job_items`: filtrados por `owner`.
- `analyses`: coluna `reference` nova — filtragem por `owner` já existente.

## 6. Migração

- `bpm-match`: adicionar coluna `reference` (nullable) em `analyses` via `ALTER TABLE`
  guardado por `pragma_table_info` (padrão já usado no repo).
- `library`: `init_schema` com as duas tabelas novas (criativas, sem drop).

## 7. Testes de integração / regressão (a ampliar)

`tests/e2e/test_flow.py` (migrados e mantidos):
- `test_web_download_confirmation_and_import_lookup` — via sessão anônima: import de
  arquivo, dryrun → confirma com `dry_run_id`, polla `/api/library/jobs/{id}`.
- `test_web_bff_and_ui` — via sessão anônima: UI estática, `/api/me` anônimo,
  análise por `reference="file:<import_id>"`.
- `test_analysis_reuse_by_reference`, `test_catalog_unavailable_is_not_insufficient`,
  `test_download_requires_bound_dryrun` — mantidos (downstream via JWT de identidade).

`tests/e2e/test_web_session.py` (novo — sessão anônima + OAuth/Spotify + fake Spotify):
- `test_anonymous_session_and_cookie` — cookie HttpOnly, owner estável, CSRF rotacionado,
  `Authorization` vindo do navegador é ignorado.
- `test_csrf_required_and_rotated` — mutações sem CSRF/CSRF antigo → 403; logout revoga.
- `test_login_and_register_are_disabled` — 410 `LOCAL_AUTH_DISABLED`.
- `test_spotify_pkce_connect_import_and_analyze` — popup (authorize→callback), status
  conectado, `/api/playlists`, import `spotify:pl_main`, análise por referência.
- `test_oauth_state_is_bound_to_the_session` / `test_oauth_state_is_single_use`.
- `test_spotify_token_rotation_and_refresh` / `test_spotify_refresh_failure_revokes_connection`.
- `test_spotify_rate_limit_surfaces_as_error` — 429 → `SPOTIFY_API_ERROR`.
- `test_no_tokens_leak_in_api_responses` — nenhum token em respostas do BFF.

`tests/contract/test_contracts.py`:
- `parse_reference` / validação de `spotify:*` e `file:*`.

## 8. Config

- `.env.example`: adicionar comentário para `DRYRUN_TTL_MINUTES` (default 15).
- `BaseConfig`: `dryrun_ttl_minutes = int(os.getenv("DRYRUN_TTL_MINUTES", "15"))`.