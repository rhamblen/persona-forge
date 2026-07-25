// Persona Forge — Prompt Studio (phase 2).
// Vanilla JS on purpose: keeps the image build-step-free until the UI references
// land and we commit to a framework.

const POLL_MS = 15000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDur = (secs) => {  // 95 -> "1m35s", 24 -> "24s", 3800 -> "1h03m20s"
  const s = Math.round(secs || 0);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return `${m}m${String(r).padStart(2, "0")}s`;
  const h = Math.floor(m / 60), rm = m % 60;
  return `${h}h${String(rm).padStart(2, "0")}m${String(r).padStart(2, "0")}s`;
};

let state = { projectId: null, versions: [], current: null, checkpoints: [], defaultCheckpoint: "" };

async function api(path, opts) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status} ${r.statusText}`);
  return body;
}

function msg(el, text, kind = "") {
  el.textContent = text || "";
  el.className = "hint " + kind;
}

/* ---------------- status (pinned sidebar) ---------------- */

function setDot(el, ok) {
  el.className = "dot " + (ok === true ? "dot-ok" : ok === false ? "dot-bad" : "dot-unknown");
}

function rows(pairs) {
  return pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

async function refreshStatus() {
  await refreshContainers();
  try {
    const s = await api("/api/comfyui/status");
    setDot($("comfy-dot"), s.connected);
    $("comfy-value").textContent = s.connected ? `${s.latency_ms} ms` : "offline";
    $("comfy-meta").textContent = s.connected ? (s.gpu || "") : (s.url || "");
    renderContainerCtl("comfy-actions", "comfy-start", "comfy-restart", "comfyui");
    if ($("comfy-detail")) $("comfy-detail").innerHTML = s.connected
      ? rows([["Status", '<span class="ok">connected</span>'], ["URL", s.url], ["Version", s.comfyui_version],
              ["Output dir", s.output_directory], ["GPU", s.gpu],
              ["VRAM", s.vram_total_mb ? `${s.vram_free_mb} / ${s.vram_total_mb} MB free` : null]])
      : rows([["Status", '<span class="bad">not connected</span>'], ["URL", s.url], ["Error", s.error]]);
  } catch (e) {
    setDot($("comfy-dot"), false); $("comfy-value").textContent = "error";
    renderContainerCtl("comfy-actions", "comfy-start", "comfy-restart", "comfyui");
  }

  try {
    const s = await api("/api/storage/status");
    const good = s.mounted && s.writable;
    setDot($("storage-dot"), good);
    $("storage-value").textContent = good ? "read/write" : s.mounted ? "read-only" : "not mounted";
    $("storage-meta").textContent = s.builds_root || "";
    if ($("storage-detail")) $("storage-detail").innerHTML = rows([
      ["Path", s.builds_root],
      ["Mounted", s.mounted ? '<span class="ok">yes</span>' : '<span class="bad">no</span>'],
      ["Writable", s.writable ? '<span class="ok">yes</span>' : '<span class="bad">no</span>'],
      ["Error", s.error],
      ["DB dir", s.db_mounted ? `${s.db_dir} (ok)` : `${s.db_dir} (missing)`],
      ["Log dir", s.log_mounted ? `${s.log_dir} (ok)` : `${s.log_dir} (missing)`]]);
  } catch (e) { setDot($("storage-dot"), false); $("storage-value").textContent = "error"; }

  if ($("builds-list")) {
    try {
      const s = await api("/api/builds");
      $("builds-list").innerHTML = !s.builds?.length
        ? '<p class="muted">No builds yet.</p>'
        : `<table><thead><tr><th>Name</th><th>lora/</th><th>images/</th><th>Images</th></tr></thead><tbody>` +
          s.builds.map((b) => `<tr><td>${esc(b.name)}</td>
            <td>${b.has_lora ? '<span class="ok">yes</span>' : '<span class="muted">—</span>'}</td>
            <td>${b.has_images ? '<span class="ok">yes</span>' : '<span class="muted">—</span>'}</td>
            <td>${b.image_count}</td></tr>`).join("") + `</tbody></table>`;
    } catch { /* non-fatal */ }
  }

  // Ollama shares the pinned sidebar block, so it rides the same poll.
  await refreshAiStatus();
}

/* ---------------- projects ---------------- */

async function loadProjects(selectId) {
  const { projects } = await api("/api/projects");
  const sel = $("project-select");
  sel.innerHTML = projects.length
    ? projects.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")
    : `<option value="">— none —</option>`;
  if (projects.length) {
    state.projectId = selectId || state.projectId || projects[0].id;
    sel.value = String(state.projectId);
    await loadProject();
  } else {
    state.projectId = null;
    $("no-project").hidden = false;
    $("studio").hidden = true;
  }
}

async function loadCheckpoints() {
  try {
    const { models, default: def } = await api("/api/models?kind=checkpoints");
    state.checkpoints = models;
    state.defaultCheckpoint = def || models[0] || "";
    $("f-checkpoint").innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    // ComfyUI lists checkpoints in folder order, so option 0 is a photoreal model.
    $("f-checkpoint").value = state.defaultCheckpoint;
  } catch {
    $("f-checkpoint").innerHTML = `<option value="">(ComfyUI unreachable)</option>`;
  }
}

async function loadProject() {
  if (!state.projectId) return;
  const detail = await api(`/api/projects/${state.projectId}`);
  state.current = detail.current_version;
  $("no-project").hidden = true;
  $("studio").hidden = false;
  $("prompt-subtitle").textContent = `${detail.project.name} — build folder: ${detail.build_dir}`;
  fillForm(state.current);
  await loadVersions();
}

function fillForm(v) {
  if (!v) return;
  $("f-character").value = v.character || "";
  $("f-style").value = v.style || "";
  $("f-negative").value = v.negative || "";
  $("f-seed").value = v.seed ?? 0;
  // Versions saved before 0.2.8 have an empty checkpoint — fall back to the
  // resolved default rather than letting the browser show option 0 (photoreal).
  if (v.checkpoint && state.checkpoints.includes(v.checkpoint)) $("f-checkpoint").value = v.checkpoint;
  else if (state.defaultCheckpoint) $("f-checkpoint").value = state.defaultCheckpoint;
  $("current-version-chip").textContent = `v${v.id}${v.signed_off ? " · signed off" : ""}`;
  $("current-version-chip").className = "chip" + (v.signed_off ? " chip-good" : "");
}

function formValues() {
  return {
    character: $("f-character").value,
    style: $("f-style").value,
    negative: $("f-negative").value,
    checkpoint: $("f-checkpoint").value,
    seed: parseInt($("f-seed").value || "0", 10),
  };
}

/* ---------------- version history (VCS-style) ---------------- */

function diffSummary(v, parent) {
  if (!parent) return '<span class="muted">initial version</span>';
  const changed = ["character", "style", "negative", "checkpoint", "seed"]
    .filter((k) => String(v[k]) !== String(parent[k]));
  if (!changed.length) return '<span class="muted">no field changes</span>';
  return changed.map((k) => `<span class="tag">${k}</span>`).join(" ");
}

async function loadVersions() {
  const data = await api(`/api/projects/${state.projectId}/versions`);
  state.versions = data.versions;
  const byId = Object.fromEntries(data.versions.map((v) => [v.id, v]));
  const list = [...data.versions].reverse();

  $("version-list").innerHTML = list.map((v) => {
    const isCurrent = v.id === data.current_version_id;
    return `<div class="version ${isCurrent ? "is-current" : ""}">
      <div class="version-rail"><span class="node ${v.signed_off ? "node-good" : ""}"></span></div>
      <div class="version-body">
        <div class="version-head">
          <strong>v${v.id}</strong>
          ${v.signed_off ? '<span class="chip chip-good">signed off</span>' : ""}
          ${isCurrent ? '<span class="chip chip-current">current</span>' : ""}
          <span class="chip chip-src">${esc(v.source)}</span>
          <span class="muted small">${esc(v.created_at)}</span>
        </div>
        ${v.note ? `<div class="version-note">${esc(v.note)}</div>` : ""}
        <div class="version-diff">${diffSummary(v, byId[v.parent_id])}</div>
        <div class="version-actions">
          ${isCurrent ? "" : `<button class="btn btn-sm" data-rollback="${v.id}">Roll back to this</button>`}
          ${v.signed_off ? "" : `<button class="btn btn-sm" data-signoff="${v.id}">Sign off</button>`}
        </div>
      </div>
    </div>`;
  }).join("");

  $("version-list").querySelectorAll("[data-rollback]").forEach((b) =>
    b.addEventListener("click", () => rollback(b.dataset.rollback)));
  $("version-list").querySelectorAll("[data-signoff]").forEach((b) =>
    b.addEventListener("click", () => signOff(b.dataset.signoff)));
}

async function rollback(vid) {
  await api(`/api/projects/${state.projectId}/rollback/${vid}`, { method: "POST" });
  msg($("studio-msg"), `Rolled back to v${vid}. Nothing was deleted.`, "ok");
  await loadProject();
}

async function signOff(vid) {
  await api(`/api/versions/${vid}/signoff`, { method: "POST" });
  msg($("studio-msg"), `v${vid} signed off as the baseline.`, "ok");
  await loadProject();
}

/* ---------------- actions ---------------- */

async function saveVersion(note = "manual edit") {
  const body = { ...formValues(), source: "manual", note };
  const { version } = await api(`/api/projects/${state.projectId}/versions`, {
    method: "POST", body: JSON.stringify(body),
  });
  await loadProject();
  return version;
}

async function generate() {
  const btn = $("generate-btn");
  btn.disabled = true;
  msg($("studio-msg"), "Generating… (first run loads the checkpoint, ~30–60s)");
  $("preview").innerHTML = '<div class="spinner"></div>';
  try {
    const res = await api(`/api/projects/${state.projectId}/generate`, {
      method: "POST",
      body: JSON.stringify({ workflow: "base-character", params: formValues() }),
    });
    const img = res.images?.[0];
    if (img) {
      const url = `/api/image?filename=${encodeURIComponent(img.filename)}&subfolder=${encodeURIComponent(img.subfolder)}`;
      state.previewUrl = url;
      $("preview").innerHTML =
        `<button class="preview-frame" id="preview-zoom" title="Click to zoom">
           <img src="${url}" alt="preview" />
         </button>
         <div class="preview-meta muted small">
           ${esc(img.subfolder)}/${esc(img.filename)}
           · <a href="${url}" target="_blank" rel="noopener">open in new tab ↗</a>
         </div>`;
      $("preview-zoom").addEventListener("click", () => openLightbox(url));
    } else {
      state.previewUrl = "";
      $("preview").innerHTML = '<p class="bad">No image returned.</p>';
    }
    msg($("studio-msg"), "Done.", "ok");
  } catch (e) {
    $("preview").innerHTML = `<p class="bad">${esc(e.message)}</p>`;
    msg($("studio-msg"), e.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- AI prompt assistant (Ollama) ---------------- */

let aiMode = "replace";
let aiUndo = null; // previous field values, for reject

async function refreshAiStatus() {
  const chip = $("ai-status");
  let s;
  try {
    s = await api("/api/ai/status");
  } catch {
    s = { reachable: false };
  }
  // Sidebar row
  setDot($("ollama-dot"), s.reachable ? (s.loaded ? true : null) : false);
  $("ollama-value").textContent = !s.reachable ? "offline" : s.loaded ? "loaded" : "idle";
  $("ollama-meta").textContent = s.reachable ? (s.model || "") : (s.url || "");
  $("ollama-actions").hidden = !s.reachable;
  $("ollama-connect").hidden = !!s.loaded;   // Connect only when not loaded
  $("ollama-unload").hidden = !s.loaded;     // Unload only when loaded
  renderContainerCtl("ollama-container-actions", "ollama-start", "ollama-restart", "ollama");
  // Studio chip
  if (!s.reachable) {
    chip.textContent = "Ollama offline";
    chip.className = "chip small chip-bad";
    chip.title = s.error || s.url || "";
  } else {
    chip.textContent = s.loaded ? "Ollama · loaded" : "Ollama · idle";
    chip.className = "chip small " + (s.loaded ? "chip-good" : "");
    chip.title = `${s.url} — ${(s.models || []).length} models`;
  }
}

async function ollamaAction(path, label) {
  const c = $("ollama-connect"), u = $("ollama-unload");
  c.disabled = u.disabled = true;
  msg($("ai-msg"), `${label}…`);
  try {
    await api(path, { method: "POST" });
    msg($("ai-msg"), `${label} done.`, "ok");
  } catch (e) {
    msg($("ai-msg"), e.message, "bad");
  } finally {
    c.disabled = u.disabled = false;
    refreshAiStatus();
  }
}

$("ollama-connect").addEventListener("click", () => ollamaAction("/api/ai/warm", "Connecting Ollama"));
$("ollama-unload").addEventListener("click", () => ollamaAction("/api/ai/unload", "Unloading model"));

/* ---------------- container control (via socket proxy) ---------------- */

async function refreshContainers() {
  try {
    state.containers = await api("/api/containers/status");
  } catch {
    state.containers = { enabled: false, containers: {} };
  }
}

// Show Start only when definitively stopped, Restart only when running; hide the
// group otherwise (disabled, unknown, or proxy unreachable).
function renderContainerCtl(actionsId, startId, restartId, key) {
  const wrap = $(actionsId), start = $(startId), restart = $(restartId);
  const enabled = state.containers?.enabled;
  const info = state.containers?.containers?.[key];
  if (!enabled || !info) { wrap.hidden = true; return; }
  start.hidden = info.running !== false;
  restart.hidden = info.running !== true;
  wrap.hidden = start.hidden && restart.hidden;
}

async function containerAction(key, action, label, force = false) {
  const url = `/api/containers/${key}/${action}` + (force ? "?force=true" : "");
  try {
    await api(url, { method: "POST" });
  } catch (e) {
    // ComfyUI refuses a restart while its queue is busy — offer to force it.
    if (/force=true/.test(e.message) && confirm(`${e.message}\n\nRestart anyway?`)) {
      return containerAction(key, action, label, true);
    }
    alert(`${label} failed: ${e.message}`);
    return;
  }
  await refreshStatus();
}

$("comfy-start").addEventListener("click", () => containerAction("comfyui", "start", "Start ComfyUI"));
$("comfy-restart").addEventListener("click", () => {
  if (confirm("Restart the ComfyUI container?")) containerAction("comfyui", "restart", "Restart ComfyUI");
});
$("ollama-start").addEventListener("click", () => containerAction("ollama", "start", "Start Ollama"));
$("ollama-restart").addEventListener("click", () => {
  if (confirm("Restart the Ollama container?")) containerAction("ollama", "restart", "Restart Ollama");
});

$("ai-mode").addEventListener("click", (e) => {
  const t = e.target.closest(".seg-tile");
  if (!t) return;
  aiMode = t.dataset.mode;
  [...$("ai-mode").children].forEach((c) => c.classList.toggle("sel", c === t));
});

const AI_FIELDS = [["Character", "character"], ["Style", "style"], ["Negative", "negative"]];
// Per-field ordered parts from the last suggestion: {eq} for unchanged text, or
// {del, ins, rejected} for a change the user can accept (default) or reject.
let aiDiffParts = { character: null, style: null, negative: null };

// LCS over whitespace-preserving tokens → ordered parts (changes grouped).
function diffParts(oldStr, newStr) {
  const a = (oldStr || "").split(/(\s+)/);
  const b = (newStr || "").split(/(\s+)/);
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const parts = [];
  let pend = null; // { del, ins } accumulating a change until the next eq run
  const flush = () => { if (pend) { parts.push({ del: pend.del, ins: pend.ins, rejected: false }); pend = null; } };
  const eq = (t) => { const last = parts[parts.length - 1]; if (last && last.eq !== undefined) last.eq += t; else parts.push({ eq: t }); };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { flush(); eq(a[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { (pend ||= { del: "", ins: "" }).del += a[i++]; }
    else { (pend ||= { del: "", ins: "" }).ins += b[j++]; }
  }
  while (i < n) { (pend ||= { del: "", ins: "" }).del += a[i++]; }
  while (j < m) { (pend ||= { del: "", ins: "" }).ins += b[j++]; }
  flush();
  return parts;
}

// Field value implied by the current accept/reject choices.
function fieldFromParts(parts) {
  return parts.map((p) => p.eq !== undefined ? p.eq : (p.rejected ? p.del : p.ins)).join("");
}

function renderDiffRow(k) {
  const parts = aiDiffParts[k];
  if (!parts) return "";
  const label = AI_FIELDS.find(([, key]) => key === k)[0];
  const html = parts.map((p, i) => {
    if (p.eq !== undefined) return esc(p.eq);
    const del = p.del ? `<del>${esc(p.del)}</del>` : "";
    const ins = p.ins ? `<ins>${esc(p.ins)}</ins>` : "";
    if (p.rejected) {
      // change undone: original text kept, the addition ghosted; button re-applies
      const kept = p.del ? esc(p.del) : `<ins class="ghost">${esc(p.ins)}</ins>`;
      return `<span class="chg rejected">${kept}` +
        `<button class="chg-x" data-f="${k}" data-i="${i}" title="Re-apply this change">↺</button></span>`;
    }
    return `<span class="chg">${del}${ins}` +
      `<button class="chg-x" data-f="${k}" data-i="${i}" title="Reject this change">×</button></span>`;
  }).join("");
  return `<div class="ai-diff-row" data-f="${k}"><span class="ai-diff-label">${label}</span>
    <div class="ai-diff-text">${html || '<span class="muted">(empty)</span>'}</div></div>`;
}

function renderAiDiff(before, after) {
  aiDiffParts = { character: null, style: null, negative: null };
  for (const [, k] of AI_FIELDS) {
    if ((before[k] || "") !== (after[k] || "")) aiDiffParts[k] = diffParts(before[k], after[k]);
  }
  paintAiDiff();
}

function paintAiDiff() {
  const box = $("ai-diff");
  const rows = AI_FIELDS.map(([, k]) => renderDiffRow(k)).filter(Boolean);
  if (!rows.length) { box.innerHTML = '<div class="ai-diff-head muted">No changes.</div>'; box.hidden = false; return; }
  box.innerHTML = `<div class="ai-diff-head"><ins>added</ins> · <del>removed</del>` +
    ` · <span class="muted">click ✕ to reject a change</span></div>` + rows.join("");
  box.hidden = false;
}

function toggleChange(k, i) {
  const parts = aiDiffParts[k];
  if (!parts || !parts[i]) return;
  parts[i].rejected = !parts[i].rejected;
  $("f-" + k).value = fieldFromParts(parts); // apply just this field's current choices
  paintAiDiff();
}

// A field's diff goes stale once the user hand-edits it — drop it so a later
// reject can't clobber the manual edit.
function dropFieldDiff(k) {
  if (!aiDiffParts[k]) return;
  aiDiffParts[k] = null;
  if (!AI_FIELDS.some(([, key]) => aiDiffParts[key])) clearAiDiff(); else paintAiDiff();
}

function clearAiDiff() {
  aiDiffParts = { character: null, style: null, negative: null };
  const b = $("ai-diff"); b.hidden = true; b.innerHTML = "";
}

async function aiSuggest() {
  const instruction = $("ai-instruction").value.trim();
  if (!instruction) return msg($("ai-msg"), "Describe what you want first.", "bad");
  const btn = $("ai-suggest-btn");
  btn.disabled = true;
  clearAiDiff();
  msg($("ai-msg"), `Asking Ollama to ${aiMode === "modify" ? "edit the prompt" : "write a prompt"}… (the first call loads the model, up to ~60s)`);
  try {
    const before = { character: $("f-character").value, style: $("f-style").value, negative: $("f-negative").value };
    const { suggestion } = await api("/api/ai/suggest-prompt", {
      method: "POST",
      body: JSON.stringify({ instruction, mode: aiMode, ...before }),
    });
    aiUndo = before;
    $("f-character").value = suggestion.character || "";
    $("f-style").value = suggestion.style || "";
    $("f-negative").value = suggestion.negative || "";
    renderAiDiff(before, suggestion);
    $("ai-msg").innerHTML =
      `<span class="ok">Applied.</span> Review the changes below — click <b>✕</b> to reject any single one, ` +
      `or <a href="#" id="ai-undo">reject all &amp; undo</a>. Then edit freely and Save.`;
    $("ai-undo").addEventListener("click", (e) => { e.preventDefault(); aiRevert(); });
  } catch (e) {
    msg($("ai-msg"), e.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

function aiRevert() {
  if (!aiUndo) return;
  $("f-character").value = aiUndo.character;
  $("f-style").value = aiUndo.style;
  $("f-negative").value = aiUndo.negative;
  aiUndo = null;
  clearAiDiff();
  msg($("ai-msg"), "Reverted to the previous prompt.", "ok");
}

$("ai-suggest-btn").addEventListener("click", aiSuggest);

// Per-change reject/re-apply (event-delegated on the diff panel).
$("ai-diff").addEventListener("click", (e) => {
  const btn = e.target.closest(".chg-x");
  if (!btn) return;
  toggleChange(btn.dataset.f, parseInt(btn.dataset.i, 10));
});
// Hand-editing a field retires its diff so a later reject can't overwrite the edit.
for (const [, k] of AI_FIELDS) {
  $("f-" + k).addEventListener("input", () => dropFieldDiff(k));
}

/* ---------------- preview lightbox ---------------- */

function openLightbox(url) {
  const box = $("lightbox");
  $("lightbox-img").src = url;
  box.hidden = false;
  document.body.classList.add("no-scroll");
}
function closeLightbox() {
  $("lightbox").hidden = true;
  $("lightbox-img").src = "";
  document.body.classList.remove("no-scroll");
}
$("lightbox").addEventListener("click", (e) => {
  // Click the backdrop or the close button collapses it; clicking the image itself does not.
  if (e.target.id !== "lightbox-img") closeLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("lightbox").hidden) closeLightbox();
});

/* ---------------- logs (terminal style — matches the house standard) ---------------- */

const LOG_RANK = { verbose: 0, debug: 1, info: 2, warn: 3, error: 4 };
let logTimer = null;
let logState = {
  minLevel: "info",
  levelOn: { error: 1, warn: 1, info: 1, debug: 1, verbose: 1 },
  catOn: { boot: 1, integration: 1, process: 1, local: 1 },
  search: "",
  autoscroll: true,
  persisted: false, // "Previous runs" shows a static file snapshot instead of live polling
  entries: [],
};

function logPasses(e) {
  const rank = LOG_RANK[e.level] ?? 1;
  return logState.levelOn[e.level] !== 0 &&
    rank >= LOG_RANK[logState.minLevel] &&
    logState.catOn[e.category] !== 0 &&
    (!logState.search || `${e.category} ${e.message}`.toLowerCase().includes(logState.search));
}

function logLineNode(e) {
  const t = (e.ts || "").replace("T", " ").replace(/\+.*$/, "").slice(11, 23); // HH:MM:SS.mmm
  const det = e.detail ? ` <span class="dt">${esc(JSON.stringify(e.detail))}</span>` : "";
  const div = document.createElement("div");
  div.className = "logline " + esc(e.level);
  div.innerHTML =
    `<span class="t">[${esc(t)}]</span>` +
    `<span class="lv">${esc((e.level || "").toUpperCase())}</span>` +
    `<span class="tg">${esc(e.category)}:</span>` +
    `<span class="mg">${esc(e.message)}${det}</span>`;
  return div;
}

function renderLogView() {
  const v = $("log-list");
  const shown = logState.entries.filter(logPasses);
  v.innerHTML = "";
  if (!shown.length) { v.innerHTML = '<div class="empty">No logs to display</div>'; }
  else { const f = document.createDocumentFragment(); shown.forEach((e) => f.appendChild(logLineNode(e))); v.appendChild(f); }
  $("log-count").textContent = logState.entries.length;
  if (logState.autoscroll) v.scrollTop = v.scrollHeight;
}

function setLogState(text, live) {
  const el = $("log-state");
  el.textContent = text;
  el.className = "wsstate " + (live ? "live" : "off");
}

async function refreshLogs() {
  if (logState.persisted) return; // static snapshot; don't overwrite with live
  try {
    const data = await api("/api/logs?limit=500");
    logState.entries = data.entries;
    setLogState("● live", true);
    renderLogView();
  } catch (e) {
    setLogState("○ disconnected", false);
  }
}

async function loadPersistedLogs() {
  try {
    const data = await api("/api/logs/persisted?limit=500");
    logState.entries = data.entries;
    setLogState("○ previous runs", false);
    renderLogView();
  } catch (e) { $("log-list").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function startLogPolling(on) {
  clearInterval(logTimer);
  if (on) logTimer = setInterval(() => { if (!$("view-logs").hidden) refreshLogs(); }, 4000);
}

/* ---------------- dataset builder (Phase B) ---------------- */

let dsTimer = null;

function dsImageUrl(img) {
  return `/api/image?filename=${encodeURIComponent(img.filename)}&subfolder=${encodeURIComponent(img.subfolder)}`;
}

function stopDatasetPolling() { clearInterval(dsTimer); dsTimer = null; }

function startDatasetPolling() {
  if (dsTimer) return;
  dsTimer = setInterval(() => {
    if ($("view-dataset").hidden) return stopDatasetPolling();
    loadDataset();
  }, 3000);
}

async function loadDataset() {
  const noproj = $("dataset-noproject"), main = $("dataset-main");
  if (!state.projectId) { noproj.hidden = false; main.hidden = true; return; }
  noproj.hidden = true; main.hidden = false;
  try {
    const data = await api(`/api/projects/${state.projectId}/dataset`);
    renderDataset(data);
    if (data.generating) startDatasetPolling(); else stopDatasetPolling();
  } catch (e) {
    msg($("ds-msg"), e.message, "bad");
  }
}

function renderDataset(data) {
  const { target, counts, reached, generating, images } = data;
  if ($("ds-target").value === "" || document.activeElement !== $("ds-target")) $("ds-target").value = target;
  $("ds-count").textContent = `${counts.selected} / ${target} selected`;
  $("ds-count").className = reached ? "ok" : "";
  $("ds-candidates").textContent = `${counts.candidates} candidate${counts.candidates === 1 ? "" : "s"}`;
  const pct = Math.min(100, target ? Math.round((counts.selected / target) * 100) : 0);
  $("ds-fill").style.width = pct + "%";
  $("ds-fill").classList.toggle("full", reached);
  $("ds-genstate").textContent = generating ? `generating… ${counts.pending} left in queue` : "";

  const grid = $("ds-grid");
  if (!images.length) {
    grid.innerHTML = '<p class="muted" id="ds-empty">No candidates yet — hit Generate 30.</p>';
    return;
  }
  grid.innerHTML = images.map((img) =>
    `<button type="button" class="ds-thumb${img.selected ? " sel" : ""}" data-id="${img.id}"
       title="${esc(img.filename)}">
       <img src="${dsImageUrl(img)}" alt="candidate" loading="lazy" />
       <span class="ds-check">✓</span>
       <span class="ds-zoom" role="button" aria-label="Zoom" title="Zoom to examine">⤢</span>
     </button>`).join("");
}

async function toggleDatasetSelect(id, el) {
  const nowSel = !el.classList.contains("sel");
  el.classList.toggle("sel", nowSel); // optimistic
  try {
    await api(`/api/projects/${state.projectId}/dataset/select`, {
      method: "POST",
      body: JSON.stringify({ image_id: id, selected: nowSel }),
    });
    loadDataset(); // refresh counts/progress
  } catch (e) {
    el.classList.toggle("sel", !nowSel); // revert
    msg($("ds-msg"), e.message, "bad");
  }
}

async function datasetGenerate(count) {
  if (!state.projectId) return;
  const btnA = $("ds-generate"), btnB = $("ds-more");
  btnA.disabled = btnB.disabled = true;
  msg($("ds-msg"), `Queuing ${count} image${count === 1 ? "" : "s"}…`);
  try {
    const { queued } = await api(`/api/projects/${state.projectId}/dataset/generate`, {
      method: "POST",
      body: JSON.stringify({ count }),
    });
    msg($("ds-msg"), `Queued ${queued}. They'll appear below as ComfyUI finishes them.`, "ok");
    startDatasetPolling();
    loadDataset();
  } catch (e) {
    msg($("ds-msg"), e.message, "bad");
  } finally {
    btnA.disabled = btnB.disabled = false;
  }
}

$("ds-grid").addEventListener("click", (e) => {
  const thumb = e.target.closest(".ds-thumb");
  if (!thumb) return;
  // The zoom badge opens the lightbox and does NOT toggle selection.
  if (e.target.closest(".ds-zoom")) {
    const img = thumb.querySelector("img");
    if (img) openLightbox(img.src);
    return;
  }
  toggleDatasetSelect(parseInt(thumb.dataset.id, 10), thumb);
});
$("ds-generate").addEventListener("click", () => datasetGenerate(30));
$("ds-more").addEventListener("click", () => datasetGenerate(10));
$("ds-target").addEventListener("change", async () => {
  const target = parseInt($("ds-target").value, 10);
  if (!target || target < 1 || !state.projectId) return;
  try {
    await api(`/api/projects/${state.projectId}/dataset/target`, {
      method: "POST", body: JSON.stringify({ target }),
    });
    loadDataset();
  } catch (e) { msg($("ds-msg"), e.message, "bad"); }
});

/* ---------------- LoRA (Phase C) ---------------- */

let loraTimer = null;
function stopLoraPolling() { clearInterval(loraTimer); loraTimer = null; }
function startLoraPolling() {
  if (loraTimer) return;
  loraTimer = setInterval(() => {
    if ($("view-lora").hidden) return stopLoraPolling();
    loadLora();
  }, 5000);
}

async function loadLora() {
  const np = $("lora-noproject"), main = $("lora-main");
  if (!state.projectId) { np.hidden = false; main.hidden = true; return; }
  np.hidden = true; main.hidden = false;
  try {
    const d = await api(`/api/projects/${state.projectId}/lora`);
    $("lora-selected").textContent = `${d.selected_count} selected`;
    if (document.activeElement !== $("lora-trigger")) $("lora-trigger").value = d.trigger_word;
    $("lora-staged").innerHTML = d.staged
      ? `<span class="ok">yes → input/${esc(d.input_folder)}</span>`
      : `<span class="muted">not staged</span>`;
    $("lora-list").innerHTML = d.loras.length
      ? d.loras.map((l) => `<div class="small">${esc(l)}</div>`).join("")
      : '<p class="muted">None yet.</p>';
    // training state (with live elapsed + ETA from the previous run)
    const ts = d.train_status;
    const st = $("lora-train-status");
    const lastNote = d.last_train_seconds
      ? ` — previous run: ${fmtDur(d.last_train_seconds)}${d.last_train_steps ? ` (${d.last_train_steps} steps)` : ""}`
      : "";
    if (ts === "training") {
      let t = "training… on the GPU";
      if (d.elapsed_seconds != null) t += ` · ${fmtDur(d.elapsed_seconds)} elapsed`;
      if (d.remaining_seconds != null) t += ` · ~${fmtDur(d.remaining_seconds)} left (est. from previous run)`;
      else if (lastNote) t += lastNote;
      st.textContent = t;
    } else if (ts === "done") {
      st.textContent = `last run finished ✓${lastNote}`;
    } else if (ts === "error") {
      st.textContent = `last run failed — check the logs${lastNote}`;
    } else {
      st.textContent = lastNote ? lastNote.replace(/^ — /, "") : "";
    }
    st.className = "lora-train-status small " + (ts === "training" ? "warn" : ts === "done" ? "ok" : ts === "error" ? "bad" : "muted");
    $("lora-train-btn").disabled = ts === "training";
    if (ts === "training") startLoraPolling(); else stopLoraPolling();
    loadBuild();
  } catch (e) { msg($("lora-msg"), e.message, "bad"); }
}

$("lora-trigger-save").addEventListener("click", async () => {
  const t = $("lora-trigger").value.trim();
  if (!t || !state.projectId) return;
  try {
    const r = await api(`/api/projects/${state.projectId}/lora/trigger`, {
      method: "POST", body: JSON.stringify({ trigger_word: t }),
    });
    $("lora-trigger").value = r.trigger_word;
    msg($("lora-msg"), `Trigger word set: ${r.trigger_word}`, "ok");
  } catch (e) { msg($("lora-msg"), e.message, "bad"); }
});

$("lora-stage").addEventListener("click", async () => {
  if (!state.projectId) return;
  const btn = $("lora-stage");
  btn.disabled = true;
  msg($("lora-msg"), "Staging selected images to ComfyUI…");
  try {
    const r = await api(`/api/projects/${state.projectId}/lora/stage`, { method: "POST" });
    msg($("lora-msg"), `Staged ${r.staged}/${r.total} image(s) to input/${r.input_folder}.`, "ok");
    loadLora();
  } catch (e) { msg($("lora-msg"), e.message, "bad"); }
  finally { btn.disabled = false; }
});

$("lora-train-btn").addEventListener("click", async () => {
  if (!state.projectId) return;
  const steps = parseInt($("lora-steps").value, 10) || 500;
  const rank = parseInt($("lora-rank").value, 10) || 16;
  const learning_rate = parseFloat($("lora-lr").value) || 0.0005;
  const btn = $("lora-train-btn");
  btn.disabled = true;
  msg($("lora-msg"), "Freeing VRAM and starting training…");
  try {
    const r = await api(`/api/projects/${state.projectId}/lora/train`, {
      method: "POST", body: JSON.stringify({ steps, rank, learning_rate }),
    });
    msg($("lora-msg"), `Training started — ${r.steps} steps, rank ${r.rank}. It'll show under Trained LoRAs when done.`, "ok");
    startLoraPolling();
    loadLora();
  } catch (e) { msg($("lora-msg"), e.message, "bad"); btn.disabled = false; }
});

/* ---------------- unattended build (Phase 7, job engine) ---------------- */

let buildTimer = null;
function stopBuildPolling() { clearInterval(buildTimer); buildTimer = null; }
function startBuildPolling() {
  if (buildTimer) return;
  buildTimer = setInterval(() => {
    if ($("view-lora").hidden) return stopBuildPolling();
    loadBuild();
  }, 5000);
}

async function loadBuild() {
  if (!state.projectId) return;
  try {
    const d = await api(`/api/projects/${state.projectId}/jobs`);
    const build = (d.jobs || []).find((j) => j.kind === "lora_build"); // list is newest-first
    const st = $("build-status"), prog = $("build-progress"), bar = $("build-bar");
    const btn = $("build-btn"), m = $("build-msg");
    if (!build) { st.textContent = ""; st.className = "lora-train-status small muted"; prog.hidden = true; btn.disabled = false; stopBuildPolling(); return; }
    const active = build.status === "queued" || build.status === "running";
    btn.disabled = active;
    st.textContent = build.status === "running" ? `running · ${build.stage || "starting"}`
      : build.status === "queued" ? "queued"
      : build.status === "done" ? "done ✓"
      : build.status === "error" ? "failed"
      : build.status === "canceled" ? "canceled" : build.status;
    st.className = "lora-train-status small " + (active ? "warn" : build.status === "done" ? "ok" : build.status === "error" ? "bad" : "muted");
    prog.hidden = false;
    bar.style.width = Math.round((build.progress || 0) * 100) + "%";
    bar.classList.toggle("full", build.status === "done");
    m.textContent = build.message || "";
    if (active) startBuildPolling(); else stopBuildPolling();
  } catch (e) { /* soft — the LoRA tab already surfaces project errors */ }
}

$("build-btn").addEventListener("click", async () => {
  if (!state.projectId) return;
  const params = {
    steps: parseInt($("build-steps").value, 10) || 1500,
    rank: parseInt($("build-rank").value, 10) || 16,
    lora_strength: parseFloat($("build-strength").value) || 1.0,
    preset: "expressions",
  };
  const btn = $("build-btn");
  btn.disabled = true;
  msg($("lora-build-err"), "Starting build…");
  try {
    await api(`/api/projects/${state.projectId}/jobs`, {
      method: "POST", body: JSON.stringify({ kind: "lora_build", params }),
    });
    msg($("lora-build-err"), "Build started — it runs on the server. You can close the tab and come back.", "ok");
    startBuildPolling();
    loadBuild();
  } catch (e) { msg($("lora-build-err"), e.message, "bad"); btn.disabled = false; }
});

/* ---------------- poses (Phase D) ---------------- */

let posesTimer = null;
let posesCache = [];
let selectedPoseId = null;
let exportGenerating = false;

function poseImageUrl(p) {
  if (!p.filename) return "";
  return `/api/image?filename=${encodeURIComponent(p.filename)}&subfolder=${encodeURIComponent(p.subfolder)}`;
}
function stopPosesPolling() { clearInterval(posesTimer); posesTimer = null; }
function startPosesPolling() {
  if (posesTimer) return;
  posesTimer = setInterval(() => {
    if ($("view-poses").hidden) return stopPosesPolling();
    loadPoses();
  }, 3000);
}

async function loadPoses() {
  const np = $("poses-noproject"), main = $("poses-main");
  if (!state.projectId) { np.hidden = false; main.hidden = true; return; }
  np.hidden = true; main.hidden = false;
  try {
    const data = await api(`/api/projects/${state.projectId}/poses`);
    posesCache = data.poses;
    renderPosesGrid(data.poses);
    $("poses-genstate").textContent = data.generating
      ? `rendering… ${data.counts.pending} in queue` : "";
    if (selectedPoseId != null) {
      const cur = posesCache.find((p) => p.id === selectedPoseId);
      if (cur) refreshPosePreview(cur); else closePoseEditor();
    }
    await loadExport();
    if (data.generating || exportGenerating) startPosesPolling(); else stopPosesPolling();
  } catch (e) { msg($("poses-msg"), e.message, "bad"); }
}

async function loadPoseConfig() {
  if (!state.projectId) { $("pose-lora-panel").hidden = true; return; }
  try {
    const d = await api(`/api/projects/${state.projectId}/pose-config`);
    const sel = $("pose-lora-select");
    if (document.activeElement !== sel) {
      const opts = ['<option value="">None — base character (no LoRA)</option>'].concat(
        d.loras.map((l) =>
          `<option value="${esc(l.name)}"${l.name === d.selected ? " selected" : ""}>` +
          `${esc(l.name)}${l.comfy_visible ? "" : " — not in ComfyUI"}</option>`)
      );
      sel.innerHTML = opts.join("");
      sel.value = d.selected || "";
    }
    if (document.activeElement !== $("pose-lora-strength")) $("pose-lora-strength").value = d.strength;

    const hint = $("pose-lora-hint");
    if (!d.loras.length) {
      hint.innerHTML = d.train_status === "training"
        ? "Training in progress — the LoRA will appear here when it finishes."
        : "No trained LoRA yet. Train one on the <strong>LoRA</strong> tab to keep poses on-model; " +
          "for now poses render from the base character prompt.";
      hint.className = "hint muted";
    } else if (d.needs_extra_paths) {
      hint.innerHTML = "Trained LoRA found on disk but ComfyUI can’t see it yet. Add " +
        "<code>persona_forge:</code> → <code>loras: /builds</code> to ComfyUI’s " +
        "<code>extra_model_paths.yaml</code> and restart ComfyUI, then Apply.";
      hint.className = "hint bad";
    } else if (d.selected) {
      hint.innerHTML = `Pose renders load <strong>${esc(d.selected)}</strong> and prepend trigger ` +
        `<code>${esc(d.trigger_word)}</code>. Regenerate poses after changing this.`;
      hint.className = "hint ok";
    } else {
      hint.innerHTML = `A trained LoRA is available — select it to keep poses on-model ` +
        `(trigger <code>${esc(d.trigger_word)}</code>).`;
      hint.className = "hint muted";
    }
    $("pose-lora-panel").hidden = false;
  } catch (e) { /* pose-config is best-effort; don't block the poses grid */ }
}

$("pose-lora-save").addEventListener("click", async () => {
  if (!state.projectId) return;
  const lora = $("pose-lora-select").value;
  const strength = parseFloat($("pose-lora-strength").value) || 1.0;
  const btn = $("pose-lora-save");
  btn.disabled = true;
  try {
    await api(`/api/projects/${state.projectId}/pose-lora`, {
      method: "POST", body: JSON.stringify({ lora, strength }),
    });
    msg($("poses-msg"), lora
      ? `LoRA applied: ${lora}. Regenerate poses to render with it.`
      : "LoRA cleared — poses will render from the base character.", "ok");
    loadPoseConfig();
  } catch (e) { msg($("poses-msg"), e.message, "bad"); }
  finally { btn.disabled = false; }
});

function poseStatusBadge(p) {
  if (p.status === "pending") return '<span class="pose-badge b-pending">rendering…</span>';
  if (p.status === "error") return '<span class="pose-badge b-error">failed</span>';
  if (p.status === "empty" || !p.filename) return '<span class="pose-badge b-empty">not rendered</span>';
  return "";
}

function renderPosesGrid(poses) {
  const grid = $("poses-grid");
  if (!poses.length) {
    grid.innerHTML = '<p class="muted">No poses yet — add one or load a preset.</p>';
    return;
  }
  grid.innerHTML = poses.map((p) => {
    const img = p.filename
      ? `<img src="${poseImageUrl(p)}" alt="${esc(p.name)}" loading="lazy" />`
      : '<div class="pose-empty">—</div>';
    return `<button type="button" class="pose-card${p.id === selectedPoseId ? " sel" : ""}" data-id="${p.id}">
      <div class="pose-thumb">${img}${poseStatusBadge(p)}</div>
      <div class="pose-name">${esc(p.name)}</div>
    </button>`;
  }).join("");
}

function selectPose(id) {
  selectedPoseId = id;
  const p = posesCache.find((x) => x.id === id);
  if (!p) return;
  $("poses-editor").hidden = false;
  $("pose-ed-title").textContent = p.name;
  $("pose-ed-name").value = p.name;
  $("pose-ed-modifier").value = p.modifier || "";
  $("pose-ed-ai").value = "";
  msg($("pose-ed-msg"), "");
  refreshPosePreview(p);
  renderPosesGrid(posesCache); // reflect selection ring
}

function refreshPosePreview(p) {
  const box = $("pose-ed-preview");
  const url = poseImageUrl(p);
  box.innerHTML = url
    ? `<button type="button" class="pose-zoom-frame" id="pose-ed-zoomimg"><img src="${url}" alt="${esc(p.name)}" /></button>`
    : `<div class="pose-empty-lg">${p.status === "pending" ? "rendering…" : "not rendered yet"}</div>`;
  const z = $("pose-ed-zoomimg");
  if (z) z.addEventListener("click", () => openLightbox(url));
}

function closePoseEditor() { selectedPoseId = null; $("poses-editor").hidden = true; renderPosesGrid(posesCache); }

async function savePose(regen) {
  if (selectedPoseId == null) return;
  const name = $("pose-ed-name").value.trim();
  const modifier = $("pose-ed-modifier").value.trim();
  if (!name) return msg($("pose-ed-msg"), "Name can't be empty.", "bad");
  try {
    await api(`/api/projects/${state.projectId}/poses/${selectedPoseId}`, {
      method: "PATCH", body: JSON.stringify({ name, modifier }),
    });
    if (regen) {
      await api(`/api/projects/${state.projectId}/poses/${selectedPoseId}/generate`, { method: "POST" });
      msg($("pose-ed-msg"), "Saved — regenerating…", "ok");
      startPosesPolling();
    } else {
      msg($("pose-ed-msg"), "Saved.", "ok");
    }
    loadPoses();
  } catch (e) { msg($("pose-ed-msg"), e.message, "bad"); }
}

async function poseAiSuggest() {
  const instruction = $("pose-ed-ai").value.trim();
  if (!instruction || selectedPoseId == null) return;
  const btn = $("pose-ed-ai-btn");
  btn.disabled = true;
  msg($("pose-ed-msg"), "Asking Ollama…");
  try {
    const { modifier } = await api(`/api/projects/${state.projectId}/poses/${selectedPoseId}/ai`, {
      method: "POST", body: JSON.stringify({ instruction }),
    });
    $("pose-ed-modifier").value = modifier;
    msg($("pose-ed-msg"), "Applied to the field. Review, then Save & regenerate.", "ok");
  } catch (e) { msg($("pose-ed-msg"), e.message, "bad"); }
  finally { btn.disabled = false; }
}

async function addPose() {
  if (!state.projectId) return;
  try {
    const p = await api(`/api/projects/${state.projectId}/poses`, {
      method: "POST", body: JSON.stringify({ name: "New pose", modifier: "" }),
    });
    await loadPoses();
    selectPose(p.id);
    $("pose-ed-name").focus();
    $("pose-ed-name").select();
  } catch (e) { msg($("poses-msg"), e.message, "bad"); }
}

async function posesPreset(preset) {
  try {
    const r = await api(`/api/projects/${state.projectId}/poses/preset`, {
      method: "POST", body: JSON.stringify({ preset }),
    });
    msg($("poses-msg"), r.added ? `Added ${r.added} pose(s).` : "Those poses already exist.", "ok");
    loadPoses();
  } catch (e) { msg($("poses-msg"), e.message, "bad"); }
}

async function posesGenerateAll() {
  if (!state.projectId) return;
  const btn = $("poses-generate-all");
  btn.disabled = true;
  msg($("poses-msg"), "Queuing renders…");
  try {
    const { queued } = await api(`/api/projects/${state.projectId}/poses/generate-all`, { method: "POST" });
    msg($("poses-msg"), `Queued ${queued} render(s).`, "ok");
    startPosesPolling();
    loadPoses();
  } catch (e) { msg($("poses-msg"), e.message, "bad"); }
  finally { btn.disabled = false; }
}

async function deletePose() {
  if (selectedPoseId == null) return;
  const p = posesCache.find((x) => x.id === selectedPoseId);
  if (!confirm(`Delete pose "${p ? p.name : ""}"?`)) return;
  try {
    await api(`/api/projects/${state.projectId}/poses/${selectedPoseId}`, { method: "DELETE" });
    closePoseEditor();
    loadPoses();
  } catch (e) { msg($("pose-ed-msg"), e.message, "bad"); }
}

/* ---- export to SillyTavern (0.6.1) ---- */
async function loadExport() {
  if (!state.projectId) { exportGenerating = false; return; }
  try {
    const d = await api(`/api/projects/${state.projectId}/poses/export`);
    exportGenerating = d.generating;
    renderExport(d);
  } catch (e) { /* soft — the poses grid already surfaces project errors */ }
}

function renderExport(d) {
  $("poses-export-btn").disabled = !d.exportable || d.generating;
  $("poses-export-folder").textContent = d.folder ? `/builds/${d.folder}` : "";
  const st = $("poses-export-state");
  if (d.generating) st.textContent = `removing backgrounds… ${d.counts.pending} left`;
  else if (d.counts.done) st.textContent =
    `${d.counts.done} sprite(s) ready${d.counts.error ? `, ${d.counts.error} failed` : ""}`;
  else st.textContent = d.exportable ? `${d.exportable} rendered pose(s) ready to export` : "render some poses first";
  $("poses-export-grid").innerHTML = (d.sprites || []).map((s) => {
    const url = `/api/image?filename=${encodeURIComponent(s.filename)}&subfolder=${encodeURIComponent(s.subfolder)}`;
    return `<figure class="sprite-card"><div class="sprite-thumb"><img src="${url}" loading="lazy" alt="${esc(s.target_name)}" /></div>` +
      `<figcaption>${esc(s.target_name)}</figcaption></figure>`;
  }).join("");
}

async function startExport() {
  if (!state.projectId) return;
  const btn = $("poses-export-btn");
  btn.disabled = true;
  msg($("poses-export-msg"), "Removing backgrounds…");
  try {
    const r = await api(`/api/projects/${state.projectId}/poses/export`, { method: "POST" });
    msg($("poses-export-msg"),
      `Exporting ${r.queued} sprite(s) → /builds/${r.folder}`, "ok");
    exportGenerating = true;
    startPosesPolling();
    loadExport();
  } catch (e) { msg($("poses-export-msg"), e.message, "bad"); btn.disabled = false; }
}

$("poses-export-btn").addEventListener("click", startExport);

$("poses-grid").addEventListener("click", (e) => {
  const card = e.target.closest(".pose-card");
  if (card) selectPose(parseInt(card.dataset.id, 10));
});
$("poses-generate-all").addEventListener("click", posesGenerateAll);
$("poses-add").addEventListener("click", addPose);
$("poses-preset-starter").addEventListener("click", () => posesPreset("starter"));
$("poses-preset-expr").addEventListener("click", () => posesPreset("expressions"));
$("pose-ed-close").addEventListener("click", closePoseEditor);
$("pose-ed-save").addEventListener("click", () => savePose(false));
$("pose-ed-regen").addEventListener("click", () => savePose(true));
$("pose-ed-ai-btn").addEventListener("click", poseAiSuggest);
$("pose-ed-delete").addEventListener("click", deletePose);
$("pose-ed-zoom").addEventListener("click", () => {
  const p = posesCache.find((x) => x.id === selectedPoseId);
  const url = p && poseImageUrl(p);
  if (url) openLightbox(url); else msg($("pose-ed-msg"), "No image yet — regenerate first.", "bad");
});

/* ---------------- wiring ---------------- */

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  document.querySelectorAll(".nav-item[data-view]").forEach((a) =>
    a.classList.toggle("is-active", a.dataset.view === name));
}

document.querySelectorAll(".nav-item[data-view]").forEach((a) =>
  a.addEventListener("click", (e) => {
    e.preventDefault();
    showView(a.dataset.view);
    refreshStatus();
    if (a.dataset.view === "logs") refreshLogs();
    if (a.dataset.view === "dataset") loadDataset();
    else stopDatasetPolling();
    if (a.dataset.view === "lora") loadLora();
    if (a.dataset.view === "poses") { loadPoses(); loadPoseConfig(); }
    else stopPosesPolling();
  }));

$("log-search").addEventListener("input", (e) => {
  logState.search = e.target.value.toLowerCase().trim();
  renderLogView();
});
$("log-minlevel").addEventListener("change", (e) => { logState.minLevel = e.target.value; renderLogView(); });
$("log-level-chips").addEventListener("click", (e) => {
  const c = e.target.closest(".chip"); if (!c) return;
  logState.levelOn[c.dataset.lvl] = logState.levelOn[c.dataset.lvl] ? 0 : 1;
  c.classList.toggle("on");
  renderLogView();
});
$("log-cat-chips").addEventListener("click", (e) => {
  const c = e.target.closest(".chip"); if (!c) return;
  logState.catOn[c.dataset.cat] = logState.catOn[c.dataset.cat] ? 0 : 1;
  c.classList.toggle("on");
  renderLogView();
});
$("log-autoscroll").addEventListener("click", () => {
  logState.autoscroll = !logState.autoscroll;
  $("log-autoscroll").classList.toggle("on", logState.autoscroll);
  if (logState.autoscroll) renderLogView();
});
$("log-clear").addEventListener("click", () => { logState.entries = []; renderLogView(); });
$("log-persisted").addEventListener("click", () => {
  logState.persisted = !logState.persisted;
  $("log-persisted").classList.toggle("on", logState.persisted);
  if (logState.persisted) loadPersistedLogs(); else refreshLogs();
});

$("project-select").addEventListener("change", (e) => {
  state.projectId = parseInt(e.target.value, 10) || null;
  loadProject().catch((err) => msg($("studio-msg"), err.message, "bad"));
  stopDatasetPolling();
  stopPosesPolling();
  stopLoraPolling();
  stopBuildPolling();
  selectedPoseId = null;
  if (!$("view-dataset").hidden) loadDataset();
  if (!$("view-lora").hidden) loadLora();
  if (!$("view-poses").hidden) { loadPoses(); loadPoseConfig(); }
});

$("generate-btn").addEventListener("click", () => generate());
$("reroll-seed").addEventListener("click", () => { $("f-seed").value = Math.floor(Math.random() * 2 ** 31); });

$("save-version-btn").addEventListener("click", async () => {
  try { const v = await saveVersion(); msg($("studio-msg"), `Saved as v${v.id}.`, "ok"); }
  catch (e) { msg($("studio-msg"), e.message, "bad"); }
});

$("signoff-btn").addEventListener("click", async () => {
  try {
    // capture any unsaved edits first so the baseline matches what's on screen
    const cur = state.current || {};
    const f = formValues();
    const dirty = ["character", "style", "negative", "checkpoint"].some((k) => f[k] !== (cur[k] ?? "")) ||
                  f.seed !== cur.seed;
    const v = dirty ? await saveVersion("signed-off baseline") : cur;
    await signOff(v.id);
  } catch (e) { msg($("studio-msg"), e.message, "bad"); }
});

// modal — shared by "new" and "clone"
let modalMode = "new";
function openModal(mode = "new") {
  modalMode = mode;
  const cloning = mode === "clone";
  $("np-title").textContent = cloning ? "Clone persona" : "New persona";
  $("np-blurb").innerHTML = cloning
    ? "Copies the current prompt into a new persona so you can vary it — e.g. the same character skiing vs. on the beach. Identity is kept, so the parent's LoRA can be reused later."
    : "Creates a build folder with <code>lora/</code> and <code>images/</code> in the shared builds root.";
  $("np-style-wrap").hidden = !cloning;
  $("np-name").value = "";
  $("np-style").value = cloning ? ($("f-style").value || "") : "";
  msg($("np-msg"), "");
  $("modal").hidden = false;
  $("np-name").focus();
}
$("new-project-btn").addEventListener("click", () => openModal("new"));
$("empty-new-project").addEventListener("click", () => openModal("new"));
$("clone-project-btn").addEventListener("click", () => {
  if (!state.projectId) return msg($("studio-msg"), "Select a persona to clone first.", "bad");
  openModal("clone");
});
$("np-cancel").addEventListener("click", () => ($("modal").hidden = true));
$("np-create").addEventListener("click", async () => {
  const name = $("np-name").value.trim();
  if (!name) return msg($("np-msg"), "Give it a name.", "bad");
  try {
    const detail = modalMode === "clone"
      ? await api(`/api/projects/${state.projectId}/clone`, {
          method: "POST",
          body: JSON.stringify({ name, style: $("np-style").value }),
        })
      : await api("/api/projects", {
          method: "POST",
          body: JSON.stringify({
            name,
            character: $("f-character").value || "",
            style: $("f-style").value || "",
            negative: $("f-negative").value || "",
            checkpoint: $("f-checkpoint").value || "",
            seed: parseInt($("f-seed").value || "123456789", 10),
          }),
        });
    $("modal").hidden = true;
    await loadProjects(detail.project.id);
    msg($("studio-msg"),
      (modalMode === "clone" ? `Cloned to "${detail.project.name}" → ` : `Created "${detail.project.name}" → `) + detail.build_dir,
      "ok");
  } catch (e) { msg($("np-msg"), e.message, "bad"); }
});

/* ---------------- boot ---------------- */

(async function boot() {
  try { $("app-version").textContent = "v" + (await api("/api/health")).version; } catch {}
  showView("prompt");
  await refreshStatus();
  await loadCheckpoints();
  await loadProjects().catch((e) => msg($("studio-msg"), e.message, "bad"));
  setInterval(refreshStatus, POLL_MS);
  startLogPolling(true);
})();
