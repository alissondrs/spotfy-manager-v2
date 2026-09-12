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

## Fluxo de trabalho

1. Identifique o serviço/contrato alvo antes de editar.
2. Implemente a menor mudança coerente com o serviço.
3. Valide: `make test-contract` e `make test-e2e` (fluxo completo).
4. Garanta `/health` e `/metrics` e logs úteis nos serviços afetados.
5. Atualize documentação e, se mudar env/portas, o `.env.example`.

## Comandos úteis

```bash
make setup                # venv + contracts + deps
make run-<serviço>        # ex.: make run-bpm-match
make test                 # roda tudo
make up / make down       # docker compose
```