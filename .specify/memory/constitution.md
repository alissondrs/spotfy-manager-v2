# Constitution do Projeto — spotfy-manager-v2 (BPM Match)

Este arquivo define os princípios específicos do projeto, derivados da base
`projects/harness-kit/constitution-base.md`. Ausências herdam da base; nada aqui
contradiz as políticas de segurança do workspace.

## Autoridade

```text
security/policies → AGENTS.md raiz → harness-kit → constitution do projeto → spec → plan → tasks → agente → código
```

## Princípios deste projeto

- **Segurança primeiro no mote**: decisões `recommended` exigem BPM disponível e
  coerente; caso contrário `insufficient`/`conflict`. Nunca recomendar com dados
  insuficientes.
- **Contratos como fonte**: mudanças de schema/payload entre serviços passam por
  `spotfy_contracts` (`contracts/`) e atualizam docs.
- **Download seguro**: manter dry-run → seleções → confirmação (`ALLOW_DOWNLOADS=1`).
- **Decisões rastreáveis**: cada passo deve ser explicável e registrado.

## Regras adicionais

- Toda mudança de contrato exige spec (`specs/<id>/spec.md`) e atualização de
  `contracts/`, `docs/` e `.env.example` quando aplicável.
- Serviços devem expor `/health` e `/metrics`.
- Validação: `make test-contract`, `make test-e2e` e `make test`.

## Estrutura de specs

```text
.specify/memory/constitution.md   # este arquivo
specs/<id>-<descricao>/            # artefatos da mudança
  ├── spec.md                      # problema, escopo, critérios de aceite
  ├── plan.md                      # plano de implementação
  └── tasks.md                     # tarefas concretas
```

Fluxo completo: `constitution → specify → clarify → plan → tasks → analyze → implement → converge`.
Fluxo reduzido (bugs pequenos, mudança documental): `spec mínima → implement → teste/revisão leve`.