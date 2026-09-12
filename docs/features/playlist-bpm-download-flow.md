# Feature Spec — Fluxo integrado Spotify → BPM Match → Download

## 1. Resumo executivo

O `spotfy-manager-v2` já possui oito microsserviços FastAPI, UI estática, autenticação
local, integração Spotify, catálogo Tidal/HiFi, BPM Match, biblioteca/downloads e
relatórios. A dor principal é um fluxo web fragmentado: login pouco confiável, playlists
não disponíveis de forma consistente após autenticar e dificuldade para concluir o fluxo
playlist → BPM Match → download.

Esta feature transforma o navegador em um fluxo único de trabalho:

> Preparar um set musical inteiro em um único fluxo seguro: autenticar, escolher uma
> playlist, validar o BPM Match e baixar somente as faixas confirmadas.

### Problema

- **Login**: a sessão depende de token JWT guardado no `localStorage` sem estado útil de
  pós-login; expiração não comunica uma mensagem compreensível.
- **Playlists**: o token do Spotify vive em memória no BFF (`_spotify_token`), perdido em
  restart; a UI exige cliques extras ("Carregar playlists") e usa `localStorage` como
  fallback, dificultando recuperação e consistência.
- **BPM Match**: `/analyze` recebe as faixas copiadas no corpo, sem referência estável à
  playlist originária; cada clique gera análise duplicada da mesma playlist; falhas do
  catálogo se confundem com ausência real de dados.
- **Download**: o botão de confirmação envia seleções reenviadas pelo cliente, sem vínculo
  com o dry-run; download não mostra progresso por item; a UI não conclui o fluxo real.

### Proposta de valor

Um DJ/produtor prepara um set de forma segura e auditável: autentica, escolhe a playlist,
vê recomendações explicáveis, decide o que baixar e acompanha cada arquivo com status e
erros rastreáveis, sem risco de recomendação incorreta ou download não confirmado.

## 2. Usuário e personas

Principal: **DJ/Produtor musical** que monta sets, verifica compatibilidade de BPM e baixa
faixas para a biblioteca local.

### User stories

| # | Como | Eu quero | Para |
|---|------|----------|------|
| US-01 | DJ | entrar com usuário/senha locais | proteger acesso aos meus dados e downloads |
| US-02 | DJ | chegar após o login a uma tela útil | saber o que posso fazer e ver minhas playlists/imports direto |
| US-03 | DJ | ver uma mensagem clara quando a sessão expira | não ficar preso em erro silencioso |
| US-04 | DJ | conectar o Spotify e listar minhas playlists | escolher a fonte do set |
| US-05 | DJ | que minhas playlists/imports permaneçam disponíveis após refresh/relogin | continuar o trabalho sem recomeçar |
| US-06 | DJ | selecionar uma playlist e mandar para o BPM Match sem copiar faixas | reduzir erro e fricção |
| US-07 | DJ | reutilizar a análise já existente da mesma playlist | não duplicar trabalho |
| US-08 | DJ | ver BPM, confiança, divergências e motivo de cada decisão | confiar na recomendação |
| US-09 | DJ | escolher explicitamente quais faixas recomendadas vão para a biblioteca | baixar só o que eu quero |
| US-10 | DJ | ver faltantes vs biblioteca, dry-run com qualidade e motivos | decidir antes de baixar |
| US-11 | DJ | confirmar o download de forma explícita e acompanhar progresso por lote e por item | saber o que aconteceu, com erros rastreáveis |
| US-12 | DJ | entender quando o problema é Spotify/Tidal (não "não tem dados") | agir corretamente |

## 3. Critérios de aceite (rastreáveis)

### Autenticação e sessão
- CA-01: Usuário autenticado chega a um estado útil sem recarregar manualmente (dashboard
  com tickets/playlists).
- CA-02: Sessão expirada redireciona para login com mensagem compreensível, sem perder o
  contexto do tab.
- CA-03: Token Spotify conectado → playlists carregam automaticamente na aba Importar.

### Playlists e fontes
- CA-04: Playlists Spotify listadas para o usuário autenticado (`GET /api/playlists`).
- CA-05: Importações (Spotify e arquivo) sobrevivem a refresh/relogin via backend
  (`/api/synced`, `/api/imports`) — `localStorage` deixa de ser fonte primária.
- CA-06: A referência de origem (ex.: `spotify:<id>` ou `file:<import_id>`) é persistida e
  recuperável.

### BPM Match
- CA-07: `/analyze` aceita uma referência estável de playlist e resolve as faixas no
  serviço (sem reenviar faixas quebradas).
- CA-08: Analisar a mesma playlist (mesmo usuário + referência + alvo) retorna a análise
  existente marcada como reaproveitada; `force=true` cria análise nova.
- CA-09: Cada item mostra decisão, confiança, motivo e divergências.
- CA-10: Falha do catálogo (indisponibilidade) é distinguível de ausência real de dados
  (`dependency_error` por item; flag de catálogo indisponível na análise).

### Biblioteca e download
- CA-11: Nenhuma faixa é pré-selecionada para download; o usuário marca explicitamente as
  recommended que entram no compare/dry-run.
- CA-12: `POST /library/dryrun` retorna `dry_run_id` persistido, pertencente ao usuário e
  com expiração.
- CA-13: `POST /library/download` só executa com `dry_run_id` válido, não expirado e do
  mesmo usuário; retorna job com progresso por lote e por item.
- CA-14: Confirmações repetidas/alteradas são bloqueadas; expiração exige novo dry-run.
- CA-15: A UI mostra linha por item (baixado/falha/erro) via polling do job.

### Erros e segurança
- CA-16: Erros Spotify/Tidal aparecem como erro acionável (ex.: "Conecte o Spotify"),
  nunca como lista vazia enganosa.
- CA-17: Nenhum endpoint de domínio acessível sem JWT; ownership respeitado (401/403).
- CA-18: Bootstrap `/_debug/bootstrap` continua restrito a `APP_ENV=development` +
  flag explícita.

## 4. Escopo MVP e backlog

### Dentro do MVP
- Autenticação local confiável com recuperação de sessão explícita e mensagens por erro.
- Listagem/carregamento/seleção de playlists Spotify pós-login, com estado recuperável.
- Referência estável de playlist no BPM Match + reuso de análise (mesma playlist/alvo).
- Revisão/aprovação/rejeição com seleção explícita de recommended para biblioteca.
- Compare → dry-run (`dry_run_id`) → confirmação → download via job com progresso
  por lote + por item.
- Relatório mínimo por análise/exportação já existente preservado.

### Fora do MVP (backlog)
- OAuth completo do Spotify com refresh persistente (hoje token de sessão via header).
- Multiusuário avançado, papéis e permissões administrativas.
- Filas assíncernas/worker dedicado para lotes grandes (job em thread in-process).
- Aplicativo mobile (ver seção Mobile Readiness).
- Relatórios e observabilidade avançados.

## 5. Impacto nos serviços atuais

| Serviço | Mudança |
| ------- | ------- |
| `web` (BFF) | Persistir token Spotify por usuário (SQLite) em vez de memória global; claim do token após OAuth; proxy com `X-Request-Id` e passthrough de headers; estados de sessão na UI |
| `identity` | Sem mudança de contrato; reutilizado (login/me) |
| `playlist` | Expor importações com referência estável `spotify:<id>`; garantir recuperação por usuário |
| `fileimport` | Expor importações com referência estável `file:<import_id>` (já recuperável) |
| `catalog` | Sem mudança de contrato (falhas já possuem códigos `TIDAL_*`/`CATALOG_UNREACHABLE`) |
| `bpm-match` | `/analyze` aceita `reference` e resolve faixas; reuso de análise; `dependency_error` por item + flag de catálogo indisponível |
| `library` | `POST /dryrun` retorna `dry_run_id`; `POST /download` consome `dry_run_id` (vínculo+owner+expiração) e vira job com progresso por item; `GET /jobs/{id}` devolve itens |
| `report` | Sem mudança funcional; continua consumindo `GET /analyses/{id}` |

## 6. Dependências

- **Auth**: JWT HS256 compartilhado (`JWT_SECRET`, min. 32 chars); PBKDF2 nas senhas.
- **Spotify**: `SPOTIFY_CLIENT_ID/SECRET` (OAuth) ou `SPOTIFY_ACCESS_TOKEN` direto;
  token propagado via `X-Spotify-Token` do BFF para o `playlist`.
- **Tidal/HiFi**: `HIFI_API_URL` + token Tidal; `TIDAL_MOCK=1` para demo/testes.
- **Downloads**: `ALLOW_DOWNLOADS=1` para execução real; `DOWNLOAD_DIR` para persistência.
- **Persistência**: SQLite por serviço (`DATA_DIR`).

## 7. Riscos e decisões de compatibilidade

| Risco | Controle |
| ----- | -------- |
| Token Spotify perdido em restart | Persistir por usuário no BFF; `localStorage` deixa de ser fonte primária |
| Análise duplicada da mesma playlist | Chave de reuso `owner+reference+target_bpm`; `force=true` para reanalisar |
| Falha do catálogo parecer ausência de dados | `dependency_error` por item + flag de catálogo indisponível na análise |
| Download não confirmado/duplicado | `dry_run_id` persistido, ownership + expiração (15 min) no servidor |
| Download lento parecer travado | Job com `progress` e itens atualizados por item; UI faz polling |
| Recomendação incorreta | Preservado: `recommended` só com dados suficientes/coerentes; revisão humana antes do download |

## 8. Mobile Readiness

Sem implementar mobile nesta iteração, a UI permanece **desktop-first** (tabelas e
tiles). Requisitos para adoção futura em telas pequenas:

- Layout por cards com gradiente de tabelas (já há `.compact` e `.grid` reutilizáveis).
- Estados de loading/vazio/erro sem dependência de hover (já respeitados).
- Fluxo de confirmação com um dedo (botões grandes, sem drag-and-drop).
- APIs stateless e reutilizáveis por clientes futuros (nenhuma regra crítica em JS/localStorage).
- Tokens em `localStorage` serão substituídos por cookie/secao segura se houver cliente mobile.

## 9. Definição de pronto

- Todos os critérios CA-01..CA-18 verificados por testes de contrato/e2e ou revisão manual.
- `make test` verde (contract + e2e), `compileall` e `node --check` sem erros.
- `docker compose config` válido e smoke de boot documentado.
- README, `context.md`, `docs/architecture.md` e `docs/operations.md` atualizados.