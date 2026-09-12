# Spotfy Manager v2 · BPM Match

Gerente de biblioteca musical local-first com **BPM Match** (compatibilidade de BPM entre
faixas), importação de playlists (Spotify/arquivo), recomendação segura, revisão na UI,
download com tagging e relatórios. Preserva as integrações do projeto legado
(`spotfy-manager`: hifi-api/Tidal e Spotify) separando responsabilidades em
microsserviços.

## Objetivo do MVP

Fluxo completo do **BPM Match** reduzindo o risco de recomendações incorretas:

1. **Importar** playlists (markdown/csv/json) ou sincronizar do Spotify.
2. **Analisar** compatibilidade de cada faixa com o BPM alvo (tolerância configurável).
3. **Revisar** decisões na UI (aprovar/rejeitar), com explicação de cada decisão.
4. **Exportar** relatório (markdown/csv/html).
5. **Validar na biblioteca**: comparar faltantes, dry-run e baixar com tagging.
6. **Download seguro**: `ALLOW_DOWNLOADS=1` obrigatório para baixar de verdade.
   O dry-run devolve o `dry_run_id` (persistido, com expiração); a confirmação envia
   apenas `{dry_run_id}` e vira um job assíncrono consultado por
   `GET /api/library/jobs/{id}` (progresso por lote + por item na UI).

Decisões só são *recomendadas* com dados suficientes e coerentes; dados ausentes ou
conflitantes geram `insufficient`/`conflict` (nunca recomendação insegura).

## Arquitetura em resumo

| Serviço     | Porta | Responsabilidade |
| ----------- | ----: | ---------------- |
| `web`       | 8000  | BFF + UI estática (sessão anônima por cookie, OAuth Spotify PKCE, JWT interno) |
| `identity`  | 8101  | Usuários locais, hash PBKDF2, JWT HS256 (serviço de identidade legado) |
| `fileimport`| 8102  | Upload e parsing de markdown/csv/json |
| `playlist`  | 8103  | Spotify: listar, importar e sincronizar playlists |
| `catalog`   | 8104  | Catálogo Tidal via hifi-api (busca, candidatos, playback) |
| `bpm-match` | 8105  | Motor de compatibilidade, confiança e decisões |
| `library`   | 8106  | Comparação com arquivos locais, dry-run, download + tagging |
| `report`    | 8107  | Relatórios markdown/csv/html e histórico |

O BFF (`web`) usa **sessão anônima por navegador** (cookie HttpOnly `bpm_session` /
`__Host-bpm_session` em produção): não há login/registro local. O Spotify é autorizado
via **Authorization Code + PKCE (S256)** em popup; tokens e `code_verifier` ficam
cifrados em repouso (Fernet) no BFF, que emite um **JWT interno curto** (`sub=owner_claim`)
para as chamadas downstream — o navegador nunca envia `Authorization` nem tem acesso
aos tokens.

Contratos e biblioteca compartilhada vivem em `contracts/` (`spotfy_contracts`), instalado
como pacote local. Todos os serviços usam `build_app()` (FastAPI) com `/health`, `/metrics`,
logging e tratamento de `DomainError`.

## Começar rápido

### Local (dev)

```bash
cp .env.example .env          # ajuste conforme necessário (TIDAL_MOCK=1 já vem em modo demo)
make setup                    # venv + contracts + deps dos serviços
```

Suba cada serviço em um terminal (portas 8101-8107):

```bash
make run-identity
make run-catalog              # TIDAL_MOCK=1 por padrão (demo sem Tidal)
make run-playlist             # requer SPOTIFY_CLIENT_ID + redirect no .env
make run-bpm-match
make run-library              # DOWNLOAD_DIR=./downloads
make run-report
make run-web                  # http://127.0.0.1:8000 (sessão anônima + Spotify via popup)
```

Acesse `http://127.0.0.1:8000` — a UI abre no dashboard sem login e mostra o
badge **Sessão anônima**. Para o Spotify funcionar: crie o app em
https://developer.spotify.com/dashboard, copie o `SPOTIFY_CLIENT_ID` para o `.env`
e adicione `SPOTIFY_REDIRECT_URI` (default `http://127.0.0.1:8000/api/auth/spotify/callback`)
nas Redirect URIs do app. Em dev, use `WEB_SESSION_COOKIE_SECURE=0`
(cookie `bpm_session`); em produção, HTTPS + `WEB_SESSION_COOKIE_SECURE=1`
(cookie `__Host-bpm_session`) e `WEB_TOKEN_ENCRYPTION_KEY` definida.

Bootstrap de dev opcional: com `APP_ENV=development` e `IDENTITY_ENABLE_BOOTSTRAP=1`
no ambiente do serviço `identity`, `curl localhost:8101/_debug/bootstrap` cria
`admin`/`admin12345` (fluxo legado, apenas para compatibilidade de dados).

### Container

```bash
docker compose up --build -d          # sobe os 8 serviços + Prometheus
docker compose --profile grafana up   # + Grafana (http://localhost:3000)
docker compose logs -f web
```

A UI fica em http://localhost:8000. Métricas: http://localhost:9090 (Prometheus).

## Dados e segredos

- `JWT_SECRET`: obrigatório e forte (mín. 32 chars, sem valor padrão fraco).
- `DATA_DIR`: SQLite por serviço (`./data/<serviço>*.sqlite`). O BFF persiste
  `web_spotify.sqlite` com sessões (só o hash do cookie), transações OAuth e
  conexões Spotify **cifradas em repouso** (Fernet).
- `WEB_TOKEN_ENCRYPTION_KEY`: chave Fernet base64url (32 bytes) para os tokens do
  Spotify e `code_verifier`; obrigatória em produção. Em dev, se vazia, é gerada
  em `<DATA_DIR>/web_encryption.key`.
- `DOWNLOAD_DIR`: onde os arquivos baixados são gravados (library).
- Credenciais nunca entram em arquivos versionados (ver `.env.example`).

## Testes

```bash
make test               # contract + e2e
make test-contract      # unitários dos contratos
make test-e2e           # fluxo completo com serviços reais em portas efêmeras
```

O e2e sobe todos os serviços localmente (uvicorn em threads, dados temporários,
`TIDAL_MOCK=1`), junto com um **fake Spotify** (`tests/e2e/fake_spotify.py`) que
valida PKCE S256 de verdade, rotação de token e cenários de falha. Os testes cobrem
sessão anônima (cookie + CSRF rotativo + logout), OAuth por popup, importação por
`spotify:<id>`, análise por referência, revisão, relatório, comparação com a
biblioteca, dry-run e download via job verificando o tagging `mp3`.

## Documentação

- `docs/architecture.md` — decisões de arquitetura e contratos entre serviços
- `docs/operations.md` — execução local, container, observabilidade e solução de problemas
- `docs/features/playlist-bpm-download-flow.md` — PRD do fluxo web (Spotify → BPM Match → download)
- `docs/handoffs/backend-flow.md` e `docs/handoffs/frontend-flow.md` — handoffs da implementação
- `context.md` — contexto para agentes que forem trabalhar neste repositório
- `AGENTS.md` — regras de trabalho específicas deste projeto

## Roadmap / pendências conhecidas

- Streaming manifesto `application/vnd.tidal.bts` em modo real (hifi-api externo requerido)
  ou cliente Tidal direto.
- Fila/estado assíncrono para análises muito grandes (hoje síncrono no MVP).
- Correlation ID e métricas de domínio por endpoint/job.