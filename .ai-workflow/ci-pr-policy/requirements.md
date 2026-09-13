# Requisitos — Política de fluxo pré-develop (CI + `pr-policy`)

- **Origem do trabalho**: branch `pre-develop/ci-pr-policy`, criada de
  `origin/develop` (SHA `6ed2d5c09639a86c57c387ed9a7eb077e12798d1`).
- **Não alterar** proteção remota de branches, não fazer push, não abrir PR,
  não habilitar auto-merge e não mudar código de produção (~serviços/contratos).

## Regras de política (a serem implementadas)

1. Toda PR para `develop` deve ter head `pre-develop/*`.
2. Toda PR para `main` deve vir apenas de `develop`.

## Infraestrutura

3. Check estável nomeado `pr-policy`, em workflow próprio
   (`.github/workflows/pr-policy.yml`).
4. O workflow deve ser seguro para o evento `pull_request`: mínimo de
   permissões (`contents: read`), sem executar código do PR com privilégios
   elevados, sem acesso a secrets elevados e fail-closed.
5. **Preservar os 14 nomes de checks atuais** de `ci.yml`
   (`test-contract`, `test-e2e`, `lint`, `pip-audit`, `compose-validate`,
   `secret-scan` + `docker-build-<serviço>` × 8).

## Documentação

6. Atualizar `docs/ci-cd.md` e `AGENTS.md`: origem em `origin/develop`,
   worktree isolada, PR draft, CI obrigatório e squash/auto-merge só após checks.

## Validação local

7. Adicionar validação local simples e testável, **sem dependências novas**
   (stdlib), com casos válidos e inválidos + parsing do YAML de política.
8. Testar, corrigir falhas e registrar em `verification.log`.

## Entrega

9. Criar `.ai-workflow/ci-pr-policy/{requirements.md, plan.md, implementation.md,
   verification.log, status.md}` (sem segredos).
10. Commit local claro e resumo final com arquivos, testes, hash e limitações.