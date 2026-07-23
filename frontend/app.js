// Persona Forge 0.1.x — skeleton wiring.
// Polls the two infrastructure checks and renders them into the pinned sidebar
// status block plus the overview cards.

const POLL_MS = 15000;

const $ = (id) => document.getElementById(id);

function setDot(el, state) {
  el.className = "dot " + (state === true ? "dot-ok" : state === false ? "dot-bad" : "dot-unknown");
}

function rows(pairs) {
  return pairs
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
    .join("");
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function refreshComfy() {
  try {
    const s = await getJSON("/api/comfyui/status");
    setDot($("comfy-dot"), s.connected);
    $("comfy-value").textContent = s.connected ? `${s.latency_ms} ms` : "offline";
    $("comfy-meta").textContent = s.connected ? (s.gpu || "") : (s.url || "");
    $("comfy-detail").innerHTML = s.connected
      ? rows([
          ["Status", '<span class="ok">connected</span>'],
          ["URL", s.url],
          ["Version", s.comfyui_version],
          ["Python", s.python_version],
          ["GPU", s.gpu],
          ["VRAM", s.vram_total_mb ? `${s.vram_free_mb} / ${s.vram_total_mb} MB free` : null],
        ])
      : rows([
          ["Status", '<span class="bad">not connected</span>'],
          ["URL", s.url],
          ["Error", s.error],
        ]);
  } catch (e) {
    setDot($("comfy-dot"), false);
    $("comfy-value").textContent = "error";
    $("comfy-detail").innerHTML = rows([["Error", e.message]]);
  }
}

async function refreshStorage() {
  try {
    const s = await getJSON("/api/storage/status");
    const good = s.mounted && s.writable;
    setDot($("storage-dot"), good);
    $("storage-value").textContent = good ? "read/write" : s.mounted ? "read-only" : "not mounted";
    $("storage-meta").textContent = s.builds_root || "";
    $("storage-detail").innerHTML = rows([
      ["Path", s.builds_root],
      ["Mounted", s.mounted ? '<span class="ok">yes</span>' : '<span class="bad">no</span>'],
      ["Writable", s.writable ? '<span class="ok">yes</span>' : '<span class="bad">no</span>'],
      ["Error", s.error],
      ["Appdata", s.appdata_mounted ? `${s.appdata_root} (ok)` : `${s.appdata_root} (missing)`],
    ]);
  } catch (e) {
    setDot($("storage-dot"), false);
    $("storage-value").textContent = "error";
    $("storage-detail").innerHTML = rows([["Error", e.message]]);
  }
}

async function refreshBuilds() {
  try {
    const s = await getJSON("/api/builds");
    if (s.error) {
      $("builds-list").innerHTML = `<p class="bad">${s.error}</p>`;
      return;
    }
    if (!s.builds.length) {
      $("builds-list").innerHTML =
        '<p class="muted">No builds yet. Creating a named project in Phase&nbsp;2 will add one here.</p>';
      return;
    }
    $("builds-list").innerHTML =
      `<table><thead><tr><th>Name</th><th>lora/</th><th>images/</th><th>Images</th></tr></thead><tbody>` +
      s.builds
        .map(
          (b) =>
            `<tr><td>${b.name}</td>` +
            `<td>${b.has_lora ? '<span class="ok">yes</span>' : '<span class="muted">—</span>'}</td>` +
            `<td>${b.has_images ? '<span class="ok">yes</span>' : '<span class="muted">—</span>'}</td>` +
            `<td>${b.image_count}</td></tr>`
        )
        .join("") +
      `</tbody></table>`;
  } catch (e) {
    $("builds-list").innerHTML = `<p class="bad">${e.message}</p>`;
  }
}

async function refreshVersion() {
  try {
    const h = await getJSON("/api/health");
    $("app-version").textContent = "v" + h.version;
  } catch {
    $("app-version").textContent = "—";
  }
}

async function refreshAll() {
  await Promise.all([refreshComfy(), refreshStorage(), refreshBuilds()]);
}

refreshVersion();
refreshAll();
setInterval(refreshAll, POLL_MS);
