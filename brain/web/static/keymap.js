// ib keymap editor — view + edit the ability->key map, save back to YAML.
"use strict";
const $ = (id) => document.getElementById(id);

// theme (shared with the dashboard)
const themeSel = $("theme");
const saved = localStorage.getItem("ib-theme");
if (saved) { document.documentElement.dataset.theme = saved; themeSel.value = saved; }
themeSel.onchange = () => {
  document.documentElement.dataset.theme = themeSel.value;
  localStorage.setItem("ib-theme", themeSel.value);
};

let km = null;   // the loaded keymap object (mutated in place on save)

function row(role, entry, container, isMacro) {
  const div = document.createElement("div");
  div.className = "km-row" + (entry.key ? "" : " unmapped");
  const desc = entry.desc || "";
  const mode = entry.mode || (isMacro ? "macro" : "");
  div.innerHTML =
    `<div class="km-role">${role}${isMacro ? '<span class="km-tag">macro</span>' : ""}</div>` +
    `<div class="km-desc">${desc}</div>` +
    `<input class="km-key" data-role="${role}" data-macro="${isMacro ? 1 : 0}" value="${entry.key || ""}" placeholder="unmapped" spellcheck="false" />` +
    (isMacro ? `<div class="km-mode-cell">—</div>`
             : `<select class="km-mode" data-role="${role}">
                  <option value="auto"${mode === "auto" ? " selected" : ""}>auto</option>
                  <option value="manual"${mode === "manual" ? " selected" : ""}>manual</option>
                </select>`);
  container.appendChild(div);
}

function render() {
  const ab = $("abilities");
  ab.innerHTML =
    `<div class="km-row km-head"><div>role</div><div>description</div><div>key</div><div>mode</div></div>`;
  for (const [role, entry] of Object.entries(km.abilities || {})) row(role, entry, ab, false);
  for (const [role, entry] of Object.entries(km.macros || {})) row("macro:" + role, entry, ab, true);

  const tg = $("targets");
  tg.innerHTML = `<div class="km-target km-thead"><span>slot</span><span>character name</span><span>target key</span></div>`;
  const roles = km.slot_roles || [];
  const names = km.names || {};
  for (let i = 0; i < 6; i++) {
    const k = (km.group_target_keys || [])[i] || "";
    const role = roles[i] ? ` (${roles[i]})` : (i === 0 ? " (self)" : "");
    const nm = (names[i] ?? names[String(i)] ?? "");
    const d = document.createElement("div");
    d.className = "km-target";
    d.innerHTML = `<span>slot ${i}${role}</span>` +
      `<input class="km-name" data-slot="${i}" placeholder="character name" value="${nm.replace(/"/g, "&quot;")}" spellcheck="false" />` +
      `<input class="km-tk" data-slot="${i}" value="${k.replace(/"/g, "&quot;")}" spellcheck="false" />`;
    tg.appendChild(d);
  }
  $("tankSlot").value = km.tank_slot ?? 0;
  // live unmapped-highlight as you type
  document.querySelectorAll(".km-key").forEach((inp) => {
    inp.oninput = () => inp.closest(".km-row").classList.toggle("unmapped", !inp.value.trim());
  });
}

async function load() {
  const r = await fetch("/api/keymap");
  km = await r.json();
  render();
  setStatus("loaded", false);
}

function collect() {
  document.querySelectorAll(".km-key").forEach((inp) => {
    const role = inp.dataset.role, isMacro = inp.dataset.macro === "1";
    const bag = isMacro ? km.macros[role.replace(/^macro:/, "")] : km.abilities[role];
    if (bag) bag.key = inp.value.trim();
  });
  document.querySelectorAll(".km-mode").forEach((sel) => {
    if (km.abilities[sel.dataset.role]) km.abilities[sel.dataset.role].mode = sel.value;
  });
  km.group_target_keys = Array.from(document.querySelectorAll(".km-tk"))
    .sort((a, b) => a.dataset.slot - b.dataset.slot).map((i) => i.value.trim());
  km.names = {};                       // slot -> character name (combat detection + display)
  document.querySelectorAll(".km-name").forEach((i) => { km.names[i.dataset.slot] = i.value.trim(); });
  km.tank_slot = parseInt($("tankSlot").value, 10) || 0;
}

function setStatus(msg, bad) {
  [$("status"), $("status2")].forEach((el) => {
    el.textContent = msg;
    el.className = "km-status" + (bad ? " bad" : msg ? " good" : "");
  });
}

$("save").onclick = async () => {
  collect();
  setStatus("saving…", false);
  try {
    const r = await fetch("/api/keymap", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(km),
    });
    const j = await r.json();
    if (j.ok) { setStatus("saved ✓", false); render(); }
    else setStatus("error: " + (j.error || r.status), true);
  } catch (e) { setStatus("error: " + e, true); }
};
$("reload").onclick = load;

load();
