# Handoff de Frontend — Fluxo Spotify → BPM Match → Download

Leia junto com `context.md`, `services/web/static/app.js`, `index.html`, o PRD
(`docs/features/playlist-bpm-download-flow.md`), o protótipo
(`docs/features/prototypes/playlist-bpm-download-flow.html`) e o handoff de backend
(`docs/handoffs/backend-flow.md`).

## 1. Rotas / superfícies da UI (abas atuais, sem roteador)

| Aba | Estado atual | Ajuste |
| --- | ------------ | ------ |
| Sessão anônima | removida (login/registro) | abre no dashboard direto; badge "Sessão anônima"; botão "Nova sessão" (logout + reload) |
| Dashboard | tiles + fluxo | estado útil pós-sessão; vazio com CTA para Importar |
| Importar | manual ("Carregar playlists") | carregar playlists automático pós-conexão; mensagens por falha Spotify |
| BPM Match | origem + analisar | origem = referência estável; reuso de análise com banner; seleção de recommended |
| Biblioteca | compare/dryrun/confirm | dry_run_id; progresso por lote + por item via polling |
| Relatórios | list + download | sem mudança funcional |

## 2. Estado global mínimo (em `state` de app.js)

```js
state = {
  session: null,             // {owner, csrf_token, ...} de GET /api/session
  analysis: null,            // análise atual (com items, summary)
  sourceOptions: [],         // [{key:"spotify:<id>"|"file:<import_id>", server: bool, ...}]
  selectedOrdinals: Set(),   // ordinais de faixas recommended escolhidas → library
  lastDryRun: null,          // {dry_run_id, expires_at, selections, consumed}
  activeJob: null,           // {job_id, poll}
  spotifyPopup/spotifyPoll/spotifyPopupWatch,  // estado do fluxo de autorização
}
```
- **Sessão**: criada com `GET /api/session` (cookie HttpOnly) no `init()`. Sem JWT nem
  usuário em `localStorage`.
- **Fontes**: fonte primária via `/api/synced` + `/api/imports` (marcadas `server: true`).
  `localStorage` (`bpm_sources`) é fallback legado apenas quando o backend está vazio.

## 3. Sessão anônima e CSRF

- `init()`: `GET /api/session` → `state.session`; se recém-criada (`created`), banner
  informativo ("Nova sessão anônima criada..."). `api()` envia `X-CSRF-Token`
  automaticamente em POST/PUT/PATCH/DELETE.
- Em `401` com erro `SESSION_INVALID`: `api()` recria a sessão (`initSession(true)`)
  e relança erro com aviso — a UI sugere recarregar se algo não funcionar.
- Botão **Nova sessão**: `POST /api/auth/logout` + `location.reload()` (confirma antes,
  pois dados da sessão anterior não são mais acessíveis).
- `GET /api/auth/login` / `register` não existem mais na UI (backend retorna 410).

## 4. Importar (playlists)

- Ao entrar na aba Importar:
  1. `GET /api/auth/spotify/status`.
  2. Se conectado → `GET /api/playlists` (auto) → popular `#playlist-select` com
     `{id}` como valor (o import envia `reference: "spotify:<id>"`); mensagem
     "nenhuma playlist" quando vazio.
  3. Se não conectado → botão **Conectar Spotify** destacado.
- Conectar via OAuth (PKCE, popup):
  1. `GET /api/auth/spotify` → `{url}`.
  2. `window.open(url)` (se bloqueado: link de fallback + polling de status).
  3. Listener `window.message` para `bpm-spotify-oauth` (`ok`) fecha o popup e conclui.
  4. `GET /api/auth/spotify/status` em polling enquanto o popup está aberto
     (fallback quando o postMessage não chegar); em `connected`, carrega playlists.
- Importar selecionada: `POST /api/import/spotify {reference: "spotify:<id>"}`;
  erro não vira lista vazia silenciosa — exibe a mensagem do backend.
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
| `SESSION_INVALID` | Sessão expirada — uma nova sessão anônima foi criada. Recarregue se necessário. |
| `CSRF_INVALID` | Sessão protegida — recarregue a página para renovar o token. |
| `SPOTIFY_NOT_CONNECTED` / `SPOTIFY_REVOKED` / `SPOTIFY_REFRESH_FAILED` | Conecte o Spotify para ver suas playlists. |
| `OAUTH_STATE_INVALID` (callback) | Autorização não concluída. Tente novamente. |
| `PLAYLIST_NOT_FOUND` / `PLAYLIST_INACCESSIBLE` | Playlist indisponível. Escolha outra. |
| `SPOTIFY_API_ERROR` (429) | Spotify limitou as requisições — aguarde e tente novamente. |
| `DRYRUN_EXPIRED` / `DRYRUN_NOT_FOUND` | Dry-run expirado. Rode a comparação novamente. |
| `DOWNLOAD_JOB_CONFIRM_REQUIRED` | Nenhuma faixa apta para download. Verifique ALLOW_DOWNLOADS. |
| `TIDAL_UPSTREAM_ERROR` / `CATALOG_UNREACHABLE` | Catálogo indisponível — tente novamente. |
| `REPORT_FORMAT_UNSUPPORTED` | Formato de relatório não suportado. |

## 8. Estados visuais (toda tela)

- **loading**: botão/guia com spinner; nunca tela branca.
- **vazio**: mensagem + CTA (ex.: dashboard vazio → "Importar").
- **erro recuperável**: banner com mensagem do mapa; ação para repetir.
- **sessão recriada**: banner informativo (dados da sessão anterior inacessíveis).
- **sucesso parcial**: resumo do job com itens ok/falha e link para relatório de falhas.

## 9. Testes de interface / regressão

- `tests/e2e/test_web_session.py` cobre o contrato do frontend:
  - sessão anônima + CSRF + logout via `test_anonymous_session_and_cookie`,
    `test_csrf_required_and_rotated`;
  - fluxo Spotify completo (popup → import → analyze) em
    `test_spotify_pkce_connect_import_and_analyze`;
  - estados de erro e `test_no_tokens_leak_in_api_responses`.
- `tests/e2e/test_flow.py`: UI e fluxo web migrados para sessão anônima
  (`test_web_bff_and_ui`, `test_web_download_confirmation_and_import_lookup`).
- Smoke: `GET /` e `/static/app.js` retornam 200 e contêm marcadores novos
  (`X-CSRF-Token`, `bpm-spotify-oauth`, `spotify:<id>`, `Nova sessão`).

## 10. Não fazer

- Não introduzir framework/bundler.
- Não mover regra de negócio crítica para `localStorage`.
- Não pré-selecionar faixas recommended.
- Não includir tela de login/registro (removida; backend retorna 410).
- Não mostrar tokens Spotify em respostas/console.