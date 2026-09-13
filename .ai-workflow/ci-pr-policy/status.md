run_id: ci-pr-policy
created_at: 2026-09-13T15:48:00-03:00
producer: opencode
status: implementado, local_only (5 commits) (fix pip-audit após CI remoto 34784182136)
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
- `docs/ci-cd.md`, `AGENTS.md` — fluxo pré-develop documentado (incl. bootstrap
  exigindo a branch padrão `main` antes da ativação).
- `.ai-workflow/ci-pr-policy/*` — requisitos/plano/impl/verificação/status.

## Revisão corretiva (2º commit, sem push/PR)

O check original (`pull_request` + `checkout` + rodar scripts do head) executava
código não confiável no runner. Corrigido para `pull_request_target` com lógica
inline somente sobre `head.ref`/`base.ref`; `scripts/validate_pr_policy.py` segue
apenas como validação local; testes garantem consistência (inline == YAML, mesmo
veredito em matriz) e ausência de checkout/pip/steps/execução do PR. Bootstrap
corrigido (workflow presente na **branch padrão `main`** antes de required)
documentado em `docs/ci-cd.md`.

## Revisão corretiva (3º commit, sem push/PR)

A documentação oficial do GitHub confirma que `pull_request_target` roda **no
contexto da branch padrão** (`main` neste repo): o workflow usado é o da branch
padrão, não o da base/head do PR. O bootstrap anterior (merger em `develop` +
PR seguinte) estava incorreto — sem o workflow em `main` o check nunca dispara.
Rollout corrigido em `docs/ci-cd.md`, `AGENTS.md`, comentário do workflow e
artefatos `.ai-workflow/*`: merge em `develop` (sem required) → promoção
`develop`→`main` pelo fluxo autorizado → PR piloto `pre-develop/*`→`develop`
verde → só então adicionar `pr-policy` aos required checks de `develop`/`main`.
Nova suíte de testes garante que a documentação menciona `main`/branch padrão
antes da ativação. Nenhuma ação remota; código de produção intocado.

## Autorização (4º commit, sem push/PR)

Nova autorização documentada: ao concluir o pipeline multiagente/local com
**revisão PASS**, **testes obrigatórios verdes**, **branch `pre-develop/*`
válida** e **worktree limpa**, o orquestrador **faz push** e **abre/atualiza a PR
draft para `develop`** **antes** do GitHub CI (`ci.yml` dispara em
`pull_request`) e depois **acompanha o CI**. Sem `ready`, sem auto-merge e sem
merge automático; checks em falha/skipped/cancelled/ausentes/inconclusivos
mantêm a PR **draft e bloqueada**. Novos testes documentais
(`TestAutoDraftPRDocs`) garantem a consistência em `AGENTS.md` e `docs/ci-cd.md`.
Sem ações remotas; código de produção intocado.

## Correção do job `pip-audit` (5º commit, sem push/PR) — CI remoto 34784182136

A PR draft #1 executou o CI e somente o job `pip-audit` falhou (run
34784182136). Causa-raiz comprovada no log: `cat services/*/requirements.txt |
sort -u` concatenava o fim de um requirements.txt **sem newline final**
(`services/playlist/requirements.txt` termina em `httpx>=0.27`) com o início do
seguinte (`services/report/requirements.txt` começa com `fastapi>=0.115`),
produzindo a linha inválida `httpx>=0.27fastapi>=0.115` e quebrando o `pip-audit`.

Correção mínima e robusta em `.github/workflows/ci.yml` (job `pip-audit`):
`cat ... | sort -u` → `awk 1 ... | sort -u` (`awk 1` garante newline por
registro antes do `sort -u`; portátil p/ GNU awk no runner e o awk do
macOS/BSD na validação local). Nenhuma alteração em requirements.txt/código de
produção.

Regressão local adicionada: `scripts/test_ci_pip_audit.py` (stdlib + pytest,
PyYAML opcional) — falha com o comportamento antigo e valida o novo:
`TestOldBehaviorIsBroken` reproduz a linha fundida `httpx>=0.27fastapi>=0.115`;
`TestNewBehaviorIsWellFormed` valida a concatenação com newline sobre os
arquivos reais; `TestCiPipAuditUsesSafeAssembly`/`...StructureYaml` garantem que
o `ci.yml` mantém a separação por newline antes do `sort -u`. Novos alvo
`make test-ci-audit` (incluído no `make test`). Resultados: 9 passed/2 skipped
sem PyYAML; 11 passed com PyYAML; com o comando antigo (revertido), 4 testes do
guard falham. Commit local separado; **sem push/PR**.

## Checks preservados

Os 14 checks de `ci.yml` não foram renomeados/removidos; `pr-policy` é o +15º.

## Para ativação na proteção de branches (fora desta branch)

Após merge em `develop` e promoção `develop` → `main` (workflow na **branch
padrão**), abrir uma PR piloto `pre-develop/*` → `develop` confirmando o check
verde e só então adicionar `pr-policy` à lista de *required checks* de `develop`
e `main` na proteção de branches (não feito aqui, por escopo).

## Pendências / limitações

- Proteção remota não foi alterada (requisito). `pr-policy` precisa do
  **bootstrap** na **branch padrão `main`** (merge em develop → promoção
  develop→main → PR piloto verde) antes de entrar na lista de required checks;
  ver `docs/ci-cd.md`.
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