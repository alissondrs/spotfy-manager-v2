/* BPM Match UI — spotfy-manager-v2
   Sessão anônima por navegador (cookie HttpOnly) + OAuth Spotify (Authorization Code + PKCE).
   Sem login/registro local e sem JWT no navegador/localStorage. */
"use strict";

const SRC_KEY = "bpm_sources"; // fallback legado de fontes, não substitui o backend
const state = {
  session: null,
  analysis: null,
  sourceOptions: [],
  selectedOrdinals: new Set(),
  lastDryRun: null,
  activeJob: null,
  jobTimer: null,
  spotifyPopup: null,
  spotifyPoll: null,
  spotifyPopupWatch: null,
  spotifyFinished: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && state.session) headers["X-CSRF-Token"] = state.session.csrf_token;
  const resp = await fetch(path, { ...opts, method, headers, credentials: "same-origin" });
  const ct = resp.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await resp.json() : await resp.text();
  if (resp.status === 401 && body?.error === "SESSION_INVALID") {
    await initSession(true);
    throw new Error("Sessão expirada. Uma nova sessão anônima foi criada — recarregue a página se algo não funcionar.");
  }
  return { status: resp.status, body, resp };
}
const apiJson = (p, opts) => api(p, opts).then((r) => r.body);
function guard(r) {
  if (r.status >= 400) throw new Error(r.body?.detail || r.body?.error || "Erro desconhecido.");
  return r.body;
}

/* ---------------- navegação ---------------- */
const TAB = { dashboard: renderDashboard, import: renderImport, analyze: () => renderAnalyze(), library: renderLibrary, reports: renderReports };
function show(tab) {
  $$(".tab-view").forEach((v) => v.classList.add("hidden"));
  $("#" + (tab === "import" ? "view-import" : "view-" + tab)).classList.remove("hidden");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  TAB[tab]?.();
}
function showApp() {
  $("#nav").classList.remove("hidden");
  if (state.session) {
    const short = state.session.owner.length > 18 ? state.session.owner.slice(0, 12) + "…" : state.session.owner;
    $("#session-badge").textContent = `Sessão anônima · ${short}`;
  }
  show("dashboard");
}

$("#nav").addEventListener("click", (e) => { const t = e.target.closest("button[data-tab]"); if (t) show(t.dataset.tab); });
$("#new-session").onclick = async () => {
  if (!confirm("Iniciar uma nova sessão anônima? Playlists importadas, análises e relatórios desta sessão não serão mais acessíveis.")) return;
  try { await apiJson("/api/auth/logout", { method: "POST", body: JSON.stringify({}) }); } catch {}
  location.reload();
};

function showBanner(message) {
  const el = $("#banner");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

async function initSession(silent) {
  const data = await apiJson("/api/session");
  state.session = data.session;
  if (data.created && !silent) showBanner("Nova sessão anônima criada. Dados de uma sessão anterior não são mais acessíveis.");
  showApp();
}

/* ---------------- dashboard ---------------- */
async function renderDashboard() {
  const tiles = $("#dash-tiles"); tiles.innerHTML = "";
  const defs = [
    ["/api/synced", "Playlists", (b) => b.synced?.length ?? 0],
    ["/api/analyses", "Análises", (b) => b.analyses?.length ?? 0],
    ["/api/library", "Arquivos", (b) => b.count ?? 0],
    ["/api/reports", "Relatórios", (b) => b.reports?.length ?? 0],
  ];
  for (const [path, label, pick] of defs) {
    try { const d = await apiJson(path); tiles.insertAdjacentHTML("beforeend", `<div class="tile"><b>${pick(d)}</b><span>${label}</span></div>`); }
    catch { tiles.insertAdjacentHTML("beforeend", `<div class="tile"><b>—</b><span>${label}</span></div>`); }
  }
}

/* ---------------- import ---------------- */
async function renderImport() {
  const sel = $("#playlist-select");
  const btn = $("#import-playlist");
  $("#file-result").textContent = "";
  try {
    const st = await apiJson("/api/auth/spotify/status");
    if (st.connected) {
      $("#spotify-status").textContent = "Spotify conectado";
      $("#spotify-status").className = "badge-ok";
      $("#connect-spotify").textContent = "Reconectar Spotify";
      for (const p of st.player_name ? [] : []) {}
      if (sel.options.length === 0) await loadSpotifyPlaylists(btn);
    } else {
      $("#spotify-status").textContent = "Spotify não conectado";
      $("#spotify-status").className = "muted";
      $("#connect-spotify").textContent = "Conectar Spotify";
    }
  } catch (err) {
    $("#spotify-status").textContent = "Status indisponível: " + err.message;
    $("#spotify-status").className = "error";
  }
}

function setSpotifyStatus(message, tone = "muted") {
  $("#spotify-status").textContent = message;
  $("#spotify-status").className = tone;
}
function resetSpotifyFallback() {
  const box = $("#spotify-fallback");
  box.classList.add("hidden");
  box.innerHTML = "";
  stopSpotifyPoll();
}

$("#connect-spotify").onclick = async (e) => {
  const btn = e.target;
  btn.disabled = true;
  state.spotifyFinished = false;
  resetSpotifyFallback();
  setSpotifyStatus("Preparando autorização...", "muted");
  try {
    const data = await apiJson("/api/auth/spotify"); // gera state + code_verifier (BFF)
    const w = 500, h = 600;
    const left = (screen.width - w) / 2, top = (screen.height - h) / 2;
    const popup = window.open("about:blank", "spotify-auth", `width=${w},height=${h},left=${left},top=${top}`);
    if (!popup || popup.closed) {
      throw new PopupBlockedError(data.url);
    }
    state.spotifyPopup = popup;
    popup.location.href = data.url;
    setSpotifyStatus("Autorize no popup do Spotify...", "muted");
    startSpotifyStatusPoll();
    watchPopupClosed(popup, btn);
  } catch (err) {
    if (err instanceof PopupBlockedError) {
      setSpotifyStatus("Popup bloqueado. Abra a autorização na própria aba:", "error");
      showSpotifyFallback(err.url);
      startSpotifyStatusPoll();
      btn.disabled = false;
    } else {
      setSpotifyStatus(err.message, "error");
      btn.disabled = false;
    }
  }
};

class PopupBlockedError extends Error {
  constructor(url) { super("Popup bloqueado."); this.url = url; }
}
function showSpotifyFallback(url) {
  const box = $("#spotify-fallback");
  box.innerHTML = "";
  const a = document.createElement("a");
  a.href = url; a.target = "_blank"; a.rel = "noopener"; a.className = "ghost";
  a.textContent = "Abrir autorização do Spotify";
  box.appendChild(a);
  box.appendChild((() => { const s = document.createElement("span"); s.innerHTML = " — conclua e a aba se fecha sozinha."; return s; })());
  box.classList.remove("hidden");
}

function startSpotifyStatusPoll() {
  if (state.spotifyPoll) return;
  let ticks = 0;
  state.spotifyPoll = setInterval(async () => {
    ticks += 1;
    try {
      const st = await apiJson("/api/auth/spotify/status");
      if (st.connected) { finishSpotifyConnect(); return; }
      if (ticks > 150) { setSpotifyStatus("Autorização pendente há muito tempo. Tente novamente.", "error"); stopSpotifyPoll(); }
    } catch (err) {
      setSpotifyStatus("Falha ao consultar status: " + err.message, "error");
      stopSpotifyPoll();
      const btn = $("#connect-spotify");
      btn.disabled = false;
    }
  }, 2000);
}
function stopSpotifyPoll() {
  if (state.spotifyPoll) { clearInterval(state.spotifyPoll); state.spotifyPoll = null; }
}
function watchPopupClosed(popup, btn) {
  if (state.spotifyPopupWatch) clearInterval(state.spotifyPopupWatch);
  state.spotifyPopupWatch = setInterval(() => {
    if (!popup || popup.closed !== false) {
      clearInterval(state.spotifyPopupWatch);
      state.spotifyPopupWatch = null;
      state.spotifyPopup = null;
      if (!state.spotifyFinished) {
        setSpotifyStatus("Popup fechado sem concluir. Use o botão para tentar de novo.", "error");
        btn.disabled = false;
      }
    }
  }, 1000);
}
function finishSpotifyConnect() {
  if (state.spotifyFinished) return;
  state.spotifyFinished = true;
  stopSpotifyPoll();
  if (state.spotifyPopupWatch) { clearInterval(state.spotifyPopupWatch); state.spotifyPopupWatch = null; }
  if (state.spotifyPopup) { try { state.spotifyPopup.close(); } catch {} state.spotifyPopup = null; }
  resetSpotifyFallback();
  setSpotifyStatus("Spotify conectado", "badge-ok");
  $("#connect-spotify").textContent = "Reconectar Spotify";
  $("#connect-spotify").disabled = false;
  loadSpotifyPlaylists();
}

window.addEventListener("message", (ev) => {
  if (ev.origin !== window.location.origin) return;
  if (!ev.data || ev.data.type !== "bpm-spotify-oauth") return;
  if (ev.data.ok) {
    try { ev.source?.close?.(); } catch {}
    finishSpotifyConnect();
  } else {
    setSpotifyStatus("Autorização não concluída (" + (ev.data.error || "erro desconhecido") + ").", "error");
    $("#connect-spotify").disabled = false;
  }
});

async function loadSpotifyPlaylists(btn) {
  try {
    const data = await apiJson("/api/playlists");
    const sel = $("#playlist-select");
    sel.innerHTML = "";
    const items = data.playlists || [];
    if (btn) btn.classList.contains("hidden") || (btn.textContent = "Playlists carregadas");
    if (items.length === 0) {
      setFileResult("Nenhuma playlist retornada. Verifique as permissões da sua conta Spotify.", "error");
      sel.classList.add("hidden");
      $("#import-playlist").classList.add("hidden");
      return;
    }
    for (const p of items) sel.appendChild(new Option(`${p.name} (${p.tracks_total ?? "?"}) — ${p.owner || ""}`, p.id));
    sel.classList.remove("hidden");
    $("#import-playlist").classList.remove("hidden");
    setFileResult("");
  } catch (err) {
    if (btn) btn.textContent = "Carregar playlists";
    setFileResult("Spotify: " + err.message, "error");
  }
}
function setFileResult(message, tone = "muted") {
  const el = $("#file-result");
  el.textContent = message;
  el.className = tone;
  el.classList.toggle("hidden", !message);
  $("#spotify-error").classList.add("hidden");
}

$("#load-playlists").onclick = async (e) => {
  const btn = e.target;
  btn.textContent = "Carregando...";
  try { await loadSpotifyPlaylists(btn); } finally { if (btn.textContent !== "Playlists carregadas") btn.textContent = "Carregar playlists"; }
};
$("#import-playlist").onclick = async () => {
  const id = $("#playlist-select").value;
  if (!id) { setFileResult("Selecione uma playlist.", "error"); return; }
  try {
    const data = guard(await apiJson("/api/import/spotify", { method: "POST", body: JSON.stringify({ reference: `spotify:${id}` }) }));
    setFileResult(`Importada "${data.playlist_name}" — ${data.track_count} faixas.`, "badge-ok");
    await refreshSourceSelect();
  } catch (err) { setFileResult("Erro: " + err.message, "error"); }
};
$("#upload-file").onclick = async () => {
  const input = $("#file-input");
  if (!input.files.length) { setFileResult("Escolha um arquivo primeiro.", "error"); return; }
  const fd = new FormData(); fd.append("file", input.files[0]);
  try {
    const data = guard(await apiJson("/api/import/file", { method: "POST", body: fd }));
    setFileResult(`Arquivo "${data.filename}" validado — ${data.track_count} faixas (import_id: ${data.import_id}).`, "badge-ok");
    await refreshSourceSelect();
  } catch (err) { setFileResult("Erro: " + err.message, "error"); }
};

/* cache local opcional (fallback legado) para fontes de faixas */
function getCachedSources() { try { return JSON.parse(localStorage.getItem(SRC_KEY) || "[]"); } catch { return []; } }

/* ---------------- analyze ---------------- */
async function refreshSourceSelect() {
  const [syncedResp, importsResp] = await Promise.allSettled([apiJson("/api/synced"), apiJson("/api/imports")]);
  const list = [];
  if (syncedResp.status === "fulfilled") {
    for (const p of syncedResp.value.synced || []) {
      list.push({ key: `spotify:${p.id}`, kind: "spotify", id: p.id, label: p.name, count: p.track_count, server: true });
    }
  }
  if (importsResp.status === "fulfilled") {
    for (const it of importsResp.value.imports || []) {
      list.push({ key: `file:${it.import_id}`, kind: "file", id: it.import_id, label: it.name || it.filename || it.import_id, count: it.track_count, server: true });
    }
  }
  if (!list.length) {
    for (const cached of getCachedSources()) {
      const key = `${cached.kind}:${cached.id}`;
      if (!list.find((item) => item.key === key)) list.push({ key, kind: cached.kind, id: cached.id, label: cached.label, count: cached.count, server: false });
    }
  }
  state.sourceOptions = list;
  const sel = $("#source-select");
  sel.innerHTML = "";
  for (const s of list) sel.appendChild(new Option(`${s.label} (${s.count ?? 0} faixas)`, s.key));
  if (!list.length) sel.innerHTML = '<option value="">Nenhuma fonte importada ainda</option>';
}
async function resolveTracks(source) {
  const s = state.sourceOptions.find((it) => it.key === source);
  if (!s) return [];
  if (s.kind === "file") {
    const entry = await apiJson(`/api/import/${encodeURIComponent(s.id)}`);
    return entry.tracks || [];
  }
  const data = await apiJson(`/api/synced/${encodeURIComponent(s.id)}`);
  return data.tracks || [];
}
async function renderAnalyze() {
  await refreshSourceSelect();
}

$("#tolerance").oninput = () => $("#tol-label").textContent = $("#tolerance").value;
$("#analyze-form").onsubmit = async (e) => {
  e.preventDefault();
  runAnalyze(false);
};
async function runAnalyze(force) {
  $("#analyze-error").classList.add("hidden");
  $("#reuse-banner").classList.add("hidden");
  const target = parseFloat($("#target-bpm").value);
  const tolerance = parseFloat($("#tolerance").value);
  const name = $("#analysis-name").value || "Meu set";
  const key = $("#source-select").value || "";
  const source = state.sourceOptions.find((it) => it.key === key);
  if (!source || !source.count) { $("#analyze-error").textContent = "Selecione uma origem com faixas importadas."; $("#analyze-error").classList.remove("hidden"); return; }
  try {
    const btn = $("#analyze-btn");
    btn.disabled = true;
    const body = { name, target_bpm: target, tolerance_bpm: tolerance };
    if (source.server) {
      body.reference = key; // spotify:<id> ou file:<import_id> — o BPM Match resolve no backend
      body.tracks = [];
    } else {
      const tracks = await resolveTracks(key);
      if (!tracks.length) throw new Error("Fonte sem faixas resolvíveis. Reimporte a playlist/arquivo.");
      body.tracks = tracks; // fallback legado (fonte apenas no cache local)
    }
    if (force) body.force = true;
    const data = guard(await apiJson("/api/analyze", { method: "POST", body: JSON.stringify(body) }));
    if (data.reused) {
      $("#reuse-banner").classList.remove("hidden");
      $("#reuse-banner-text").textContent = `Reanalisamos a mesma origem (${data.reference || "referência"}) com BPM alvo ${data.target_bpm}. Resultado reaproveitado da análise anterior — use o botão para forçar nova análise.`;
    }
    state.analysis = data;
    renderAnalysis(data);
  } catch (err) {
    $("#analyze-error").textContent = err.message;
    $("#analyze-error").classList.remove("hidden");
  } finally {
    $("#analyze-btn").disabled = false;
  }
}
$("#reanalyze").onclick = () => runAnalyze(true);
const DECISION_LABEL = { recommended: ["ok", "RECOMENDADA"], rejected: ["no", "REJEITADA"], insufficient: ["mid", "INSUFICIENTE"], conflict: ["no", "CONFLITO"] };
function renderAnalysis(data) {
  say(data);
  state.selectedOrdinals = new Set();
  $("#analysis-result").classList.remove("hidden");
  $("#analysis-title").textContent = data.name;
  const s = data.summary || {};
  const catUnavail = data.catalog_unavailable ? " · ⚠ alguns catálogos indisponíveis, itens marcados como dependência" : "";
  $("#analysis-summary").textContent =
    `Alvo ${data.target_bpm} BPM ±${data.tolerance_bpm} · ${s.total} faixas · ` +
    `recomendadas ${s.recommended} · rejeitadas ${s.rejected} · insuficientes ${s.insufficient} · conflito ${s.conflict} · confiança média ${(s.avg_confidence * 100).toFixed(0)}%${catUnavail}`;
  const tb = $("#analysis-table");
  tb.innerHTML = "<thead><tr><th>Sel.</th><th>#</th><th>Faixa</th><th>Artistas</th><th>BPM</th><th>Confiança</th><th>Decisão</th><th>Como</th><th>Revisão</th></tr></thead><tbody>";
  data.items.forEach((it) => {
    const [cls, label] = DECISION_LABEL[it.decision] || ["mid", it.decision];
    const bpm = it.chosen?.bpm ?? it.candidates?.find((c) => c.bpm)?.bpm ?? "—";
    const tabs = it.reasons?.join(" ") || "";
    const rev = it.review === "approved" ? "✓" : it.review === "rejected" ? "✗" : "—";
    const dep = it.dependency_error ? ` <span class="badge-mid" title="${esc(it.dependency_error)}">dep.</span>` : "";
    const selCell = it.decision === "recommended" && !it.dependency_error
      ? `<input type="checkbox" class="sel-chk" data-ordinal="${it.ordinal}">`
      : "";
    tb.querySelector("tbody").insertAdjacentHTML("beforeend",
      `<tr><td>${selCell}</td><td>${it.ordinal}</td><td>${esc(it.track.name)}</td><td>${esc(it.track.artists.join(", "))}</td>
       <td>${bpm}</td><td>${(it.confidence * 100).toFixed(0)}%</td>
       <td><span class="badge-${cls}">${label}</span>${dep}</td>
       <td>${esc(tabs)}</td>
       <td>${rev} <button class="ghost" data-approve="${it.ordinal}">aprov.</button> <button class="ghost" data-reject="${it.ordinal}">rej.</button></td></tr>`);
  });
  tb.querySelector("tbody").addEventListener("click", (ev) => {
    const chk = ev.target.closest(".sel-chk");
    if (chk) {
      const o = parseInt(chk.dataset.ordinal, 10);
      if (chk.checked) state.selectedOrdinals.add(o); else state.selectedOrdinals.delete(o);
      $("#sel-count").textContent = state.selectedOrdinals.size;
      return;
    }
    const b = ev.target.closest("[data-approve],[data-reject]");
    if (!b) return;
    const idx = parseInt(b.dataset.approve ?? b.dataset.reject) - 1;
    const decision = b.dataset.approve ? "approved" : "rejected";
    (async () => {
      try {
        guard(await apiJson(`/api/analyses/${data.analysis_id}/review?item_index=${idx}&decision=${decision}`, { method: "POST", body: JSON.stringify({}) }));
        const fresh = await apiJson(`/api/analyses/${data.analysis_id}`);
        state.analysis = fresh; renderAnalysis(fresh);
      } catch (err) { alert(err.message); }
    })();
  });
}
$("#export-md").onclick = () => exportReport("markdown");
$("#export-csv").onclick = () => exportReport("csv");
$("#export-html").onclick = () => exportReport("html");
async function exportReport(fmt) {
  if (!state.analysis) return;
  try {
    const data = guard(await apiJson("/api/reports", { method: "POST", body: JSON.stringify({ analysis_id: state.analysis.analysis_id, format: fmt }) }));
    window.open(`/api/reports/${data.report_id}/download`, "_blank");
  } catch (err) { alert(err.message); }
}

/* ---------------- library ---------------- */
function setDownloadFeedback(message, tone = "muted") {
  const el = $("#download-feedback");
  el.textContent = message || "";
  el.className = tone + (message ? "" : " hidden");
  el.classList.remove("badge-ok");
  if (tone === "badge-ok") el.classList.add("badge-ok");
}
function resetDownloadResult() { $("#download-result").innerHTML = ""; $("#download-result").classList.add("hidden"); }

async function renderLibrary() {
  try {
    const data = await apiJson("/api/library");
    $("#library-info").textContent = `Diretório: ${data.directory} · ${data.count} arquivos.`;
    const tb = $("#library-table");
    tb.innerHTML = data.count ? "<thead><tr><th>Arquivo</th><th>Ext</th><th>Faixa</th><th>Tamanho</th></tr></thead><tbody>" +
      data.files.slice(0, 50).map((f) => `<tr><td>${esc(f.filename)}</td><td>${esc(f.extension)}</td><td>${esc(f.track_name)}</td><td>${(f.size_bytes / 1024).toFixed(0)} kB</td></tr>`).join("") + "</tbody>"
      : "<tbody></tbody>";
  } catch (err) { $("#library-info").textContent = "Erro: " + err.message; }
}
$("#refresh-library").onclick = renderLibrary;
$("#go-match").onclick = async () => {
  if (!state.analysis) { alert("Rode uma análise BPM Match primeiro."); return; }
  try {
    setDownloadFeedback("");
    resetDownloadResult();
    const items = state.analysis.items.filter((i) => i.decision === "recommended" && !i.dependency_error);
    const picked = state.selectedOrdinals.size
      ? items.filter((i) => state.selectedOrdinals.has(i.ordinal))
      : [];
    const tracks = picked.length ? picked.map((i) => i.track) : items.map((i) => i.track);
    if (!tracks.length) { alert("Nenhuma faixa recomendada para comparar."); return; }
    if (!picked.length) {
      setDownloadFeedback("Nenhuma faixa selecionada — marque as recomendadas que deseja confirmar; nada é selecionado por padrão.", "muted");
    }
    const cmp = guard(await apiJson("/api/library/compare", { method: "POST", body: JSON.stringify({ tracks }) }));
    $("#dryrun-info").textContent = `Faltantes: ${cmp.missing_count} de ${tracks.length} faixas · na biblioteca: ${cmp.matched_count}.`;
    const dr = guard(await apiJson("/api/library/dryrun", { method: "POST", body: JSON.stringify({ tracks: cmp.missing }) }));
    const md = $("#missing-table");
    md.innerHTML = "<thead><tr><th>Faixa</th><th>Status</th><th>Qualidade</th><th>Motivo</th></tr></thead><tbody>" +
      dr.selections.map((s) => `<tr><td>${esc(s.track.name)}</td><td class="badge-${s.status === "found" ? "ok" : "no"}">${esc(s.status)}</td><td>${esc(s.quality || "—")}</td><td>${esc(s.reason || "")}</td></tr>`).join("") + "</tbody>";
    $("#missing-card").classList.remove("hidden");
    const found = dr.selections.filter((s) => s.status === "found");
    const btn = $("#confirm-download");
    btn.classList.toggle("hidden", !found.length);
    const expiresMs = dr.expires_in ? Date.now() + Number(dr.expires_in) * 1000 : Date.now() + 15 * 60 * 1000;
    state.lastDryRun = { id: dr.dry_run_id, expiresAt: expiresMs, consumed: false, selections: dr.selections || [], foundCount: found.length };
    setDownloadFeedback(found.length ? `Dry-run pronto. ${found.length} faixa(s) apta(s) para confirmação.` : "Dry-run concluído sem faixas aptas para download.");
    btn.onclick = async () => {
      if (!state.lastDryRun || !state.lastDryRun.id) {
        setDownloadFeedback("Dry-run ausente. Rode a comparação novamente.", "error");
        return;
      }
      if (state.lastDryRun.consumed) {
        setDownloadFeedback("Esse lote já foi confirmado. Rode novo dry-run para repetir.", "error");
        return;
      }
      if (Date.now() > state.lastDryRun.expiresAt) {
        setDownloadFeedback("Confirmação expirada. Rode novo dry-run antes de baixar.", "error");
        return;
      }
      const pick = state.lastDryRun.selections.filter((s) => s.status === "found");
      if (!pick.length) return;
      if (!confirm(`Baixar ${pick.length} faixa(s)?`)) return;
      btn.disabled = true;
      const oldText = btn.textContent;
      btn.textContent = "Confirmando...";
      try {
        const resp = await api("/api/library/download", { method: "POST", body: JSON.stringify({ dry_run_id: state.lastDryRun.id }) });
        if (resp.status >= 400) {
          const detail = resp.body?.detail || "Falha ao confirmar download.";
          if (["DOWNLOAD_JOB_CONFIRM_REQUIRED", "DRYRUN_EXPIRED", "DRYRUN_NOT_FOUND"].includes(resp.body?.error)) {
            state.lastDryRun = null;
            setDownloadFeedback("Lote de confirmação inválido ou expirado. Rode novo dry-run.", "error");
          }
          throw new Error(detail);
        }
        const data = resp.body || {};
        state.lastDryRun.consumed = true;
        setDownloadFeedback("Download iniciado. Acompanhe o progresso abaixo.", "badge-ok");
        pollJob(data.job_id);
      } catch (err) {
        setDownloadFeedback(err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = oldText;
      }
    };
  } catch (err) { alert(err.message); }
};

function pollJob(jobId) {
  if (state.jobTimer) { clearInterval(state.jobTimer); state.jobTimer = null; }
  state.activeJob = jobId;
  const tick = async () => {
    try {
      const data = guard(await apiJson(`/api/library/jobs/${jobId}`));
      renderJobProgress(data);
      if (data.status === "completed" || data.status === "failed") {
        clearInterval(state.jobTimer); state.jobTimer = null;
        state.activeJob = null;
        setDownloadFeedback(data.message || "Download concluído.", data.status === "failed" ? "error" : "badge-ok");
        await renderLibrary();
        return;
      }
    } catch { /* job pode não estar pronto ainda */ }
  };
  tick();
  state.jobTimer = setInterval(tick, 1500);
}

function renderJobProgress(job) {
  const rows = (job.items || []).map((it) => {
    const name = it.track?.name || "—";
    const status = it.status === "downloading" ? "baixando…" : it.status === "pending" ? "aguardando" : it.status === "downloaded" ? "✓ concluída" : it.status === "failed" ? "✗ falha" : it.status;
    const cls = it.status === "downloaded" ? "ok" : it.status === "failed" ? "no" : "mid";
    return `<tr><td>${esc(name)}</td><td><span class="badge-${cls}">${esc(status)}</span></td><td>${esc(it.filename || "—")}</td><td>${esc(it.error || "")}</td></tr>`;
  }).join("");
  $("#download-result").innerHTML =
    `<div class="card"><p><b>Job:</b> ${esc(job.job_id)} · ${job.completed ?? 0}/${job.total ?? 0} · status <span class="badge-mid">${esc(job.status)}</span></p>` +
    `<table class="compact"><thead><tr><th>Faixa</th><th>Status</th><th>Arquivo</th><th>Erro</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  $("#download-result").classList.remove("hidden");
}

/* ---------------- reports ---------------- */
async function renderReports() {
  try {
    const data = await apiJson("/api/reports");
    const rows = data.reports || [];
    $("#reports-empty").classList.toggle("hidden", rows.length > 0);
    $("#reports-table").innerHTML = rows.length ? "<thead><tr><th>ID</th><th>Análise</th><th>Formato</th><th>Arquivo</th><th>Tamanho</th><th>Ações</th></tr></thead><tbody>" +
      rows.map((r) => `<tr><td>${esc(r.report_id)}</td><td>${esc(r.analysis_id)}</td><td>${esc(r.format)}</td><td>${esc(r.filename)}</td><td>${Math.round(r.bytes / 1024)} kB</td><td><a href="/api/reports/${r.report_id}/download" target="_blank">baixar</a></td></tr>`).join("") + "</tbody>"
      : "<tbody></tbody>";
  } catch (err) { $("#reports-empty").textContent = "Erro: " + err.message; }
}

/* helper de debug */
function say(data) { console.debug("bpm-match:", data); }

/* init */
(function init() {
  initSession(false).catch((err) => { console.error(err); showBanner("Falha ao iniciar sessão: " + err.message); });
})();