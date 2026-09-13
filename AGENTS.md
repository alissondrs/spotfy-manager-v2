# AGENTS.md — spotfy-manager-v2 (BPM Match)

## Contexto do Projeto

Gerente de biblioteca musical local-first com BPM Match: importação de playlists
(Spotify/arquivo), análise de compatibilidade por BPM, revisão na UI, exportação de
relatórios, comparação com a biblioteca local, download com tagging e integração
preservada com hifi-api/Tidal e Spotify. Divididos em 8 microsserviços FastAPI + um
pacote de contratos compartilhado (`contracts/`). Funciona local e em docker compose.

## Regras

- **Decisões do BPM Match são explicáveis e conservadoras**: nunca recomendar com dados
  insuficientes/conflitantes (`insufficient`/`conflict`). Preserve o fluxo de confiança.
- **Contratos em `contracts/`**: mudanças de schema/payload entre serviços devem atualizar
  `spotfy_contracts` (e reinstalar com `pip install -e ./contracts`).
- **Erros**: use `raise_error(código, msg)` → `DomainError` com HTTP status adequado.
- **Envs**: tudo por variável de ambiente (ver `.env.example`). Não versionar `.env`,
  segredos (`JWT_SECRET`, tokens) em arquivos versionados.
- **Download seguro**: manter a porta de confirmação (dry-run → seleções → `/download`)
  e `ALLOW_DOWNLOADS=1` para execução real.
- **URLs entre serviços** por `_URL` env (com default docker compose) — não fixar portas
  em código de chamada.
- Manter local simples e rastreável; evitar infraestrutura desnecessária.
- Atualizar `README.md`, `docs/` e `context.md` quando o fluxo mudar.
- **Fluxo pré-develop (obrigatório)**: trabalhar em `pre-develop/*` com **origem em
  `origin/develop`** e **worktree isolada** (`git worktree add -b pre-develop/<assunto>`
  a partir de `origin/develop`). Não commitar direto em `develop`.
- **PR draft + CI obrigatório**: o PR para `develop` deve ser aberto em **draft** e
  permanecer draft até todos os checks obrigatórios (`ci.yml` + `pr-policy`) estarem
  verdes.
- **Squash e auto-merge só após checks**: merge via **squash**; **auto-merge apenas
  depois dos checks verdes** — nunca habilitar auto-merge que ignore checks.
- **Política de PRs**: PR para `develop` exige head `pre-develop/*`; PR para `main`
  só pode vir de `develop`. Verificado pelo check `pr-policy`, que roda em
  `pull_request_target` com **lógica inline** (`permissions: contents: read`, sem
  checkout, sem pip e sem executar código do head do PR) espelhando
  `.github/pr-policy.yml`; `scripts/test_pr_policy.py` garante a consistência.
  `pull_request_target` usa o workflow da **branch padrão (`main`)** — por isso,
  antes de tornar `pr-policy` required, o bootstrap exige o workflow presente em
  `main` (merge em develop → promoção `develop`→`main` → PR piloto verde → só
  então required checks de `develop`/`main`; ver `docs/ci-cd.md`).

## Fluxo de trabalho

1. Identifique o serviço/contrato alvo antes de editar.
2. Implemente a menor mudança coerente com o serviço.
3. Valide: `make test-contract`, `make test-e2e` e `make test-policy` (política pr-policy).
4. Garanta `/health` e `/metrics` e logs úteis nos serviços afetados.
5. Atualize documentação e, se mudar env/portas, o `.env.example`.

## Comandos úteis

```bash
make setup                # venv + contracts + deps
make run-<serviço>        # ex.: make run-bpm-match
make test                 # roda tudo (contract + e2e + policy)
make test-policy          # parser + consistência/segurança do workflow inline
make up / make down       # docker compose
python3 scripts/validate_pr_policy.py --head <branch> --base <branch>   # valida PR localmente
# Sem .venv local, use uma venv existente com pytest:
make VENV=/caminho/da/venv test-policy
```