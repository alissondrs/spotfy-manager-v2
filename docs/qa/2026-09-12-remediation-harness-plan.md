# Plano de correção sincronizada — Backend, Frontend e QA

Data: 2026-09-12  
Origem: `docs/qa/2026-09-12-full-qa-pass.md`  
Estado inicial: **NO-GO**, 38 testes aprovados e 6 falhando por cinco grupos de defeitos.

## 1. Objetivo

Corrigir os cinco grupos de defeitos reproduzidos, aprimorar os contratos relacionados e conduzir Backend, Frontend e QA em um único ciclo rastreável do harness, sem relaxar ou remover testes.

A entrega termina somente quando:

- todas as regressões existentes estiverem verdes;
- os fluxos críticos tiverem caso feliz, inválido, borda e ownership;
- confirmação repetida e concorrente de dry-run estiver bloqueada no servidor;
- decisões BPM forem conservadoras, determinísticas e explicáveis;
- erros de upload e autenticação forem estáveis e acionáveis na UI;
- suíte completa, sintaxe, compose e revisão final estiverem verdes;
- documentação e critérios de aceite refletirem o comportamento implementado.

## 2. Pré-condição obrigatória: higiene de versionamento

O Git atual tem raiz em `/Users/alisson/labs-env/projects`, possui apenas um commit de QA e mostra quase todo o código de `spotfy-manager-v2` como não rastreado. Isso impede diff confiável, divisão segura de trabalho e revisão por papel.

Decisão registrada pelo responsável: **usar repositório dedicado em `spotfy-manager-v2`**. Essa fronteira isola o projeto e permite commits pequenos por slice sem misturar os demais projetos. A raiz Git atual em `projects/` deverá ser migrada com cuidado, preservando o commit de QA e sem incluir `.env`, bancos, downloads, caches ou outros projetos.

Gate H0:

- raiz Git decidida;
- baseline do projeto rastreado;
- working tree do projeto compreensível;
- alterações de QA preservadas;
- nenhuma implementação começa antes desse gate.

## 3. Política de coordenação do harness

### Papéis

- **Backend Engineer:** altera contratos, regras, persistência e APIs; não modifica expectativas de regressão para fazê-las passar.
- **QA Specialist:** mantém os testes vermelhos como contrato, acrescenta reprodução de concorrência/API e executa os gates; não altera produção.
- **Frontend Engineer:** ajusta mensagens e estados da UI depois que os códigos de erro e payloads forem congelados; não implementa segurança apenas no cliente.
- **Review & QA:** revisa arquitetura, concorrência, segurança e ausência de regressões ao final de cada slice P1.
- **Documentation Specialist:** atualiza os documentos somente após o comportamento estar comprovado.
- **Coordenador:** serializa mudanças em arquivos compartilhados, registra decisões e aceita/rejeita cada checkpoint.

### Regra de sincronização

Cada slice segue esta ordem:

1. QA apresenta o teste vermelho e a reprodução.
2. Backend congela a decisão de contrato em um mini-handoff.
3. Backend implementa a menor correção.
4. QA executa teste direcionado + suíte mínima de regressão.
5. Frontend ajusta integração/feedback quando houver impacto de contrato.
6. QA valida API/UI e Review & QA fecha o checkpoint.
7. Só então o próximo slice começa.

Não permitir escrita concorrente no mesmo arquivo. Backend é dono de `contracts/` e `services/*/main.py`; Frontend é dono de `services/web/static/`; QA é dono de `tests/` e `docs/qa/` durante execução.

## 4. Decisões técnicas propostas

### D1 — Conflito BPM usa BPM bruto

Para candidatos plausíveis (título e artista acima dos limiares atuais) e com BPM válido:

- calcular conflito usando os valores BPM brutos;
- manter inicialmente o limiar já sugerido pelo código: `max(bpm) - min(bpm) > 12 BPM`;
- se houver conflito, retornar `conflict`, nunca `recommended` ou `rejected`;
- registrar os BPMs conflitantes em `divergences`/`reasons` para explicabilidade.

O limiar de 12 BPM deve ser explicitado em documentação e isolado em constante nomeada para futura calibração.

### D2 — Seleção de candidato é independente da ordem de entrada

Quando não houver conflito:

1. considerar apenas candidatos plausíveis;
2. priorizar candidatos dentro da tolerância;
3. entre eles, ordenar por menor distância absoluta do BPM alvo;
4. desempatar por confiança de título/artista;
5. aplicar preferência de versão já documentada: original explícita, original limpa, versionada;
6. usar ID estável como último desempate para resultado determinístico.

Se nenhum candidato plausível estiver dentro da tolerância, escolher o melhor candidato apenas para explicar `rejected`.

### D3 — Dry-run tem consumo atômico e código próprio

Adicionar ao estado persistido de dry-run:

- `consumed_at TEXT NULL`;
- `job_id TEXT NULL`.

Adicionar erro estável:

- `DRYRUN_ALREADY_USED` → HTTP 409 → “Dry-run já confirmado. Execute novo dry-run para baixar novamente.”

A confirmação deve ocorrer em uma única transação SQLite:

1. validar existência, owner, expiração e seleções;
2. gerar `job_id`;
3. marcar o dry-run como consumido somente se ainda não consumido;
4. criar job e itens;
5. commit único;
6. iniciar a thread apenas após commit.

Duas confirmações concorrentes devem produzir exatamente um 200 e um 409. Desabilitar botão na UI é melhoria de UX, não controle de segurança.

### D4 — JSON de importação valida estrutura antes de iterar

- `playlist`, quando presente, deve ser objeto;
- `tracks` deve ser lista;
- cada item pode seguir os formatos já suportados, mas contêiner inválido retorna `FILE_PARSE_ERROR`;
- nenhum `AttributeError`, 500 ou faixa fabricada a partir de chave de objeto.

### D5 — JWT exige subject não vazio

Após decodificar e validar assinatura/expiração:

- `sub` deve existir;
- deve ser `str`;
- `sub.strip()` não pode ser vazio;
- caso contrário retornar `UNAUTHORIZED`;
- não normalizar um subject inválido para owner vazio.

## 5. Plano por slices

## Slice 0 — Baseline e contrato de trabalho

Responsável: Coordenador + QA.

Entregas:

- resolver Gate H0;
- registrar resultado inicial de `pytest`;
- confirmar os seis testes vermelhos sem alteração;
- mapear arquivos por responsável;
- criar branch/ciclo de correção conforme a topologia Git escolhida.

Gate S0: diff confiável e reprodução idêntica ao relatório de QA.

## Slice 1 — Segurança BPM (P1)

Responsável principal: Backend.  
Pareamento: QA.  
Revisão: Review & QA.

Arquivos esperados:

- `services/bpm-match/main.py`;
- testes BPM em `tests/e2e/test_critical_regressions.py` ou novo módulo unitário de serviço;
- documentação técnica afetada somente após aprovação.

Implementação:

- corrigir `_has_conflict` para comparar BPM bruto;
- separar candidato plausível de score BPM;
- selecionar o melhor candidato de forma independente da ordem;
- tornar limiar de conflito constante nomeada;
- preservar `insufficient` quando não há BPM ou candidato confiável.

QA:

- manter verdes os dois testes atuais de regressão;
- adicionar permutação da ordem dos candidatos;
- adicionar fronteiras de conflito: 12 BPM não conflita, acima de 12 conflita;
- adicionar caso sem BPM e candidato fraco;
- confirmar razões e divergências explicáveis.

Gate S1:

- regressões BPM verdes;
- nenhuma recomendação com conflito/dados insuficientes;
- suíte contract + BPM/e2e verde;
- Review & QA sem P0/P1 aberto nesse slice.

## Slice 2 — Confirmação idempotente/atômica de download (P1)

Responsável principal: Backend.  
Pareamento: QA.  
Integração: Frontend.

Arquivos esperados:

- `contracts/spotfy_contracts/errors.py`;
- `contracts/spotfy_contracts/store.py` se for necessário expor transação segura;
- `services/library/main.py`;
- `services/web/static/app.js`;
- testes contract/e2e correspondentes.

Implementação Backend:

- migração aditiva e idempotente de `dryruns`;
- consumo e criação de job na mesma transação;
- erro `DRYRUN_ALREADY_USED`;
- thread iniciada somente após persistência confirmada;
- preservar checks de owner, expiração, manifest e `ALLOW_DOWNLOADS`.

Implementação Frontend:

- marcar confirmação como em andamento imediatamente;
- impedir duplo clique enquanto a chamada está pendente;
- mapear `DRYRUN_ALREADY_USED` para mensagem acionável;
- limpar dry-run consumido e oferecer “Executar novo dry-run”;
- manter o servidor como autoridade.

QA:

- manter o teste serial de uso único;
- adicionar teste concorrente com barreira: duas chamadas simultâneas, um job apenas;
- confirmar que owners diferentes continuam recebendo 403;
- confirmar expiração 409 e inexistência 404;
- confirmar persistência de `consumed_at/job_id` após reabertura do store;
- verificar que uma falha antes do commit não inicia thread órfã.

Gate S2:

- exatamente um job por dry-run;
- serial e concorrente verdes;
- fluxo BFF/UI preservado;
- nenhum download duplicado no diretório temporário.

## Slice 3 — Parsing seguro e JWT obrigatório (P2)

Responsável principal: Backend.  
Pareamento: QA.  
Integração: Frontend para mensagens.

Arquivos esperados:

- `contracts/spotfy_contracts/files.py`;
- `contracts/spotfy_contracts/auth.py`;
- `services/web/static/app.js`, apenas se o mapa atual não cobrir os códigos;
- testes contract e API.

Implementação:

- validar contêineres JSON antes de acessar/iterar;
- lançar `FILE_PARSE_ERROR` para forma inválida;
- validar `sub` após `decode_token` ou antes de retornar usuário;
- manter códigos HTTP e mensagens estáveis.

QA:

- manter os três testes vermelhos atuais;
- adicionar testes API para `/import` com ambas as formas inválidas;
- garantir 422 controlado, nunca 500;
- testar `sub` ausente, vazio, whitespace e não-string;
- confirmar JWT válido, expirado e segredo incorreto;
- confirmar que nenhum recurso pode ser criado com owner vazio.

Frontend:

- exibir erro de arquivo inválido sem perder a tela/import anterior;
- em `UNAUTHORIZED`, preservar o comportamento de sessão expirada já documentado.

Gate S3:

- erros estáveis na API e UI;
- nenhuma faixa fabricada;
- nenhum owner vazio;
- suites contract, import e auth verdes.

## Slice 4 — Aprimoramentos de frontend e experiência

Responsável principal: Frontend.  
Contrato: Backend congelado.  
Validação: QA.

Entregas:

- estado de loading/disabled em confirmação;
- mensagens específicas para dry-run usado, expirado e inexistente;
- badge/explicação clara para conflito BPM;
- mensagem distinta para catálogo indisponível;
- upload inválido com feedback acionável;
- nenhuma regra crítica mantida apenas em JavaScript/localStorage.

QA:

- smoke de API via BFF;
- validação de marcadores JavaScript;
- browser smoke dos estados de erro, loading e recuperação;
- confirmação de que refresh não reabilita dry-run consumido.

Gate S4: frontend alinhado aos contratos e sem regressão do fluxo principal.

## Slice 5 — Regressão total, revisão e documentação

Responsáveis: QA Specialist → Review & QA → Documentation Specialist.

Comandos mínimos:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/contract
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/e2e
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
node --check services/web/static/app.js
docker compose config --quiet
```

Validações adicionais:

- smoke de health/metrics;
- diff por slice e diff acumulado;
- nenhuma remoção, skip, xfail ou relaxamento de teste;
- nenhuma credencial real;
- documentação de arquitetura, backend handoff, frontend handoff, operações e QA atualizada;
- relatório final com comandos, resultados e riscos residuais.

Gate S5 / Release:

- suíte completa verde e sem warnings relevantes;
- todos os P1 fechados;
- P2 fechados ou formalmente aceitos pelo responsável;
- browser smoke verde;
- compose válido;
- decisão QA: **APROVADO** ou **APROVADO COM RESSALVAS**, nunca NO-GO.

## 6. Matriz de responsabilidade

| Item | Backend | Frontend | QA | Review/Docs |
|---|---|---|---|---|
| Conflito BPM bruto | Implementa | Exibe explicação | Contrato/regressão | Revisa política/documenta |
| Seleção por compatibilidade | Implementa | Sem regra duplicada | Ordem/bordas | Revisa determinismo |
| Dry-run single-use | Transação/API | Loading/erro/CTA | Serial+concorrente | Revisa corrida/documenta |
| JSON inválido | Validação/erro | Feedback | Contract+API | Revisa segurança |
| JWT sem `sub` | Validação auth | Sessão expirada | Contract+API/ownership | Revisa segurança |

## 7. Estratégia de commits sugerida

Depois de resolver H0, usar commits pequenos e revisáveis:

1. `fix(bpm-match): enforce conflict and compatible candidate selection`
2. `fix(library): consume dry-runs atomically`
3. `fix(auth-import): reject invalid subjects and JSON containers`
4. `fix(web): handle consumed dry-runs and actionable errors`
5. `test(qa): add concurrency and API regressions`
6. `docs: align BPM and download safety contracts`

Não misturar baseline, produção, testes e documentação não relacionados em um único commit.

## 8. Riscos e controles

- **Mudança de política BPM:** controlar com testes de borda e documentação do limiar.
- **Race condition SQLite:** exigir transação e teste concorrente, não apenas check-then-update.
- **Migração de bancos existentes:** somente `ALTER TABLE` aditivo/idempotente; testar schema antigo.
- **Job órfão:** thread apenas depois do commit; simular falha de persistência.
- **UI aparentar segurança sem backend:** teste deve chamar API diretamente e repetir confirmação.
- **Git misturar projetos:** bloquear execução no H0 até a fronteira estar decidida.

## 9. Ordem recomendada de execução

`H0 → S1 BPM → S2 dry-run → S3 parsing/JWT → S4 frontend → S5 QA/review/docs`

Essa ordem fecha primeiro os P1 que podem produzir recomendação insegura ou download duplicado, estabiliza os P2 de entrada/autenticação e só depois consolida a experiência frontend e a documentação.
