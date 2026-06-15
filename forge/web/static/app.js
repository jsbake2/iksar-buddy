// ib · forge — crafting control dashboard. Live websocket telemetry, two bot
// panels built from a <template>, per-bot craft controls. Mirrors the healer
// app.js conventions (theme persistence, post helpers, ws auto-reconnect).
"use strict";
const $ = (id) => document.getElementById(id);
const pct = (v) => Math.round((v ?? 0) * 100);
const clamp = (v) => Math.max(0, Math.min(1, v ?? 0));

// ---- theme persistence ----------------------------------------------------
const themeSel = $("theme");
const savedTheme = localStorage.getItem("ibf-theme");
if (savedTheme) { document.documentElement.dataset.theme = savedTheme; themeSel.value = savedTheme; }
themeSel.onchange = () => {
  document.documentElement.dataset.theme = themeSel.value;
  localStorage.setItem("ibf-theme", themeSel.value);
};

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
    addrow: q(".bot-addrow"),
    queue: q(".bot-queue"),
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
    start: q(".bot-start"),
    stop: q(".bot-stop"),
    pause: q(".bot-pause"),
    launch: q(".bot-launch"),
    camp: q(".bot-camp"),
    switch: q(".bot-switch"),
    shutdown: q(".bot-shutdown"),
    log: q(".bot-log"),
    vm: bot.vm || "",
    uiMode: bot.mode || "single",
    queueSig: "",
  };
  botEls[bot.id] = refs;

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
  refs.recipe.onchange = () => post(`/api/bot/${id}/config`, { recipe: refs.recipe.value });
  refs.search.onchange = () => post(`/api/bot/${id}/config`, { search: refs.search.value });
  refs.count.onchange = () => post(`/api/bot/${id}/config`, { count: parseInt(refs.count.value) || 1 });
  refs.ocr.onclick = () => post(`/api/bot/${id}/ocr`);
  refs.readlog.onclick = () => post(`/api/bot/${id}/readlog`);
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
    post(`/api/bot/${id}/start`, {
      mode: refs.uiMode, trade_class: c ? c.class : "",
      recipe: refs.recipe.value, search: refs.search.value,
      count: parseInt(refs.count.value) || 1,
    });
  };
  refs.stop.onclick = () => post(`/api/bot/${id}/stop`);
  refs.pause.onclick = () => post(`/api/bot/${id}/pause`);
  refs.launch.onclick = () => {
    const c = selectedCrafter(refs);   // send the dropdown's crafter so the backend
    post(`/api/bot/${id}/launch`, c ? { character: c.character, trade_class: c.class } : {});
  };
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
function pushQueueRow(refs, item) {
  const li = document.createElement("li");
  li.className = "qrow" + (item.done >= item.count && item.count ? " done" : "");
  li.innerHTML =
    `<input class="qname" type="text" value="${(item.name || "").replace(/"/g, "&quot;")}" placeholder="recipe name" />` +
    `<input class="qsearch" type="text" maxlength="18" value="${(item.search || "").replace(/"/g, "&quot;")}" placeholder="search (blank=name)" />` +
    `<input class="qcount" type="number" min="1" max="999" value="${item.count || 1}" />` +
    `<button class="qdel" title="remove">×</button>`;
  const id = refs.root.dataset.bot;
  li.querySelector(".qname").onchange = () => saveQueue(id, refs);
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
  })).filter((it) => it.name);
}
function saveQueue(id, refs) {
  post(`/api/bot/${id}/queue`, { queue: readQueueDom(refs) });
}
function renderQueue(refs, queue) {
  // only rebuild from telemetry when the queue actually changed AND the user
  // isn't mid-edit (so typing/counts aren't clobbered by the ~1Hz stream).
  const sig = (queue || []).map((q) => `${q.name}:${q.search || ""}:${q.count}:${q.done}`).join("|");
  if (sig === refs.queueSig || queueRowFocused(refs)) return;
  refs.queueSig = sig;
  refs.queue.innerHTML = "";
  (queue || []).forEach((it) => pushQueueRow(refs, it));
}

// ---- per-bot render -------------------------------------------------------
function updateBotPanel(refs, bot) {
  refs.root.classList.toggle("disabled", !bot.enabled);
  if (document.activeElement !== refs.enable) refs.enable.checked = !!bot.enabled;

  const st = bot.state || "off";
  refs.state.textContent = st.replace("_", " ");
  refs.state.className = "state-pill bot-state s-" + st;

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
