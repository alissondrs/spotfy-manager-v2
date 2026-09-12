# Operações — spotfy-manager-v2 (BPM Match)

Guia de execução local, container, configuração e observabilidade.

## 1. Execução local (dev)

### Pré-requisitos

- Python 3.11+ (testado com 3.14 também)
- docker + docker compose (para o modo container)
- (opcional) credenciais Spotify/Tidal para integrações reais

### Setup

```bash
cp .env.example .env     # ajuste conforme necessário
make setup               # cria .venv, instala contracts (-e) e deps dos serviços
```

### Rodando os serviços

```bash
make run-identity        # http://127.0.0.1:8101
make run-catalog         # http://127.0.0.1:8104 (TIDAL_MOCK=1 no .env ⇒ demo local)
make run-bpm-match
make run-library
make run-report
make run-web             # http://127.0.0.1:8000  ← UI
```

Use um terminal por serviço ou rode dentro de um terminal multiplexado. Cada serviço
lê as URLs dos demais de `*_URL` do ambiente (presentes no `.env` para `127.0.0.1`).

### Primeiro acesso

1. Abra http://127.0.0.1:8000 — a UI abre no dashboard, **sem login**,
   com o badge "Sessão anônima".
2. Para o Spotify: crie o app em https://developer.spotify.com/dashboard,
   copie o `SPOTIFY_CLIENT_ID` para o `.env` e adicione `SPOTIFY_REDIRECT_URI`
   nas Redirect URIs do app. Clique em **Conectar Spotify** (popup; se bloqueado,
   a UI oferece um link alternativo).
3. Importe uma playlist do Spotify (`spotify:<id>`) ou envie um arquivo md/csv/json
   e rode o **BPM Match**.

## 2. Modo container

```bash
docker compose up --build -d     # web + 7 serviços + Prometheus
docker compose ps                # status e healthchecks
```

A inicialização aguarda os healthchecks dos serviços antes de iniciar o web,
Prometheus e os consumidores dependentes. Se um serviço ficar `unhealthy`,
consulte seus logs antes de repetir o `up`.

Acesso:
- UI: http://localhost:8000
- Prometheus: http://localhost:9090 (scrape das 8 aplicações)
- Report (download direto de arquivos): http://localhost:8107
- Catalog/OpenAPI: http://localhost:8104/docs

Comandos:
```bash
docker compose logs -f web
docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
docker compose down              # derruba (mantém volumes)
docker compose volume ls         # volumes "spotfy-manager-v2_<serviço>_data"
```

### Smoke check pós-deploy

Após o stack ficar saudável, valide as rotas operacionais e o scrape:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8104/health
curl -fsS http://localhost:9090/-/ready
curl -fsS http://localhost:9090/api/v1/targets | grep -q '"health":"up"'
```

Para um diagnóstico rápido, `docker compose ps` deve mostrar `healthy` para
os serviços de aplicação. O endpoint `/metrics` expõe contadores de requests,
duração acumulada e respostas por status; use-o para confirmar que o tráfego
está sendo observado sem alterar contratos de domínio.

Opcional grafana:
```bash
docker compose --profile grafana up -d   # http://localhost:3000 (admin/admin padrão)
```

## 3. Configuração (variáveis de ambiente)

| Variável | Default | Onde afeta |
| -------- | ------- | ---------- |
| `JWT_SECRET` | `dev-secret-please-change-1234567890-abcdefghijklmnopqrstuvwxyz` | assinatura de tokens (todos) |
| `JWT_TTL_MINUTES` | `480` | expiração do token |
| `JWT_ALLOW_INSECURE_FOR_DEV` | `0` | só permite segredo fraco em `development/test` quando `1` |
| `IDENTITY_ENABLE_BOOTSTRAP` | `0` | habilita `/_debug/bootstrap` apenas em `APP_ENV=development` |
| `DATA_DIR` | `./data` | persistência SQLite (por serviço) |
| `LOG_LEVEL` | `INFO` | logging |
| `TIDAL_MOCK` | `0` (`1` no compose) | catálogo determinístico sem Tidal |
| `HIFI_API_URL` | — | hifi-api externo (modo real) |
| `SPOTIFY_CLIENT_ID` | — | app Spotify (Authorization Code + PKCE) |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8000/api/auth/spotify/callback` | callback OAuth (precisa estar cadastrado no app) |
| `SPOTIFY_AUTH_URL/TOKEN_URL/API_URL` | URLs públicas do Spotify | sobrescrever em testes/proxy (ex.: fake Spotify) |
| `SPOTIFY_SCOPES` | `user-library-read playlist-read-private playlist-read-collaborative` | escopos pedidos no OAuth |
| `SPOTIFY_ACCESS_TOKEN` | — | fallback legado de token fixo (sem prioridade) |
| `WEB_TOKEN_ENCRYPTION_KEY` | — | chave Fernet base64url (32 bytes); obrigatória em produção |
| `WEB_SESSION_COOKIE_SECURE` | `0` | `1` em produção ⇒ cookie `__Host-bpm_session` (HTTPS) |
| `WEB_SESSION_COOKIE_SAMESITE` | `lax` | SameSite do cookie de sessão |
| `WEB_SESSION_IDLE_TTL_SECONDS` | `2592000` | TTL de inatividade da sessão anônima |
| `WEB_SESSION_ABSOLUTE_TTL_SECONDS` | `15552000` | TTL absoluto da sessão |
| `WEB_OAUTH_STATE_TTL_SECONDS` | `600` | validade do `state` do OAuth |
| `WEB_INTERNAL_JWT_TTL_MINUTES` | `5` | TTL do JWT interno do BFF p/ downstream |
| `ALLOW_DOWNLOADS` | `0` | habilita execução real de `/download` |
| `DOWNLOAD_DIR` | `./data/downloads` | destino dos arquivos baixados |
| `DRYRUN_TTL_MINUTES` | `15` | tempo de validade do `dry_run_id` antes da confirmação |
| `*_URL` | docker compose names | URLs entre serviços (identidade, catálogo, etc.) |

**Importante**: `JWT_SECRET` deve ser o mesmo em todos os serviços (compartilhe via
`.env` no compose), ter ao menos 32 caracteres e não ser valor padrão fraco.

## 4. Observabilidade

- `/health` de cada serviço: `{service, status, version, uptime_s, time}`.
- `/metrics` de cada serviço (texto Prometheus): `http_requests_total`,
  `http_request_duration_ms_sum`, `http_request_duration_ms_count`,
  `http_responses_total` e `process_uptime_seconds`.
- Prometheus já coleta tudo (15s). Para Grafana, use a opção de profile ou aponte um
  datasource para `http://prometheus:9090`.

## 5. Testes

```bash
make test-contract    # 26 unitários dos contratos
make test-e2e         # 29 de fluxo completo (sobe stack + fake Spotify em portas efêmeras)
make test             # todos
```

Os testes e2e **não usam as portas padrão** (portas efêmeras) e rodam com
`TIDAL_MOCK=1`, sessão anônima configurada e dados temporários — não interferem
no seu dev. O fake Spotify (`tests/e2e/fake_spotify.py`) valida PKCE S256 de
verdade, rotação/revogação de refresh e cenários 429.

## 6. Solução de problemas

- **UI responde 502/503 no /api**: habilite os proxies capturando logs — cada serviço
  interno precisa estar no ar e com o mesmo `JWT_SECRET`.
- **`SESSION_INVALID` em todo /api**: cookie de sessão ausente/expirado — recarregue;
  no dev local certifique-se de `WEB_SESSION_COOKIE_SECURE=0` (com `1` e HTTP o
  navegador não guarda o cookie `__Host-*`).
- **`CSRF_INVALID` em POSTs**: o BFF rotaciona o CSRF a cada `GET /api/session`;
  recarregue a página para pegar o token atual.
- **`WEB_TOKEN_ENCRYPTION_KEY inválida`**: a variável deve ser base64url de 32 bytes;
  gere com `python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.
- **Spotify retorna `redirect_uri_mismatch`**: adicione exatamente o
  `SPOTIFY_REDIRECT_URI` configurado nas Redirect URIs do app no dashboard.
- **Downloads não executam (`DOWNLOAD_JOB_CONFIRM_REQUIRED`)**: rode dry-run e confirme
  seleções com manifest; confirme `ALLOW_DOWNLOADS=1`. A UI invalida confirmações
  repetidas/expiradas e pede novo dry-run nesses casos.
- **`/candidates` vazio com hifi-api real**: verifique `HIFI_API_URL`, token Tidal
  (`TIDAL_*`), e que o hifi-api esteja no ar (`/health/upstream`).
- **Porta 8xxx ocupada**: use o Makefile (local) ou volte para docker compose
  (a rede interna usa os nomes de serviço).
- **Dados locais nas pastas `data/` e `downloads/`**: estão no `.gitignore`; apague
  para resetar (exceto via git).

## 7. Backup / reset

- Persistência por SQLite: `make down` preserva volumes; para reset completo:
  `docker compose down -v` (apaga volumes).
- Local: apague `data/` e `downloads/` manualmente (fora do git).