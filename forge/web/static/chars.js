// ib · forge — class→character config editor (like the healer keymap page).
"use strict";
const $ = (id) => document.getElementById(id);

// theme (shared with the dashboard)
const ts = $("theme");
const saved = localStorage.getItem("ibf-theme");
if (saved) { document.documentElement.dataset.theme = saved; ts.value = saved; }
ts.onchange = () => { document.documentElement.dataset.theme = ts.value; localStorage.setItem("ibf-theme", ts.value); };

let trades = [];
let map = {};

function render() {
  const tbl = $("tbl");
  tbl.innerHTML = "";
  const head = document.createElement("div");
  head.className = "cc-row head";
  head.innerHTML = `<span>tradeskill class</span><span>character</span>`;
  tbl.appendChild(head);
  trades.forEach((t) => {
    const row = document.createElement("div");
    row.className = "cc-row";
    const v = (map[t] || "").replace(/"/g, "&quot;");
    row.innerHTML = `<span class="cc-class">${t}</span>` +
      `<input class="cc-in" data-class="${t}" value="${v}" placeholder="character name (e.g. Foxyman)" />`;
    tbl.appendChild(row);
  });
}

fetch("/api/classchars").then((r) => r.json()).then((d) => {
  trades = d.trade_classes || [];
  map = d.class_chars || {};
  render();
}).catch(() => {});

$("save").onclick = () => {
  const m = {};
  document.querySelectorAll("[data-class]").forEach((i) => {
    const v = i.value.trim();
    if (v) m[i.dataset.class] = v;
  });
  fetch("/api/classchars", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ class_chars: m }),
  }).then((r) => r.json()).then((d) => {
    const s = $("status");
    if (d.ok) { s.textContent = "saved ✓"; s.className = "cc-status good"; map = d.class_chars; }
    else { s.textContent = d.error || "error"; s.className = "cc-status bad"; }
    setTimeout(() => { s.textContent = ""; }, 2500);
  }).catch(() => {});
};
