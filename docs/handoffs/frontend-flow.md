# Handoff de Frontend — Fluxo Spotify → BPM Match → Download

Leia junto com `context.md`, `services/web/static/app.js`, `index.html`, o PRD
(`docs/features/playlist-bpm-download-flow.md`), o protótipo
(`docs/features/prototypes/playlist-bpm-download-flow.html`) e o handoff de backend
(`docs/handoffs/backend-flow.md`).

## 1. Rotas / superfícies da UI (abas atuais, sem roteador)

| Aba | Estado atual | Ajuste |
| --- | ------------ | ------ |
| Login / Registro | funcional | mensagem para sessão expirada; loading no botão; erro por código |
| Dashboard | tiles + fluxo | estado útil pós-login; vazio com CTA para Importar |
| Importar | manual ("Carregar playlists") | carregar playlists automático pós-conexão; mensagens por falha Spotify |
| BPM Match | origem + analisar | origem = referência estável; reuso de análise com banner; seleção de recommended |
| Biblioteca | compare/dryrun/confirm | dry_run_id; progresso por lote + por item via polling |
| Relatórios | list + download | sem mudança funcional |

## 2. Estado global mínimo (em `state` de app.js)

```js
state = {
  token, user,
  analysis: null,            // análise atual (com items, summary)
  sourceOptions: [],         // [{key:"spotify:<id>"|"file:<import_id>", ...}]
  selectedOrdinals: Set(),   // ordinais de faixas recommended escolhidas → library
  lastDryRun: null,          // {dry_run_id, expires_at, selections, consumed}
  activeJob: null,           // {job_id, poll}
}
```
- **Fonte primária de fontes**: `/api/synced` + `/api/imports`. `localStorage`
  (`bpm_sources`) vira fallback legado apenas quando backend vazio e pode ser removido.

## 3. Autenticação e sessão

- `api()`: em `401`, guarda mensagem (`msg`) e chama `showLogin(msg)`.
- `showLogin(message)` preenche `#login-error` com texto explicativo:
  - expirada: "Sua sessão expirou. Entre novamente — seus dados continuam salvos."
  - `UNAUTHORIZED` em login: "Usuário ou senha inválidos."
- Botão de login tem estado `loading` (disabled + spinner) enquanto a chamada roda.
- Pós-login: `showApp()` mostra dashboard (renderDashboard) e, se `spotify connected`,
  dispara carregamento de playlists em background.

## 4. Importar (playlists)

- Ao entrar na aba Importar:
  1. `GET /api/auth/spotify/status`.
  2. Se conectado → `GET /api/playlists` (auto) → popular `#playlist-select` com
     `{id}` como valor de referência (`spotify:<id>`); mensagem "nenhuma playlist" quando vazio.
  3. Se não conectado → botão "Conectar Spotify" destacado + mensagem acionável
     (`SPOTIFY_NOT_CONNECTED`/`SPOTIFY_TOKEN_EXPIRED`).
- Conectar via OAuth: abre popup, polling de `status` (já existe) e, ao confirmar,
  chama `POST /api/auth/spotify/claim` para vincular o token pendente ao usuário.
- Importar selecionada: `POST /api/import/spotify {reference}`; erro não vira lista
  vazia silenciosa — exibe a mensagem do backend.
- Depois de importar, atualizar `refreshSourceSelect()` (já existe) e forçar recarga.

## 5. BPM Match

- Origem `#source-select` usa `state.sourceOptions` (chaves `spotify:<id>` / `file:<import_id>`).
- Envio: `POST /api/analyze` com `reference` (chave da origem sem prefixo quando playlist)
  e `tracks: []` quando a origem for resolvível (playlist/file) — o backend resolve.
  Para origem de arquivo, enviar `reference: file:<import_id>`.
- Resposta: se `reused === true`, mostrar banner "Análise reutilizada de {created_at} ·
  [Reanalisar]" (Reanalisar chama com `force: true` e não reutiliza).
- Tabela de resultados (mesma), com coluna extra **Sel.** (checkbox) habilitado apenas
  para `decision === "recommended"` e `dependency_error` vazio:
  - **nenhuma faixa pré-selecionada**.
  - linha com `dependency_error` usa badge "DEP. INDISPONÍVEL" e checkbox desabilitado.
  - linha `conflict`/`insufficient`/`rejected`: checkbox desabilitado (não vão para library).
- `selectedOrdinals` atualiza o rótulo do botão "Ir para biblioteca (N selecionadas)".
- Só aprovar/rejeitar itens ainda em `pending` (revisão); manter comportamento atual.

## 6. Biblioteca e download

- "Comparar faltantes": usa `selectedOrdinals` (da última análise); se vazio → mensagem
  "Marque ao menos uma faixa recomendada antes de comparar."
  - Envia `tracks` das recommended selecionadas → `POST /api/library/compare`
    → `missing` → `POST /api/library/dryrun`.
- Dry-run: guarda `lastDryRun = {dry_run_id, expires_at, selections}` no estado/componente.
- Confirmação: `POST /api/library/download {dry_run_id}` (não reenviar selections).
  - Antes: valida `consumed` e expiração local (15 min) com feedback claro.
  - Resposta `{job_id, status:"running"}` → `activeJob = {job_id}`.
- **Progresso por lote + por item**: polling de `GET /api/library/jobs/{job_id}` a cada 1.5s:
  - barra de progresso (job.progress × 100%);
  - tabela `#download-result` com linha por item: status (`downloaded`/`failed`/`downloading`),
    filename, erro;
  - ao `completed` → parar polling, mostrar resumo "X baixadas · Y falhas", `renderLibrary()`.
- Erro ao confirmar (`DRYRUN_EXPIRED`/`DRYRUN_NOT_FOUND`): limpar `lastDryRun`, feedback
  "Dry-run expirado. Rode a comparação novamente." e desabilitar botão.

## 7. Mensagens por código de erro (mapa mínimo)

| Código | Mensagem na UI |
| ------ | -------------- |
| `UNAUTHORIZED` (login) | Usuário ou senha inválidos. |
| `UNAUTHORIZED` (sessão) | Sessão expirada — entre novamente. |
| `SPOTIFY_NOT_CONNECTED` / `SPOTIFY_TOKEN_EXPIRED` | Conecte o Spotify para ver suas playlists. |
| `PLAYLIST_NOT_FOUND` / `PLAYLIST_INACCESSIBLE` | Playlist indisponível. Escolha outra. |
| `DRYRUN_EXPIRED` / `DRYRUN_NOT_FOUND` | Dry-run expirado. Rode a comparação novamente. |
| `DOWNLOAD_JOB_CONFIRM_REQUIRED` | Nenhuma faixa apta para download. Verifique ALLOW_DOWNLOADS. |
| `TIDAL_UPSTREAM_ERROR` / `CATALOG_UNREACHABLE` | Catálogo indisponível — tente novamente. |
| `REPORT_FORMAT_UNSUPPORTED` | Formato de relatório não suportado. |

## 8. Estados visuais (toda tela)

- **loading**: botão/guia com spinner; nunca tela branca.
- **vazio**: mensagem + CTA (ex.: dashboard vazio → "Importar").
- **erro recuperável**: banner com mensagem do mapa; ação para repetir.
- **sessão expirada**: tela de login com mensagem.
- **sucesso parcial**: resumo do job com itens ok/falha e link para relatório de falhas.

## 9. Testes de interface / regressão

- `tests/e2e/test_flow.py` adiciona cobertura via API (contrato do frontend):
  - `test_web_dryrun_returns_id_and_download_job_pollable`
  - `test_web_analyze_reference_reuse` (reusa via BFF)
  - `test_web_spotify_token_per_user_isolation`
- Smoke: `GET /` e `/static/app.js` retornam 200 e contêm marcadores novos
  (`claim`, `dry_run_id`, `catalog_unavailable`).

## 10. Não fazer

- Não introduzir framework/bundler.
- Não mover regra de negócio crítica para `localStorage`.
- Não pré-selecionar faixas recommended.
- Não remover `refreshImportOptions`/`renderLibrary` existentes sem atualizar os testes e2e.