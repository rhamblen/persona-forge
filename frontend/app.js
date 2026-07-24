// Persona Forge — Prompt Studio (phase 2).
// Vanilla JS on purpose: keeps the image build-step-free until the UI references
// land and we commit to a framework.

const POLL_MS = 15000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let state = { projectId: null, versions: [], current: null, checkpoints: [] };

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
  try {
    const s = await api("/api/comfyui/status");
    setDot($("comfy-dot"), s.connected);
    $("comfy-value").textContent = s.connected ? `${s.latency_ms} ms` : "offline";
    $("comfy-meta").textContent = s.connected ? (s.gpu || "") : (s.url || "");
    if ($("comfy-detail")) $("comfy-detail").innerHTML = s.connected
      ? rows([["Status", '<span class="ok">connected</span>'], ["URL", s.url], ["Version", s.comfyui_version],
              ["Output dir", s.output_directory], ["GPU", s.gpu],
              ["VRAM", s.vram_total_mb ? `${s.vram_free_mb} / ${s.vram_total_mb} MB free` : null]])
      : rows([["Status", '<span class="bad">not connected</span>'], ["URL", s.url], ["Error", s.error]]);
  } catch (e) { setDot($("comfy-dot"), false); $("comfy-value").textContent = "error"; }

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
      ["Error", s.error]]);
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
    const { models } = await api("/api/models?kind=checkpoints");
    state.checkpoints = models;
    $("f-checkpoint").innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
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
  if (v.checkpoint && state.checkpoints.includes(v.checkpoint)) $("f-checkpoint").value = v.checkpoint;
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
    $("preview").innerHTML = img
      ? `<img src="/api/image?filename=${encodeURIComponent(img.filename)}&subfolder=${encodeURIComponent(img.subfolder)}" alt="preview" />
         <div class="preview-meta muted small">${esc(img.subfolder)}/${esc(img.filename)}</div>`
      : '<p class="bad">No image returned.</p>';
    msg($("studio-msg"), "Done.", "ok");
  } catch (e) {
    $("preview").innerHTML = `<p class="bad">${esc(e.message)}</p>`;
    msg($("studio-msg"), e.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- wiring ---------------- */

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  document.querySelectorAll(".nav-item[data-view]").forEach((a) =>
    a.classList.toggle("is-active", a.dataset.view === name));
}

document.querySelectorAll(".nav-item[data-view]").forEach((a) =>
  a.addEventListener("click", (e) => { e.preventDefault(); showView(a.dataset.view); refreshStatus(); }));

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

// modal
const openModal = () => { $("modal").hidden = false; $("np-name").value = ""; msg($("np-msg"), ""); $("np-name").focus(); };
$("new-project-btn").addEventListener("click", openModal);
$("empty-new-project").addEventListener("click", openModal);
$("np-cancel").addEventListener("click", () => ($("modal").hidden = true));
$("np-create").addEventListener("click", async () => {
  const name = $("np-name").value.trim();
  if (!name) return msg($("np-msg"), "Give it a name.", "bad");
  try {
    const detail = await api("/api/projects", {
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
    msg($("studio-msg"), `Created "${detail.project.name}" → ${detail.build_dir}`, "ok");
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
})();
