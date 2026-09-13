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

- `on: pull_request` com `branches: [develop, main]` (não usa o evento de
  privilégios elevados).
- `permissions: contents: read` — mínimo, sem gravação, sem secrets elevados,
  sem comentários automáticos.
- Passos:
  1. `actions/checkout@v4` (`fetch-depth: 1`);
  2. validação: `python3 scripts/validate_pr_policy.py` com `PR_HEAD`/`PR_BASE`
     do evento (apenas nomes de branch);
  3. autoteste: `pytest scripts/test_pr_policy.py` (parser + casos
     válidos/inválidos) para garantir que a própria política está saudável.

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
44 testes: parser mínimo (válidos/inválidos), fail-closed, regras válidas e
inválidas por par `(head, base)`, edges de `fnmatch`, cross-check com PyYAML
(quando presente), consistência/safety do workflow (`pull_request` apenas,
`contents: read`, job `pr-policy`, branches develop/main). Sem PyYAML, 7 testes
de cross-check são pulados e os demais seguem passando (stdlib).

### `Makefile`
Novo alvo `test-policy` (roda `pytest scripts/test_pr_policy.py`) e inclusão em
`make test`.

### `docs/ci-cd.md` e `AGENTS.md`
Fluxo pré-develop: origem em `origin/develop`, worktree isolada, PR draft, CI
obrigatório, squash e auto-merge só após checks, política `pr-policy`
(documentada com tabela base→head), lista preservada dos 14 checks.

## Não foi alterado

- Código de produção (serviços/contracts), proteção remota de branches,
  `.env*`, segredos, `ci.yml` (14 checks preservados), `build-publish.yml`,
  `ci-project.yaml`.