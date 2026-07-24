// Persona Forge — Prompt Studio (phase 2).
// Vanilla JS on purpose: keeps the image build-step-free until the UI references
// land and we commit to a framework.

const POLL_MS = 15000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

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

async function aiSuggest() {
  const instruction = $("ai-instruction").value.trim();
  if (!instruction) return msg($("ai-msg"), "Describe what you want first.", "bad");
  const btn = $("ai-suggest-btn");
  btn.disabled = true;
  msg($("ai-msg"), `Asking Ollama to ${aiMode === "modify" ? "edit the prompt" : "write a prompt"}… (the first call loads the model, up to ~60s)`);
  try {
    const { suggestion } = await api("/api/ai/suggest-prompt", {
      method: "POST",
      body: JSON.stringify({
        instruction,
        mode: aiMode,
        character: $("f-character").value,
        style: $("f-style").value,
        negative: $("f-negative").value,
      }),
    });
    aiUndo = { character: $("f-character").value, style: $("f-style").value, negative: $("f-negative").value };
    $("f-character").value = suggestion.character || "";
    $("f-style").value = suggestion.style || "";
    $("f-negative").value = suggestion.negative || "";
    $("ai-msg").innerHTML =
      `<span class="ok">Applied to the three fields.</span> Edit freely, then Save as new version — or ` +
      `<a href="#" id="ai-undo">reject and undo</a>.`;
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
  msg($("ai-msg"), "Reverted to the previous prompt.", "ok");
}

$("ai-suggest-btn").addEventListener("click", aiSuggest);

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

/* ---------------- logs ---------------- */

let logTimer = null;
let logFilters = { level: "info", category: "all", follow: true };

function initSegments() {
  // Only the log-filter segments; other .seg groups (e.g. the AI mode toggle) own
  // their own handlers.
  document.querySelectorAll("#log-level.seg, #log-category.seg").forEach((seg) => {
    seg.addEventListener("click", (e) => {
      const tile = e.target.closest(".seg-tile");
      if (!tile) return;
      seg.querySelectorAll(".seg-tile").forEach((t) => t.classList.toggle("sel", t === tile));
      logFilters[seg.id === "log-level" ? "level" : "category"] = tile.dataset.v;
      refreshLogs();
    });
  });
}

function renderLogs(entries, append = false) {
  const box = $("log-list");
  if (!entries.length && !append) { box.innerHTML = '<p class="muted">No matching entries.</p>'; return; }
  const html = entries.map((e) => {
    const det = e.detail ? `<div class="log-detail">${esc(JSON.stringify(e.detail))}</div>` : "";
    const t = (e.ts || "").replace("T", " ").replace(/\+.*$/, "").slice(0, 23);
    return `<div class="log-row log-${esc(e.level)}">
      <span class="log-ts">${esc(t)}</span>
      <span class="log-lvl lvl-${esc(e.level)}">${esc(e.level)}</span>
      <span class="log-cat cat-${esc(e.category)}">${esc(e.category)}</span>
      <span class="log-msg">${esc(e.message)}${det}</span>
    </div>`;
  }).join("");
  if (append) box.insertAdjacentHTML("beforeend", html); else box.innerHTML = html;
  if (logFilters.follow) box.scrollTop = box.scrollHeight;
}

async function refreshLogs() {
  const qs = new URLSearchParams({
    level: logFilters.level,
    category: logFilters.category,
    limit: "400",
  });
  const search = $("log-search").value.trim();
  if (search) qs.set("search", search);
  try {
    const data = await api(`/api/logs?${qs}`);
    renderLogs(data.entries);
    const st = data.stats;
    $("log-stats").textContent =
      `${st.buffered}/${st.ring_max} buffered · ` +
      Object.entries(st.by_level).map(([k, v]) => `${k}:${v}`).join("  ") +
      ` · file ${(st.file_bytes / 1024).toFixed(0)} KB`;
  } catch (e) {
    $("log-list").innerHTML = `<p class="bad">${esc(e.message)}</p>`;
  }
}

async function loadPersistedLogs() {
  try {
    const data = await api("/api/logs/persisted?limit=500");
    renderLogs(data.entries);
    $("log-stats").textContent = `showing ${data.entries.length} entries from the log file (previous runs included)`;
  } catch (e) { $("log-list").innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
}

function startLogPolling(on) {
  clearInterval(logTimer);
  if (on) logTimer = setInterval(() => { if (!$("view-logs").hidden) refreshLogs(); }, 4000);
}

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
  }));

initSegments();
$("log-search").addEventListener("input", () => { clearTimeout(window._ls); window._ls = setTimeout(refreshLogs, 300); });
$("log-refresh").addEventListener("click", refreshLogs);
$("log-persisted").addEventListener("click", loadPersistedLogs);
$("log-follow").classList.add("sel");
$("log-follow").addEventListener("click", () => {
  logFilters.follow = !logFilters.follow;
  $("log-follow").classList.toggle("sel", logFilters.follow);
  $("log-follow").setAttribute("aria-pressed", String(logFilters.follow));
  startLogPolling(logFilters.follow);
});

$("project-select").addEventListener("change", (e) => {
  state.projectId = parseInt(e.target.value, 10) || null;
  loadProject().catch((err) => msg($("studio-msg"), err.message, "bad"));
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
