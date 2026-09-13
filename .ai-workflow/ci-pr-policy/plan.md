run_id: ci-pr-policy
created_at: 2026-09-13T15:47:00-03:00
producer: opencode
status: implementado
source_refs:
  - skill:pipeline-multiagente-de-engenharia
  - branch:pre-develop/ci-pr-policy
  - base:origin/develop@6ed2d5c09639a86c57c387ed9a7eb077e12798d1

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

## Atualização corretiva (2º commit)

A decisão nº 2 foi corrigida na implementação: o events triggers passa a ser
`pull_request_target` (não `pull_request`) com **lógica inline** — sem checkout,
sem pip e sem executar código do head. Isso elimina a execução de código não
confiável do PR que existia ao rodar `scripts/validate_pr_policy.py`,
deslocou `scripts/validate_pr_policy.py` para validação local e adicionou testes
de consistência/segurança em `scripts/test_pr_policy.py`. Bootstrap documentado
em `docs/ci-cd.md`.

## Atualização corretiva (3º commit)

O bootstrap foi corrigido de forma estritamente local (docs + testes): como
`pull_request_target` usa o workflow da **branch padrão (`main`)**, mergear em
`develop` não é suficiente para o check surgir. Ordem correta: (1) merge em
`develop` sem tornar `pr-policy` required; (2) promoção `develop`→`main` pelo
fluxo autorizado; (3) PR piloto `pre-develop/*`→`develop` verde; (4) só então
adicionar o check aos required checks de `develop`/`main`. `docs/ci-cd.md`,
`AGENTS.md`, comentário do workflow, artefatos `.ai-workflow/*` e teste de
documentação refletem isso. Nenhuma ação remota; código de produção intocado.

## Atualização corretiva (4º commit) — autorização de push + PR draft automático

Nova autorização do usuário acrescentada à documentação e aos testes: ao concluir
o pipeline multiagente/local com **revisão PASS**, **testes obrigatórios verdes**,
**branch `pre-develop/*` válida** e **worktree limpa**, o orquestrador **faz push**
e **abre/atualiza a PR draft para `develop`** **antes** do GitHub CI (o `ci.yml`
dispara em `pull_request`) e depois **acompanha o CI**. Nada de `ready`, auto-merge
ou merge automático; falha/skipped/cancelled/ausência/inconclusivos mantêm a PR em
**draft e bloqueada**. Alteração documental + testes da política; sem ações remotas
e sem código de produção.