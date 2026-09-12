# CI/CD — spotfy-manager-v2

Este documento descreve o fluxo de integração, publicação e deploy do projeto,
incluindo o contrato `ci-project.yaml`, os workflows do GitHub Actions e o
deploy via Harness no ambiente lab.

## Fluxo

```text
feature/*
   ↓ PR
develop
   ├─ testes de contratos e E2E
   ├─ lint e auditoria
   ├─ build das 8 imagens
   ├─ publicação das imagens develop-*
   └─ deploy no ambiente lab via Harness
          ↓ PR
main
   ├─ CI completo
   ├─ publicação das imagens prod-*
   └─ deploy de produção via Harness (aprovação manual prévia)
```

Dois movimentos principais:

1. `feature/* → develop` — integração e validação no ambiente lab.
2. `develop → main` — promoção para produção.

Não existe publicação de produção diretamente de branch de feature.

## Arquivos

| Arquivo | Papel |
|---------|-------|
| `ci-project.yaml` | contrato de configuração (fonte de verdade) |
| `.github/workflows/ci.yml` | CI em PRs e pushes (develop/main) |
| `.github/workflows/build-publish.yml` | build + publicação de imagens após merge |
| `deploy/compose/docker-compose.deploy.yml` | compose de deploy (sem build, usa imagens publicadas) |
| `deploy/harness/README.md` | guia de deploy via Harness no lab |

## Branches

- `main` — produção. Protegida: PR vindo de `develop` (convenção), CI
  obrigatório (14 checks), sem force push, sem deleção.
- `develop` — integração/validação. Protegida: PR obrigatório, CI obrigatório
  (14 checks), sem force push, sem deleção.

Nota: a proteção foi aplicada via API de branch protection; em repositórios
privados no plano GitHub Free ela exige o GitHub Pro. Para este projeto o
repositório está **público** justamente para permitir a proteção sem custo.
Se algum dia o repo voltar a ser privado, a proteção é desativada pelo GitHub —
será necessário o Pro ou a reaplicação via regras.

Nomes de jobs exigidos na proteção de branches (estáveis):

- `test-contract`
- `test-e2e`
- `lint`
- `pip-audit`
- `compose-validate`
- `secret-scan`
- `docker-build-<serviço>` para cada um dos 8 serviços

## CI (ci.yml)

Gates, na ordem:

1. Python 3.11
2. instalação do pacote `contracts` (`pip install -e ./contracts`)
3. dependências dos 8 serviços
4. testes de contratos (`make test-contract`)
5. testes E2E (`make test-e2e`)
6. lint (`make lint`)
7. auditoria `pip-audit`
8. validação do Docker Compose (`docker compose config`)
9. scan de segredos (Gitleaks, com `fetch-depth: 0`)
10. build das 8 imagens via matriz `{SERVICE, SERVICE_PORT}`

Build por serviço:

```bash
docker build --build-arg SERVICE=<serviço> --build-arg SERVICE_PORT=<porta> .
```

## Publicação (build-publish.yml)

Roda somente após merge em `develop` ou `main`.

- Repositório Docker Hub único: `alissondrs/spotfy-manager-v2`
- Login via `DOCKERHUB_USERNAME` (variable) e `DOCKERHUB_TOKEN` (secret)
- Tags imutáveis por commit:

```text
develop-<serviço>-sha-<commit completo>
prod-<serviço>-sha-<commit completo>
```

- Gera e publica o artefato `image-manifest.json` com nome do serviço, commit,
  tag e digest. Retenção curta (7 dias).

## Deploy Harness (lab)

- Harness CD baixa as imagens publicadas no host lab via **Harness Delegate**
  (Docker Compose com `docker compose pull` + `docker compose up -d`).
- `deploy/compose/docker-compose.deploy.yml` usa imagens por variável, sem
  `build`, com volumes persistentes e `ALLOW_DOWNLOADS=0`.
- Lab usa imagens `develop-*`; produção apenas `prod-*`.
- Health checks dos 8 serviços após subir.

## Segredos

Nunca versionar `.env` (ver `.gitignore`). Valores reais:

- em CI: GitHub Secrets/Variables (`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`);
- no deploy: Harness Secrets.

Secrets esperados no Harness:

```text
JWT_SECRET
SPOTIFY_ACCESS_TOKEN
SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET
TIDAL_TOKEN_FILE
TIDAL_REFRESH_TOKEN
HIFI_API_URL
GF_ADMIN_PASSWORD
```

O Harness não recebe o arquivo `.env` completo; cada secret é referenciado
individualmente.

## Rollback

Usar tags imutáveis por commit. Para reverter, apontar o Compose de deploy para
a tag anterior e executar `docker compose up -d`. Não usar `latest`.