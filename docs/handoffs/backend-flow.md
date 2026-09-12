# Handoff de Backend — Fluxo Spotify → BPM Match → Download

Leia junto com `context.md`, `AGENTS.md` e `docs/features/playlist-bpm-download-flow.md`
(PRD). Este documento é o contrato de implementação do backend para a iteração.

## 1. Endpoints e payloads

### Auth / sessão
| Método | Rota (web BFF) | Rota interna | Notas |
| ------ | -------------- | ------------ | ----- |
| POST | `/api/auth/login` | identity `/auth/login` | sem mudança |
| GET | `/api/auth/me` | identity `/auth/me` | sem mudança |
| GET | `/api/auth/spotify/status` | — | **agora autenticado**: lê token por usuário |
| POST | `/api/auth/spotify/token` | — | **agora autenticado**: salva token por usuário (SQLite no BFF) |
| POST | `/api/auth/spotify/claim` | — | novo: adota token pendente do OAuth para o usuário atual |
| GET | `/api/auth/spotify/callback` | — | guarda token como `pending` em SQLite (não mais memória) |

**Contrato:**
- `POST /api/auth/spotify/token` request: `{"token": "..."}` → `{"ok": true, "expires_in": 3600}`.
- `POST /api/auth/spotify/claim` request: `{}` → `{"ok": true}` ou `NOT_FOUND` se não há pendência.
- `GET /api/auth/spotify/status` → `{"connected": bool, "expires_in": int|null}`.

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
| `SPOTIFY_TOKEN_EXPIRED` | 401 | Token do Spotify expirado. Conecte novamente. |
| `ANALYSIS_REUSE_FAILED` | 400 | Não foi possível reutilizar a análise anterior. |

## 4. BFF / proxy

- Extrair cliente interno compartilhado (`_internal_client()` com timeout de leitura).
- Injeta `X-Request-Id` (uuid) em todas as chamadas internas; retransmite `Authorization`.
- Propagar `status`, `content-type` e `content-disposition` em respostas upstream
  (incluindo download de relatório).
- Futuro: correlation ID em logs (mínimo nesta iteração: header presente).

## 5. Ownership e autorização

- Já vigente: todos os endpoints de domínio com `_require_user`.
- Novo: token Spotify do BFF é por usuário (chave `sub` do JWT), nunca global.
- `dryruns`, `job_items`: filtrados por `owner`.
- `analyses`: coluna `references` nova — filtragem por `owner` já existente.

## 6. Migração

- `bpm-match`: adicionar coluna `reference` (nullable) em `analyses` via `ALTER TABLE`
  guardado por `pragma_table_info` (padrão já usado no repo).
- `library`: `init_schema` com as duas tabelas novas (criativas, sem drop).

## 7. Testes de integração / regressão (a ampliar)

`tests/e2e/test_flow.py`:
- `test_web_download_confirmation_uses_dryrun_id` — dryrun → confirma com `dry_run_id`,
  polla `GET /jobs/{id}` até `completed`, item `downloaded`.
- `test_download_expired_dryrun_rejected` — dryrun com expiração zerada rejeitada (409).
- `test_analyze_reuse_same_reference` — duas análises com mesma `reference` → mesmo
  `analysis_id`; `force=true` cria nova.
- `test_analyze_resolves_reference_from_file` — `reference="file:{import_id}"` com
  `tracks=[]` resolve e analisa.
- `test_catalog_unavailable_is_not_insufficient` — com `CATALOG_URL` apontando para porta
  morta, análise marca `catalog_unavailable=true` e itens têm `dependency_error`.
- `test_spotify_token_per_user` — token salvo para A não aparece para B (via BFF).

`tests/contract/test_contracts.py`:
- `parse_reference` / validação de `spotify:*` e `file:*`.

## 8. Config

- `.env.example`: adicionar comentário para `DRYRUN_TTL_MINUTES` (default 15).
- `BaseConfig`: `dryrun_ttl_minutes = int(os.getenv("DRYRUN_TTL_MINUTES", "15"))`.