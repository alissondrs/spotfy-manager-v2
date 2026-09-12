# Arquitetura — spotfy-manager-v2 (BPM Match)

## Visão geral

Sistema local-first de microsserviços para gerenciar uma biblioteca de música e
recomendar faixas compatíveis por BPM. O usuário importa playlists (arquivo ou Spotify),
roda o BPM Match com um BPM alvo, revisa as decisões na UI, exporta relatório e, se
quiser, baixa as faixas faltantes com tagging. Tudo roda localmente — ou em docker
compose — sem dependência de nuvem para o fluxo principal.

```
                          ┌──────────┐
   UI (estática)          │   web    │  BFF · proxies /api/* + serve static
        └────────────────>│  :8000   │
                          └────┬─────┘
        ┌────────────┬─────────┼─────────┬────────────┐
        ▼            ▼         ▼         ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────┐
  │ identity │ │ fileimport │ │ playlist │ │ bpm-match │ │ library │
  │  :8101   │ │  :8102  │ │  :8103 │ │  :8105   │ │  :8106   │
  └──────────┘ └────┬─────┘ └────────┘ └────┬───┘ └────┬───┘
                    │                       │          │
                    │                 ┌─────▼───┐ ┌────▼───┐
                    └────────────────>│ catalog │ │ report │
                           candidatos │  :8104  │ │  :8107 │
                                      └────┬────┘ └────┬───┘
                                           │            │
                                     hifi-api(Tidal)  bpm-match (analyses)
```

## Serviços e responsabilidades

| Serviço | Porta | Função | Estado persistente |
| ------- | ----: | ------ | ------------------ |
| web     | 8000  | BFF: valida sessão, proxy `/api/*` manter JWT, token Spotify por usuário (OAuth + claim), UI estática | `web_spotify.sqlite` |
| identity| 8101  | Usuários locais, PBKDF2-SHA256, JWT HS256 | `identity_db.sqlite` |
| fileimport | 8102 | Upload md/csv/json → `TrackInput[]` (validado) | `fileimport_db.sqlite` |
| playlist| 8103  | Spotify client-credentials/token; importar e sincronizar | `synced_playlists.json` |
| catalog | 8104  | Busca Tidal/hifi-api, candidatos (5 estratégias), info, playback | `catalog_cache.sqlite` |
| bpm-match | 8105 | Motor de decisão (score/tolerância/confiança/explicação) + reuso por referência | `bpm-match_db.sqlite` |
| library | 8106  | Scan local, comparação fuzzy, dry-run persistido, download via job + tagging | `library_db.sqlite` |
| report  | 8107  | Relatórios md/csv/html + histórico/download | `report_db.sqlite`, `reports/` |

Todos usam `spotfy_contracts.service.build_app`:
- `GET /health` (metadados + uptime), `GET /metrics` (Prometheus text format);
- logging de requisições; `DomainError` → JSON `{error, detail}`.

## Contratos compartilhados (`contracts/spotfy_contracts`)

- `schemas.py` — `TrackInput`, `TrackCandidate`, `MatchResult`, `Analysis`,
  `DownloadSelection`, `DownloadReportItem`, `LibraryFile`, enums `Decision`/`ReviewState`.
- `auth.py` — `hash_password`/`verify_password` (PBKDF2 e nunca em claro), `create_token`/
  `decode_token` (JWT HS256), `current_username_from_header`, validação de `JWT_SECRET`.
- `ids.py` — normalização (acentos, minúsculas), `title_matches`/`artist_matches`
  (confiança 0..1), `detect_version`, `sanitize_filename`, `strip_parenthesized`.
- `files.py` — validação de upload e parsing md/csv/json (tabela legada + listas simples).
- `store.py` — `SqliteStore` (métodos `query`/`query_one`/`execute`, schema idempotente).
- `errors.py` — `DomainError` + `raise_error(código, msg)`.
- `service.py` — `BaseConfig` (env-driven) e `build_app`.

## Fluxo BPM Match

1. `bpm-match /analyze` recebe `{target_bpm, tolerance_bpm, tracks[]}` e opcionalmente
   `reference` (ex.: `spotify:<id>` ou `file:<import_id>`) e `force`. Com `reference` e
   `tracks` vazio, resolve as faixas no serviço de origem; com `reference` sem `force`,
   **reusa** a última análise concluída do usuário para aquela origem+alvo (`reused=true`).
2. Para cada faixa: `catalog /candidates` → candidatos Tidal (estratégias em cascata:
   `{nome principal album}`, `{nome principal}`, `{nome artistas}`, `{nome album}`, `{nome}`).
3. Ranking: explicit original → clean original → versioned.
4. Decisão por faixa:
   - sem candidatos → `insufficient`;
   - candidatos sem BPM → `insufficient` (ou `conflict` se houver divergência de versão);
   - melhor candidato fora da tolerância → `rejected`;
   - candidatos com BPM conflitantes → `conflict`;
   - caso contrário → `recommended`, com `reasons` e `confidence`.
5. `POST /analyses/{id}/review?item_index&decision` grava aprovação/rejeição.
6. `report /reports` gera md/csv/html a partir da análise; `/reports/{id}/download`.

## Fluxo de download seguro

1. `library /library` lista arquivos locais (`DOWNLOAD_DIR`).
2. `library /compare` marca faixas já presentes (match exato + fuzzy ≥ 0.85).
3. `library /dryrun` consulta `catalog`, monta `selections` (found/not_found, qualidade,
   `manifest_url`, extensão) e **persiste o lote** com `dry_run_id` + `expires_at`
   (`DRYRUN_TTL_MINUTES`, padrão 15 min), vinculado ao `owner`.
4. `library /download` só executa com `{dry_run_id}` válido, não expirado e do próprio
   usuário (e `ALLOW_DOWNLOADS=1`); cria um job + `job_items` e processa em segundo plano
   (ThreadPool ≤ 3): cada item vai `pending → downloading → downloaded|failed` com erro;
   tagging por extensão (FLAC/MP4-`m4a`/MP3 via mutagen).
5. `GET /jobs/{job_id}` devolve o job com `progress` (0..1), `message` e `items[]` —
   a UI faz polling a cada ~1.5s para progresso por lote + por item.

## Integrações externas

- **Spotify** (`web` BFF): OAuth via `SPOTIFY_CLIENT_ID/SECRET`; o token fica **por
  usuário** em `web_spotify.sqlite`. O callback guarda o token como pendente e
  `POST /api/auth/spotify/claim` o vincula ao usuário autenticado. `playlist` continua
  suportando `SPOTIFY_ACCESS_TOKEN`/client-credentials como fallback.
- **Tidal via hifi-api** (`catalog`): `HIFI_API_URL`. Modo **mock** (`TIDAL_MOCK=1`)
  disponível para demo/testes determinísticos — sem rede, sem credenciais.

## Persistência

- SQLite por serviço sob `DATA_DIR` (volume dedicado por serviço no docker compose).
- Sem volume compartilhado entre serviços para regra de negócio (evita acoplamento de
  estado); conteúdo gerado (relatórios/downloads) é por serviço e exposto por API.

## Segurança

- Senhas nunca em claro (PBKDF2 com salt).
- JWT HS256 com `iat/exp/jti`; `JWT_SECRET` validado (mín. 32 chars e sem default fraco).
- Endpoints de domínio exigem JWT e recursos persistidos são filtrados por owner (`user_id` lógico).
- Uploads validados por extensão/tamanho (2 MiB) e parse seguro.
- Download real tem dupla confirmação + gate de ambiente.
- Sem segredos em arquivos versionados.