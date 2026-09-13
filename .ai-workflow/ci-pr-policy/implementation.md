run_id: ci-pr-policy
created_at: 2026-09-13T15:48:00-03:00
producer: opencode
status: implementado (revisão corretiva)
source_refs:
  - skill:pipeline-multiagente-de-engenharia
  - branch:pre-develop/ci-pr-policy
  - base:origin/develop@6ed2d5c09639a86c57c387ed9a7eb077e12798d1
  - commit:486ee5a5b9be59997a490a211cece3a7731e798b (versão original)

# Implementação — Política de fluxo pré-develop (CI + `pr-policy`)

## O que foi feito

### `.github/pr-policy.yml`
Declaração das regras da política (fonte de verdade, fail-closed):

| base    | head exigido   |
|---------|----------------|
| develop | `pre-develop/*` |
| main    | `develop`       |

Subconjunto YAML mínimo, documentado no cabeçalho do próprio arquivo.

### `.github/workflows/pr-policy.yml`
Workflow próprio e estável do check `pr-policy`:

- `on: pull_request_target` com `branches: [develop, main]` e
  `permissions: contents: read` (mínimo; sem gravação, sem credenciais).
- **Sem checkout, sem setup-python, sem steps `uses:`, sem pip**: o job valida
  apenas `github.event.pull_request.head.ref`/`base.ref` com **lógica inline**
  (bloco Python dentro do próprio workflow, fail-closed). Nenhum arquivo do head
  (não confiável) do PR é baixado ou executado.
- Bootstrap documentado: como `pull_request_target` roda no contexto da
  **branch padrão (`main`)**, o workflow precisa estar em `main` antes de virar
  required check — merge em develop (sem required) → promoção `develop`→`main`
  → PR piloto verde → required checks (ver `docs/ci-cd.md`).

### `scripts/validate_pr_policy.py`
Validador CLI/env em **stdlib** (Python ≥ 3.11):

- parse da política: `yaml.safe_load` se PyYAML estiver disponível (cruzando o
  resultado), senão parser mínimo estrito próprio; `PR_POLICY_NO_YAML=1` força o
  parser mínimo (usado nos testes).
- `evaluate(head, base, policy)`: aceita se a base do PR casar ao menos uma
  regra (`head_exact` ou `head_pattern` via `fnmatch`).
- Fail-closed: arquivo ausente/vazio/malformado, chaves desconhecidas, regra sem
  `base`/`reason`/head, ou política sem cobertura de `develop` e `main` → erro.
- CLI: `--head/--base/--policy` ou vars `PR_HEAD`/`PR_BASE`/`PR_POLICY_FILE`;
  exit `0` (ok), `1` (inaprovado), `2` (erro de política/ambiente).

### `scripts/test_pr_policy.py` (pytest)
Testes do parser mínimo (válidos/inválidos), fail-closed, regras válidas e
inválidas por par `(head, base)`, edges de `fnmatch`, cross-check com PyYAML
(quando presente) e a revisão corretiva adiciona **garantias de consistência e
segurança do workflow inline**:

- `TestInlineRulesConsistency`: a lógica inline (`RULES`) do workflow é
  idêntica a `.github/pr-policy.yml`;
- `TestInlineBehaviorParity`: matriz de pares `(head, base)` — o bloco inline
  extraído e rodado como subprocesso dá o mesmo veredito que o validador local;
- `TestWorkflowNoUntrustedExecution`: ausência de `actions/checkout`, `pip`,
  `scripts/`/`pytest`/`validate_pr_policy` no workflow, sem `secrets.`/`GITHUB_TOKEN`,
  sem steps `uses:`, `pull_request_target` (não `pull_request`), `contents: read`;
- `TestWorkflowStructure` (PyYAML): `pull_request_target` único, branches
  `develop`/`main`, steps sem `uses:`, binding de head/base via evento.

Sem PyYAML, os testes estruturais/cross-check são pulados; os demais seguem
passando (stdlib).

### `Makefile`
Novo alvo `test-policy` (roda `pytest scripts/test_pr_policy.py`) e inclusão em
`make test`. `VENV` sobrescrevível (`make VENV=/caminho/da/venv test-policy`) com
fallback para `python3` do PATH quando o `.venv` local não existe (worktrees).
`scripts/validate_pr_policy.py` continua como validação local (stdlib).

### `docs/ci-cd.md` e `AGENTS.md`
Fluxo pré-develop: origem em `origin/develop`, worktree isolada, PR draft, CI
obrigatório, squash e auto-merge só após checks, política `pr-policy`
(documentada com tabela base→head), lista preservada dos 14 checks.

## Não foi alterado

- Código de produção (serviços/contracts), proteção remota de branches,
  `.env*`, segredos, `ci.yml` (14 checks preservados), `build-publish.yml`,
  `ci-project.yaml`.

## Revisão corretiva (3º commit — estritamente local, sem push/PR)

Correção do bootstrap: a documentação oficial do GitHub confirma que
`pull_request_target` executa **no contexto da branch padrão** (repo usa `main`),
utilizando o workflow da branch padrão — mergear só em `develop` não cria o check.
Ajustes de documento/comentário/teste (nenhum código de produção):

- `.github/workflows/pr-policy.yml` — comentário de bootstrap com a ordem correta
  (merge em develop → promoção develop→main → PR piloto → required checks).
- `docs/ci-cd.md` — seção "Segurança e bootstrap do check `pr-policy`" e notas
  finais agora exigem o workflow na **branch padrão `main`** antes da ativação.
- `AGENTS.md` — política de PRs registra que `pull_request_target` usa o workflow
  da branch padrão (`main`).
- `scripts/test_pr_policy.py` — novo grupo de testes verifica que `docs/ci-cd.md`
  menciona `main`/branch padrão e a ordem do rollout antes da ativação.
- Artefatos `.ai-workflow/ci-pr-policy/*` atualizados.

## Revisão de autorização (4º commit — estritamente local, sem push/PR)

Nova autorização do usuário refletida na documentação e nos testes da política.
O orquestrador **faz push** e **abre/atualiza a PR draft para `develop`** quando o
pipeline multiagente/local conclui com **revisão PASS**, **testes obrigatórios
verdes**, **branch `pre-develop/*` válida** e **worktree limpa** — a PR é aberta
**antes** do GitHub CI (o `ci.yml` atual é acionado por `pull_request`) e depois o
orquestrador **acompanha o CI**. Não marcar `ready`, não habilitar auto-merge e
não mergear automaticamente; falha, skipped, cancelled, ausência ou checks
inconclusivos mantêm a PR **draft e bloqueada**. Ajustes:

- `AGENTS.md` — nova regra "Abertura automática de PR draft (autorização)" e passo
  6 no fluxo de trabalho.
- `docs/ci-cd.md` — seção "Abertura automática de PR draft (autorização)" no fluxo,
  diagrama e seção de branches atualizados.
- `scripts/test_pr_policy.py` — novo grupo `TestAutoDraftPRDocs` garante que a
  documentação registra gates, abertura antes do CI (motivo `pull_request`),
  acompanhamento do CI, proibições (ready/auto-merge/merge) e bloqueio por checks
  falhos/skipped/cancelled/ausentes/inconclusivos.
- Artefatos `.ai-workflow/ci-pr-policy/*` atualizados.

Nenhum código de produção foi alterado; nenhuma ação remota (push/PR/auto-merge).

## Correção do job `pip-audit` (5º commit — estritamente local, sem push/PR)

CI remoto da PR draft #1 (run 34784182136): todos os checks verdes exceto
`pip-audit`. Causa-raiz: a linha `cat services/*/requirements.txt | sort -u` do
job concatenava o fim de um requirements.txt sem newline final com o início do
seguinte — `services/playlist/requirements.txt` termina em `httpx>=0.27` (sem
`\n`) e `services/report/requirements.txt` começa com `fastapi>=0.115`, gerando
`httpx>=0.27fastapi>=0.115` (requirement inválido para o `pip-audit`).

Ajustes (nenhum requirements.txt / código de produção alterado):

- `.github/workflows/ci.yml` — job `pip-audit`: `cat services/*/requirements.txt
  | sort -u > /tmp/reqs.txt` → `awk 1 services/*/requirements.txt | sort -u >
  /tmp/reqs.txt`. O `awk 1` imprime cada registro com newline (inclusive a última
  linha sem `\n`), separando corretamente os arquivos antes do `sort -u`. Mudança
  mínima, sem dependências novas no runner.
- `scripts/test_ci_pip_audit.py` (novo) — teste regressivo local stdlib+pytest
  (PyYAML opcional): reproduz o comportamento antigo (`cat` cru → linha fundida),
  valida o novo (concatenação com newline sobre os arquivos reais do repo produz
  só requirements bem formados) e garante que o `ci.yml` mantém a separação por
  newline antes do `sort -u` (guards textuais + parse estrutural com PyYAML
  quando disponível). Falha ao reverter o comando antigo no `ci.yml`.
- `Makefile` — novo alvo `test-ci-audit` (roda `scripts/test_ci_pip_audit.py`) e
  inclusão no `make test`.

Sem novas dependências; sem alterações em requirements/código de produção;
nenhuma ação remota (push/PR/merge).