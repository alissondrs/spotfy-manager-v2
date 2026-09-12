# Deploy Harness — spotfy-manager-v2 (ambiente lab)

Este guia descreve o deploy do `spotfy-manager-v2` via **Harness CD** usando
**Docker Compose** num host lab onde o **Harness Delegate** está instalado.

## Visão geral

```text
Harness CD (pipeline)
   ↓ executa comandos no Delegate
Harness Delegate (host lab)
   ↓
docker compose pull
docker compose up -d
   ↓
health checks dos 8 serviços
```

- O Compose de deploy usa **imagens publicadas** (Docker Hub), nunca `build`.
- O ambiente lab usa imagens `develop-*`; produção usa apenas `prod-*`.
- Tags imutáveis por commit (nunca `latest`).

## Pré-requisitos no host lab

- Host lab com Docker Engine e Docker Compose v2.
- Harness Delegate instalado e conectado à conta Harness.
- Acesso ao repositório Docker Hub público/privado usado pelo projeto.
- Arquivo de deploy presente no host (ou baixado pelo pipeline do repositório):
  `deploy/compose/docker-compose.deploy.yml`.

## Passos no Harness

1. **Service** com spec Kubernetes (no nosso caso, uso de manifesto):
   não aplicável ao Compose. Usará **Custom deployment** ou execução de comandos
   via Delegate.
2. **Environment**: `lab` (usado após merge em `develop`).
3. **Variables** (não secrets):
   - `DOCKERHUB_USERNAME=alissondrs`
   - `WEB_IMAGE_TAG`, `IDENTITY_IMAGE_TAG`, ... (definidos por etapa com o commit)
4. **Secrets** (referenciados individualmente, um por secret — nunca `.env`):
   ```text
   JWT_SECRET
   SPOTIFY_ACCESS_TOKEN
   SPOTIFY_CLIENT_ID
   SPOTIFY_CLIENT_SECRET
   TIDAL_TOKEN_FILE      (ou TIDAL_REFRESH_TOKEN)
   HIFI_API_URL
   GF_ADMIN_PASSWORD
   ```
5. **Pipeline steps** (via Delegate):
   ```bash
   cd /opt/spotfy-manager-v2/deploy/compose
   export DOCKERHUB_USERNAME=${DOCKERHUB_USERNAME}
   export JWT_SECRET=${JWT_SECRET}
   # ... demais secrets/variables ...
   docker compose -f docker-compose.deploy.yml pull
   docker compose -f docker-compose.deploy.yml up -d
   ```

## Health checks

Após `up -d`, validar todos os endpoints:

```bash
for p in 8000 8101 8102 8103 8104 8105 8106 8107; do
  curl -sf "http://127.0.0.1:${p}/health" >/dev/null && echo "ok ${p}" || echo "FALHA ${p}"
done
```

Todos devem responder; caso contrário, o deploy é marcado como falho e o
rollback é acionado.

## Rollback

Como as tags são imutáveis por commit, reverter = apontar o Compose para a tag
anterior e recriar:

```bash
export WEB_IMAGE_TAG=develop-web-sha-<commit-ANTERIOR>
# ... demais targets ...
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

## Segurança

- **Nunca** armazenar `.env` no host ou como artefato no Harness.
- Cada secret é injetado individualmente pelo Harness como variável de ambiente.
- `ALLOW_DOWNLOADS` permanece `0` no lab e em produção (validação por dry-run).
- Tokens desnecessários devem ser removidos dos services quando possível.

## Produção

- Aprovação manual no Harness antes do deploy produtivo.
- Usar exclusivamente tags `prod-*` no ambiente de produção.
- Mesmo Compose de deploy, apenas com `*_IMAGE_TAG` apontando para `prod-*`.