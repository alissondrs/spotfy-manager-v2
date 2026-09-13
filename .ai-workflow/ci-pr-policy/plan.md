# Plano — Política de fluxo pré-develop (CI + `pr-policy`)

## Contexto

- Repo: `spotfy-manager-v2` (worktree isolada em `pre-develop/ci-pr-policy`,
  origem `origin/develop` SHA `6ed2d5c09`).
- CI atual: `.github/workflows/ci.yml` (14 checks), `build-publish.yml`.
- Sem PyYAML nas dependências do projeto → validação local usa **stdlib**.

## Decisões

1. **Regras declaradas em YAML** (`.github/pr-policy.yml`) — fonte de verdade
   para docs e testes; parser mínimo estrito + cross-check com PyYAML quando
   disponível (sem tornar PyYAML dependência obrigatória).
2. **Check `pr-policy` em workflow próprio** (`.github/workflows/pr-policy.yml`):
   evento `pull_request`, `permissions: contents: read`, zero privilégios
   elevados; valida apenas `github.head_ref`/`github.base_ref`.
3. **Fail-closed**: política ausente/vazia/malformada ou base de PR sem regra →
   check falha.
4. **Validação local**: `scripts/validate_pr_policy.py` (CLI/env, stdlib) +
   `scripts/test_pr_policy.py` (pytest, parser + casos válidos/inválidos).
5. **Docs**: atualizar `docs/ci-cd.md` e `AGENTS.md`.

## Passos

1. Criar `.github/pr-policy.yml` e `.github/workflows/pr-policy.yml`.
2. Criar `scripts/validate_pr_policy.py` e `scripts/test_pr_policy.py`.
3. Adicionar alvo `test-policy` no `Makefile` e incluir em `make test`.
4. Atualizar `docs/ci-cd.md` e `AGENTS.md`.
5. Rodar testes (com e sem PyYAML), corrigir falhas, registrar `verification.log`.
6. Criar `.ai-workflow/ci-pr-policy/*` e commit local.