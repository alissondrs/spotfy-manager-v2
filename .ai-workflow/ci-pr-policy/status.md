# Status — Política de fluxo pré-develop (CI + `pr-policy`)

## Status: IMPLEMENTADO ✅

Check `pr-policy` funcional em workflow próprio; política em YAML; validação
local stdlib; docs atualizadas; testes verdes (44 com PyYAML / 37 + 7 skipped
sem PyYAML). Commit local realizado em `pre-develop/ci-pr-policy`.

## O que foi entregue

- `.github/pr-policy.yml` — regras (develop→`pre-develop/*`, main→`develop`).
- `.github/workflows/pr-policy.yml` — check `pr-policy` (`pull_request`,
  `contents: read`, fail-closed, autoteste).
- `scripts/validate_pr_policy.py` — validador CLI/env stdlib.
- `scripts/test_pr_policy.py` — parser + casos válidos/inválidos (44 testes).
- `Makefile` — alvo `test-policy` incluído em `make test`.
- `docs/ci-cd.md`, `AGENTS.md` — fluxo pré-develop documentado.
- `.ai-workflow/ci-pr-policy/*` — requisitos/plano/impl/verificação/status.

## Checks preservados

Os 14 checks de `ci.yml` não foram renomeados/removidos; `pr-policy` é o +15º.

## Para ativação na proteção de branches (fora desta branch)

Após merge em `develop`, adicionar `pr-policy` à lista de *required checks* de
`develop` e `main` na proteção de branches (não feito aqui, por escopo).

## Pendências / limitações

- Proteção remota não foi alterada (requisito). `pr-policy` precisa entrar na
  lista de required checks após o merge.
- Workflow ainda não foi executado em um runner GitHub (sem push/PR aqui);
  parser interno validado por testes em 2 ambientes (com/sem PyYAML).
- `actionlint` não está instalado; validado por parse YAML + testes.
- `head_pattern` usa `fnmatch` que cruza `/`, portanto `pre-develop/a/b` é aceito.
- PyYAML lê a chave `on` como bool (YAML 1.1) — normal em workflows do GitHub;
  testes tratam isso via helper.

## Sem segredos

Nenhum secret/env real foi gravado no repo ou nos artefatos `.ai-workflow/*`.