/* Recipe browser + craft-list builder, integrated into the forge dashboard.
   Browse the scraped recipe data (/recipedata), build a checkbox list, then SAVE it
   (appears in every bot's Load dropdown) or SEND it straight to a crafter's queue. */
"use strict";
const DATA = "/recipedata";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

// theme: shared key with the dashboard so the choice carries across pages
const themeSel = $("theme");
const savedTheme = localStorage.getItem("ibf-theme");
if (savedTheme) { document.documentElement.dataset.theme = savedTheme; themeSel.value = savedTheme; }
themeSel.onchange = () => { document.documentElement.dataset.theme = themeSel.value; localStorage.setItem("ibf-theme", themeSel.value); };

const state = {
  manifest: null, cls: null, isSide: false, items: [], cache: {},
  view: "tree", search: "", cat: "all", lmin: null, lmax: null,
  sort: { key: "level", dir: 1 },
  sel: new Map(),       // key -> {recipe, book, category, level, cls}
  lists: {}, bots: [],
};

const CAT_LABEL = { "TS Essentials": "Essentials", "TS Advanced": "Advanced", "TS Apprentice": "Apprentice",
  "TS Journeyman": "Journeyman", "TS Shadow": "Shadow", "TS Shadowed": "Shadowed", "Tinkering": "Tinkering", "Adornments": "Adornments" };
const catLabel = (c) => CAT_LABEL[c] || (c || "").replace(/^TS /, "");
const isAdvanced = (c) => /Advanced/i.test(c);
const isEssential = (c) => /Essentials/i.test(c);
const selKey = (cls, it) => `${cls || "?"}::${it.recipe_id || it.recipe}`;

// ---- boot -----------------------------------------------------------------
async function boot() {
  try { state.manifest = await (await fetch(DATA + "/manifest.json")).json(); }
  catch (e) {
    $("view").innerHTML = `<div class="placeholder"><div>Couldn't load recipe data.</div>
      <div class="muted">Run <code>tools/recipe_scrape/scrape.py</code> and redeploy.</div></div>`;
    return;
  }
  $("subcount").textContent = `${state.manifest.total_rows.toLocaleString()} recipe-rows · ${state.manifest.main.length} classes + ${state.manifest.side.length} skill/misc`;
  buildRail();
  await Promise.all([loadLists(), loadBots()]);
  const last = localStorage.getItem("ibf-class");
  const all = [...state.manifest.main, ...state.manifest.side];
  const pick = all.find(e => e.class === last) || state.manifest.main[0];
  selectClass(pick.class, state.manifest.side.includes(pick));
}

function buildRail() {
  const mk = (entry, side) => {
    const li = el("li"); li.dataset.cls = entry.class;
    const lvl = side ? `${entry.groups} books` : (entry.min_level != null ? `L${entry.min_level}–${entry.max_level}` : "");
    li.innerHTML = `<span class="name">${entry.class}</span><span class="count">${entry.recipes.toLocaleString()}</span>`;
    li.title = `${entry.recipes} recipe-rows · ${lvl}`;
    li.onclick = () => selectClass(entry.class, side);
    return li;
  };
  $("rail-main").replaceChildren(...state.manifest.main.map(e => mk(e, false)));
  $("rail-side").replaceChildren(...state.manifest.side.map(e => mk(e, true)));
}

async function selectClass(cls, isSide) {
  state.cls = cls; state.isSide = isSide;
  localStorage.setItem("ibf-class", cls);
  document.querySelectorAll(".rail li[data-cls]").forEach(li => li.classList.toggle("active", li.dataset.cls === cls));
  if (!state.cache[cls]) {
    const dir = isSide ? "side" : "by_class";
    const raw = await (await fetch(`${DATA}/${dir}/${cls.toLowerCase()}.json`)).json();
    const items = [];
    for (const [key, list] of Object.entries(raw)) {
      const level = /^\d+$/.test(key) ? parseInt(key, 10) : null;
      for (const r of list) items.push({ ...r, level, groupKey: key });
    }
    state.cache[cls] = items;
  }
  state.items = state.cache[cls];
  state.cat = "all"; state.search = ""; $("search").value = "";
  state.lmin = state.lmax = null; $("lmin").value = ""; $("lmax").value = "";
  buildChips(); render();
}

// ---- filtering / render (tree + table) ------------------------------------
function buildChips() {
  const cats = [...new Set(state.items.map(i => i.category))].sort();
  const box = $("catchips"); box.replaceChildren();
  const add = (val, label) => { const c = el("span", "chip" + (state.cat === val ? " active" : ""), label);
    c.onclick = () => { state.cat = val; buildChips(); render(); }; box.appendChild(c); };
  add("all", "All"); for (const c of cats) add(c, catLabel(c));
}
function filtered() {
  const q = state.search.toLowerCase();
  return state.items.filter(i => {
    if (state.cat !== "all" && i.category !== state.cat) return false;
    if (state.lmin != null && (i.level == null || i.level < state.lmin)) return false;
    if (state.lmax != null && (i.level == null || i.level > state.lmax)) return false;
    if (q && !(i.recipe.toLowerCase().includes(q) || (i.book || "").toLowerCase().includes(q) || catLabel(i.category).toLowerCase().includes(q))) return false;
    return true;
  });
}
function render() {
  const items = filtered();
  $("resultcount").textContent = `${items.length.toLocaleString()} of ${state.items.length.toLocaleString()}`;
  if (state.view === "table") renderTable(items); else renderTree(items);
  renderTray();
}
function recipeRow(it) {
  const row = el("div", "rrow");
  if (state.sel.has(selKey(state.cls, it))) row.classList.add("sel");
  const tick = el("span", "tickbox"); tick.textContent = "✓";
  const mid = el("div"); mid.appendChild(el("div", "rname", it.recipe)); mid.appendChild(el("div", "rbook", it.book || ""));
  const right = el("div");
  right.appendChild(el("span", "tier-chip " + (isAdvanced(it.category) ? "adv" : isEssential(it.category) ? "ess" : ""), catLabel(it.category)));
  row.append(tick, mid, right);
  row.onclick = () => { toggleSel(it); render(); };
  return row;
}
function renderTree(items) {
  const view = $("view"); view.replaceChildren();
  if (!items.length) { view.appendChild(emptyMsg()); return; }
  if (state.isSide) {
    const byBook = groupBy(items, i => i.groupKey);
    for (const book of Object.keys(byBook).sort(volSort)) {
      const d = el("details", "tier"); d.open = Object.keys(byBook).length <= 12;
      const s = el("summary"); s.innerHTML = `<span class="caret">▸</span><span>${book}</span><span class="tier-meta">${byBook[book].length} recipes</span>`;
      d.appendChild(s); const block = el("div", "catblock"); byBook[book].forEach(it => block.appendChild(recipeRow(it))); d.appendChild(block); view.appendChild(d);
    }
    return;
  }
  const band = (lv) => lv == null ? 9999 : lv < 10 ? 1 : Math.floor(lv / 10) * 10;
  const bands = groupBy(items, i => band(i.level));
  for (const bk of Object.keys(bands).map(Number).sort((a, b) => a - b)) {
    const lvls = [...new Set(bands[bk].map(i => i.level))].sort((a, b) => (a ?? 1e9) - (b ?? 1e9));
    const label = bk === 9999 ? "No level (quest/misc)" : bk === 1 ? "Tier 1–9" : `Tier ${bk}–${bk + 9}`;
    const d = el("details", "tier"); d.open = Object.keys(bands).length <= 4 || !!state.search;
    const s = el("summary"); s.innerHTML = `<span class="caret">▸</span><span>${label}</span><span class="tier-meta">${bands[bk].length} recipes · L${lvls[0] ?? "—"}–${lvls[lvls.length - 1] ?? "—"}</span>`;
    d.appendChild(s);
    for (const lv of lvls) {
      const lvWrap = el("div", "lvl"); lvWrap.appendChild(el("div", "lvl-head", lv == null ? "— (no level)" : `Level ${lv}`));
      const cats = groupBy(bands[bk].filter(i => i.level === lv), i => i.category);
      for (const c of Object.keys(cats).sort((a, b) => rank(a) - rank(b))) {
        const block = el("div", "catblock"); block.appendChild(el("div", "cat-label", catLabel(c)));
        cats[c].forEach(it => block.appendChild(recipeRow(it))); lvWrap.appendChild(block);
      }
      d.appendChild(lvWrap);
    }
    view.appendChild(d);
  }
}
function renderTable(items) {
  const view = $("view"); view.replaceChildren();
  if (!items.length) { view.appendChild(emptyMsg()); return; }
  const cols = state.isSide ? [["recipe", "Recipe"], ["category", "Type"], ["groupKey", "Book"]]
                            : [["recipe", "Recipe"], ["level", "Lvl"], ["category", "Type"], ["book", "Book"]];
  const sorted = [...items].sort(cmp(state.sort.key, state.sort.dir));
  const table = el("table", "flat"); const thr = el("tr"); thr.appendChild(el("th", null, ""));
  for (const [key, label] of cols) {
    const th = el("th", null, label);
    if (state.sort.key === key) th.insertAdjacentHTML("beforeend", ` <span class="arrow">${state.sort.dir > 0 ? "▲" : "▼"}</span>`);
    th.onclick = () => { if (state.sort.key === key) state.sort.dir *= -1; else state.sort = { key, dir: 1 }; render(); };
    thr.appendChild(th);
  }
  const head = el("thead"); head.appendChild(thr); table.appendChild(head);
  const body = el("tbody");
  for (const it of sorted) {
    const tr = el("tr"); if (state.sel.has(selKey(state.cls, it))) tr.classList.add("sel");
    const tick = el("td"); tick.textContent = state.sel.has(selKey(state.cls, it)) ? "✓" : ""; tr.appendChild(tick);
    for (const [key] of cols) {
      const td = el("td");
      if (key === "category") td.innerHTML = `<span class="tier-chip ${isAdvanced(it.category) ? "adv" : isEssential(it.category) ? "ess" : ""}">${catLabel(it.category)}</span>`;
      else if (key === "level") { td.textContent = it.level ?? "—"; td.className = "muted"; }
      else if (key === "book" || key === "groupKey") { td.textContent = it[key] || ""; td.className = "muted"; }
      else td.textContent = it[key] ?? "";
      tr.appendChild(td);
    }
    tr.onclick = () => { toggleSel(it); render(); }; body.appendChild(tr);
  }
  table.appendChild(body); view.appendChild(table);
}

// ---- selection / tray -----------------------------------------------------
function toggleSel(it) {
  const k = selKey(state.cls, it);
  if (state.sel.has(k)) state.sel.delete(k);
  else state.sel.set(k, { recipe: it.recipe, book: it.book, category: it.category, level: it.level, cls: state.cls });
}
function addRow(rec) {                      // add a {name,count,search} or recipe-ish object
  const recipe = rec.recipe || rec.name; if (!recipe) return;
  state.sel.set(`list::${recipe}`, { recipe, book: rec.book || "", category: rec.category || "", level: rec.level ?? null, cls: rec.cls || null, count: rec.count, search: rec.search });
}
function renderTray() {
  const list = $("traylist"); list.replaceChildren();
  const arr = [...state.sel.entries()];
  $("traycount").textContent = arr.length;
  for (const [k, v] of arr) {
    const li = el("li");
    li.append(el("span", null, v.recipe), el("span", "tl-meta", v.level != null ? `L${v.level}` : (v.cls || "")));
    const x = el("span", "x", "✕"); x.title = "remove"; x.onclick = (e) => { e.stopPropagation(); state.sel.delete(k); render(); };
    li.appendChild(x); list.appendChild(li);
  }
  const has = arr.length > 0;
  ["save-list", "export-yaml", "send-queue", "send-start", "clear-sel"].forEach(id => $(id).disabled = !has);
}

// ---- saved lists ----------------------------------------------------------
async function loadLists() {
  try { state.lists = (await (await fetch("/api/forgelists")).json()).lists || {}; }
  catch { state.lists = {}; }
  const ul = $("rail-lists"); ul.replaceChildren();
  for (const name of Object.keys(state.lists).sort()) {
    const li = el("li");
    li.innerHTML = `<span class="name" title="load ${name} into the tray">${name}</span><span class="count">${state.lists[name].length}</span>`;
    li.onclick = () => { for (const r of state.lists[name]) addRow(r); $("listname").value = name; render(); toast(`Loaded "${name}" (${state.lists[name].length})`); };
    const del = el("span", "del", "✕"); del.title = "delete saved list";
    del.onclick = async (e) => { e.stopPropagation(); if (!confirm(`Delete saved list "${name}"?`)) return; delete state.lists[name]; await putLists(); loadLists(); toast(`Deleted "${name}"`); };
    li.appendChild(del); ul.appendChild(li);
  }
  if (!Object.keys(state.lists).length) ul.appendChild(el("li", "muted", "— none yet —"));
}
function selRows() { return [...state.sel.values()].map(v => ({ name: v.recipe, count: v.count || 1, search: v.search || "" })); }
async function putLists() {
  const r = await fetch("/api/forgelists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lists: state.lists }) });
  if (!r.ok) throw new Error("save failed");
}
$("save-list").onclick = async () => {
  const name = $("listname").value.trim();
  if (!name) { toast("Name the list first", "bad"); $("listname").focus(); return; }
  state.lists[name] = selRows();
  try { await putLists(); loadLists(); toast(`Saved "${name}" (${state.lists[name].length}) — in the Load dropdowns`, "good"); }
  catch { toast("Save failed", "bad"); }
};

// ---- bots / send ----------------------------------------------------------
async function loadBots() {
  try {
    const snap = await (await fetch("/api/snapshot")).json();
    let bots = snap.bots || snap; if (!Array.isArray(bots)) bots = Object.values(bots);
    state.bots = bots.filter(b => b && b.id);
  } catch { state.bots = []; }
  const sel = $("botsel"); sel.replaceChildren();
  if (!state.bots.length) { sel.appendChild(el("option", null, "no crafters")); return; }
  for (const b of state.bots) {
    const label = `${b.id}${b.character ? " · " + b.character : ""}${b.trade_class ? " (" + b.trade_class + ")" : ""}${b.state ? " — " + b.state : ""}`;
    const o = el("option", null, label); o.value = b.id; sel.appendChild(o);
  }
}
async function send(start) {
  const rows = selRows(); if (!rows.length) return;
  const id = $("botsel").value; const bot = state.bots.find(b => b.id === id);
  if (!bot) { toast("Pick a crafter", "bad"); return; }
  try {
    let r = await fetch(`/api/bot/${id}/queue`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ queue: rows }) });
    if (!r.ok) throw new Error("queue");
    if (start) {
      r = await fetch(`/api/bot/${id}/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "writ", trade_class: bot.trade_class || "" }) });
      if (!r.ok) throw new Error("start");
      toast(`Sent ${rows.length} to ${id} and started ✓`, "good");
    } else {
      toast(`Loaded ${rows.length} into ${id}'s queue (press Start on the dashboard)`, "good");
    }
    loadBots();
  } catch (e) { toast(`Send failed (${e.message})`, "bad"); }
}
$("send-queue").onclick = () => send(false);
$("send-start").onclick = () => send(true);
$("clear-sel").onclick = () => { state.sel.clear(); render(); };
$("addall").onclick = () => { filtered().forEach(it => { if (!state.sel.has(selKey(state.cls, it))) toggleSel(it); }); render(); toast("Added filtered recipes"); };
$("export-yaml").onclick = () => {
  const name = ($("listname").value.trim() || `browser-${(state.cls || "list").toLowerCase()}`).replace(/\s+/g, "-");
  let y = `# Paste under "lists:" in config/forge/lists.yaml.\n${name}:\n`;
  for (const r of selRows()) y += `  - {name: "${r.name.replace(/"/g, '\\"')}", count: ${r.count}, search: "${r.search}"}\n`;
  const a = el("a"); a.href = URL.createObjectURL(new Blob([y], { type: "text/yaml" })); a.download = `craftlist-${name}.yaml`; a.click(); URL.revokeObjectURL(a.href);
  toast(`Exported ${state.sel.size} → ${a.download}`);
};

// ---- helpers --------------------------------------------------------------
function groupBy(arr, fn) { const o = {}; for (const x of arr) { const k = fn(x); (o[k] = o[k] || []).push(x); } return o; }
function rank(cat) { return isEssential(cat) ? 0 : isAdvanced(cat) ? 1 : 2; }
function cmp(key, dir) { return (a, b) => { let x = a[key], y = b[key]; if (key === "level") { x = x ?? 1e9; y = y ?? 1e9; return (x - y) * dir; } return String(x ?? "").localeCompare(String(y ?? "")) * dir; }; }
const ROMAN = { I: 1, II: 2, III: 3, IV: 4, V: 5, VI: 6, VII: 7, VIII: 8, IX: 9, X: 10, XI: 11, XII: 12 };
function volSort(a, b) {
  const va = (a.match(/Volume\s+([IVX]+|\d+)/i) || [])[1], vb = (b.match(/Volume\s+([IVX]+|\d+)/i) || [])[1];
  const base = a.replace(/Volume.*/i, ""), baseb = b.replace(/Volume.*/i, "");
  if (base !== baseb) return base.localeCompare(baseb);
  return ((va ? (ROMAN[va] ?? +va) : 0)) - ((vb ? (ROMAN[vb] ?? +vb) : 0));
}
function emptyMsg() { const d = el("div", "placeholder"); d.innerHTML = "<div>No recipes match these filters.</div>"; return d; }
let toastT;
function toast(msg, kind) { const t = $("toast"); t.textContent = msg; t.className = "toast show" + (kind ? " " + kind : ""); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2200); }

// ---- wiring ---------------------------------------------------------------
$("search").oninput = (e) => { state.search = e.target.value.trim(); render(); };
$("lmin").oninput = (e) => { state.lmin = e.target.value ? +e.target.value : null; render(); };
$("lmax").oninput = (e) => { state.lmax = e.target.value ? +e.target.value : null; render(); };
document.querySelectorAll(".seg-btn").forEach(b => b.onclick = () => {
  document.querySelectorAll(".seg-btn").forEach(x => x.classList.toggle("active", x === b)); state.view = b.dataset.view; render();
});

boot();
