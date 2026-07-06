// harvest dashboard (REFACTOR P5.2 — moved out of index.html's inline <script>;
// all wiring lives here, index.html carries zero inline handlers/styles now).
// Uses ibUI (ui-core.js) for $, esc, theme persistence and JSON POSTs.
"use strict";
const { $, esc } = ibUI;
ibUI.theme($("theme"), "ibh-theme");

let grecording = false, tab = "session";
// POST + parsed-JSON response; ibUI.postJSON returns null on failure, so call
// sites use `(await post(...)) || {}` when they read the result.
const post = (u, b) => ibUI.postJSON(u, b);
function mv(dir, ms) { post("/api/move", { dir, ms: ms || 450 }); }
function unstuck() { post("/api/move", { dir: "jump", ms: 300 }); setTimeout(() => post("/api/move", { dir: "back", ms: 600 }), 350); }
function webConsole() { window.open(location.origin + "/static/spice/console.html?port=5900", "ibhcon", "width=1040,height=620"); }

async function loadRecGrids() {
  try {
    const d = await (await fetch("/api/grids")).json(); const sel = $("recgrid");
    sel.innerHTML = (d.grids || []).map((g) => `<option value="${esc(g.file)}">extend: ${esc(g.name)} (${g.points} pts)</option>`).join("") + '<option value="__new__">＋ new grid…</option>';
    recGridChange();
  } catch (e) {}
}
function recGridChange() { $("recname").hidden = $("recgrid").value !== "__new__"; }
async function toggleGridRec() {
  const btn = $("recbtn");
  if (!grecording) {
    const sel = $("recgrid").value; let body = { action: "start" };
    if (sel === "__new__") { const nm = $("recname").value.trim(); if (!nm) { alert("name the new grid first"); return; } body.name = nm; }
    else if (sel) { body.grid = sel; }
    await post("/api/graph", body); grecording = true; btn.textContent = "⏹ stop & save"; btn.classList.add("rec");
  } else {
    const r = (await post("/api/graph", { action: "stop" })) || {};
    grecording = false; btn.textContent = "⏺ record map"; btn.classList.remove("rec");
    alert("saved " + (r.name || "grid") + " — " + (r.points || 0) + " pts, " + (r.edges || 0) + " edges");
    loadGrids(); loadRecGrids();
  }
}

let chars = [], acct = "robskin2004";
async function loadChars() { try { chars = await (await fetch("/api/characters")).json(); } catch (e) { chars = []; } renderChars(); }
function setAcct(u) { acct = u; $("acc1").classList.toggle("on", u === "meatwad33w"); $("acc2").classList.toggle("on", u === "robskin2004"); renderChars(); }
function renderChars() {
  const box = $("charlist"); box.innerHTML = "";
  const list = chars.filter((c) => c.user === acct);
  if (!list.length) { box.innerHTML = '<span class="muted">no characters for this account</span>'; return; }
  list.forEach((c) => {
    const b = document.createElement("button");
    b.textContent = c.character;
    b.onclick = () => loginChar(c.character);
    box.appendChild(b);
  });
}
async function loginChar(ch) { $("charmsg").textContent = "launching + logging in " + ch + "… (boots VM ~3-5 min) — watch the controller log"; await post("/api/launch", { character: ch }); }

const sessmsg = () => $("sessmsg");
async function launch() { sessmsg().textContent = "launching + logging in (boots VM, ~3-5 min)… watch the controller log"; await post("/api/launch", {}); }
async function camp() { sessmsg().textContent = "camping to desktop…"; const r = (await post("/api/camp", {})) || {}; sessmsg().textContent = r.ok ? "camped (client closing)" : "camp failed"; }
async function shutdownVM() { if (!confirm("Shut down the VM?")) return; sessmsg().textContent = "shutting down VM…"; const r = (await post("/api/shutdown", {})) || {}; sessmsg().textContent = r.ok ? "VM shutting down" : "shutdown failed"; }

async function loadKeymap() { try { const k = await (await fetch("/api/keymap")).json(); $("keymaprows").innerHTML = (k.binds || []).map((b) => `<tr><td><b>${esc(b.key)}</b></td><td>${esc(b.action)}${b.note ? ` <span class="muted">(${esc(b.note)})</span>` : ""}</td></tr>`).join(""); } catch (e) {} }
async function loadGrids() { try { const d = await (await fetch("/api/grids")).json(); const sel = $("gridsel"); sel.innerHTML = (d.grids || []).map((g) => `<option value="${esc(g.file)}">${esc(g.name)} — ${g.points} pts</option>`).join("") || '<option value="">no grid recorded</option>'; } catch (e) {} }

const harvmsg = () => $("harvmsg");
async function startHarvest() { const grid = $("gridsel").value; const laps = parseInt($("laps").value) || 40; harvmsg().textContent = "starting harvest…"; const r = (await post("/api/gather", { action: "start", grid, laps })) || {}; harvmsg().textContent = r.ok ? `harvesting ${r.grid} (${r.laps} laps)` : `start failed: ${r.err || "?"}`; }
async function stopHarvest() { harvmsg().textContent = "stopping…"; await post("/api/gather", { action: "stop" }); harvmsg().textContent = "stopped"; }
async function recal() { const p = $("m_pos").textContent.split(",").map(Number); if (p.length < 3 || isNaN(p[0])) return alert("no live position"); const r = (await post("/api/recalibrate", { x: p[0], y: p[1], z: p[2] })) || {}; alert("offset " + (r.primary || "not found")); }
function refreshFrame() { const f = $("frame"); const img = new Image(); img.onload = () => { f.src = img.src; }; img.src = "/api/frame.jpg?t=" + Date.now(); }

async function tick() {
  let d; try { d = await (await fetch("/api/state")).json(); } catch (e) { $("link").className = "pill"; return; }
  const st = d.state || {}, live = st.ok && st.pos;
  $("link").className = "pill " + (live ? "live" : "");
  $("zone").textContent = "zone: " + (d.zone || "—");
  $("zone2").textContent = d.zone || "(RE pending — zone name)";
  $("pos").textContent = live ? ("pos: " + st.pos.join(", ")) : "pos: " + (st.err || "—");
  $("m_pos").textContent = live ? st.pos.join(", ") : "—";
  $("m_hdg").textContent = (live && st.heading != null) ? (st.heading + "° " + (st.compass || "")) : "—";
  const g = d.graph || {};
  $("recinfo").textContent = g.recording ? ("● " + (g.points || 0) + " pts · " + (g.edges || 0) + " edges") : "";
  $("clog").innerHTML = (d.log || []).slice(-30).reverse().map((l) => "<div>" + esc(l) + "</div>").join("");
  // nearby NODES — live from the game's harvestable array (fast, every poll, REAL nodes only)
  const nodes = st.nodes || [];
  $("nodeage").textContent = live ? ("· " + nodes.length + " tracked") : "";
  $("nodes").innerHTML = (live && nodes.length) ? nodes.slice(0, 14).map((n) =>
    `<div class="mon-row"><span class="zone">⛏ ${esc(n.name || "unknown/too far")}</span><span>${n.dist} m · ${n.xyz[0]}, ${n.xyz[2]}</span></div>`).join("")
    : (live ? '<span class="muted">no harvestables within 220 m</span>' : '<span class="pending">…</span>');
  // harvested-items table (session / all-time) — resource name · source node · qty
  const hv = d.harvest || { session: {}, all_time: {} }; const tbl = hv[tab === "all" ? "all_time" : "session"] || {};
  $("tabS").style.borderColor = tab === "session" ? "var(--accent)" : "";
  $("tabA").style.borderColor = tab === "all" ? "var(--accent)" : "";
  const rows = Object.entries(tbl).sort((a, b) => b[1].qty - a[1].qty);
  $("harvrows").innerHTML = rows.length ? rows.map(([item, e]) =>
    `<tr><td>${e.rare ? '<span class="rec" title="rare">★ </span>' : ""}${esc(item)}</td><td class="muted">${esc(e.node || "")}</td><td>${e.qty}</td></tr>`).join("")
    : '<tr><td class="muted" colspan=3>nothing harvested yet</td></tr>';
  // chat monitor (tells)
  const tells = hv.tells || [];
  $("chatmon").innerHTML = tells.length ? tells.slice(-8).reverse().map((t) =>
    `<div><b class="zone">${esc(t.from || "?")}</b>: ${esc(t.msg || "")}</div>`).join("") : "no tells yet";
  // alerts
  const al = d.alerts || []; const col = { combat: "var(--bad)", pm: "var(--accent)", rare: "var(--good)", stuck: "var(--warn)", bags: "var(--warn)" };
  $("alerts").innerHTML = al.length ? al.map((a) =>
    `<div style="color:${col[a.kind] || "var(--fg)"}">● ${a.kind.toUpperCase()} — ${esc(a.msg || "")}</div>`).join("") : '<span class="muted">clear</span>';
  // toasts + OS notifications for new rares / tells (diff by newest timestamp)
  notifyNew((d.harvest && d.harvest.rares) || [], "rare", (r) => ["Rare harvest found!", r.item + (r.node ? ` (${r.node})` : ""), "good"]);
  notifyNew((d.harvest && d.harvest.tells) || [], "tell", (t) => [`Tell from ${t.from}`, t.msg, "warn"]);
  notifyNew((d.harvest && d.harvest.bagsfull) || [], "bags", () => ["Bags full", "inventory full — harvesting is blocked", "error"]);
}
const _seen = {};
function notifyNew(arr, key, fmt) {
  if (!arr.length) return;
  const newest = Math.max(...arr.map((x) => x.t || 0));
  if (_seen[key] === undefined) { _seen[key] = newest; return; }  // prime: skip backlog on load
  if (newest <= _seen[key]) return;
  arr.filter((x) => (x.t || 0) > _seen[key]).forEach((x) => {
    const [t, dt, lv] = fmt(x); ibNotify.show(t, dt, lv);
    if (typeof Notification !== "undefined" && Notification.permission === "granted") { try { new Notification(t, { body: dt || "" }); } catch (e) {} }
  });
  _seen[key] = newest;
}

// ---- wiring (was inline onclick= in index.html) ----------------------------
$("launchBtn").onclick = launch;
$("campBtn").onclick = camp;
$("vmoffBtn").onclick = shutdownVM;
$("harvbtn").onclick = startHarvest;
$("stopharvBtn").onclick = stopHarvest;
$("acc1").onclick = () => setAcct("meatwad33w");
$("acc2").onclick = () => setAcct("robskin2004");
$("recgrid").onchange = recGridChange;
$("recbtn").onclick = toggleGridRec;
document.querySelectorAll("[data-mv]").forEach((b) => (b.onclick = () => mv(b.dataset.mv, b.dataset.ms ? parseInt(b.dataset.ms) : undefined)));
document.querySelectorAll("[data-post]").forEach((b) => (b.onclick = () => post(b.dataset.post)));
$("unstuckBtn").onclick = unstuck;
$("webconBtn").onclick = webConsole;
$("tabS").onclick = () => { tab = "session"; };
$("tabA").onclick = () => { tab = "all"; };
$("recalBtn").onclick = recal;
ibNotify.phone($("phoneBtn"));

loadRecGrids(); loadChars(); setAcct("robskin2004"); loadKeymap(); loadGrids();
setInterval(tick, 1500); tick(); setInterval(refreshFrame, 1500); refreshFrame();
