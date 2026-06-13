// ib · forge — keymap editor: camp command + counter#×mode art keys.
"use strict";
const $ = (id) => document.getElementById(id);

const ts = $("theme");
const saved = localStorage.getItem("ibf-theme");
if (saved) { document.documentElement.dataset.theme = saved; ts.value = saved; }
ts.onchange = () => { document.documentElement.dataset.theme = ts.value; localStorage.setItem("ibf-theme", ts.value); };

function renderGrid(arts) {
  const dur = arts.durability || [];
  const prog = arts.progress || [];
  let html = `<span class="h"></span><span class="h">durability</span><span class="h">progress</span>`;
  for (let i = 0; i < 3; i++) {
    html += `<span class="rl">Counter #${i + 1}</span>` +
      `<input class="km-in km-key dur" value="${(dur[i] || "").replace(/"/g, "&quot;")}" />` +
      `<input class="km-in km-key prog" value="${(prog[i] || "").replace(/"/g, "&quot;")}" />`;
  }
  $("grid").innerHTML = html;
}

fetch("/api/forgekeymap").then((r) => r.json()).then((d) => {
  $("camp").value = d.camp || "/camp";
  $("mana").value = d.mana_recover || "";
  renderGrid(d.arts || {});
}).catch(() => renderGrid({}));

$("save").onclick = () => {
  const dur = [...document.querySelectorAll(".dur")].map((i) => i.value.trim());
  const prog = [...document.querySelectorAll(".prog")].map((i) => i.value.trim());
  fetch("/api/forgekeymap", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camp: $("camp").value.trim() || "/camp",
                           mana_recover: $("mana").value.trim(),
                           arts: { durability: dur, progress: prog } }),
  }).then((r) => r.json()).then((d) => {
    const s = $("status");
    if (d.ok) { s.textContent = "saved ✓"; s.className = "km-status good"; }
    else { s.textContent = d.error || "error"; s.className = "km-status bad"; }
    setTimeout(() => { s.textContent = ""; }, 2500);
  }).catch(() => {});
};
