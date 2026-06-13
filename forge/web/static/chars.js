// ib · forge — crafter roster editor: character + tradeskill class + VM.
"use strict";
const $ = (id) => document.getElementById(id);

// theme (shared with the dashboard)
const ts = $("theme");
const saved = localStorage.getItem("ibf-theme");
if (saved) { document.documentElement.dataset.theme = saved; ts.value = saved; }
ts.onchange = () => { document.documentElement.dataset.theme = ts.value; localStorage.setItem("ibf-theme", ts.value); };

let trades = [];
let vms = [];          // [{vm, label, dom}]
let rows = [];         // [{character, class, vm}]

function vmOptions(sel) {
  // fall back to vm1/vm2 if the dashboard hasn't reported bot VMs
  const opts = vms.length ? vms.map((v) => v.vm) : ["vm1", "vm2"];
  return opts.map((v) => `<option value="${v}"${v === sel ? " selected" : ""}>${v}</option>`).join("");
}
function classOptions(sel) {
  return `<option value=""${sel ? "" : " selected"}>—</option>` +
    trades.map((t) => `<option value="${t}"${t === sel ? " selected" : ""}>${t}</option>`).join("");
}

function render() {
  const tbl = $("tbl");
  tbl.innerHTML = "";
  const head = document.createElement("div");
  head.className = "cc-row head";
  head.innerHTML = `<span>character</span><span>tradeskill class</span><span>VM</span><span></span>`;
  tbl.appendChild(head);
  rows.forEach((r, i) => {
    const row = document.createElement("div");
    row.className = "cc-row";
    row.innerHTML =
      `<input class="cc-in cc-char" value="${(r.character || '').replace(/"/g, '&quot;')}" placeholder="character name" />` +
      `<select class="cc-in cc-class">${classOptions(r.class)}</select>` +
      `<select class="cc-in cc-vm">${vmOptions(r.vm)}</select>` +
      `<button class="cc-del" title="remove">×</button>`;
    row.querySelector(".cc-char").onchange = (e) => { rows[i].character = e.target.value.trim(); };
    row.querySelector(".cc-class").onchange = (e) => { rows[i].class = e.target.value; };
    row.querySelector(".cc-vm").onchange = (e) => { rows[i].vm = e.target.value; };
    row.querySelector(".cc-del").onclick = () => { rows.splice(i, 1); render(); };
    tbl.appendChild(row);
  });
}

fetch("/api/crafters").then((r) => r.json()).then((d) => {
  trades = d.trade_classes || [];
  vms = d.vms || [];
  rows = (d.crafters || []).map((c) => ({ character: c.character || "", class: c.class || "", vm: c.vm || "" }));
  render();
}).catch(() => {});

$("add").onclick = () => { rows.push({ character: "", class: "", vm: (vms[0] || {}).vm || "vm1" }); render(); };

$("save").onclick = () => {
  // pull live values straight from the inputs (in case onchange didn't fire)
  const tblRows = [...document.querySelectorAll(".cc-row")].slice(1);
  const out = tblRows.map((tr) => ({
    character: tr.querySelector(".cc-char").value.trim(),
    class: tr.querySelector(".cc-class").value,
    vm: tr.querySelector(".cc-vm").value,
  })).filter((r) => r.character);
  fetch("/api/crafters", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ crafters: out }),
  }).then((r) => r.json()).then((d) => {
    const s = $("status");
    if (d.ok) { s.textContent = "saved ✓"; s.className = "cc-status good"; rows = d.crafters; render(); }
    else { s.textContent = d.error || "error"; s.className = "cc-status bad"; }
    setTimeout(() => { s.textContent = ""; }, 2500);
  }).catch(() => {});
};
