// ib · forge — crafting control dashboard. Live websocket telemetry, two bot
// panels built from a <template>, per-bot craft controls. Mirrors the healer
// app.js conventions (theme persistence, post helpers, ws auto-reconnect).
"use strict";
const { $, pct } = ibUI;   // shared helpers from ui-core.js (web_common, P5.4)
const clamp = (v) => Math.max(0, Math.min(1, v ?? 0));

// ---- theme persistence ----------------------------------------------------
ibUI.theme($("theme"), "ibf-theme");

// ---- action helpers -------------------------------------------------------
function post(url, body) {
  fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).catch(() => {});
}

// ---- console connect modal (web console + copy-paste tunnel command) -------
const IB_LAN = "10.0.0.16";          // server LAN IP (home network)
const IB_SSH_USER = "jbaker";
function openConsoleModal(title, spicePort) {
  const sp = spicePort || 5910;
  const localPort = 5950 + Math.floor((sp - 5900) / 10);   // 5910->5951, 5920->5952
  const cmd = `ssh -fN -L ${localPort}:127.0.0.1:${sp} ${IB_SSH_USER}@${IB_LAN} 2>/dev/null; `
            + `remote-viewer spice://127.0.0.1:${localPort}`;
  $("cmTitle").textContent = `Connect — ${title}`;
  $("cmCmd").textContent = cmd;
  $("cmWeb").onclick = () => {     // same-origin: works on LAN AND through Cloudflare
    window.open(`${location.origin}/spice/console.html?port=${sp}`,
      "ibweb", "width=1300,height=820,menubar=no,toolbar=no,location=no");
  };
  // one-click native viewer (works if this machine ran the ib-console installer)
  $("cmNative").onclick = () => { window.location.href = `ibconsole://open?port=${sp}`; };
  $("cmCopy").onclick = () => navigator.clipboard.writeText(cmd).then(() => {
    $("cmCopy").textContent = "Copied!";
    setTimeout(() => ($("cmCopy").textContent = "Copy"), 1500);
  });
  $("consoleModal").hidden = false;
}
$("cmClose").onclick = () => ($("consoleModal").hidden = true);
$("consoleModal").onclick = (e) => { if (e.target.id === "consoleModal") $("consoleModal").hidden = true; };

// ---- keymap editor modal (embeds keymap.html chrome-stripped) --------------
const keymapModal = $("keymapModal"), kmFrame = $("kmFrame"), keymapBtn = $("keymapBtn");
function openKeymap() {
  if (!keymapModal || !kmFrame) return;
  kmFrame.src = "/keymap.html?embed=1&t=" + Date.now();      // fresh each open
  keymapModal.hidden = false;
}
function closeKeymap() { if (keymapModal) { keymapModal.hidden = true; kmFrame.src = "about:blank"; } }
if (keymapBtn) keymapBtn.onclick = openKeymap;
ibNotify.phone(document.getElementById("phoneBtn"));
if ($("kmClose")) $("kmClose").onclick = closeKeymap;
if (keymapModal) keymapModal.onclick = (e) => { if (e.target === keymapModal) closeKeymap(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && keymapModal && !keymapModal.hidden) closeKeymap(); });

// ---- bot panels -----------------------------------------------------------
const botEls = {};          // id -> { root, refs..., uiMode, queueSig }
const tpl = $("botTpl");

// Crafter roster: [{character, class, vm}]. Each bot's dropdown shows only the
// crafters for its VM, labelled "class (character)"; selecting one sets both the
// bot's character (for char-select) and trade_class (for crafting).
let crafters = [];
const craftersForVm = (vm) => crafters.filter((c) => c.vm === vm);

// saved profit-craft lists {name: [{name,count}]} — loaded into a bot's queue.
let forgeLists = {};
function populateListSelects() {
  const names = Object.keys(forgeLists).sort();
  Object.values(botEls).forEach((refs) => {
    if (!refs.listsel) return;
    const cur = refs.listsel.value;
    refs.listsel.innerHTML = `<option value="">— saved lists —</option>` +
      names.map((n) => `<option value="${n.replace(/"/g, "&quot;")}">${n}</option>`).join("");
    if (cur && forgeLists[cur]) refs.listsel.value = cur;
  });
}
function fetchLists() {
  fetch("/api/forgelists").then((r) => r.json()).then((d) => {
    forgeLists = d.lists || {}; populateListSelects();
  }).catch(() => {});
}
function saveLists() { post("/api/forgelists", { lists: forgeLists }); }
const crafterLabel = (c) => (c.class ? `${c.class} (${c.character})` : `(${c.character}) — no class`);
const selectedCrafter = (refs) =>
  crafters.find((c) => c.vm === refs.vm && c.character === refs.trade.value) || null;
function refreshCrafterOptions() {
  Object.values(botEls).forEach((refs) => {
    const cur = refs.trade.value;
    const list = craftersForVm(refs.vm);
    refs.trade.innerHTML = list.map((c) =>
      `<option value="${c.character}">${crafterLabel(c)}</option>`).join("");
    if (cur && list.some((c) => c.character === cur)) refs.trade.value = cur;
  });
}

function buildBotPanel(bot, tradeClasses) {
  const root = tpl.content.firstElementChild.cloneNode(true);
  root.dataset.bot = bot.id;
  const q = (sel) => root.querySelector(sel);

  // static identity
  q(".bot-name").textContent = bot.label || bot.id;
  q(".bot-char").textContent = bot.character || "—";
  q(".bot-dom").textContent = bot.dom || "—";

  // crafter options — only the crafters on THIS bot's VM, "class (character)"
  const trade = q(".bot-trade");
  trade.innerHTML = craftersForVm(bot.vm).map((c) =>
    `<option value="${c.character}">${crafterLabel(c)}</option>`).join("");

  const refs = {
    root,
    state: q(".bot-state"),
    agent: q(".bot-agent"),
    enable: q(".bot-enable"),
    charName: q(".bot-char"),
    console: q(".bot-console"),
    live: q(".bot-live-frame"),
    tabs: [...root.querySelectorAll(".mode-tab")],
    paneSingle: q(".pane-single"),
    paneWrit: q(".pane-writ"),
    trade,
    recipe: q(".bot-recipe"),
    search: q(".bot-search"),
    count: q(".bot-count"),
    ocr: q(".bot-ocr"),
    readlog: q(".bot-readlog"),
    scribe: q(".bot-scribe"),
    addrow: q(".bot-addrow"),
    queue: q(".bot-queue"),
    stations: q(".bot-stations"),
    station: "",                 // selected table filter ("" = all)
    listsel: q(".bot-listsel"),
    listload: q(".bot-listload"),
    listsave: q(".bot-listsave"),
    listdel: q(".bot-listdel"),
    progRecipe: q(".prog-recipe"),
    progItem: q(".prog-item"),
    progFill: q(".prog-fill"),
    progTxt: q(".prog-txt"),
    stDone: q(".st-done"),
    stReact: q(".st-react"),
    stRate: q(".st-rate"),
    stModeStat: q(".stat.dura"),
    stMode: q(".st-mode"),
    power: q(".bot-power"),
    stPower: q(".st-power"),
    shutdone: q(".bot-shutdone"),
    start: q(".bot-start"),
    stop: q(".bot-stop"),
    pause: q(".bot-pause"),
    launch: q(".bot-launch"),
    camp: q(".bot-camp"),
    switch: q(".bot-switch"),
    shutdown: q(".bot-shutdown"),
    debug: q(".bot-debug"),
    debugWrap: q(".bot-debug-wrap"),
    debugList: q(".bot-debug-list"),
    debugOn: false,
    log: q(".bot-log"),
    vm: bot.vm || "",
    uiMode: bot.mode || "single",
    queueSig: "",
  };
  botEls[bot.id] = refs;
  // unique radio-group name per card so the writ-mode toggle is mutually exclusive WITHIN a
  // card but independent across cards
  root.querySelectorAll(".bot-writmode").forEach((r) => { r.name = "writmode-" + bot.id; });

  // ---- wire controls ----
  const id = bot.id;
  refs.enable.onchange = () => post(`/api/bot/${id}/enable`, { on: refs.enable.checked });
  refs.tabs.forEach((tab) => (tab.onclick = () => {
    refs.uiMode = tab.dataset.mode;
    applyMode(refs);
    post(`/api/bot/${id}/config`, { mode: refs.uiMode });
  }));
  refs.trade.onchange = () => {
    const c = selectedCrafter(refs);
    if (c) post(`/api/bot/${id}/config`, { character: c.character, trade_class: c.class });
  };
  refs.shutdone.onchange = () => post(`/api/bot/${id}/config`, { shutdown_when_done: refs.shutdone.checked });
  refs.recipe.onchange = () => post(`/api/bot/${id}/config`, { recipe: refs.recipe.value });
  refs.search.onchange = () => post(`/api/bot/${id}/config`, { search: refs.search.value });
  refs.count.onchange = () => post(`/api/bot/${id}/config`, { count: parseInt(refs.count.value) || 1 });
  refs.ocr.onclick = () => post(`/api/bot/${id}/ocr`);
  refs.readlog.onclick = () => post(`/api/bot/${id}/readlog`);
  // toggling capture: first click marks the log, second reads only what was scribed since.
  // Send the dropdown's crafter so the backend knows whose log to read even if the
  // crafter dropdown's onchange never fired (it shows a default without registering).
  refs.scribe.onclick = () => {
    const c = selectedCrafter(refs);
    post(`/api/bot/${id}/${refs.scribeMarked ? "scriberead" : "scribemark"}`,
         c ? { character: c.character, trade_class: c.class } : {});
  };
  refs.addrow.onclick = () => { pushQueueRow(refs, { name: "", count: 1 }); saveQueue(id, refs); };
  // saved lists: load fills the queue; save names the current queue; delete removes
  refs.listload.onclick = () => {
    const items = forgeLists[refs.listsel.value];
    if (items) post(`/api/bot/${id}/queue`, { queue: items });
  };
  refs.listsave.onclick = () => {
    const items = readQueueDom(refs);
    if (!items.length) { alert("Queue is empty — add recipes first."); return; }
    const name = (prompt("Save this queue as list name:", refs.listsel.value || "") || "").trim();
    if (!name) return;
    forgeLists[name] = items;
    saveLists();
    setTimeout(() => { fetchLists(); refs.listsel.value = name; }, 150);
  };
  refs.listdel.onclick = () => {
    const n = refs.listsel.value;
    if (n && forgeLists[n] && confirm(`Delete list "${n}"?`)) {
      delete forgeLists[n]; saveLists(); setTimeout(fetchLists, 150);
    }
  };
  refs.start.onclick = () => {
    const c = selectedCrafter(refs);
    const wm = (refs.root.querySelector(".bot-writmode:checked") || {}).value || "standard";
    post(`/api/bot/${id}/start`, {
      mode: refs.uiMode, trade_class: c ? c.class : "",
      recipe: refs.recipe.value, search: refs.search.value,
      count: parseInt(refs.count.value) || 1,
      station: refs.uiMode === "writ" ? (refs.station || "") : "",   // craft only the shown table
      writ_mode: refs.uiMode === "writ" ? wm : "standard",
    });
  };
  refs.stop.onclick = () => post(`/api/bot/${id}/stop`);
  refs.pause.onclick = () => post(`/api/bot/${id}/pause`);
  refs.launch.onclick = () => {
    const c = selectedCrafter(refs);   // send the dropdown's crafter so the backend
    post(`/api/bot/${id}/launch`, c ? { character: c.character, trade_class: c.class } : {});
  };
  // --- OCR debug: toggle capture + browse the ring buffer -------------------
  function renderDebug(st) {
    refs.debugOn = !!(st && st.enabled);
    refs.debug.textContent = "🐞 Debug: " + (refs.debugOn ? "ON" : "off");
    refs.debug.classList.toggle("on", refs.debugOn);
    refs.debugWrap.hidden = !refs.debugOn;
    if (!refs.debugOn) return;
    const rows = (st.log || []).slice().reverse();       // newest first
    refs.debugList.innerHTML = rows.length ? "" : "<li class=\"dbg-empty\">no captures yet — run a recipe</li>";
    rows.forEach((line) => {
      const li = document.createElement("li");
      li.className = "dbg-row";
      const m = line.match(/->\s*(\S+\.png)/);            // pull the screenshot name out of the log line
      li.textContent = line;
      if (m) {
        li.classList.add("has-shot");
        li.title = "open frame " + m[1];
        li.onclick = () => window.open(`/api/bot/${id}/debug/shot/${encodeURIComponent(m[1])}`, "_blank");
      }
      refs.debugList.appendChild(li);
    });
  }
  async function loadDebug() {
    try { renderDebug(await (await fetch(`/api/bot/${id}/debug`)).json()); } catch (_) {}
  }
  refs.debug.onclick = async () => {
    try {
      const r = await (await fetch(`/api/bot/${id}/debug`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: !refs.debugOn }) })).json();
      refs.debugOn = !!r.enabled;
    } catch (_) {}
    loadDebug();
  };
  loadDebug();                                           // reflect persisted state on load
  refs._debugTimer = setInterval(() => { if (refs.debugOn) loadDebug(); }, 4000);
  refs.camp.onclick = () => post(`/api/bot/${id}/camp`);
  refs.switch.onclick = () => post(`/api/bot/${id}/switch`);
  refs.shutdown.onclick = () => {
    if (confirm(`Shut down ${refs.root.querySelector(".bot-name").textContent}? Quits EQ2 and powers off the VM.`))
      post(`/api/bot/${id}/shutdown`);
  };
  refs.console.onclick = () => openConsoleModal(`${bot.label || id} (${bot.dom || ""})`, bot.spice_port);
  refs.live.onclick = refs.console.onclick;

  // initial input values
  if (bot.character) refs.trade.value = bot.character;
  applyMode(refs);
  $("bots").appendChild(root);
  return refs;
}

function applyMode(refs) {
  refs.tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === refs.uiMode));
  refs.paneSingle.hidden = refs.uiMode !== "single";
  refs.paneWrit.hidden = refs.uiMode !== "writ";
}

// ---- writ queue editing ---------------------------------------------------
function queueRowFocused(refs) {
  return refs.queue.contains(document.activeElement);
}
// chars a recipe never has (only letters, digits, space, apostrophe, parens are ok)
function oddChars(s) {
  const m = (s || "").match(/[^A-Za-z0-9 '()]/g);
  return m ? [...new Set(m)].sort().join("") : "";
}
function pushQueueRow(refs, item) {
  const li = document.createElement("li");
  li.className = "qrow" + (item.done >= item.count && item.count ? " done" : "");
  li.dataset.station = item.station || "";
  li.dataset.verified = item.verified ? "1" : "0";
  li.dataset.warn = item.warn || "";
  const badge = item.warn
    ? `<span class="q-station warn" title="OCR found unexpected character(s): ${item.warn} — recipes only use ' and (). Edit the name to fix.">⚠</span>`
    : item.station
    ? `<span class="q-station" title="${item.station}">${item.station}</span>`
    : `<span class="q-station ${item.verified ? "" : "warn"}" title="${item.verified ? "" : "not in recipe DB — verify by hand"}">${item.verified ? "—" : "⚠"}</span>`;
  const nameTitle = item.warn ? ` title="OCR found unexpected char(s): ${item.warn} — fix the name (recipes only use ' and ())"` : "";
  li.innerHTML = badge +
    `<input class="qname${item.warn ? " charwarn" : ""}"${nameTitle} type="text" value="${(item.name || "").replace(/"/g, "&quot;")}" placeholder="recipe name" />` +
    `<input class="qsearch" type="text" maxlength="18" value="${(item.search || "").replace(/"/g, "&quot;")}" placeholder="search (blank=name)" />` +
    `<input class="qcount" type="number" min="1" max="999" value="${item.count || 1}" />` +
    `<button class="qdel" title="remove">×</button>`;
  const id = refs.root.dataset.bot;
  const nameInp = li.querySelector(".qname");
  nameInp.oninput = () => {                      // live: clear/keep the odd-char flag as they fix it
    const w = oddChars(nameInp.value);
    nameInp.classList.toggle("charwarn", !!w);
    li.dataset.warn = w;
  };
  nameInp.onchange = () => saveQueue(id, refs);
  li.querySelector(".qsearch").onchange = () => saveQueue(id, refs);
  li.querySelector(".qcount").onchange = () => saveQueue(id, refs);
  li.querySelector(".qdel").onclick = () => { li.remove(); saveQueue(id, refs); };
  refs.queue.appendChild(li);
}
function readQueueDom(refs) {
  return [...refs.queue.querySelectorAll(".qrow")].map((row) => ({
    name: row.querySelector(".qname").value.trim(),
    search: row.querySelector(".qsearch").value.trim(),
    count: parseInt(row.querySelector(".qcount").value) || 1,
    station: row.dataset.station || "",
    verified: row.dataset.verified === "1",
    warn: oddChars(row.querySelector(".qname").value),   // recompute from current name
  })).filter((it) => it.name);
}
function saveQueue(id, refs) {
  post(`/api/bot/${id}/queue`, { queue: readQueueDom(refs) });
}
// Radio bar of the crafting tables present in THIS writ. Selecting one filters the queue
// view AND scopes Start to that table (owner reads one journal, crafts table-by-table).
function renderStations(refs, queue) {
  const stations = [...new Set((queue || []).map((q) => q.station).filter(Boolean))].sort();
  const bar = refs.stations;
  if (stations.length < 2) {                 // single (or no) table -> no filter needed
    bar.hidden = true; bar.innerHTML = ""; refs.station = ""; applyStationFilter(refs); return;
  }
  if (refs.station && refs.station !== "all" && !stations.includes(refs.station)) refs.station = "";
  bar.hidden = false;
  const mk = (val, label, count) => {
    const b = document.createElement("button");
    b.className = "st-chip" + ((refs.station || "all") === val ? " active" : "");
    b.textContent = count != null ? `${label} (${count})` : label;
    b.onclick = () => { refs.station = val === "all" ? "" : val; renderStations(refs, queue); applyStationFilter(refs); };
    return b;
  };
  bar.replaceChildren(mk("all", "All", (queue || []).length),
    ...stations.map((s) => mk(s, s, (queue || []).filter((q) => q.station === s).length)));
}
function applyStationFilter(refs) {
  const sel = refs.station || "";
  refs.queue.querySelectorAll(".qrow").forEach((row) => {
    row.style.display = (!sel || row.dataset.station === sel) ? "" : "none";
  });
}
function renderQueue(refs, queue) {
  // only rebuild from telemetry when the queue actually changed AND the user
  // isn't mid-edit (so typing/counts aren't clobbered by the ~1Hz stream).
  const sig = (queue || []).map((q) => `${q.name}:${q.search || ""}:${q.count}:${q.done}:${q.station || ""}`).join("|");
  if (sig === refs.queueSig || queueRowFocused(refs)) return;
  refs.queueSig = sig;
  refs.queue.innerHTML = "";
  (queue || []).forEach((it) => pushQueueRow(refs, it));
  renderStations(refs, queue);
  applyStationFilter(refs);
}

// ---- per-bot render -------------------------------------------------------
function updateBotPanel(refs, bot) {
  refs.root.classList.toggle("disabled", !bot.enabled);
  if (document.activeElement !== refs.enable) refs.enable.checked = !!bot.enabled;

  const st = bot.state || "off";
  refs.state.textContent = st.replace("_", " ");
  refs.state.className = "state-pill bot-state s-" + st;
  if (refs.agent) {
    const up = !!bot.agent_up;
    refs.agent.classList.toggle("up", up);
    refs.agent.classList.toggle("down", !up);
    refs.agent.title = up ? "in-guest reflex agent: UP (fast reactions)"
                          : "in-guest reflex agent: down (host-side fallback)";
  }

  // selected crafter (dropdown value = character; don't clobber an open dropdown)
  if (document.activeElement !== refs.trade && bot.character && refs.trade.value !== bot.character)
    refs.trade.value = bot.character;
  // header shows the selected character
  if (refs.charName) refs.charName.textContent = bot.character || "—";
  // recipe / count only when not focused
  if (document.activeElement !== refs.recipe && bot.recipe && refs.uiMode === "single")
    if (!refs.recipe.value) refs.recipe.placeholder = bot.recipe;
  if (document.activeElement !== refs.search && bot.search && refs.uiMode === "single")
    if (!refs.search.value) refs.search.placeholder = bot.search;

  if (refs.scribe) {
    refs.scribeMarked = !!bot.scribe_marked;
    refs.scribe.textContent = bot.scribe_marked ? "📖 Read scribed ▸" : "📖 Mark for scribe";
    refs.scribe.classList.toggle("armed", !!bot.scribe_marked);
  }

  if (refs.shutdone && document.activeElement !== refs.shutdone)
    refs.shutdone.checked = !!bot.shutdown_when_done;

  renderQueue(refs, bot.queue);

  // progress
  const c = bot.count || { done: 0, total: 0 };
  refs.progRecipe.textContent = bot.recipe || "—";
  const item = bot.item || { idx: 0, total: 0 };
  refs.progItem.textContent = item.total ? `recipe ${item.idx}/${item.total}` : "";
  const frac = c.total ? c.done / c.total : 0;
  refs.progFill.style.width = clamp(frac) * 100 + "%";
  refs.progTxt.textContent = c.total ? `${c.done}/${c.total}` : "";
  refs.stDone.textContent = bot.crafts_done ?? 0;
  refs.stReact.textContent = bot.reactions ?? 0;
  refs.stRate.textContent = bot.crafts_per_hr ?? 0;
  const mode = bot.durability_mode;
  refs.stMode.textContent = mode ? (mode === "progress" ? "progress" : "durability") : "—";
  refs.stModeStat.classList.toggle("progress", mode === "progress");
  refs.stModeStat.classList.toggle("durability", mode === "durability");

  const pw = bot.power ?? 0;
  refs.power.style.width = pct(pw) + "%";
  refs.power.classList.toggle("gated", !!bot.power_gated);
  refs.stPower.textContent = pct(pw) + "%" + (bot.power_gated ? " ⏳" : "");

  // per-bot console log
  renderLog(refs.log, bot.log || []);

  // TRACK-FAILURES notification: the worker stashes a failure_report when a tracked list
  // finishes. Pop it ONCE (newer ts than last shown) and only when something actually failed.
  const fr = bot.failure_report;
  if (fr && fr.ts && fr.ts !== refs.lastFailTs) {
    refs.lastFailTs = fr.ts;
    if ((fr.items || []).length) showFailureModal(bot.id, bot.character || "crafter", fr);
  }
}

function showFailureModal(botId, char, report) {
  document.getElementById("failModal")?.remove();
  const rows = (report.items || [])
    .map((it) => `<li><b>${it.count}×</b> ${escapeHtml(it.name)}</li>`).join("");
  const n = (report.items || []).reduce((a, it) => a + (it.count || 0), 0);
  const ov = document.createElement("div");
  ov.id = "failModal";
  ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;"
    + "align-items:center;justify-content:center;z-index:9999";
  ov.innerHTML =
    `<div style="background:#1b1d24;color:#e6e6e6;border:1px solid #3a3f4b;border-radius:10px;`
    + `max-width:480px;width:90%;max-height:80vh;overflow:auto;padding:18px 20px;`
    + `box-shadow:0 12px 40px rgba(0,0,0,.5);font:14px system-ui,sans-serif">`
    + `<div style="font-size:16px;font-weight:700;margin-bottom:6px">⚑ Craft failures — ${escapeHtml(char)}</div>`
    + `<div style="color:#c9c9d2;margin-bottom:10px">${n} craft(s) across ${(report.items || []).length} `
    + `recipe(s) didn't succeed and were saved as list <b style="color:#9fd6ff">${escapeHtml(report.list)}</b>.</div>`
    + `<ul style="margin:0 0 14px 18px;padding:0;line-height:1.7">${rows}</ul>`
    + `<div style="display:flex;gap:8px;justify-content:flex-end">`
    + `<button id="failLoad" style="padding:7px 12px;border-radius:6px;border:0;background:#2f6f4f;color:#fff;cursor:pointer">Load into queue</button>`
    + `<button id="failClose" style="padding:7px 12px;border-radius:6px;border:1px solid #3a3f4b;background:#2a2d36;color:#e6e6e6;cursor:pointer">Close</button>`
    + `</div></div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  ov.querySelector("#failClose").onclick = close;
  ov.querySelector("#failLoad").onclick = () => {
    post(`/api/bot/${botId}/queue`, { queue: report.items });
    const refs = botEls[botId];
    if (refs && refs.listsel) setTimeout(() => { fetchLists(); refs.listsel.value = report.list; }, 150);
    close();
  };
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const logSigs = new Map();
function renderLog(ul, log) {
  const sig = log.length ? log[log.length - 1].ts + ":" + log.length : "0";
  if (logSigs.get(ul) === sig) return;
  logSigs.set(ul, sig);
  ul.innerHTML = "";
  log.slice(-40).forEach((e) => {
    const li = document.createElement("li");
    const t = new Date(e.ts * 1000).toLocaleTimeString();
    li.innerHTML = `<span class="lt">${t.slice(0, 8)}</span>${e.text}`;
    ul.appendChild(li);
  });
  ul.scrollTop = ul.scrollHeight;
}

// ---- shared event stream --------------------------------------------------
let lastEvTs = 0;
function renderEvents(events) {
  const ev = $("events");
  ev.innerHTML = "";
  const latest = events.length ? events[events.length - 1].ts : 0;
  events.slice(-60).reverse().forEach((e) => {
    const li = document.createElement("li");
    if (e.ts === latest && latest !== lastEvTs) li.className = "fresh";
    const t = new Date(e.ts * 1000).toLocaleTimeString();
    li.innerHTML =
      `<span class="t">${t}</span><span class="b">${e.bot || ""}</span>` +
      `<span class="k" data-k="${e.kind}">${e.kind}</span><span>${e.detail}</span>`;
    ev.appendChild(li);
  });
  lastEvTs = latest;
  const c = $("evCount");
  if (c) c.textContent = events.length ? `${events.length}` : "";
}

// ---- top render -----------------------------------------------------------
let built = false;
function render(s) {
  const order = s.order || [];
  const bots = s.bots || {};
  const cr = s.crafters || [];
  const crChanged = JSON.stringify(cr) !== JSON.stringify(crafters);
  crafters = cr;
  if (!built && order.length) {
    order.forEach((id) => buildBotPanel(bots[id]));
    built = true;
    fetchLists();
  } else if (crChanged) {
    refreshCrafterOptions();
  }
  order.forEach((id) => {
    const refs = botEls[id];
    if (refs && bots[id]) updateBotPanel(refs, bots[id]);
  });
  renderEvents(s.events || []);
  ibNotify.fromSnapshot(s);
}

// ---- global controls ------------------------------------------------------
$("allLaunch").onclick = () => {
  Object.keys(botEls).forEach((id) => {
    const c = selectedCrafter(botEls[id]);
    post(`/api/bot/${id}/launch`, c ? { character: c.character, trade_class: c.class } : {});
  });
};
$("allStop").onclick = () => {
  if (confirm("Stop ALL bots?")) Object.keys(botEls).forEach((id) => post(`/api/bot/${id}/stop`));
};
const allCamp = $("allCamp");
if (allCamp) allCamp.onclick = () => {
  if (confirm("Camp ALL bots out to char-select?")) post("/api/campall");
};
const allShutdown = $("allShutdown");
if (allShutdown) allShutdown.onclick = () => {
  if (confirm("Shut down ALL forge VMs? Quits EQ2 and powers them off.")) post("/api/shutdownall");
};

// ---- live VM screen per bot -----------------------------------------------
// Poll each bot's frame endpoint; swap the panel background only once the new
// JPEG decodes (no broken-image flash). 503 (VM not grabbable) -> keep "no signal".
const frameLoading = {};
const frameUrls = {};
function refreshFrames() {
  if (document.hidden) return;
  Object.keys(botEls).forEach((id) => {
    if (frameLoading[id]) return;
    const refs = botEls[id];
    if (!refs || !refs.live) return;
    frameLoading[id] = true;
    fetch(`/api/bot/${id}/frame.jpg?t=${Date.now()}`).then(async (r) => {
      if (r.status === 200) {
        const url = URL.createObjectURL(await r.blob());
        refs.live.style.backgroundImage = `url(${url})`;
        refs.live.classList.add("has-img");
        refs.live.classList.remove("powered-off");
        if (frameUrls[id]) URL.revokeObjectURL(frameUrls[id]);
        frameUrls[id] = url;
      } else if (r.status === 409) {
        // VM powered off -> drop the stale frame, show the placeholder.
        refs.live.style.backgroundImage = "";
        refs.live.classList.remove("has-img");
        refs.live.classList.add("powered-off");
        if (frameUrls[id]) { URL.revokeObjectURL(frameUrls[id]); frameUrls[id] = ""; }
      } else {
        refs.live.classList.remove("has-img");   // 503: transient, keep last
      }
    }).catch(() => { refs.live.classList.remove("has-img"); })
      .finally(() => { frameLoading[id] = false; });
  });
}
setInterval(refreshFrames, 1500);

// ---- websocket with auto-reconnect ---------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => $("conn").classList.add("on");
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => { $("conn").classList.remove("on"); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
}
connect();
