// ib dashboard — live websocket telemetry + manual control surface.
"use strict";
const $ = (id) => document.getElementById(id);
const fmt = (v, s = "") => (v === null || v === undefined ? "—" : v + s);
const pct = (v) => Math.round((v ?? 0) * 100);

const CURES = ["noxious", "elemental", "trauma", "arcane", "curse"];
const CURE_ABBR = { noxious: "nox", elemental: "ele", trauma: "tra", arcane: "arc", curse: "cur" };
const FALLBACK_NAMES = ["self", "slot1", "slot2", "slot3", "slot4", "slot5"];

// ---- theme persistence ----------------------------------------------------
const themeSel = $("theme");
const savedTheme = localStorage.getItem("ib-theme");
if (savedTheme) { document.documentElement.dataset.theme = savedTheme; themeSel.value = savedTheme; }
themeSel.onchange = () => {
  document.documentElement.dataset.theme = themeSel.value;
  localStorage.setItem("ib-theme", themeSel.value);
};

// ---- action helpers -------------------------------------------------------
function post(url) { fetch(url, { method: "POST" }).catch(() => {}); }

document.querySelectorAll("[data-ov]").forEach((b) => (b.onclick = () => post(`/api/override/${b.dataset.ov}`)));
document.querySelectorAll("[data-ctl]").forEach((b) => (b.onclick = () => post(`/api/control/${b.dataset.ctl}`)));
document.querySelectorAll("[data-group]").forEach((b) => (b.onclick = () => post(`/api/act/${b.dataset.group}`)));
$("launchBtn").onclick = () => post("/api/launch");

// ---- build the per-member action grid (modernized action_list) ------------
let gridBuilt = false;
function buildGrid(members) {
  const grid = $("actionGrid");
  grid.innerHTML = "";
  const head = document.createElement("div");
  head.className = "ctl-row head";
  head.innerHTML = `<span>member</span><span>heal</span><span>ward</span>` +
    CURES.map((c) => `<span>${CURE_ABBR[c]}</span>`).join("");
  grid.appendChild(head);

  members.forEach((m) => {
    const row = document.createElement("div");
    row.className = "ctl-row";
    const name = m.name || FALLBACK_NAMES[m.slot] || `slot${m.slot}`;
    const role = m.role || "";
    const cures = CURES.map(
      (c) => `<button class="act cure" data-c="${c}" data-act="cure_${c}" data-slot="${m.slot}">${CURE_ABBR[c]}</button>`
    ).join("");
    row.innerHTML =
      `<div class="who">${name}<small>${role}</small></div>` +
      `<button class="act heal" data-act="heal" data-slot="${m.slot}">heal</button>` +
      `<button class="act ward" data-act="ward" data-slot="${m.slot}">ward</button>` + cures;
    grid.appendChild(row);
  });

  grid.querySelectorAll("[data-act]").forEach(
    (b) => (b.onclick = () => post(`/api/act/${b.dataset.act}/${b.dataset.slot}`))
  );
  gridBuilt = true;
}

// ---- power sparkline ------------------------------------------------------
const powerHist = [];
function drawSpark(val) {
  powerHist.push(val);
  if (powerHist.length > 60) powerHist.shift();
  const cv = $("powerSpark");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim() || "#5b8cff";
  const n = powerHist.length;
  if (n < 2) return;
  ctx.beginPath();
  powerHist.forEach((v, i) => {
    const x = (i / (n - 1)) * W;
    const y = H - v * (H - 3) - 1;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
  ctx.fillStyle = accent + "22"; ctx.fill();
  ctx.beginPath();
  powerHist.forEach((v, i) => {
    const x = (i / (n - 1)) * W;
    const y = H - v * (H - 3) - 1;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = accent; ctx.lineWidth = 1.5; ctx.stroke();
}

// ---- sensor bar coloring --------------------------------------------------
function setBar(id, ratio, { invert = false, warn = 0.6, bad = 0.85 } = {}) {
  const el = $(id);
  const r = Math.max(0, Math.min(1, ratio));
  el.style.width = r * 100 + "%";
  const sev = invert ? 1 - r : r;
  el.classList.toggle("bad", sev >= bad);
  el.classList.toggle("warn", sev >= warn && sev < bad);
}

// ---- render ---------------------------------------------------------------
let lastEventTs = 0;
function render(s) {
  // ---- header / state ----
  const state = s.state || "—";
  $("state").textContent = state;
  const pill = $("statePill");
  pill.className = "state-pill" +
    (state === "IN_COMBAT" ? " combat" : state === "REZ_LOOP" ? " rez" : state === "WIPE_RECOVERY" ? " wipe" : "");
  const ovrPill = $("ovrPill");
  ovrPill.hidden = !s.override;
  if (s.override) $("ovrName").textContent = s.override;
  const armed = $("armed");
  armed.textContent = s.running ? "armed" : "disarmed";
  armed.classList.toggle("on", !!s.running);

  // ---- vm ----
  const vm = s.vm || {};
  $("vmName").textContent = vm.name || "iksar_buddy";
  $("vmDot").classList.toggle("on", !!vm.running);

  // ---- chat safety banner (§6.2) ----
  const cf = s.chat_focus || {};
  $("chatAlarm").hidden = cf.safe !== false;
  $("aborted").textContent = cf.aborted_injections ?? 0;

  // ---- self ----
  const own = s.own || {};
  const pw = own.power ?? 0;
  const pf = $("power");
  pf.style.width = pct(pw) + "%";
  pf.classList.toggle("gated", !!own.mana_gated);
  $("powerPct").textContent = pct(pw) + "%";
  const cast = $("castFlag");
  cast.textContent = own.casting ? "casting" : "idle";
  cast.classList.toggle("casting", !!own.casting);
  const mana = $("manaFlag");
  mana.textContent = own.mana_gated ? "MANA GATED" : "mana ok";
  mana.classList.toggle("gated", !!own.mana_gated);
  drawSpark(pw);

  // ---- sensors ----
  const a = s.agent || {};
  $("conn").classList.toggle("on", !!a.connected);
  $("latency").textContent = fmt(a.latency_ms, " ms");
  $("hz").textContent = fmt(a.capture_hz, " Hz");
  $("ocr").textContent = a.ocr_conf == null ? "—" : Math.round(a.ocr_conf * 100) + "%";
  $("logfresh").textContent = fmt(a.log_fresh_s, " s");
  setBar("latBar", (a.latency_ms ?? 0) / 500, { warn: 0.5, bad: 0.8 });
  setBar("hzBar", (a.capture_hz ?? 0) / 15, { invert: true, warn: 0.4, bad: 0.7 });
  setBar("ocrBar", a.ocr_conf ?? 0, { invert: true, warn: 0.2, bad: 0.35 });
  setBar("logBar", (a.log_fresh_s ?? 0) / 5, { warn: 0.5, bad: 0.8 });
  $("abortedC").textContent = cf.aborted_injections ?? 0;
  $("alarmsC").textContent = cf.alarms ?? 0;
  $("alarmsC").parentElement.classList.toggle("hot", (cf.alarms ?? 0) > 0);

  // ---- status ----
  $("stateK").textContent = state;
  $("override").textContent = s.override || "none";
  $("groupSizeK").textContent = s.group_size ?? 0;
  $("groupSize").textContent = `${s.group_size ?? 0}/6`;
  const runK = $("runningK");
  runK.textContent = s.running ? "armed" : "disarmed";
  runK.className = s.running ? "good" : "";
  const chatK = $("chatK");
  chatK.textContent = cf.safe === false ? "UNSAFE" : cf.safe === true ? "safe" : "—";
  chatK.className = cf.safe === false ? "bad" : cf.safe === true ? "good" : "";
  $("vmK").textContent = vm.running ? `running ${vm.ip || ""}`.trim() : "off";

  // ---- group members ----
  const members = s.members || [];
  renderMembers(members);
  if (!gridBuilt || members.length) maybeRebuildGrid(members);

  // ---- events ----
  renderEvents(s.events || []);
}

const memberEls = {};
function renderMembers(members) {
  const box = $("members");
  members.forEach((m) => {
    let el = memberEls[m.slot];
    if (!el) {
      el = document.createElement("div");
      el.dataset.slot = m.slot;
      box.appendChild(el);
      memberEls[m.slot] = el;
    }
    const name = m.name || FALLBACK_NAMES[m.slot] || `slot${m.slot}`;
    const role = m.role || "";
    const hpP = pct(m.hp);
    const crit = !!m.critical;
    el.className = "member" + (m.dead ? " dead" : "") + (!m.present ? " absent" : "") + (crit ? " critical" : "");
    el.innerHTML =
      `<div class="id"><div class="nm">${name}</div><div class="rl">${role}</div></div>` +
      `<div class="barwrap">` +
        `<div class="hp${crit ? " crit" : ""}"><div class="crit-band"></div>` +
          `<div class="fill" style="width:${hpP}%"></div><span class="hp-txt">${m.dead ? "DEAD" : hpP + "%"}</span></div>` +
        `<div class="ward-row"><div class="ward${m.ward ? " up" : ""}"><i></i></div>` +
          `<span class="ward-lbl">${m.ward ? "ward" : "no ward"}</span></div>` +
      `</div>` +
      `<div class="dets">` +
        CURES.map((c) => {
          const on = (m.detriments || []).includes(c);
          return `<span class="det${on ? " on" : ""}" data-d="${c}">${CURE_ABBR[c]}</span>`;
        }).join("") +
      `</div>`;
  });
}

let gridSig = "";
function maybeRebuildGrid(members) {
  const sig = members.map((m) => `${m.slot}:${m.name || ""}:${m.role || ""}`).join("|");
  if (sig !== gridSig) { gridSig = sig; buildGrid(members); }
}

function renderEvents(events) {
  const ev = $("events");
  ev.innerHTML = "";
  const latest = events.length ? events[events.length - 1].ts : 0;
  events.slice(-50).reverse().forEach((e) => {
    const li = document.createElement("li");
    if (e.ts === latest && latest !== lastEventTs) li.className = "fresh";
    const t = new Date(e.ts * 1000).toLocaleTimeString();
    li.innerHTML =
      `<span class="t">${t}</span><span class="k" data-k="${e.kind}">${e.kind}</span><span>${e.detail}</span>`;
    ev.appendChild(li);
  });
  lastEventTs = latest;
}

// ---- websocket with auto-reconnect ---------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => { $("conn").classList.remove("on"); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
}
connect();
