// ib group — pop-out per-member control: manually top off / pre-ward / cure / rez
// any group member during a long fight, without waiting for the auto loop.
"use strict";
const $ = (id) => document.getElementById(id);
const pct = (v) => Math.round((v ?? 0) * 100);
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const FALLBACK_NAMES = ["self", "slot1", "slot2", "slot3", "slot4", "slot5"];
let maintRole = "ward";   // 'ward' (Defiler) | 'hot' (Fury) — from the active profile
let kind = "healer";      // 'healer' (heal/ward/cure) | 'dirge' (per-member buffs)

// Per-member buttons. action -> POST /api/act/<action>/<slot> (manual; the agent
// targets that member's F-key then casts). Cure is generic.
const HEALER_BTNS = [
  { act: "heal", label: "Heal", cls: "b-heal" },
  { act: "ward", label: "Ward", cls: "b-ward" },
  { act: "cure", label: "Cure", cls: "b-cure" },
  { act: "follow", label: "Follow", cls: "b-follow" },
  { act: "rez", label: "Rez", cls: "b-rez" },
];
// Dirge: the SAME buffs as the main-page matrix — every temp + individual buff, one
// per-member cast button (labelled by the owner's keymap name) + follow. Derived live
// from the profile's actions so it always matches the main page.
function dirgeButtonsFrom(profile) {
  const a = (profile || {}).actions || {};
  const btns = [...(a.temp || []), ...(a.individual || [])].map((b) => ({
    act: b.role, label: b.name || b.label || b.role, cls: "b-buff" + (b.key ? "" : " unset"),
  }));
  btns.push({ act: "follow", label: "Follow", cls: "b-follow" });
  return btns;
}
let BTNS = HEALER_BTNS;
let buffSig = "";         // (kind + button set) signature — rebuild cards when it changes

// theme (shared with dashboard/focus)
const savedTheme = localStorage.getItem("ib-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("theme").value = savedTheme || "midnight";
$("theme").onchange = () => {
  document.documentElement.dataset.theme = $("theme").value;
  localStorage.setItem("ib-theme", $("theme").value);
};

let running = false;
function post(url, btn) {
  fetch(url, { method: "POST" }).catch(() => {});
  if (btn) {
    btn.classList.add("fired");
    setTimeout(() => btn.classList.remove("fired"), 300);
  }
}

const els = {};   // slot -> card element (built once so taps feel instant)
function cardFor(slot) {
  const el = document.createElement("div");
  el.className = "gmember";
  el.innerHTML =
    `<div class="gm-top"><span class="gm-name"></span><span class="gm-role"></span>` +
    `<span class="gm-hp"></span></div>` +
    `<div class="gm-bar"><i></i></div>` +
    `<div class="gm-btns">` +
    BTNS.map((b) => {
      const act = b.act === "ward" ? maintRole : b.act;
      const label = b.act === "ward" ? cap(maintRole) : b.label;
      return `<button class="gm-act ${b.cls}" data-act="${act}" data-slot="${slot}">${label}</button>`;
    }).join("") +
    `</div>`;
  el.querySelectorAll("[data-act]").forEach((b) => {
    b.onclick = () => post(`/api/act/${b.dataset.act}/${slot}`, b);
  });
  return el;
}

function render(s) {
  running = !!s.running;
  // Only adjust the layout when the frame actually carries a profile — some WS frames
  // through Cloudflare arrive without it, and must not flip us back to the healer set.
  const p = s.profile;
  if (p) {
    const k = p.maint_role === "none" ? "dirge" : "healer";
    const btns = k === "dirge" ? dirgeButtonsFrom(p) : HEALER_BTNS;
    const sig = k + ":" + btns.map((b) => `${b.act}/${b.label}/${b.cls}`).join(",");
    if (sig !== buffSig) {          // kind or buff-set (names/keys) changed -> rebuild cards
      buffSig = sig; kind = k; BTNS = btns;
      Object.keys(els).forEach((slot) => { els[slot].remove(); delete els[slot]; });
    }
    // ward->hot 1:1 for a Fury profile (relabel existing cards + use it for new ones)
    const mr = p.maint_role || "ward";
    if (mr !== maintRole) {
      maintRole = mr;
      document.querySelectorAll(".gm-act.b-ward").forEach((b) => {
        b.dataset.act = mr; b.textContent = cap(mr);
      });
    }
  }
  const cf = s.chat_focus || {};
  const arm = $("fArm");
  arm.classList.toggle("ok", running);
  arm.textContent = running ? "armed" : "off";
  const ch = $("fChat");
  ch.classList.toggle("ok", cf.safe === true);
  ch.classList.toggle("bad", cf.safe === false);
  ch.textContent = cf.safe === false ? "chat busy" : "chat ok";

  const box = $("gmembers");
  const members = (s.members || []).filter((m) => m.present);
  if (!members.length) { box.innerHTML = `<div class="gm-empty">no group members present</div>`; return; }
  if (box.querySelector(".gm-empty")) box.innerHTML = "";

  members.forEach((m) => {
    let el = els[m.slot];
    if (!el) { el = cardFor(m.slot); els[m.slot] = el; box.appendChild(el); }
    const name = m.name || FALLBACK_NAMES[m.slot] || `slot${m.slot}`;
    const hp = pct(m.hp);
    const crit = !!m.critical, needsCure = (m.detriments || []).length > 0;
    el.className = "gmember" + (m.dead ? " dead" : "") + (crit ? " crit" : "");
    el.querySelector(".gm-name").textContent = name;
    el.querySelector(".gm-role").textContent = m.role || "";
    el.querySelector(".gm-hp").textContent = m.dead ? "DEAD" : hp + "%";
    const fill = el.querySelector(".gm-bar i");
    fill.style.width = (m.dead ? 100 : hp) + "%";
    fill.className = m.dead ? "dead" : crit ? "crit" : hp < 60 ? "low" : "";
    // highlight the action that matters: cure when afflicted, rez when dead
    // (.b-cure is healer-only — the Dirge card has no cure button)
    el.querySelector(".b-cure")?.classList.toggle("flagged", needsCure && !m.dead);
    el.querySelector(".b-rez")?.classList.toggle("flagged", m.dead);
  });
  // drop cards for members no longer present
  Object.keys(els).forEach((slot) => {
    if (!members.some((m) => String(m.slot) === slot)) { els[slot].remove(); delete els[slot]; }
  });
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onerror = () => ws.close();
}
// HTTP snapshot poll (unique path defeats the CF edge cache) — the WS doesn't reliably
// reach this popout through Cloudflare, so drive state off a cache-busted GET too.
async function poll() {
  try { render(await (await fetch("/api/live/" + Date.now())).json()); } catch (_) {}
}
connect();
poll();
setInterval(poll, 1500);
