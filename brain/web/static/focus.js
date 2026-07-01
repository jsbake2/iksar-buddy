// ib focus — configurable pop-out quick-action overlay.
"use strict";
const $ = (id) => document.getElementById(id);

// Full catalog. kind 'group' -> POST /api/act/<action>. kind 'role' -> resolve
// the slot of <role> from live telemetry, then POST /api/act/<action>/<slot>.
// kind 'member' -> POST /api/act/<action>/<slot> with an EXPLICIT slot (buffs).
const HEALER_CATALOG = [
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
  { id: "follow_dps", label: "Follow DPS", kind: "role", action: "follow", role: "dps", hot: 1 },
  { id: "follow", label: "Follow", kind: "group", action: "follow" },
  { id: "stop_follow", label: "Stop Follow", kind: "group", action: "stop_follow" },
  { id: "call_home", label: "Call Home", kind: "group", action: "call_home" },
  { id: "jump", label: "Jump", kind: "group", action: "jump" },
  { id: "sow", label: "SoW", kind: "group", action: "sow" },
  { id: "hail", label: "Hail", kind: "group", action: "hail" },
  { id: "collect", label: "Collect", kind: "group", action: "collect" },
  { id: "evac", label: "Evac", kind: "group", action: "evac", danger: 1 },
  { id: "reset_combat", label: "⟲ RESET COMBAT", kind: "post", path: "/api/combat/reset", big: 1, hot: 1 },
  { id: "force_in", label: "⚔ Force In Combat", kind: "override", action: "force_combat", hot: 1 },
  { id: "force_out", label: "⏹ Force OOC", kind: "override", action: "force_ooc", danger: 1, hot: 1 },
  { id: "auto_combat", label: "↻ Auto (clear override)", kind: "override", action: "clear", hot: 1 },
  { id: "reengage", label: "⚔ RE-ENGAGE", kind: "group", action: "attack", big: 1, hot: 1 },
  { id: "debuff", label: "Debuff", kind: "group", action: "debuff" },
  { id: "spell_attack", label: "Spell Atk", kind: "group", action: "spell_attack", hot: 1 },
  { id: "food", label: "🍖 Food", kind: "post", path: "/api/macro/food", hot: 1 },
  { id: "deaggro", label: "De-aggro", kind: "group", action: "deaggro" },
  { id: "rez_group", label: "Rez Group", kind: "group", action: "rez" },
];

// Dirge (support) catalog — COMBAT (attacks / debuffs / aoe) + utility + state
// control. NO heals/wards/cures and NO buffs (buffs are cast per-member on the MAIN
// page's buff matrix). Roles match config/profiles/joar.yaml.
const DIRGE_CATALOG = [
  { id: "attack_1", label: "Attack 1", kind: "group", action: "attack_1", hot: 1 },
  { id: "attack_2", label: "Attack 2", kind: "group", action: "attack_2", hot: 1 },
  { id: "enc_attack_1", label: "Enc Attack 1", kind: "group", action: "enc_attack_1", hot: 1 },
  { id: "enc_attack_2", label: "Enc Attack 2", kind: "group", action: "enc_attack_2" },
  { id: "aoe_attack", label: "AoE Attack", kind: "group", action: "aoe_attack", hot: 1 },
  { id: "debuff_1", label: "Debuff 1", kind: "group", action: "debuff_1", hot: 1 },
  { id: "debuff_2", label: "Debuff 2", kind: "group", action: "debuff_2" },
  { id: "deaggro", label: "De-aggro", kind: "group", action: "deaggro" },
  { id: "follow", label: "Follow", kind: "group", action: "follow", hot: 1 },
  { id: "stop_follow", label: "Stop Follow", kind: "group", action: "stop_follow" },
  { id: "jump", label: "Jump", kind: "group", action: "jump" },
  { id: "sow", label: "SoW", kind: "group", action: "sow" },
  { id: "call_home", label: "Call Home", kind: "group", action: "call_home" },
  { id: "evac", label: "Evac", kind: "group", action: "evac", danger: 1 },
  { id: "hail", label: "Hail", kind: "group", action: "hail" },
  { id: "item_use", label: "Item", kind: "group", action: "item_use" },
  { id: "camp", label: "Camp", kind: "group", action: "camp" },
  { id: "reset_combat", label: "⟲ RESET COMBAT", kind: "post", path: "/api/combat/reset", big: 1, hot: 1 },
  { id: "force_in", label: "⚔ Force In Combat", kind: "override", action: "force_combat", hot: 1 },
  { id: "force_out", label: "⏹ Force OOC", kind: "override", action: "force_ooc", danger: 1, hot: 1 },
];

// active catalog swaps with the profile kind (healer heal-grid vs dirge buffs).
const FOCUS_BUILD = "b8";        // bump on focus.js changes — shown in the header for verification
let kind = "healer";
let CATALOG = HEALER_CATALOG;
let BY_ID = Object.fromEntries(CATALOG.map((c) => [c.id, c]));
let DEFAULT = CATALOG.filter((c) => c.hot).map((c) => c.id);
function setKind(k) {
  kind = k;
  CATALOG = k === "dirge" ? DIRGE_CATALOG : HEALER_CATALOG;
  BY_ID = Object.fromEntries(CATALOG.map((c) => [c.id, c]));
  DEFAULT = CATALOG.filter((c) => c.hot).map((c) => c.id);
  ENSURE = ENSURE_BY_KIND[k] || [];
  layout = loadLocal();     // this kind's cached layout (or its default); server overrides
  const b = document.querySelector(".focus-brand");   // show detected kind + build (diagnostic)
  if (b) b.textContent = "ib focus · " + k + " · " + FOCUS_BUILD;
}
// kind from the live profile: prefer the explicit `kind` (what the main page uses),
// fall back to deriving from maint_role.
function kindOf(profile) {
  const p = profile || {};
  return p.kind || (p.maint_role === "none" ? "dirge" : "healer");
}

// ward->hot 1:1 by active healer profile (Defiler wards vs Fury HoTs).
let maintRole = "ward";
const MAINT_MAP = {
  ward_tank:      { ward: { label: "Ward Tank", action: "ward" },               hot: { label: "HoT Tank", action: "hot" } },
  group_ward:     { ward: { label: "Group Ward", action: "group_ward" },         hot: { label: "Group HoT", action: "group_hot" } },
  emergency_ward: { ward: { label: "Emerg Ward", action: "emergency_ward" },     hot: { label: "Emerg HoT", action: "emergency_hot" } },
};
const resolved = (c) => (MAINT_MAP[c.id] ? { ...c, ...MAINT_MAP[c.id][maintRole] } : c);

// ---- persisted layout (per browser + server, PER KIND) --------------------
const LS = () => "ib-focus-layout-" + kind;    // cache key per profile kind
// Buttons added after the first release. Each is merged into an existing saved
// layout ONCE (tracked in `ensured`) so the owner gets them without a reset, but
// a later manual delete still sticks. Per kind — the Dirge catalog is all-new.
const ENSURE_BY_KIND = {
  healer: ["reset_combat", "force_in", "force_out", "auto_combat", "follow_tank", "follow_dps", "buff_self", "buff_tank", "buff_dps", "buff", "spell_attack", "reengage", "food"],
  dirge: [],
};
let ENSURE = ENSURE_BY_KIND.healer;
function mergeLayout(s) {
  if (!s || !Array.isArray(s.ids)) return { ids: DEFAULT.slice(), cols: 3, ensured: ENSURE.slice(), colors: {} };
  s.ids = s.ids.filter((id) => BY_ID[id]);          // drop actions not in THIS kind's catalog
  if (!s.ids.length) s.ids = DEFAULT.slice();       // file was all wrong-kind ids -> use this kind's default
  const ensured = new Set(s.ensured || []);
  for (const id of ENSURE) {
    if (!ensured.has(id)) { if (!s.ids.includes(id)) s.ids.push(id); ensured.add(id); }
  }
  s.ensured = [...ensured];
  if (typeof s.cols !== "number") s.cols = 3;
  if (!s.colors || typeof s.colors !== "object") s.colors = {};   // per-button custom colors
  return s;
}
function loadLocal() {
  let s = null;
  try { s = JSON.parse(localStorage.getItem(LS())); } catch (_) {}
  return mergeLayout(s);
}
// Persist to the SERVER (survives cache clears / different browsers / our updates) PLUS a
// localStorage cache for instant first paint. The server copy is the source of truth.
function saveLayout() {
  localStorage.setItem(LS(), JSON.stringify(layout));
  // POST with our KIND so a healer window can't overwrite the dirge file (and vice-versa)
  fetch("/api/focus-layout?kind=" + kind, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(layout) }).catch(() => {});
}
let layout = loadLocal();           // instant from cache; the server copy overrides on load
async function loadServerLayout() {
  try {
    const r = await fetch("/api/focus-layout?kind=" + kind);
    if (r.ok) {
      const s = await r.json();
      if (s && Array.isArray(s.ids) && s.ids.length) {   // server has a saved layout -> source of truth
        layout = mergeLayout(s);
        localStorage.setItem(LS(), JSON.stringify(layout));
      }
      // server empty -> keep this kind's default (set by setKind); do NOT auto-POST.
    }
  } catch (_) {}
  render();   // ALWAYS repaint (default or server layout) — a kind switch must show up
}

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
// the arm chip is the on/off button: tap to arm/disarm the bot
const armChip = $("fArm");
if (armChip) armChip.onclick = () =>
  fetch(`/api/control/${state.running ? "pause" : "resume"}`, { method: "POST" }).catch(() => {});
function applyState(s) {
  // swap the whole catalog + saved layout when the active profile's kind changes
  const k = kindOf(s.profile);
  if (k !== kind) { setKind(k); render(); loadServerLayout(); }   // repaint immediately in the new kind
  // ward->hot relabel on profile change (re-render once; maintRole then stable)
  const mr = (s.profile || {}).maint_role || "ward";
  if (mr !== maintRole) { maintRole = mr; render(); }
  state.running = !!s.running;
  const cf = s.chat_focus || {};
  state.chat_safe = cf.safe;
  state.roleSlot = {};
  // first present member of each role wins (e.g. Follow DPS -> the first of the 4 dps)
  (s.members || []).forEach((m) => {
    if (m.present && m.role && state.roleSlot[m.role] === undefined) state.roleSlot[m.role] = m.slot;
  });
  const arm = $("fArm");
  arm.classList.toggle("ok", state.running);
  arm.textContent = state.running ? "● armed" : "○ off";
  const ch = $("fChat");
  ch.classList.toggle("ok", state.chat_safe === true);
  ch.classList.toggle("bad", state.chat_safe === false);
  ch.textContent = state.chat_safe === false ? "chat busy" : "chat ok";
  // combat state (+ a manual override marker if one is latched)
  const st = $("fState");
  const sm = s.state || "OOC", ov = s.override;
  const combat = sm === "IN_COMBAT" || sm === "WIPE_RECOVERY" || sm === "REZ_LOOP";
  st.classList.toggle("combat", combat);
  st.classList.toggle("ok", !combat && !ov);
  st.textContent = (ov ? "⏚ " : "") + (sm === "IN_COMBAT" ? "in combat"
    : sm === "REZ_LOOP" ? "rez" : sm === "WIPE_RECOVERY" ? "wipe" : "ooc");
  // buttons that can't fire (disarmed / no role present) look dimmed
  document.querySelectorAll(".fbtn").forEach((b) => {
    const c = BY_ID[b.dataset.id];
    if (!c) return;
    const missing = c.kind === "role" && state.roleSlot[c.role] === undefined;
    // Manual fire works DISARMED — arming gates only the autonomous loop, never the
    // owner's hotkeys. A button is dead only if its target role isn't present, or if
    // chat is unsafe (the inject would be aborted server-side either way).
    const dis = (c.kind === "override" || c.kind === "post")
      ? false : (missing || state.chat_safe === false);
    b.classList.toggle("disabled", dis);
  });
  // DIAGNOSTIC: what does the websocket actually deliver for `profile`?
  const brand = document.querySelector(".focus-brand");
  const p = s.profile;
  if (brand) brand.textContent = "focus·" + kind + "·" + FOCUS_BUILD + "·ws:" +
    (p ? (p.kind ?? "?") + "/" + (p.maint_role ?? "?") : "NO-PROFILE");
}

// ---- fire ----------------------------------------------------------------
function fire(c, btn) {
  let url, msg;
  if (c.kind === "role") {
    const slot = state.roleSlot[c.role];
    if (slot === undefined) { flash(btn, "no " + c.role); return; }
    url = `/api/act/${c.action}/${slot}`;
    msg = state.chat_safe === false ? "chat busy" : "sent";
  } else if (c.kind === "member") {              // explicit slot (Dirge buffs: tank=1, self=0)
    url = `/api/act/${c.action}/${c.slot}`;
    msg = state.chat_safe === false ? "chat busy" : "sent";
  } else if (c.kind === "override") {
    url = `/api/override/${c.action}`;          // state control: works disarmed
    msg = "set";
  } else if (c.kind === "post") {
    url = c.path;                                // raw endpoint (e.g. combat reset)
    msg = "ok";
  } else {
    url = `/api/act/${c.action}`;
    msg = state.chat_safe === false ? "chat busy" : "sent";
  }
  fetch(url, { method: "POST" }).catch(() => {});
  flash(btn, msg);
}
// readable text (black/white) for a given #rrggbb background, by luminance
function textOn(hex) {
  const c = hex.replace("#", "");
  if (c.length < 6) return "#fff";
  const r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55 ? "#10131a" : "#fff";
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
    const c = resolved(BY_ID[id]); if (!BY_ID[id]) return;
    const b = document.createElement("button");
    b.className = "fbtn" + (c.danger ? " danger" : "") + (c.big ? " big" : "");
    b.dataset.id = id;
    const col = (layout.colors || {})[id];
    if (col) {                       // custom color overrides the theme gradient
      b.style.background = `linear-gradient(${col}, color-mix(in srgb, ${col} 55%, #000))`;
      b.style.borderColor = `color-mix(in srgb, ${col} 75%, #000)`;
      b.style.color = textOn(col);   // black or white, whichever is readable
    }
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
    const col = (layout.colors || {})[id] || "#3a4150";
    li.innerHTML = `<span class="ed-grip">⋮⋮</span>` +
      `<input type="checkbox" class="ed-on" ${on ? "checked" : ""} />` +
      `<span class="ed-name">${c.label}</span>` +
      `<span class="ed-kind">${c.kind === "role" ? c.role : ""}</span>` +
      `<input type="color" class="ed-color" value="${col}" title="button color" />` +
      `<button class="ed-colreset" title="reset color">↺</button>`;
    li.querySelector(".ed-on").onchange = (e) => {
      if (e.target.checked) { if (!layout.ids.includes(id)) layout.ids.push(id); }
      else layout.ids = layout.ids.filter((x) => x !== id);
      saveLayout(); render();
    };
    li.querySelector(".ed-color").oninput = (e) => {
      (layout.colors = layout.colors || {})[id] = e.target.value;
      saveLayout(); render();
    };
    li.querySelector(".ed-colreset").onclick = () => {
      if (layout.colors) delete layout.colors[id];
      saveLayout(); render(); renderEditor();
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
// HTTP snapshot poll — the WS doesn't reliably reach this popout through Cloudflare,
// so drive state (incl. the profile kind) off a cache-busted GET as the source of
// truth. Cheap, and it makes the window correct even if the WS never connects.
async function poll() {
  try {
    // UNIQUE PATH per request — the CF edge caches by path and was serving a stale
    // /api/snapshot (ignoring the ?t query). A fresh path is always a cache miss.
    const s = await (await fetch("/api/live/" + Date.now())).json();
    applyState(s);
  } catch (_) {}
}
// determine the profile kind FIRST (so the right catalog + layout load), then paint.
async function init() {
  try {
    const p = await (await fetch("/api/profiles?t=" + Date.now())).json();
    setKind(kindOf(p));
  } catch (_) {}
  render();
  connect();
  loadServerLayout();   // override the local cache with the server-saved layout for this kind
  poll();
  setInterval(poll, 1500);
}
init();
