// ib dashboard — live websocket telemetry + manual control surface.
"use strict";
const $ = (id) => document.getElementById(id);
const fmt = (v, s = "") => (v === null || v === undefined ? "—" : v + s);
const pct = (v) => Math.round((v ?? 0) * 100);

const CURES = ["noxious", "elemental", "trauma", "arcane", "curse"];
const CURE_ABBR = { noxious: "nox", elemental: "ele", trauma: "tra", arcane: "arc", curse: "cur" };
const FALLBACK_NAMES = ["self", "slot1", "slot2", "slot3", "slot4", "slot5"];

// Maintenance role follows the active profile: 'ward' (Defiler) or 'hot' (Fury).
// 1:1 — every ward/group-ward label + action becomes hot/group-hot for a Fury.
let maintRole = "ward";
let groupMaintRole = "group_ward";

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
function postJSON(url, body) {
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }).catch(() => {});
}
// live tunables (ward heartbeat). Load current value, save on change.
const wardHb = document.getElementById("wardHb");
if (wardHb) {
  fetch("/api/tunables").then((r) => r.json()).then((t) => {
    if (t && t.ward_heartbeat_s != null) wardHb.value = t.ward_heartbeat_s;
  }).catch(() => {});
  wardHb.onchange = () => {
    const v = parseFloat(wardHb.value);
    if (!isNaN(v)) postJSON("/api/tunables", { ward_heartbeat_s: v });
  };
}

// ---- healer profile selector (top bar) ------------------------------------
const profileSel = $("profile");
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
let profileSig = "";
function renderProfile(p) {
  if (!p || !profileSel) return;
  const sig = (p.available || []).join(",");
  if (sig !== profileSig) {            // rebuild options only when the set changes
    profileSig = sig;
    profileSel.innerHTML = (p.available || [])
      .map((n) => `<option value="${n}">${cap(n)}</option>`).join("");
  }
  if (document.activeElement !== profileSel && p.active) profileSel.value = p.active;
  if (p.healer && $("selfClass")) $("selfClass").textContent = cap(p.healer);
  // 1:1 ward->hot relabel + repoint when the profile's maintenance role changes
  if (p.maint_role && p.maint_role !== maintRole) {
    maintRole = p.maint_role;
    groupMaintRole = p.group_maint_role || "group_" + maintRole;
    gridSig = "";                       // force per-member grid rebuild w/ new label
    const gb = document.querySelector('[data-maint="group"]');
    if (gb) { gb.dataset.group = groupMaintRole; gb.textContent = "group " + maintRole; }
    const eb = document.querySelector('[data-maint="emergency"]');
    if (eb) { eb.dataset.group = "emergency_" + maintRole; eb.textContent = "emergency " + maintRole; }
    document.querySelectorAll(".maint-word").forEach((el) => (el.textContent = maintRole));
  }
}
if (profileSel) profileSel.onchange = () => {
  if (confirm(`Switch healer profile to ${cap(profileSel.value)}?\n\nThis swaps the keymap + character config only (no in-game camp). Use ⇄ swap to camp + switch the live character.`))
    post(`/api/profile/${profileSel.value}`);
};
const swapBtn = $("swapBtn");
if (swapBtn) swapBtn.onclick = () => {
  const v = profileSel ? profileSel.value : "";
  if (!v) return;
  if (confirm(`Camp + switch to ${cap(v)}?\n\nCamps the current character out to char-select, selects ${cap(v)}'s character, and loads in-world. Then re-ARM when ready. (Same-account toons only.)`))
    post(`/api/profile/${v}/swap`);
};

document.querySelectorAll("[data-ov]").forEach((b) => (b.onclick = () => post(`/api/override/${b.dataset.ov}`)));
document.querySelectorAll("[data-ctl]").forEach((b) => (b.onclick = () => post(`/api/control/${b.dataset.ctl}`)));
document.querySelectorAll("[data-group]").forEach((b) => (b.onclick = () => post(`/api/act/${b.dataset.group}`)));
document.querySelectorAll("[data-accept]").forEach((b) => (b.onclick = () => post(`/api/accept/${b.dataset.accept}`)));
document.querySelectorAll("[data-nudge]").forEach((b) => (b.onclick = () => post(`/api/nudge/${b.dataset.nudge}`)));
const resetCombatBtn = $("resetCombatBtn");
if (resetCombatBtn) resetCombatBtn.onclick = () => post("/api/combat/reset");
$("launchBtn").onclick = () => post("/api/launch");
const stopBtn = $("stopBtn");
if (stopBtn) stopBtn.onclick = () => {
  if (confirm("Stop Bot?\n\nPresses your camp key for a clean logout, waits out the countdown, then shuts down the VM.")) post("/api/stop");
};
const shutdownBtn = $("shutdownBtn");
if (shutdownBtn) shutdownBtn.onclick = () => {
  if (confirm("Shutdown VM?\n\nPowers off the VM immediately — NO camp logout. Windows closes EQ2 cleanly; forces off if it hangs.")) post("/api/shutdown");
};
const focusBtn = $("focusBtn");
if (focusBtn) focusBtn.onclick = () =>
  window.open("focus.html", "ibfocus", "width=430,height=600,menubar=no,toolbar=no,location=no,status=no");
const groupBtn = $("groupBtn");
if (groupBtn) groupBtn.onclick = () =>
  window.open("group.html", "ibgroup", "width=480,height=640,menubar=no,toolbar=no,location=no,status=no");

// ---- build the per-member action grid (modernized action_list) ------------
let gridBuilt = false;
function buildGrid(members) {
  const grid = $("actionGrid");
  grid.innerHTML = "";
  const head = document.createElement("div");
  head.className = "ctl-row head";
  head.innerHTML = `<span>member</span><span>heal</span><span>${maintRole}</span>` +
    CURES.map((c) => `<span>${CURE_ABBR[c]}</span>`).join("") + `<span>rez</span>`;
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
      `<button class="act ward" data-act="${maintRole}" data-slot="${m.slot}">${maintRole}</button>` + cures +
      `<button class="act rez" data-act="rez" data-slot="${m.slot}">rez</button>`;
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

// ---- group HP history graph -----------------------------------------------
const hpHist = {};                 // slot -> [hp 0..1, ...]
const HP_LINE = ["#37d39b", "#5b8cff", "#ffb454", "#ff5b6e", "#8ab4ff", "#a06bff"];
function pushHpHistory(members) {
  members.forEach((m) => {
    if (!m.present) return;
    (hpHist[m.slot] = hpHist[m.slot] || []).push(m.hp ?? 0);
    if (hpHist[m.slot].length > 120) hpHist[m.slot].shift();
  });
}
function drawHpGraph(members) {
  const cv = $("hpGraph"); if (!cv) return;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const grid = css.getPropertyValue("--line").trim() || "#223049";
  // horizontal gridlines at 25/50/75/100%
  ctx.strokeStyle = grid; ctx.lineWidth = 1;
  [0.25, 0.5, 0.75, 1].forEach((f) => {
    const y = H - f * (H - 6) - 3;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  });
  const present = members.filter((m) => m.present);
  present.forEach((m, i) => {
    const h = hpHist[m.slot] || [];
    if (h.length < 2) return;
    ctx.beginPath();
    h.forEach((v, j) => {
      const x = (j / (h.length - 1)) * W;
      const y = H - Math.max(0, Math.min(1, v)) * (H - 6) - 3;
      j ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = HP_LINE[m.slot % HP_LINE.length];
    ctx.lineWidth = 2; ctx.stroke();
  });
  const legend = $("graphLegend");
  if (legend) legend.innerHTML = present.map((m) =>
    `<span style="color:${HP_LINE[m.slot % HP_LINE.length]}">${m.name || "slot" + m.slot}</span>`).join("  ");
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
  // profile FIRST: sets maintRole so the per-member grid builds with ward/hot right
  renderProfile(s.profile);
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
  const armBtn = $("armBtn");
  if (armBtn) { armBtn.classList.toggle("on", !!s.running); armBtn.textContent = s.running ? "▶ ARMED" : "▶ ARM"; }

  // ---- live-view overlay chips ----
  const host = s.host || {};
  if ($("ovState")) $("ovState").textContent = state;
  if ($("ovGpu")) $("ovGpu").textContent = host.gpu_util == null ? "GPU —" : `GPU ${host.gpu_util}%`;
  if ($("ovLoad")) $("ovLoad").textContent = host.load == null ? "load —" : `load ${host.load}`;

  // ---- vm ----
  const vm = s.vm || {};
  $("vmName").textContent = vm.name || "iksar_buddy";
  $("vmDot").classList.toggle("on", !!vm.running);

  // ---- chat safety banner (§6.2) ----
  // Only alarm when the bot is ARMED and chat is unsafe -- a disarmed (sense-only)
  // bot isn't injecting, so a red "INJECTION BLOCKED" banner would be noise.
  const cf = s.chat_focus || {};
  $("chatAlarm").hidden = !(s.running && cf.safe === false);
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
  { const gs = $("groupSize"); if (gs) gs.textContent = `${s.group_size ?? 0}/6`; }
  const runK = $("runningK");
  runK.textContent = s.running ? "armed" : "disarmed";
  runK.className = s.running ? "good" : "";
  const chatK = $("chatK");
  // Calm + informative: chat input active vs clear (the actual detection), and
  // whether the guard would allow a press.
  chatK.textContent = cf.chat_active ? "chat active" : cf.safe ? "clear" : "—";
  chatK.className = cf.chat_active ? "warn" : cf.safe ? "good" : "";
  $("vmK").textContent = vm.running ? `running ${vm.ip || ""}`.trim() : "off";

  // ---- group members ----
  const members = s.members || [];
  renderMembers(members);
  pushHpHistory(members);
  drawHpGraph(members);
  if (!gridBuilt || members.length) maybeRebuildGrid(members);

  // ---- events ----
  renderEvents(s.events || []);
}

const ROLES = ["healer", "tank", "dps", "support", "none"];
const memberEls = {};
// Build the static skeleton ONCE per slot (so the role <select> survives the ~1Hz
// re-render and can be opened); subsequent frames only update dynamic fields.
function buildMemberEl(slot) {
  const el = document.createElement("div");
  el.dataset.slot = slot;
  el.innerHTML =
    `<div class="id"><div class="nm"></div>` +
      `<select class="rl-sel" title="group role (tank is targeted by the loop)">` +
        ROLES.map((r) => `<option value="${r}">${r}</option>`).join("") + `</select>` +
      `<button class="mfollow" title="target this member (F#) and autofollow them">follow</button></div>` +
    `<div class="barwrap">` +
      `<div class="hp"><div class="crit-band"></div>` +
        `<div class="fill"></div><span class="hp-txt"></span></div>` +
      `<div class="ward-row"><div class="ward"><i></i></div><span class="ward-lbl"></span></div>` +
    `</div>` +
    `<div class="dets">` +
      CURES.map((c) => `<span class="det" data-d="${c}">${CURE_ABBR[c]}</span>`).join("") +
      `<span class="rez-badge">rez sick</span>` +
    `</div>`;
  const sel = el.querySelector(".rl-sel");
  sel.onchange = () => post(`/api/role/${slot}/${sel.value}`);
  el.querySelector(".mfollow").onclick = () => post(`/api/act/follow/${slot}`);
  return el;
}
function renderMembers(members) {
  const box = $("members");
  if (!box) return;            // group panel removed from main page (use the ⊟ pop-out)
  members.forEach((m) => {
    let el = memberEls[m.slot];
    if (!el) { el = buildMemberEl(m.slot); memberEls[m.slot] = el; box.appendChild(el); }
    const name = m.name || FALLBACK_NAMES[m.slot] || `slot${m.slot}`;
    const hpP = pct(m.hp);
    const crit = !!m.critical;
    el.className = "member" + (m.dead ? " dead" : "") + (!m.present ? " absent" : "") +
      (crit ? " critical" : "") + (m.rez_sick ? " rezsick" : "");
    el.querySelector(".nm").textContent = name;
    const sel = el.querySelector(".rl-sel");
    if (document.activeElement !== sel && m.role && sel.value !== m.role) sel.value = m.role;
    // no point following yourself -- hide the follow button on the healer slot
    el.querySelector(".mfollow").style.display = m.role === "healer" ? "none" : "";
    el.querySelector(".hp").classList.toggle("crit", crit);
    el.querySelector(".fill").style.width = hpP + "%";
    el.querySelector(".hp-txt").textContent = m.dead ? "DEAD" : hpP + "%";
    const ward = el.querySelector(".ward");
    ward.classList.toggle("up", !!m.ward);
    el.querySelector(".ward-lbl").textContent = m.ward ? maintRole : "no " + maintRole;
    CURES.forEach((c) => {
      el.querySelector(`.det[data-d="${c}"]`).classList.toggle("on", (m.detriments || []).includes(c));
    });
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

// ---- live VM frame --------------------------------------------------------
// Reload the JPEG endpoint on a timer (brain caches ~0.7s). Only swap when the
// new image has actually decoded, so we never show a broken-image flash; pause
// while the tab is hidden to save bandwidth.
const liveImg = $("liveFrame");
const liveWrap = liveImg ? liveImg.closest(".live-wrap") : null;
let liveLoading = false;
let liveUrl = "";
function refreshFrame() {
  if (document.hidden || liveLoading || !liveImg) return;
  liveLoading = true;
  fetch("/api/frame.jpg?t=" + Date.now()).then(async (r) => {
    if (r.status === 200) {
      const url = URL.createObjectURL(await r.blob());
      liveImg.src = url;
      if (liveUrl) URL.revokeObjectURL(liveUrl);
      liveUrl = url;
      if (liveWrap) liveWrap.classList.remove("powered-off");
      if ($("liveAge")) $("liveAge").textContent = "live";
    } else if (r.status === 409) {
      // VM powered off -> clear the stale frame, show the placeholder.
      liveImg.removeAttribute("src");
      if (liveUrl) { URL.revokeObjectURL(liveUrl); liveUrl = ""; }
      if (liveWrap) liveWrap.classList.add("powered-off");
      if ($("liveAge")) $("liveAge").textContent = "powered off";
    } else if ($("liveAge")) {
      $("liveAge").textContent = "no signal";   // 503: transient, keep last frame
    }
  }).catch(() => { if ($("liveAge")) $("liveAge").textContent = "no signal"; })
    .finally(() => { liveLoading = false; });
}
setInterval(refreshFrame, 1200);
refreshFrame();

// Console connect: a modal offering the in-browser SPICE view (one click) OR the
// copy-paste tunnel command for the native viewer — so it works from ANY computer
// with nothing pre-installed. LAN-only (the server's SPICE/WS aren't public).
const IB_LAN = "10.0.0.16";          // server LAN IP (home network)
const IB_SSH_USER = "jbaker";
function openConsoleModal(title, spicePort) {
  const sp = spicePort || 5900;
  const localPort = 5950 + Math.floor((sp - 5900) / 10);   // 5900->5950, 5910->5951...
  const cmd = `ssh -fN -L ${localPort}:127.0.0.1:${sp} ${IB_SSH_USER}@${IB_LAN} 2>/dev/null; `
            + `remote-viewer spice://127.0.0.1:${localPort}`;
  $("cmTitle").textContent = `Connect — ${title}`;
  $("cmCmd").textContent = cmd;
  // SAME-ORIGIN web console: works on the LAN and remotely through Cloudflare.
  $("cmWeb").onclick = () => {
    window.open(`${location.origin}/spice/console.html?port=${sp}`,
      "ibweb", "width=1300,height=820,menubar=no,toolbar=no,location=no");
  };
  // one-click native viewer (works if this machine ran the ib-console installer)
  $("cmNative").onclick = () => { window.location.href = `ibconsole://open?port=${sp}`; };
  $("cmCopy").onclick = () => {
    navigator.clipboard.writeText(cmd).then(() => {
      $("cmCopy").textContent = "Copied!";
      setTimeout(() => ($("cmCopy").textContent = "Copy"), 1500);
    });
  };
  $("consoleModal").hidden = false;
}
$("cmClose").onclick = () => ($("consoleModal").hidden = true);
$("consoleModal").onclick = (e) => { if (e.target.id === "consoleModal") $("consoleModal").hidden = true; };
if (liveImg) {
  liveImg.title = "click to connect to the console";
  liveImg.onclick = () => openConsoleModal("healer (iksar_buddy)", 5900);
}
const consoleBtn = $("consoleBtn");
if (consoleBtn) consoleBtn.onclick = () => openConsoleModal("healer (iksar_buddy)", 5900);
const spiceRestartBtn = $("spiceRestartBtn");
if (spiceRestartBtn) spiceRestartBtn.onclick = () => {
  spiceRestartBtn.disabled = true;
  fetch("/api/spice/restart", { method: "POST" })
    .finally(() => setTimeout(() => { spiceRestartBtn.disabled = false; }, 2500));
};

// ---- websocket with auto-reconnect ---------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => { $("conn").classList.remove("on"); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
}
connect();
