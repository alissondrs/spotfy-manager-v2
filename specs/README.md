# Specs — spotfy-manager-v2

Mudanças versionadas por feature, bug ou assessment (fluxo Spec Kit).

## Estrutura

```text
specs/<id>-<descricao>/
  ├── spec.md      # problema, escopo, critérios de aceite
  ├── plan.md      # plano de implementação, riscos, abordagem
  └── tasks.md     # tarefas concretas e mensuráveis
```

## Convenção de id

`<data>-<tipo>-<descricao>`, ex.: `2026-09-melhorar-bpm-conflict`, `2026-10-fix-download-timeout`.

## Fluxo

1. **specify/clarify** — definir problema, escopo e critérios de aceite
2. **plan** — planejar abordagem e riscos
3. **tasks** — decompor em tarefas
4. **analyze/implement** — validar premissas e implementar respeitando gates
5. **converge** — revisar e fechar com evidências

Fluxo reduzido (bugs pequenos, mudança documental): `spec mínima → implement → teste/revisão leve`,
decisão explícita no próprio `spec.md`.