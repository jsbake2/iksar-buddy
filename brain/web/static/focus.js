// ib focus — configurable pop-out quick-action overlay.
"use strict";
const $ = (id) => document.getElementById(id);

// Full catalog. kind 'group' -> POST /api/act/<action>. kind 'role' -> resolve
// the slot of <role> from live telemetry, then POST /api/act/<action>/<slot>.
const CATALOG = [
  { id: "gather", label: "⛏ Gather", kind: "group", action: "gather", hot: 1 },
  { id: "pre_pull", label: "Pre-Pull", kind: "group", action: "pre_pull", hot: 1 },
  { id: "heal_tank", label: "Heal Tank", kind: "role", action: "heal", role: "tank", hot: 1 },
  { id: "heal_self", label: "Heal Self", kind: "role", action: "heal", role: "healer" },
  { id: "ward_tank", label: "Ward Tank", kind: "role", action: "ward", role: "tank", hot: 1 },
  { id: "cure_tank", label: "Cure Tank", kind: "role", action: "cure_curse", role: "tank" },
  { id: "rez_tank", label: "Rez Tank", kind: "role", action: "rez", role: "tank" },
  { id: "group_heal", label: "Group Heal", kind: "group", action: "group_heal", hot: 1 },
  { id: "group_ward", label: "Group Ward", kind: "group", action: "group_ward", hot: 1 },
  { id: "group_cure", label: "Group Cure", kind: "group", action: "group_cure" },
  { id: "emergency_heal", label: "Emerg Heal", kind: "group", action: "emergency_heal", danger: 1 },
  { id: "emergency_ward", label: "Emerg Ward", kind: "group", action: "emergency_ward", danger: 1 },
  { id: "buff_tank", label: "Buff Tank", kind: "group", action: "buff_tank", hot: 1 },
  { id: "buff_dps", label: "Buff DPS", kind: "group", action: "buff_dps", hot: 1 },
  { id: "buff_self", label: "Buff Self", kind: "group", action: "buff_self", hot: 1 },
  { id: "buff", label: "Buff", kind: "group", action: "buff", hot: 1 },
  { id: "follow_tank", label: "Follow Tank", kind: "role", action: "follow", role: "tank", hot: 1 },
  { id: "follow", label: "Follow", kind: "group", action: "follow" },
  { id: "stop_follow", label: "Stop Follow", kind: "group", action: "stop_follow" },
  { id: "call_home", label: "Call Home", kind: "group", action: "call_home" },
  { id: "jump", label: "Jump", kind: "group", action: "jump" },
  { id: "sow", label: "SoW", kind: "group", action: "sow" },
  { id: "hail", label: "Hail", kind: "group", action: "hail" },
  { id: "collect", label: "Collect", kind: "group", action: "collect" },
  { id: "evac", label: "Evac", kind: "group", action: "evac", danger: 1 },
  { id: "debuff", label: "Debuff", kind: "group", action: "debuff" },
  { id: "rez_group", label: "Rez Group", kind: "group", action: "rez" },
];
const BY_ID = Object.fromEntries(CATALOG.map((c) => [c.id, c]));
const DEFAULT = CATALOG.filter((c) => c.hot).map((c) => c.id);

// ---- persisted layout (per browser) --------------------------------------
const LS = "ib-focus-layout-v1";
// Buttons added after the first release. Each is merged into an existing saved
// layout ONCE (tracked in `ensured`) so the owner gets them without a reset, but
// a later manual delete still sticks.
const ENSURE = ["follow_tank", "buff_self", "buff_tank", "buff_dps", "buff"];
function loadLayout() {
  let s = null;
  try { s = JSON.parse(localStorage.getItem(LS)); } catch (_) {}
  if (!s || !Array.isArray(s.ids)) return { ids: DEFAULT.slice(), cols: 3, ensured: ENSURE.slice() };
  s.ids = s.ids.filter((id) => BY_ID[id]);          // drop retired actions (e.g. buff1/buff2)
  const ensured = new Set(s.ensured || []);
  for (const id of ENSURE) {
    if (!ensured.has(id)) { if (!s.ids.includes(id)) s.ids.push(id); ensured.add(id); }
  }
  s.ensured = [...ensured];
  if (typeof s.cols !== "number") s.cols = 3;
  localStorage.setItem(LS, JSON.stringify(s));        // persist the migration
  return s;
}
function saveLayout() { localStorage.setItem(LS, JSON.stringify(layout)); }
let layout = loadLayout();

// theme (shared with dashboard)
const savedTheme = localStorage.getItem("ib-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("theme").value = savedTheme || "midnight";
$("theme").onchange = () => {
  document.documentElement.dataset.theme = $("theme").value;
  localStorage.setItem("ib-theme", $("theme").value);
};

// ---- live state ----------------------------------------------------------
let state = { running: false, chat_safe: null, roleSlot: {} };
function applyState(s) {
  state.running = !!s.running;
  const cf = s.chat_focus || {};
  state.chat_safe = cf.safe;
  state.roleSlot = {};
  (s.members || []).forEach((m) => { if (m.present && m.role) state.roleSlot[m.role] = m.slot; });
  const arm = $("fArm");
  arm.classList.toggle("ok", state.running);
  arm.textContent = state.running ? "● armed" : "○ off";
  const ch = $("fChat");
  ch.classList.toggle("ok", state.chat_safe === true);
  ch.classList.toggle("bad", state.chat_safe === false);
  ch.textContent = state.chat_safe === false ? "chat busy" : "chat ok";
  // buttons that can't fire (disarmed / no role present) look dimmed
  document.querySelectorAll(".fbtn").forEach((b) => {
    const c = BY_ID[b.dataset.id];
    const missing = c.kind === "role" && state.roleSlot[c.role] === undefined;
    b.classList.toggle("disabled", !state.running || missing);
  });
}

// ---- fire ----------------------------------------------------------------
function fire(c, btn) {
  let url;
  if (c.kind === "role") {
    const slot = state.roleSlot[c.role];
    if (slot === undefined) { flash(btn, "no " + c.role); return; }
    url = `/api/act/${c.action}/${slot}`;
  } else url = `/api/act/${c.action}`;
  fetch(url, { method: "POST" }).catch(() => {});
  flash(btn, state.running ? "sent" : "disarmed");
}
function flash(btn, msg) {
  btn.classList.add("fired");
  const t = btn.querySelector(".fb-flash"); if (t) { t.textContent = msg; }
  setTimeout(() => btn.classList.remove("fired"), 320);
}

// ---- render buttons ------------------------------------------------------
function render() {
  const grid = $("grid");
  grid.style.gridTemplateColumns = `repeat(${layout.cols}, 1fr)`;
  grid.innerHTML = "";
  layout.ids.forEach((id) => {
    const c = BY_ID[id]; if (!c) return;
    const b = document.createElement("button");
    b.className = "fbtn" + (c.danger ? " danger" : "");
    b.dataset.id = id;
    b.innerHTML = `<span class="fb-label">${c.label}</span><span class="fb-flash"></span>`;
    b.onclick = () => fire(c, b);
    grid.appendChild(b);
  });
  applyState(state);
}

// ---- editor (pick + drag-reorder) ----------------------------------------
function renderEditor() {
  const list = $("edList");
  list.innerHTML = "";
  // enabled first (in order), then the rest
  const order = [...layout.ids, ...CATALOG.map((c) => c.id).filter((i) => !layout.ids.includes(i))];
  order.forEach((id) => {
    const c = BY_ID[id];
    const on = layout.ids.includes(id);
    const li = document.createElement("li");
    li.className = "ed-item"; li.draggable = true; li.dataset.id = id;
    li.innerHTML = `<span class="ed-grip">⋮⋮</span>` +
      `<input type="checkbox" ${on ? "checked" : ""} />` +
      `<span class="ed-name">${c.label}</span>` +
      `<span class="ed-kind">${c.kind === "role" ? c.role : ""}</span>`;
    li.querySelector("input").onchange = (e) => {
      if (e.target.checked) { if (!layout.ids.includes(id)) layout.ids.push(id); }
      else layout.ids = layout.ids.filter((x) => x !== id);
      saveLayout(); render();
    };
    // drag reorder
    li.addEventListener("dragstart", (e) => { e.dataTransfer.setData("id", id); li.classList.add("drag"); });
    li.addEventListener("dragend", () => li.classList.remove("drag"));
    li.addEventListener("dragover", (e) => e.preventDefault());
    li.addEventListener("drop", (e) => {
      e.preventDefault();
      const from = e.dataTransfer.getData("id");
      if (!from || from === id) return;
      // ensure both enabled, then reorder within ids
      [from, id].forEach((x) => { if (!layout.ids.includes(x)) layout.ids.push(x); });
      layout.ids = layout.ids.filter((x) => x !== from);
      const at = layout.ids.indexOf(id);
      layout.ids.splice(at, 0, from);
      saveLayout(); render(); renderEditor();
    });
    list.appendChild(li);
  });
}
$("cols").value = String(layout.cols);
$("cols").onchange = () => { layout.cols = parseInt($("cols").value, 10); saveLayout(); render(); };
const editBtn = $("editBtn");
function toggleEditor(force) {
  const ed = $("editor");
  const open = force !== undefined ? force : ed.hidden;   // hidden -> open it
  ed.hidden = !open;
  editBtn.classList.toggle("active", open);
  editBtn.textContent = open ? "✕" : "⚙";   // gear toggles to a close X
  if (open) renderEditor();
}
editBtn.onclick = () => toggleEditor();          // same button toggles on/off
const doneBtn = $("doneBtn"); if (doneBtn) doneBtn.onclick = () => toggleEditor(false);

// ---- websocket (live status) ---------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => { try { applyState(JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onerror = () => ws.close();
}
render();
connect();
