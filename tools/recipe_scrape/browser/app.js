/* iksar_buddy recipe browser. Vanilla JS, no deps. Loads the scraped per-class JSON
   on demand, renders a class-first tier tree OR a flat filterable table ("Both",
   owner's pick), and builds an exportable craft list. */
"use strict";
const DATA = "../data";   // served from tools/recipe_scrape root (see serve.py)
const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

// ---- theme persistence (same key convention as the dashboard) -------------
const themeSel = $("theme");
const savedTheme = localStorage.getItem("ibf-theme");
if (savedTheme) { document.documentElement.dataset.theme = savedTheme; themeSel.value = savedTheme; }
themeSel.onchange = () => { document.documentElement.dataset.theme = themeSel.value; localStorage.setItem("ibf-theme", themeSel.value); };

// ---- state ----------------------------------------------------------------
const state = {
  manifest: null,
  cls: null,            // active class name
  isSide: false,        // skill/misc class (grouped by book, not level)
  items: [],            // normalized recipes for the active class
  cache: {},            // class -> normalized items
  view: "tree",
  search: "",
  cat: "all",           // active category filter (raw category or "all")
  lmin: null, lmax: null,
  sort: { key: "level", dir: 1 },
  sel: new Map(),       // key -> {recipe, book, category, level, cls}
};

const CAT_LABEL = {
  "TS Essentials": "Essentials", "TS Advanced": "Advanced", "TS Apprentice": "Apprentice",
  "TS Journeyman": "Journeyman", "TS Shadow": "Shadow", "TS Shadowed": "Shadowed",
  "Tinkering": "Tinkering", "Adornments": "Adornments",
};
const catLabel = (c) => CAT_LABEL[c] || c.replace(/^TS /, "");
const isAdvanced = (c) => /Advanced/i.test(c);
const isEssential = (c) => /Essentials/i.test(c);
const selKey = (cls, it) => `${cls}::${it.recipe_id || it.recipe}`;

// ---- boot -----------------------------------------------------------------
async function boot() {
  try {
    state.manifest = await (await fetch(DATA+"/manifest.json")).json();
  } catch (e) {
    $("view").innerHTML = `<div class="placeholder"><div>Couldn't load <code>data/manifest.json</code>.</div>
      <div class="muted">Run the scraper, then launch via <code>serve.py</code> (not file://).</div></div>`;
    return;
  }
  $("subcount").textContent = `${state.manifest.total_rows.toLocaleString()} recipe-rows · ${state.manifest.main.length} classes + ${state.manifest.side.length} skill/misc`;
  buildRail();
  // restore last class or pick the first
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
  document.querySelectorAll(".rail li").forEach(li => li.classList.toggle("active", li.dataset.cls === cls));
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
  buildChips();
  render();
}

// ---- filtering ------------------------------------------------------------
function buildChips() {
  const cats = [...new Set(state.items.map(i => i.category))].sort();
  const box = $("catchips"); box.replaceChildren();
  const add = (val, label) => {
    const c = el("span", "chip" + (state.cat === val ? " active" : ""), label);
    c.onclick = () => { state.cat = val; buildChips(); render(); };
    box.appendChild(c);
  };
  add("all", "All");
  for (const c of cats) add(c, catLabel(c));
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

// ---- render ---------------------------------------------------------------
function render() {
  const items = filtered();
  $("resultcount").textContent = `${items.length.toLocaleString()} of ${state.items.length.toLocaleString()}`;
  if (state.view === "table") renderTable(items);
  else renderTree(items);
  renderTray();
}

function recipeRow(it) {
  const row = el("div", "rrow");
  if (state.sel.has(selKey(state.cls, it))) row.classList.add("sel");
  const tick = el("span", "tickbox"); tick.textContent = "✓";
  const mid = el("div");
  mid.appendChild(el("div", "rname", it.recipe));
  const book = el("div", "rbook", it.book || "");
  mid.appendChild(book);
  const right = el("div");
  const chip = el("span", "tier-chip " + (isAdvanced(it.category) ? "adv" : isEssential(it.category) ? "ess" : ""), catLabel(it.category));
  right.appendChild(chip);
  row.append(tick, mid, right);
  row.onclick = () => { toggleSel(it); render(); };
  return row;
}

function renderTree(items) {
  const view = $("view"); view.replaceChildren();
  if (!items.length) { view.appendChild(emptyMsg()); return; }

  if (state.isSide) {                       // skill-based: group by book (volume order)
    const byBook = groupBy(items, i => i.groupKey);
    for (const book of Object.keys(byBook).sort(volSort)) {
      const d = el("details", "tier"); d.open = Object.keys(byBook).length <= 12;
      const s = el("summary");
      s.innerHTML = `<span class="caret">▸</span><span>${book}</span><span class="tier-meta">${byBook[book].length} recipes</span>`;
      d.appendChild(s);
      const block = el("div", "catblock");
      byBook[book].forEach(it => block.appendChild(recipeRow(it)));
      d.appendChild(block);
      view.appendChild(d);
    }
    return;
  }

  // main classes: tier band -> level -> category. EQ2 tiers: 1-9, 10-19, 20-29…
  const band = (lv) => lv == null ? 9999 : lv < 10 ? 1 : Math.floor(lv / 10) * 10;
  const bands = groupBy(items, i => band(i.level));
  const bandKeys = Object.keys(bands).map(Number).sort((a, b) => a - b);
  for (const bk of bandKeys) {
    const lvls = [...new Set(bands[bk].map(i => i.level))].sort((a, b) => (a ?? 1e9) - (b ?? 1e9));
    const label = bk === 9999 ? "No level (quest/misc)" : bk === 1 ? "Tier 1–9" : `Tier ${bk}–${bk + 9}`;
    const d = el("details", "tier"); d.open = bandKeys.length <= 4 || !!state.search;
    const s = el("summary");
    s.innerHTML = `<span class="caret">▸</span><span>${label}</span><span class="tier-meta">${bands[bk].length} recipes · L${lvls[0] ?? "—"}–${lvls[lvls.length - 1] ?? "—"}</span>`;
    d.appendChild(s);
    for (const lv of lvls) {
      const lvItems = bands[bk].filter(i => i.level === lv);
      const lvWrap = el("div", "lvl");
      lvWrap.appendChild(el("div", "lvl-head", lv == null ? "— (no level)" : `Level ${lv}`));
      // Essentials first, then Advanced, then the rest — no duplication, just labelled sections
      const cats = groupBy(lvItems, i => i.category);
      const order = Object.keys(cats).sort((a, b) => rank(a) - rank(b));
      for (const c of order) {
        const block = el("div", "catblock");
        block.appendChild(el("div", "cat-label", catLabel(c)));
        cats[c].forEach(it => block.appendChild(recipeRow(it)));
        lvWrap.appendChild(block);
      }
      d.appendChild(lvWrap);
    }
    view.appendChild(d);
  }
}

function renderTable(items) {
  const view = $("view"); view.replaceChildren();
  if (!items.length) { view.appendChild(emptyMsg()); return; }
  const cols = state.isSide
    ? [["recipe", "Recipe"], ["category", "Type"], ["groupKey", "Book"]]
    : [["recipe", "Recipe"], ["level", "Lvl"], ["category", "Type"], ["book", "Book"]];
  const sorted = [...items].sort(cmp(state.sort.key, state.sort.dir));
  const table = el("table", "flat");
  const thead = el("tr");
  thead.appendChild(el("th", null, ""));   // tick col
  for (const [key, label] of cols) {
    const th = el("th", null, label);
    if (state.sort.key === key) th.insertAdjacentHTML("beforeend", ` <span class="arrow">${state.sort.dir > 0 ? "▲" : "▼"}</span>`);
    th.onclick = () => { if (state.sort.key === key) state.sort.dir *= -1; else state.sort = { key, dir: 1 }; render(); };
    thead.appendChild(th);
  }
  const head = el("thead"); head.appendChild(thead); table.appendChild(head);
  const body = el("tbody");
  for (const it of sorted) {
    const tr = el("tr");
    if (state.sel.has(selKey(state.cls, it))) tr.classList.add("sel");
    const tick = el("td"); tick.innerHTML = state.sel.has(selKey(state.cls, it)) ? "✓" : "";
    tr.appendChild(tick);
    for (const [key] of cols) {
      const td = el("td");
      if (key === "category") td.innerHTML = `<span class="tier-chip ${isAdvanced(it.category) ? "adv" : isEssential(it.category) ? "ess" : ""}">${catLabel(it.category)}</span>`;
      else if (key === "level") { td.textContent = it.level ?? "—"; td.className = "muted"; }
      else if (key === "book" || key === "groupKey") { td.textContent = it[key] || ""; td.className = "muted"; }
      else td.textContent = it[key] ?? "";
      tr.appendChild(td);
    }
    tr.onclick = () => { toggleSel(it); render(); };
    body.appendChild(tr);
  }
  table.appendChild(body);
  view.appendChild(table);
}

// ---- selection / tray -----------------------------------------------------
function toggleSel(it) {
  const k = selKey(state.cls, it);
  if (state.sel.has(k)) state.sel.delete(k);
  else state.sel.set(k, { recipe: it.recipe, book: it.book, category: it.category, level: it.level, cls: state.cls });
}

function renderTray() {
  const list = $("traylist"); list.replaceChildren();
  const arr = [...state.sel.values()];
  $("traycount").textContent = arr.length;
  for (const [k, v] of state.sel) {
    const li = el("li");
    const name = el("span"); name.textContent = v.recipe;
    const meta = el("span", "tl-meta", v.level != null ? `L${v.level}` : v.cls);
    const x = el("span", "x", "✕"); x.title = "remove";
    x.onclick = (e) => { e.stopPropagation(); state.sel.delete(k); render(); };
    li.append(name, meta, x);
    list.appendChild(li);
  }
  const has = arr.length > 0;
  $("export-yaml").disabled = !has; $("copy-names").disabled = !has; $("clear-sel").disabled = !has;
}

// ---- helpers --------------------------------------------------------------
function groupBy(arr, fn) { const o = {}; for (const x of arr) { const k = fn(x); (o[k] = o[k] || []).push(x); } return o; }
function rank(cat) { return isEssential(cat) ? 0 : isAdvanced(cat) ? 1 : 2; }       // Essentials before Advanced
function cmp(key, dir) {
  return (a, b) => {
    let x = a[key], y = b[key];
    if (key === "level") { x = x ?? 1e9; y = y ?? 1e9; return (x - y) * dir; }
    return String(x ?? "").localeCompare(String(y ?? "")) * dir;
  };
}
const ROMAN = { I: 1, II: 2, III: 3, IV: 4, V: 5, VI: 6, VII: 7, VIII: 8, IX: 9, X: 10, XI: 11, XII: 12 };
function volSort(a, b) {                  // order "… Volume III" numerically, else alpha
  const va = (a.match(/Volume\s+([IVX]+|\d+)/i) || [])[1], vb = (b.match(/Volume\s+([IVX]+|\d+)/i) || [])[1];
  const na = va ? (ROMAN[va] ?? +va) : null, nb = vb ? (ROMAN[vb] ?? +vb) : null;
  const base = a.replace(/Volume.*/i, ""), baseb = b.replace(/Volume.*/i, "");
  if (base !== baseb) return base.localeCompare(baseb);
  return (na ?? 0) - (nb ?? 0);
}
function emptyMsg() { const d = el("div", "placeholder"); d.innerHTML = "<div>No recipes match these filters.</div>"; return d; }
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 1600); }

// ---- export ---------------------------------------------------------------
$("export-yaml").onclick = () => {
  const arr = [...state.sel.values()];
  const stamp = state.cls.toLowerCase().replace(/\s+/g, "-");
  let y = `# Generated by the recipe browser — paste into config/forge/lists.yaml under "lists:".\n`;
  y += `# name = full recipe (OCR row-match). search = what's typed (blank => full name typed).\n`;
  y += `browser-${stamp}:\n`;
  for (const v of arr) {
    const name = v.recipe.replace(/"/g, '\\"');
    y += `  - {name: "${name}", count: 1, search: ""}\n`;
  }
  const blob = new Blob([y], { type: "text/yaml" });
  const a = el("a"); a.href = URL.createObjectURL(blob); a.download = `craftlist-${stamp}.yaml`; a.click();
  URL.revokeObjectURL(a.href);
  toast(`Exported ${arr.length} recipes → craftlist-${stamp}.yaml`);
};
$("copy-names").onclick = async () => {
  const names = [...state.sel.values()].map(v => v.recipe).join("\n");
  try { await navigator.clipboard.writeText(names); toast("Recipe names copied"); }
  catch { toast("Clipboard blocked — select+copy the export instead"); }
};
$("clear-sel").onclick = () => { state.sel.clear(); render(); };

// ---- wiring ---------------------------------------------------------------
$("search").oninput = (e) => { state.search = e.target.value.trim(); render(); };
$("lmin").oninput = (e) => { state.lmin = e.target.value ? +e.target.value : null; render(); };
$("lmax").oninput = (e) => { state.lmax = e.target.value ? +e.target.value : null; render(); };
document.querySelectorAll(".seg-btn").forEach(b => b.onclick = () => {
  document.querySelectorAll(".seg-btn").forEach(x => x.classList.toggle("active", x === b));
  state.view = b.dataset.view; render();
});

boot();
