# CI/CD — spotfy-manager-v2

Este documento descreve o fluxo de integração, publicação e deploy do projeto,
incluindo o contrato `ci-project.yaml`, os workflows do GitHub Actions e o
deploy via Harness no ambiente lab.

## Fluxo

```text
origin/develop
   ↓ worktree isolada (git worktree add)
pre-develop/*            ← toda mudança começa aqui
   ↓ PR para develop
develop
   ├─ pr-policy (head pre-develop/*; sem elevação de privilégios)
   ├─ testes de contratos e E2E
   ├─ lint e auditoria
   ├─ build das 8 imagens
   ├─ publicação das imagens develop-*
   └─ deploy no ambiente lab via Harness
          ↓ PR (apenas de develop)
main
   ├─ CI completo
   ├─ publicação das imagens prod-*
   └─ deploy de produção via Harness (aprovação manual prévia)
```

Dois movimentos principais:

1. `pre-develop/* → develop` — PR para develop **exige head `pre-develop/*`**
   (verificado pelo check `pr-policy`). PR criado em **draft** e mantido draft até
   o CI verde; só então promovido para revisão. Integração e validação no lab.
2. `develop → main` — promoção para produção. PR para main **só pode vir de
   `develop`** (verificado pelo mesmo `pr-policy`).

Regras de merge:
- PR em draft não deve ser mergeado (esperar CI obrigatório verde).
- Merge com **squash**; **auto-merge não deve ser habilitado antes de todos os
  checks obrigatórios passarem** (nunca "auto-merge regardless of checks").
- Não existe publicação de produção diretamente de branch de feature.

## Arquivos

| Arquivo | Papel |
|---------|-------|
| `ci-project.yaml` | contrato de configuração (fonte de verdade) |
| `.github/workflows/ci.yml` | CI em PRs e pushes (develop/main) |
| `.github/workflows/pr-policy.yml` | check `pr-policy`: valida head/base de PRs |
| `.github/pr-policy.yml` | regras da política pré-develop (fonte das regras) |
| `scripts/validate_pr_policy.py` | validador local da política (stdlib, sem deps novas) |
| `scripts/test_pr_policy.py` | testes do parser e da política (casos válidos/inválidos) |
| `.github/workflows/build-publish.yml` | build + publicação de imagens após merge |
| `deploy/compose/docker-compose.deploy.yml` | compose de deploy (sem build, usa imagens publicadas) |
| `deploy/harness/README.md` | guia de deploy via Harness no lab |

## Branches

- `main` — produção. Protegida: PR vindo de `develop` (regra), CI obrigatório
  (14 checks), sem force push, sem deleção.
- `develop` — integração/validação. Protegida: PR obrigatório, CI obrigatório
  (14 checks), sem force push, sem deleção.
- `pre-develop/*` — fluxo pré-desenvolvimento. Criado com **origem em
  `origin/develop`** e **worktree isolada** (nunca commits diretos em develop):

  ```bash
  git worktree add -b pre-develop/<assunto> <caminho> origin/develop
  ```

  O PR para develop é aberto em **draft** e segue draft até todos os checks
  obrigatórios estarem verdes; merge apenas com squash e só depois do CI.

### Política de PRs (check `pr-policy`)

Regras declaradas em `.github/pr-policy.yml` e aplicadas pelo workflow
`.github/workflows/pr-policy.yml` em qualquer PR apontando para `develop`/`main`:

| base   | head exigido       |
|--------|--------------------|
| develop| `pre-develop/*`    |
| main   | `develop`          |

O check é **fail-closed**: política ausente, malformada ou sem regra para a base
do PR faz o check falhar. O workflow roda em `pull_request_target` com
`permissions: contents: read` e valida apenas `github.event.pull_request.head.ref`
/ `base.ref` em **lógica inline** — sem checkout, sem instalar dependências e
sem executar nenhum arquivo/script do head (não confiável) do PR.

### Segurança e bootstrap do check `pr-policy`

O check roda em `pull_request_target`. A documentação oficial do GitHub confirma
que esse evento executa **no contexto da branch padrão do repositório** — neste
repo, `main`: o workflow usado é o arquivo da **branch padrão**, jamais o do head
ou da base do PR. Duas consequências: (1) o head do PR não é executado (seguro);
(2) o check **só passa a existir quando o workflow estiver na branch padrão
(`main`)** — mergear apenas em `develop` não faz o evento disparar.

Rollout correto (nesta ordem):

1. **Merge deste workflow em `develop`** — **sem tornar `pr-policy` required**.
   No primeiro PR para `develop` o GitHub ainda lê o workflow da branch padrão
   (`main`), que ainda não o contém; o check pode nem aparecer. Isso é esperado.
2. **Promova `develop` → `main`** pelo fluxo autorizado (PR de `develop` para
   `main`). Com o merge, o workflow passa a estar na **branch padrão** e o GitHub
   passa a dispará-lo para eventos `pull_request_target`.
3. **Abra uma PR piloto** `pre-develop/*` → `develop` (ex.:
   `pre-develop/ativar-pr-policy`) e confirme que o check `pr-policy` roda e fica
   **verde**.
4. **Só então adicione `pr-policy` aos required checks** de `develop` e `main`.

Torná-lo required antes do bootstrap deixa **todos os PRs bloqueados** (o check
não dispara porque o workflow da branch padrão ainda não o contém).

Validação local (sem dependências novas; worktrees sem `.venv` local podem
apontar para uma venv existente):

```bash
make test-policy                          # usa .venv local, senão python3 do PATH
make VENV=/caminho/da/venv test-policy    # venv existente com pytest
python3 scripts/validate_pr_policy.py --head pre-develop/x --base develop
```

`scripts/test_pr_policy.py` cobre parser, fail-closed e a **consistência**
entre `.github/pr-policy.yml` e a lógica inline do workflow: regras idênticas,
mesmo veredito para uma matriz de pares `(head, base)` e **ausência garantida**
de `checkout`/`pip`/execução de script do PR no workflow.

Nota: a proteção foi aplicada via API de branch protection; em repositórios
privados no plano GitHub Free ela exige o GitHub Pro. Para este projeto o
repositório está **público** justamente para permitir a proteção sem custo.
Se algum dia o repo voltar a ser privado, a proteção é desativada pelo GitHub —
será necessário o Pro ou a reaplicação via regras.

Nomes de jobs exigidos na proteção de branches (estáveis — **não renomear**):

- `test-contract`
- `test-e2e`
- `lint`
- `pip-audit`
- `compose-validate`
- `secret-scan`
- `docker-build-<serviço>` para cada um dos 8 serviços

Além desses 14, o check `pr-policy` (workflow próprio) deve ser adicionado à
lista de required checks de `develop` e `main` — **somente após o bootstrap**
(workflow presente na **branch padrão `main`** e verde em uma PR piloto; ver
seção "Segurança e bootstrap do check `pr-policy`").

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

## pr-policy (pr-policy.yml)

Workflow próprio e estável, roda em `pull_request_target` (branches
`develop`/`main`) com `permissions: contents: read`:

1. `python3 - <head> <base>` com **lógica inline** (bloco dentro do próprio
   workflow) a partir de `github.event.pull_request.head.ref`/`base.ref` —
   apenas nomes de branch, sem baixar o repo, sem pip, sem executar qualquer
   arquivo do PR e sem credenciais;
2. fail-closed: head/base ausentes ou base sem regra ⇒ o check falha.

Sem `actions/checkout`, sem `setup-python`, sem steps com `uses:`. O bootstrap
(workflow presente na **branch padrão `main`** antes de ativar como required
check) está descrito na seção anterior.

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