/* BPM Match UI — spotfy-manager-v2 */
"use strict";

const TOKEN_KEY = "bpm_token";
const USER_KEY = "bpm_user";
const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: localStorage.getItem(USER_KEY) || "",
  analysis: null,
  sourceOptions: [],
  selectedOrdinals: new Set(),
  lastDryRun: null,
  activeJob: null,
  jobTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, { ...opts, headers });
  if (resp.status === 401 && state.token) { showLogin(); throw new Error("Sessão expirada."); }
  const ct = resp.headers.get("content-type") || "";
  return { status: resp.status, body: ct.includes("application/json") ? await resp.json() : await resp.text(), resp };
}
const apiJson = (p, opts) => api(p, opts).then((r) => r.body);
function guard(r) { if (r.status >= 400) throw new Error(r.body?.detail || r.body?.error || "Erro desconhecido."); return r.body; }

/* ---------------- navegação ---------------- */
const TAB = { dashboard: renderDashboard, import: renderImport, analyze: () => renderAnalyze(), library: renderLibrary, reports: renderReports };
function show(tab) {
  $$(".tab-view").forEach((v) => v.classList.add("hidden"));
  $("#" + (tab === "import" ? "view-import" : "view-" + tab)).classList.remove("hidden");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  TAB[tab]?.();
}
function showLogin() { state.token = ""; state.user = ""; localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); $("#nav").classList.add("hidden"); $("#view-login").classList.remove("hidden"); $("#view-register").classList.add("hidden"); }
function showApp() { $("#nav").classList.remove("hidden"); $("#user-label").textContent = state.user; document.querySelectorAll(".login-card").forEach((c) => c.classList.add("hidden")); show("dashboard"); }

$("#go-register").onclick = (e) => { e.preventDefault(); $("#view-login").classList.add("hidden"); $("#view-register").classList.remove("hidden"); };
$("#go-login").onclick = (e) => { e.preventDefault(); $("#view-register").classList.add("hidden"); $("#view-login").classList.remove("hidden"); };
$("#logout").onclick = showLogin;
$("#nav").addEventListener("click", (e) => { const t = e.target.closest("button[data-tab]"); if (t) show(t.dataset.tab); });

/* ---------------- auth ---------------- */
$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("#login-user").value, password: $("#login-pass").value }) });
    if (r.status === 401) throw new Error("Usuário ou senha inválidos.");
    const data = guard(r);
    state.token = data.token; state.user = data.username;
    localStorage.setItem(TOKEN_KEY, data.token); localStorage.setItem(USER_KEY, data.username);
    $("#login-error").classList.add("hidden"); showApp();
  } catch (err) { $("#login-error").textContent = err.message; $("#login-error").classList.remove("hidden"); }
};
$("#register-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/register", { method: "POST", body: JSON.stringify({ username: $("#reg-user").value, password: $("#reg-pass").value }) });
    if (r.status >= 400) throw new Error(r.body?.detail || "Erro ao registrar.");
    alert("Conta criada. Faça login."); $("#view-register").classList.add("hidden"); $("#view-login").classList.remove("hidden");
  } catch (err) { $("#register-error").textContent = err.message; $("#register-error").classList.remove("hidden"); }
};

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
  await loadSources();
  if (sel.options.length > 0) { sel.classList.remove("hidden"); btn.classList.remove("hidden"); }
  // Verifica status do Spotify
  try {
    const st = await apiJson("/api/auth/spotify/status");
    if (st.connected) {
      $("#spotify-status").textContent = "✓ Spotify conectado";
      $("#spotify-status").className = "badge-ok";
      $("#connect-spotify").textContent = "Reconectar Spotify";
      if (sel.options.length === 0) await loadSpotifyPlaylists(btn);
    } else {
      $("#spotify-status").textContent = "Spotify não conectado";
      $("#connect-spotify").textContent = "Conectar Spotify";
    }
  } catch {}
}
async function loadSources() {
  const sel = $("#playlist-select");
  if (sel.options.length > 0) return;
  try {
    const data = await apiJson("/api/synced");
    const synced = data.synced || [];
    for (const p of synced) sel.appendChild(new Option(`${p.name} (${p.track_count} faixas)`, p.id));
  } catch { /* spotify pode não estar configurado */ }
}

// Botão "Conectar Spotify"
$("#connect-spotify").onclick = async (e) => {
  const btn = e.target;
  btn.disabled = true;
  try {
    const data = await apiJson("/api/auth/spotify");
    const w = 500, h = 600;
    const left = (screen.width - w) / 2, top = (screen.height - h) / 2;
    window.open(data.url, "spotify-auth", `width=${w},height=${h},left=${left},top=${top}`);
    $("#spotify-token-section").classList.remove("hidden");
    $("#spotify-status").textContent = "Autorize no popup e aguarde...";
    const poll = setInterval(async () => {
      try {
        await api("/api/auth/spotify/claim", { method: "POST", body: JSON.stringify({}) });
        const st = await apiJson("/api/auth/spotify/status");
        if (st.connected) {
          clearInterval(poll);
          $("#spotify-status").textContent = "✓ Spotify conectado!";
          $("#spotify-status").className = "badge-ok";
          $("#connect-spotify").textContent = "Reconectar Spotify";
          $("#spotify-token-section").classList.add("hidden");
          btn.disabled = false;
        }
      } catch {}
    }, 2000);
    setTimeout(() => clearInterval(poll), 300000);
  } catch (err) {
    $("#spotify-status").textContent = err.message;
    $("#spotify-status").className = "error";
    btn.disabled = false;
  }
};

// Salvar token manual
$("#save-spotify-token").onclick = async () => {
  const token = $("#spotify-token-input").value.trim();
  if (!token) return;
  try {
    const r = await api("/api/auth/spotify/token", { method: "POST", body: JSON.stringify({ token }) });
    if (r.body?.ok) {
      $("#spotify-status").textContent = "✓ Token salvo!";
      $("#spotify-status").className = "badge-ok";
      $("#spotify-token-section").classList.add("hidden");
      $("#connect-spotify").textContent = "Reconectar Spotify";
    } else {
      $("#spotify-error").textContent = r.body?.detail || "Erro ao salvar token";
    }
  } catch (err) {
    $("#spotify-error").textContent = err.message;
  }
};

async function loadSpotifyPlaylists(btn) {
  try {
    const data = await apiJson("/api/playlists");
    if (btn) btn.textContent = "Playlists carregadas";
    const sel = $("#playlist-select"); sel.innerHTML = "";
    for (const p of data.playlists || []) sel.appendChild(new Option(`${p.name} (${p.tracks_total ?? "?"}) — ${p.owner || ""}`, `https://open.spotify.com/playlist/${p.id}`));
    if (sel.options.length === 0) { $("#file-result").textContent = "Nenhuma playlist retornada. Verifique as credenciais do Spotify."; }
  } catch (err) { if (btn) btn.textContent = "Carregar playlists"; $("#file-result").textContent = "Spotify: " + err.message; }
}

$("#load-playlists").onclick = async (e) => {
  const btn = e.target;
  btn.textContent = "Carregando...";
  await loadSpotifyPlaylists(btn);
};
$("#import-playlist").onclick = async () => {
  const ref = $("#playlist-select").value;
  if (!ref) return;
  try {
    const data = guard(await api("/api/import/spotify", { method: "POST", body: JSON.stringify({ reference: ref }) }));
    renderImportPreview(new Set()); $("#file-result").textContent = `Importada "${data.playlist_name}" — ${data.track_count} faixas.`;
    await cacheSource("spotify", data.playlist_name, data.track_count, data.playlist_id);
    await refreshImportOptions();
  } catch (err) { $("#file-result").textContent = "Erro: " + err.message; }
};
$("#upload-file").onclick = async () => {
  const input = $("#file-input");
  if (!input.files.length) return;
  const fd = new FormData(); fd.append("file", input.files[0]);
  try {
    const r = await api("/api/import/file", { method: "POST", body: fd });
    const data = guard(r);
    $("#file-result").textContent = `Arquivo "${data.filename}" validado — ${data.track_count} faixas (import_id: ${data.import_id}).`;
    await cacheSource("file", data.filename, data.track_count, data.import_id);
    await refreshImportOptions();
  } catch (err) { $("#file-result").textContent = "Erro: " + err.message; }
};

/* cache local opcional (fallback) para fontes de faixas */
const SRC_KEY = "bpm_sources";
function getCachedSources() { try { return JSON.parse(localStorage.getItem(SRC_KEY) || "[]"); } catch { return []; } }
async function cacheSource(kind, label, count, extra) {
  const list = getCachedSources();
  const id = kind === "spotify" ? (extra || label) : extra;
  if (!list.find((s) => s.id === id)) list.push({ kind, label, count, id });
  localStorage.setItem(SRC_KEY, JSON.stringify(list.slice(-10)));
}
async function refreshImportOptions() { return refreshSourceSelect(); }

function renderImportPreview(tracks) {}

/* ---------------- analyze ---------------- */
async function refreshSourceSelect() {
  const [syncedResp, importsResp] = await Promise.allSettled([apiJson("/api/synced"), apiJson("/api/imports")]);
  const list = [];
  if (syncedResp.status === "fulfilled") {
    for (const p of syncedResp.value.synced || []) {
      list.push({ key: `spotify:${p.id}`, kind: "spotify", id: p.id, label: p.name, count: p.track_count });
    }
  }
  if (importsResp.status === "fulfilled") {
    for (const it of importsResp.value.imports || []) {
      list.push({ key: `file:${it.import_id}`, kind: "file", id: it.import_id, label: it.name || it.filename || it.import_id, count: it.track_count });
    }
  }
  if (!list.length) {
    for (const cached of getCachedSources()) {
      const key = `${cached.kind}:${cached.id}`;
      if (!list.find((item) => item.key === key)) list.push({ key, ...cached });
    }
  }
  state.sourceOptions = list;
  const sel = $("#source-select");
  sel.innerHTML = "";
  for (const s of list) sel.appendChild(new Option(`${s.label} (${s.count ?? 0} faixas)`, s.key));
}
async function resolveTracks(source) {
  if (!source) return [];
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
  if (!state.sourceOptions.length) $("#source-select").innerHTML = '<option value="">Nenhuma fonte importada ainda</option>';
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
  try {
    const tracks = await resolveTracks($("#source-select").value);
    if (!tracks.length) throw new Error("Selecione uma origem com faixas importadas.");
    const key = $("#source-select").value || "";
    const ref = key.startsWith("file:") ? key.slice("file:".length) : key.slice("spotify:".length);
    const body = { name, target_bpm: target, tolerance_bpm: tolerance, tracks };
    if (ref) body.reference = ref;
    if (force) body.force = true;
    const data = guard(await api("/api/analyze", { method: "POST", body: JSON.stringify(body) }));
    if (data.reused) {
      const banner = $("#reuse-banner");
      banner.classList.remove("hidden");
      $("#reuse-banner-text").textContent = `Reanalisamos a mesma origem (${data.reference || "referência"}) com BPM alvo ${data.target_bpm}. Resultado reaproveitado da análise anterior — marque à direita para forçar nova análise.`;
    }
    state.analysis = data; renderAnalysis(data);
  } catch (err) { $("#analyze-error").textContent = err.message; $("#analyze-error").classList.remove("hidden"); }
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
    const selCell = it.decision === "recommended"
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
        guard(await api(`/api/analyses/${data.analysis_id}/review?item_index=${idx}&decision=${decision}`, { method: "POST", body: JSON.stringify({}) }));
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
    const data = guard(await api("/api/reports", { method: "POST", body: JSON.stringify({ analysis_id: state.analysis.analysis_id, format: fmt }) }));
    window.open(`/api/reports/${data.report_id}/download`, "_blank");
  } catch (err) { alert(err.message); }
}

/* ---------------- library ---------------- */
function setDownloadFeedback(message, tone = "muted") {
  const el = $("#download-feedback");
  el.textContent = message || "";
  el.className = `${tone} ${message ? "" : "hidden"}`.trim();
}
function resetDownloadResult() { $("#download-result").innerHTML = ""; $("#download-result").classList.add("hidden"); }
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
    const items = state.analysis.items.filter((i) => i.decision === "recommended");
    const picked = state.selectedOrdinals.size
      ? items.filter((i) => state.selectedOrdinals.has(i.ordinal))
      : [];
    const tracks = picked.length ? picked.map((i) => i.track) : items.map((i) => i.track);
    if (!tracks.length) { alert("Nenhuma faixa recomendada para comparar."); return; }
    if (!picked.length && items.length) {
      setDownloadFeedback("Nenhuma faixa selecionada — a coluna Sel. marca quais recomendadas serão confirmadas.", "muted");
    }
    const cmp = guard(await api("/api/library/compare", { method: "POST", body: JSON.stringify({ tracks }) }));
    $("#dryrun-info").textContent = `Faltantes: ${cmp.missing_count} de ${tracks.length} faixas · na biblioteca: ${cmp.matched_count}.`;
    const dr = guard(await api("/api/library/dryrun", { method: "POST", body: JSON.stringify({ tracks: cmp.missing }) }));
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
      const data = guard(await api(`/api/library/jobs/${jobId}`));
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

/* helper de debug: mostra JSON no console, não na tela */
function say(data) { console.debug("bpm-match:", data); }

/* init */
(function init() {
  if (state.token) showApp(); else showLogin();
})();