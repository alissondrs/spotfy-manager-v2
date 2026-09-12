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
7. **Token Spotify por usuário** vive no BFF (`web_spotify.sqlite`): o callback OAuth
   guarda o token como pendente e `POST /api/auth/spotify/claim` o vincula ao usuário
   autenticado (nada em memória global).

## Estado implementado

- Fases 1-4 (PRD → protótipo → handoffs) concluídas; Fase 5 (implementação)
  concluída e validada por testes.
- Serviços: os 8 acima, todos com `/health` e `/metrics`.
- Testes: `tests/contract` (16) e `tests/e2e` (12, fluxo completo).
- Infra: `Dockerfile` único (ARG SERVICE), `docker-compose.yml` (8 serviços +
  Prometheus + Grafana opcional), `Makefile`, `observability/prometheus.yml`.

## Aberturas / pseudo-pendências conhecidas

- Manifesto real `application/vnd.tidal.bts` exige hifi-api externo com token Tidal.
- Análise assíncrona para lots grandes (MVP é síncrono).
- Auth intra-rede opcional para endpoints internos.

## Onde está o quê

- `contracts/spotfy_contracts/` — ids, files, auth, store, schemas, errors, service
- `services/<nome>/main.py` — código de cada serviço (FastAPI)
- `services/web/static/` — UI (index.html, app.js, styles.css)
- `tests/` — contract + e2e
- `docs/` — architecture.md, operations.md, features/ (PRD + protótipo), handoffs/
- `observability/` — prometheus.yml