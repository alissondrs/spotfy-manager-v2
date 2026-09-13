run_id: ci-pr-policy
created_at: 2026-09-13T15:48:00-03:00
producer: opencode
status: implementado, local_only (aguardando push/PR autorizado)
source_refs:
  - skill:pipeline-multiagente-de-engenharia
  - branch:pre-develop/ci-pr-policy
  - base:origin/develop@6ed2d5c09639a86c57c387ed9a7eb077e12798d1
  - commit:486ee5a5b9be59997a490a211cece3a7731e798b (versão original)

# Status — Política de fluxo pré-develop (CI + `pr-policy`)

## Status: IMPLEMENTADO ✅

Check `pr-policy` funcional em workflow próprio; política em YAML; validação
local stdlib; docs atualizadas; testes verdes na revisão corretiva (53 com
PyYAML / 44 + 9 skipped sem PyYAML). Commits locais realizados em
`pre-develop/ci-pr-policy` (sem push/PR).

## O que foi entregue

- `.github/pr-policy.yml` — regras (develop→`pre-develop/*`, main→`develop`).
- `.github/workflows/pr-policy.yml` — check `pr-policy` (`pull_request_target`,
  `contents: read`, **lógica inline**, sem checkout/pip/execução do head,
  fail-closed, bootstrap documentado).
- `scripts/validate_pr_policy.py` — validador CLI/env stdlib (validação local).
- `scripts/test_pr_policy.py` — parser + casos válidos/inválidos + consistência
  inline e garantias de ausência de checkout/pip/execução de código do PR.
- `Makefile` — alvo `test-policy` incluído em `make test`; `make VENV=/caminho/da/venv
  test-policy`/fallback `python3` para worktrees sem `.venv`.
- `docs/ci-cd.md`, `AGENTS.md` — fluxo pré-develop documentado (incl. bootstrap).
- `.ai-workflow/ci-pr-policy/*` — requisitos/plano/impl/verificação/status.

## Revisão corretiva (2º commit, sem push/PR)

O check original (`pull_request` + `checkout` + rodar scripts do head) executava
código não confiável no runner. Corrigido para `pull_request_target` com lógica
inline somente sobre `head.ref`/`base.ref`; `scripts/validate_pr_policy.py` segue
apenas como validação local; testes garantem consistência (inline == YAML, mesmo
veredito em matriz) e ausência de checkout/pip/steps/execução do PR. Bootstrap
(wf presente nas bases antes de required) documentado em `docs/ci-cd.md`.

## Checks preservados

Os 14 checks de `ci.yml` não foram renomeados/removidos; `pr-policy` é o +15º.

## Para ativação na proteção de branches (fora desta branch)

Após merge em `develop`, adicionar `pr-policy` à lista de *required checks* de
`develop` e `main` na proteção de branches (não feito aqui, por escopo).

## Pendências / limitações

- Proteção remota não foi alterada (requisito). `pr-policy` precisa do
  **bootstrap** (workflow em `develop` + verde em um PR seguinte) antes de entrar
  na lista de required checks; ver `docs/ci-cd.md`.
- Workflow ainda não foi executado em um runner GitHub (sem push/PR aqui);
  lógica inline e consistência validadas por testes em 3 ambientes (pytest sem
  yaml, python3 com yaml, venv descartável com pytest+yaml).
- `actionlint` não está instalado; validado por parse YAML (PyYAML) + testes.
- `head_pattern` usa `fnmatch` que cruza `/`, portanto `pre-develop/a/b` é aceito.
- PyYAML lê a chave `on` como bool (YAML 1.1) — normal em workflows do GitHub;
  testes tratam isso via helper.
- O runner do check exige `python3` no `ubuntu-latest` (pré-instalado); não usa
  `setup-python` de propósito (zero deps).

## Sem segredos

Nenhum secret/env real foi gravado no repo ou nos artefatos `.ai-workflow/*`.