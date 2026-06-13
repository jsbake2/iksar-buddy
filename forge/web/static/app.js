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

// ---- bot panels -----------------------------------------------------------
const botEls = {};          // id -> { root, refs..., uiMode, queueSig }
const tpl = $("botTpl");

function buildBotPanel(bot, tradeClasses) {
  const root = tpl.content.firstElementChild.cloneNode(true);
  root.dataset.bot = bot.id;
  const q = (sel) => root.querySelector(sel);

  // static identity
  q(".bot-name").textContent = bot.label || bot.id;
  q(".bot-char").textContent = bot.character || "—";
  q(".bot-dom").textContent = bot.dom || "—";

  // trade-class options
  const trade = q(".bot-trade");
  trade.innerHTML = tradeClasses.map((t) => `<option value="${t}">${t}</option>`).join("");

  const refs = {
    root,
    state: q(".bot-state"),
    enable: q(".bot-enable"),
    console: q(".bot-console"),
    live: q(".bot-live-frame"),
    tabs: [...root.querySelectorAll(".mode-tab")],
    paneSingle: q(".pane-single"),
    paneWrit: q(".pane-writ"),
    trade,
    recipe: q(".bot-recipe"),
    count: q(".bot-count"),
    ocr: q(".bot-ocr"),
    readlog: q(".bot-readlog"),
    addrow: q(".bot-addrow"),
    queue: q(".bot-queue"),
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
    switch: q(".bot-switch"),
    log: q(".bot-log"),
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
  refs.trade.onchange = () => post(`/api/bot/${id}/config`, { trade_class: refs.trade.value });
  refs.recipe.onchange = () => post(`/api/bot/${id}/config`, { recipe: refs.recipe.value });
  refs.count.onchange = () => post(`/api/bot/${id}/config`, { count: parseInt(refs.count.value) || 1 });
  refs.ocr.onclick = () => post(`/api/bot/${id}/ocr`);
  refs.readlog.onclick = () => post(`/api/bot/${id}/readlog`);
  refs.addrow.onclick = () => { pushQueueRow(refs, { name: "", count: 1 }); saveQueue(id, refs); };
  refs.start.onclick = () => post(`/api/bot/${id}/start`, {
    mode: refs.uiMode, trade_class: refs.trade.value,
    recipe: refs.recipe.value, count: parseInt(refs.count.value) || 1,
  });
  refs.stop.onclick = () => post(`/api/bot/${id}/stop`);
  refs.pause.onclick = () => post(`/api/bot/${id}/pause`);
  refs.launch.onclick = () => post(`/api/bot/${id}/launch`);
  refs.switch.onclick = () => post(`/api/bot/${id}/switch`);
  refs.console.onclick = () =>
    (window.location.href = `ibconsole://open?port=${bot.spice_port || ""}`);
  refs.live.onclick = refs.console.onclick;

  // initial input values
  if (bot.trade_class) refs.trade.value = bot.trade_class;
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
    `<input class="qcount" type="number" min="1" max="999" value="${item.count || 1}" />` +
    `<button class="qdel" title="remove">×</button>`;
  const id = refs.root.dataset.bot;
  li.querySelector(".qname").onchange = () => saveQueue(id, refs);
  li.querySelector(".qcount").onchange = () => saveQueue(id, refs);
  li.querySelector(".qdel").onclick = () => { li.remove(); saveQueue(id, refs); };
  refs.queue.appendChild(li);
}
function readQueueDom(refs) {
  return [...refs.queue.querySelectorAll(".qrow")].map((row) => ({
    name: row.querySelector(".qname").value.trim(),
    count: parseInt(row.querySelector(".qcount").value) || 1,
  })).filter((it) => it.name);
}
function saveQueue(id, refs) {
  post(`/api/bot/${id}/queue`, { queue: readQueueDom(refs) });
}
function renderQueue(refs, queue) {
  // only rebuild from telemetry when the queue actually changed AND the user
  // isn't mid-edit (so typing/counts aren't clobbered by the ~1Hz stream).
  const sig = (queue || []).map((q) => `${q.name}:${q.count}:${q.done}`).join("|");
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

  // trade class (don't clobber an open dropdown)
  if (document.activeElement !== refs.trade && bot.trade_class && refs.trade.value !== bot.trade_class)
    refs.trade.value = bot.trade_class;
  // recipe / count only when not focused
  if (document.activeElement !== refs.recipe && bot.recipe && refs.uiMode === "single")
    if (!refs.recipe.value) refs.recipe.placeholder = bot.recipe;

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
  const trades = s.trade_classes || [];
  if (!built && order.length) {
    order.forEach((id) => buildBotPanel(bots[id], trades));
    built = true;
  }
  order.forEach((id) => {
    const refs = botEls[id];
    if (refs && bots[id]) updateBotPanel(refs, bots[id]);
  });
  renderEvents(s.events || []);
}

// ---- global controls ------------------------------------------------------
$("allLaunch").onclick = () => {
  Object.keys(botEls).forEach((id) => post(`/api/bot/${id}/launch`));
};
$("allStop").onclick = () => {
  if (confirm("Stop ALL bots?")) Object.keys(botEls).forEach((id) => post(`/api/bot/${id}/stop`));
};

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
