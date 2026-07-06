// ib · forge — "set up tradeskills" calibration picker. Click a point or drag a
// box on the live VM frame to capture pixels / regions / clicks / templates into
// craft.yaml. Coords map display->guest via the frame's natural size (1920x1080).
"use strict";
const { $ } = ibUI;   // ui-core.js (web_common, P5.4)

ibUI.theme($("theme"), "ibf-theme");

// Capture targets. kind: pixel (loc+color) | click (point) | pixelclick (both) |
// region {x,y,w,h} | template (drag a box -> POST as <name>.png).
const TARGETS = [
  { grp: "Crafting" },
  { id: "btn1", nm: "Reaction button #1 position", kind: "btnregion", btn: 0 },
  { id: "btn2", nm: "Reaction button #2 position", kind: "btnregion", btn: 1 },
  { id: "btn3", nm: "Reaction button #3 position", kind: "btnregion", btn: 2 },
  { id: "reaction_region", nm: "Active-reaction watch area", kind: "region", path: "reaction.region" },
  { id: "mode", nm: "Durability/Progress mode pixel", kind: "pixel", loc: "durability_mode.location", col: "durability_mode.progress_color" },
  { id: "power", nm: "Power gate pixel", kind: "pixel", loc: "power.location", col: "power.ok_color" },
  { id: "begin", nm: "Begin button (pixel + click)", kind: "pixelclick", loc: "begin.pixel.location", col: "begin.pixel.color", clk: "begin.click" },
  { id: "retry", nm: "Retry button (pixel + click)", kind: "pixelclick", loc: "retry.pixel.location", col: "retry.pixel.color", clk: "retry.click" },
  { id: "focus", nm: "Craft-window focus click", kind: "click", clk: "craft_focus_click" },
  { grp: "Writs / recipe select" },
  { id: "clear", nm: "Search: clear", kind: "click", clk: "recipe_select.clear_click" },
  { id: "search", nm: "Search: box", kind: "click", clk: "recipe_select.search_click" },
  { id: "result", nm: "Search: first result", kind: "click", clk: "recipe_select.result_click" },
  { id: "journal", nm: "Journal OCR region", kind: "region", path: "journal_ocr.region" },
  { grp: "Safety" },
  { id: "chat", nm: "Chat input region", kind: "region", path: "chat_input.region" },
];

let bot = null;
let armed = null;                 // currently-armed target id
const captured = {};              // id -> value (for display + save)

// ---- frame + coordinate mapping ------------------------------------------
const frame = $("frame");
function loadFrame() {
  if (bot) frame.src = `/api/bot/${bot}/calibframe.jpg?t=${Date.now()}`;
}
frame.onerror = () => flash("no VM screen — launch a crafter to the crafting window, then ↻ refresh", false);
frame.onload = () => { const s = $("status"); if (s.classList.contains("bad")) s.textContent = ""; };
function toGuest(e) {
  const r = frame.getBoundingClientRect();
  const sx = frame.naturalWidth / r.width;
  const sy = frame.naturalHeight / r.height;
  return [Math.round((e.clientX - r.left) * sx), Math.round((e.clientY - r.top) * sy)];
}

// ---- drag selection -------------------------------------------------------
const sel = $("sel");
let dragStart = null;
frame.addEventListener("mousedown", (e) => { if (armed) { dragStart = [e.clientX, e.clientY]; sel.hidden = false; } });
window.addEventListener("mousemove", (e) => {
  if (!dragStart) return;
  const r = $("imgbox").getBoundingClientRect();
  const x0 = Math.min(dragStart[0], e.clientX) - r.left, y0 = Math.min(dragStart[1], e.clientY) - r.top;
  sel.style.left = x0 + "px"; sel.style.top = y0 + "px";
  sel.style.width = Math.abs(e.clientX - dragStart[0]) + "px";
  sel.style.height = Math.abs(e.clientY - dragStart[1]) + "px";
});
window.addEventListener("mouseup", async (e) => {
  if (!dragStart || !armed) { dragStart = null; return; }
  const p0 = toGuest({ clientX: dragStart[0], clientY: dragStart[1] });
  const p1 = toGuest(e);
  const moved = Math.hypot(e.clientX - dragStart[0], e.clientY - dragStart[1]);
  dragStart = null; sel.hidden = true;
  const t = TARGETS.find((x) => x.id === armed);
  if (!t) return;
  const region = { x: Math.min(p0[0], p1[0]), y: Math.min(p0[1], p1[1]),
                   w: Math.abs(p1[0] - p0[0]), h: Math.abs(p1[1] - p0[1]) };
  const point = p1;
  if ((t.kind === "region" || t.kind === "btnregion") && moved > 4) {
    captured[t.id] = { _region: region };
    render();
  } else {
    await capturePoint(t, point);
  }
  disarm();
});

// ---- capture handlers -----------------------------------------------------
async function capturePoint(t, [x, y]) {
  if (t.kind === "click") {
    captured[t.id] = { _click: [x, y] };
  } else if (t.kind === "pixel" || t.kind === "pixelclick") {
    const rgb = await fetch(`/api/bot/${bot}/pixel?x=${x}&y=${y}`).then((r) => r.json()).then((d) => d.rgb).catch(() => null);
    captured[t.id] = { _loc: [x, y], _col: rgb || [0, 0, 0], _click: [x, y] };
  }
  render();
}
// ---- target list ----------------------------------------------------------
function valText(t) {
  const c = captured[t.id];
  if (!c) return "—";
  if (c._region) return `${c._region.x},${c._region.y} ${c._region.w}×${c._region.h}` + (c._tpl ? ` · ${c._tpl}` : "");
  if (c._loc) return `(${c._loc[0]},${c._loc[1]})`;
  if (c._click) return `(${c._click[0]},${c._click[1]})`;
  return "set";
}
function render() {
  const list = $("list");
  list.innerHTML = "";
  TARGETS.forEach((t) => {
    if (t.grp) { const h = document.createElement("div"); h.className = "cal-grp"; h.textContent = t.grp; list.appendChild(h); return; }
    const row = document.createElement("div");
    row.className = "cal-item" + (armed === t.id ? " armed" : "");
    const c = captured[t.id];
    const sw = (c && c._col) ? `<span class="sw" style="background:rgb(${c._col.join(",")})"></span>` : "";
    row.innerHTML = `<div><div class="nm">${t.nm}</div><div class="val ${c ? "set" : ""}">${valText(t)}${sw}</div></div>` +
      `<button class="cap-btn">${armed === t.id ? "click/drag…" : "Capture"}</button>`;
    row.querySelector(".cap-btn").onclick = () => { armed = (armed === t.id ? null : t.id); render(); };
    list.appendChild(row);
  });
}
function disarm() { armed = null; render(); }

// ---- save -----------------------------------------------------------------
function setPath(obj, path, val) {
  const keys = path.split("."); let o = obj;
  for (let i = 0; i < keys.length - 1; i++) { o[keys[i]] = o[keys[i]] || {}; o = o[keys[i]]; }
  o[keys[keys.length - 1]] = val;
}
let existing = {};
$("save").onclick = () => {
  const updates = {};
  TARGETS.forEach((t) => {
    const c = captured[t.id]; if (!c) return;
    if (t.loc && c._loc) setPath(updates, t.loc, c._loc);
    if (t.col && c._col) setPath(updates, t.col, c._col);
    if (t.clk && c._click) setPath(updates, t.clk, c._click);
    if (t.path && c._region) setPath(updates, t.path, c._region);
  });
  // assemble reaction.button_regions [#1,#2,#3] — captured this session or kept
  const exBtns = ((existing.reaction || {}).button_regions) || [];
  const btns = ["btn1", "btn2", "btn3"].map((id, i) =>
    (captured[id] && captured[id]._region) || exBtns[i]).filter(Boolean);
  if (btns.length) setPath(updates, "reaction.button_regions", btns);
  fetch("/api/calib", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ updates }),
  }).then((r) => r.json()).then((d) => flash(d.ok ? "calibration saved ✓" : (d.error || "save failed"), d.ok))
    .catch(() => flash("save failed", false));
};
function flash(msg, ok) {
  const s = $("status"); s.textContent = msg; s.className = "cal-status " + (ok ? "good" : "bad");
  setTimeout(() => { s.textContent = ""; }, 3000);
}

// ---- init -----------------------------------------------------------------
$("refresh").onclick = loadFrame;
fetch("/api/calib").then((r) => r.json()).then((d) => { existing = d || {}; }).catch(() => {});
fetch("/api/snapshot").then((r) => r.json()).then((s) => {
  const sel = $("bot");
  sel.innerHTML = (s.order || []).map((id) => {
    const b = s.bots[id]; return `<option value="${id}">${b.label || id} (${b.dom})</option>`;
  }).join("");
  bot = (s.order || [])[0] || null;
  sel.onchange = () => { bot = sel.value; loadFrame(); };
  loadFrame();
  render();
});
