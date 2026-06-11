// ib dashboard — live websocket telemetry + manual controls.
const $ = (id) => document.getElementById(id);
const fmt = (v, s = "") => (v === null || v === undefined ? "—" : v + s);

// theme persistence
const themeSel = $("theme");
const saved = localStorage.getItem("ib-theme");
if (saved) { document.documentElement.dataset.theme = saved; themeSel.value = saved; }
themeSel.onchange = () => {
  document.documentElement.dataset.theme = themeSel.value;
  localStorage.setItem("ib-theme", themeSel.value);
};

const SLOT_NAMES = ["self", "slot1", "slot2", "slot3", "slot4", "slot5"];

function render(s) {
  $("state").textContent = s.state || "—";
  $("override").textContent = s.override || "none";

  const cf = s.chat_focus || {};
  const unsafe = cf.safe === false;
  $("chatAlarm").classList.toggle("hidden", !unsafe);
  $("aborted").textContent = cf.aborted_injections ?? 0;
  $("aborted2").textContent = cf.aborted_injections ?? 0;

  const a = s.agent || {};
  $("conn").classList.toggle("on", !!a.connected);
  $("latency").textContent = fmt(a.latency_ms, " ms");
  $("hz").textContent = fmt(a.capture_hz, " Hz");
  $("ocr").textContent = a.ocr_conf == null ? "—" : Math.round(a.ocr_conf * 100) + "%";
  $("logfresh").textContent = fmt(a.log_fresh_s, " s");

  const own = s.own || {};
  $("power").style.width = ((own.power ?? 0) * 100) + "%";
  $("power").classList.add("power");
  $("casting").textContent = own.casting == null ? "—" : (own.casting ? "casting" : "idle");

  const box = $("members");
  box.innerHTML = "";
  (s.members || []).forEach((m) => {
    const row = document.createElement("div");
    row.className = "member" + (m.dead ? " dead" : "");
    const hpPct = Math.round((m.hp ?? 0) * 100);
    row.innerHTML = `
      <div class="name">${SLOT_NAMES[m.slot] ?? ("slot" + m.slot)}</div>
      <div class="bars">
        <div class="track"><div class="fill" style="width:${hpPct}%"></div></div>
        <div class="track"><div class="fill ward" style="width:${m.ward ? 100 : 0}%"></div></div>
      </div>
      <div class="pct">${hpPct}%</div>`;
    box.appendChild(row);
  });

  const ev = $("events");
  ev.innerHTML = "";
  (s.events || []).slice(-40).reverse().forEach((e) => {
    const li = document.createElement("li");
    const t = new Date(e.ts * 1000).toLocaleTimeString();
    li.innerHTML = `<span class="t">${t}</span><span class="k">${e.kind}</span><span>${e.detail}</span>`;
    ev.appendChild(li);
  });
}

// controls
document.querySelectorAll("[data-ov]").forEach((b) =>
  (b.onclick = () => fetch(`/api/override/${b.dataset.ov}`, { method: "POST" })));
document.querySelectorAll("[data-ctl]").forEach((b) =>
  (b.onclick = () => fetch(`/api/control/${b.dataset.ctl}`, { method: "POST" })));

// websocket with auto-reconnect
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => render(JSON.parse(e.data));
  ws.onclose = () => { $("conn").classList.remove("on"); setTimeout(connect, 1500); };
}
connect();
