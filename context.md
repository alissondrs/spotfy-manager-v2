# Contexto — spotfy-manager-v2 (BPM Match)

Este arquivo contém o contexto essencial para agentes que forem trabalhar neste
repositório. Leia junto com `README.md`, `docs/architecture.md` e `AGENTS.md`.

## Por que este projeto existe

O `spotfy-manager` legado concentrava acesso a contas upstream (Spotify e
Tidal/hifi-api), downloads e recomendações num único lugar, com risco alto de
**recomendação incorreta** (ex.: sugerir versão errada, apresentar conflito como certo
ou confiar em dados insuficientes). O v2 mantém os casos de uso do legado, mas:

- separa as responsabilidades em microsserviços independentes;
- exige **decisão explicável e segura** no BPM Match;
- torna cada passo rastreável (revisão, relatórios, jobs de download);
- é local-first e pronto para container.

## Termos

- **BPM Match**: compatibilidade de uma faixa candidata com o BPM alvo do set
  (tolerância configurável), com score de confiança.
- **hifi-api**: serviço do legado que encapsula o Tidal (busca, info, playback bts).
  O `catalog` o preserva como upstream (`HIFI_API_URL`).
- **Qualidades aceitáveis**: `HI_RES_LOSSLESS` e `LOSSLESS` (prioridade nesta ordem).

## Decisões críticas (não reverta sem atualizar os docs)

1. **Segurança primeiro no mote**: decisões `recommended` exigem candidatos com BPM
   disponível e coerentes; caso contrário `insufficient`/`conflict`.
2. **Contratos em `contracts/`**: schemas, erros (`DomainError`), auth (JWT/PBKDF2),
   IDs (matching) e bootstrap de serviço (`build_app`) são o contrato entre serviços.
3. **Download real exige confirmação dupla e vínculo persistido**: dry-run retorna
   `dry_run_id` persistido (com `expires_at`, `DRYRUN_TTL_MINUTES`) e `/download` só
   executa com `dry_run_id` válido, do mesmo `owner`, não expirado e
   `ALLOW_DOWNLOADS=1`; vira job assíncrono consultado via `GET /jobs/{id}`.
4. **URLs entre serviços**: padrão docker compose (`http://<serviço>:<porta>`),
   sobrescritível por env `*_URL` (usado em execução local e testes).
5. **Catálogo em modo mock**: `TIDAL_MOCK=1` gera candidatos determinísticos para
   demo/testes sem Tidal; em produção, remover e apontar `HIFI_API_URL` real.
6. **Portas default**: web 8000, identity 8101, fileimport 8102, playlist 8103,
   catalog 8104, bpm-match 8105, library 8106, report 8107.
7. **Sessão anônima por navegador (sem login/registro)**: o BFF cria um cookie HttpOnly
   (`bpm_session` em dev/HTTP; `__Host-bpm_session` com Secure em produção) e só
   persiste o **hash** do token. Cada `GET /api/session` rotaciona o **CSRF token**
   (guardado hashado, enviado como `X-CSRF-Token` em operações mutáveis). `logout`
   revoga a sessão. `/api/auth/login` e `/api/auth/register` retornam 410.
8. **OAuth Spotify via Authorization Code + PKCE (S256)**: o BFF gera `state` +
   `code_verifier` (guardados cifrados em `web_spotify.sqlite`, tabelas
   `sessions`/`oauth_transactions`/`spotify_connections`, com `schema_meta` v1);
   tokens e `code_verifier` são cifrados em repouso com Fernet
   (`WEB_TOKEN_ENCRYPTION_KEY`, 32 bytes base64url). O callback valida state da
   própria sessão (dono/expiração/uso único) e troca o código **sem client_secret**.
9. **JWT interno curto do BFF**: o navegador nunca envia `Authorization`; o BFF emite
   um token interno (`web_internal_jwt_ttl_minutes`) com `sub=owner_claim` (ex.
   `anon_<uuid>`) para cada chamada downstream, mantendo compatibilidade com
   `current_username_from_header()`. Tokens Spotify nunca saem do BFF.
   Refreshes têm trava por owner (`threading.Lock`); em `invalid_grant` a conexão é
   revogada localmente.

## Estado implementado

- Fases 1-4 (PRD → protótipo → handoffs) concluídas; Fase 5 (implementação)
  concluída e validada por testes.
- Sessão anônima + OAuth Spotify PKCE implementados no BFF e na UI (popup com
  fallback de link, badge "Sessão anônima", botão "Nova sessão").
- Serviços: os 8 acima, todos com `/health` e `/metrics`. Referências de fonte
  estáveis: `spotify:<playlist_id>` e `file:<import_id>`; BPM Match resolve com
  `reference` + `tracks: []`.
- Testes: `tests/contract` (26) e `tests/e2e` (29), incluindo `fake_spotify.py`
  (faz validação real de PKCE S256, rotação/revogação de refresh e cenários 429).
- Infra: `Dockerfile` único (ARG SERVICE), `docker-compose.yml` (8 serviços +
  Prometheus + Grafana opcional), `Makefile`, `observability/prometheus.yml`.

## Aberturas / pseudo-pendências conhecidas

- Manifesto real `application/vnd.tidal.bts` exige hifi-api externo com token Tidal.
- Análise assíncrona para lots grandes (MVP é síncrono).
- Serviço `identity` mantido apenas para compatibilidade de dados legados
  (login/registro desativados no BFF).

## Onde está o quê

- `contracts/spotfy_contracts/` — ids, files, auth, store, schemas, errors, service
- `services/<nome>/main.py` — código de cada serviço (FastAPI)
- `services/web/static/` — UI (index.html, app.js, styles.css)
- `tests/` — contract + e2e
- `docs/` — architecture.md, operations.md, features/ (PRD + protótipo), handoffs/
- `observability/` — prometheus.yml