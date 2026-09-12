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

1. Abra http://127.0.0.1:8000.
2. Na tela de login, clique em **Registrar usuário** (ou, em desenvolvimento explícito,
   habilite `IDENTITY_ENABLE_BOOTSTRAP=1` e chame
   `curl -s http://127.0.0.1:8101/_debug/bootstrap` → `admin`/`admin12345`).
3. Importe uma playlist (Spotify — requer credenciais — ou envie um arquivo md/csv/json)
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
| `SPOTIFY_ACCESS_TOKEN` | — | token direto do Spotify |
| `SPOTIFY_CLIENT_ID/SECRET` | — | client-credentials do Spotify |
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
make test-contract    # 16 unitários dos contratos
make test-e2e         # 10 de fluxo completo (sobe stack em portas efêmeras)
make test             # todos
```

Os testes e2e **não usam as portas padrão** (portas efêmeras) e rodam com
`TIDAL_MOCK=1` e dados temporários — não interferem no seu dev.

## 6. Solução de problemas

- **UI responde 502/503 no /api**: habilite os proxies capturando logs — cada serviço
  interno precisa estar no ar e com o mesmo `JWT_SECRET`.
- **`/api/login` retorna 401 apesar da senha certa**: `JWT_SECRET` divergente entre
  identity e o consumer que valida o token.
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