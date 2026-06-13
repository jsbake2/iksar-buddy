# FORGE.md — ib Crafting Automation

Technical spec + roadmap for the **crafting** tool. Sibling to `PROJECT.md` (the
healer). Same operating rules apply (`CLAUDE.md`): opsec naming, chat-safety
invariant, server-stack-intact, terse comms, owner is in-game SME / Claude is code SME.

> **Codename: Forge.** Package `forge/`, process title `ibf`, dashboard on **:18081**.
> No runtime-visible artifact contains `eq2`/`bot` (PROJECT.md §7.5).

---

## 0. Lineage — what we keep, what we burn

The old code under `~/from-windows/.../craft_bot/` is the **dinosaur**: we study its
*crafting logic* (the working game-mechanic heuristics), then throw the skeleton away.

**Keep (logic, ported):**
- The craft cycle: detect Begin/Retry → run a craft → detect complete → loop N times.
- **Reaction-event detection by template match** on a small screen region → press the
  matching counter art. (`CounterWatcher` in the old `craft.py`.)
- **Durability/progress mode** switch by a pixel color → spam art-set A vs art-set B.
- **Power gate**: pixel check; pause + use a power ability when low.
- **Writ flow**: OCR the quest journal → `{recipe: count}` → select each recipe via the
  search box (type name, handle parens, click first result) → craft it `count` times.
- The OCR preprocessing + count-regex parsing (`(N/M)` anchor, prefix stripping).

**Burn (everything about *how* it touched the machine):**
- PySide6 GUI, `pyautogui`, `pygetwindow`, `mss`, in-VM execution, absolute desktop
  coords from a 2560×1440 screen, per-script `config.json`, subprocess-per-item.

**Replace with the healer's plumbing** (the sleek bot — this is the real instruction
from the owner): everything is **host-side**, driving the guest over libvirt.

| Need | Old (dino) | New (from healer) |
|---|---|---|
| See the screen | `mss` in the VM | `virsh screenshot` → PPM, crop with `magick` (host-side) |
| OCR | `pytesseract` in VM | host `tesseract` (already used by healer accept-helpers) |
| Press keys | `pyautogui.press` | write `C:\ib\keys.txt` + fire `ibkey` scheduled task (AHK Event mode, `infra/vm/ahk/key_ev.ahk`) |
| Click | `pyautogui.click` | `gclick.py` (qemu `input-send-event`, abs coords) |
| Type text | `pyautogui.write` | `gtype.py` (`virsh send-key`, already handles `()` via shift+9/0) |
| Read game log | n/a | `gexec.py` PowerShell tail (like the healer combat-log loop) |
| Config | per-script JSON | YAML in `config/forge/`, hot-reload (`brain/config.py` pattern) |
| UI / telemetry | PySide6 window | FastAPI + websocket dashboard, themeable (healer `brain/web/`) |
| Transport | n/a | **none** — see §2 (collapse the healer's vestigial socket) |

---

## 1. Summary

Automate EQ2 **tradeskill crafting** across **up to two guests at once**. The tool
runs entirely on a host, senses each guest's craft window by screenshot, presses the
reaction arts, and loops crafts. It grows in three stages:

1. **Single crafts** — pick a recipe (or assume one's loaded), craft it N times.
2. **Writs / batches** — OCR a writ's required items, craft the whole list.
3. **Movement + writs** — walk between the crafting station and the writ-giver,
   accept/turn-in, repeat. (Stretch; §11 Phase 5.)

Two **bot slots**, each bound to a guest VM, each independently enable/start/stoppable,
each with its own console + status in the dashboard. Both may run together or solo.

## 2. Topology — one host process, N guests (no socket)

The healer keeps a brain↔agent TCP socket only because its agent *used* to live in the
VM; today both halves run on the host over localhost — it's vestigial. **Forge drops
it.** One asyncio process on the host:

```
HOST (CachyOS)                         GUEST A (libvirt dom "iksar_buddy")
┌───────────────────────────────┐     ┌──────────────────────────────┐
│ ibf (single process)          │     │  EQ2 client, craft window      │
│  ├ FastAPI dashboard :18081   │ ──► │  (sensed by screenshot only,   │
│  ├ Telemetry / websocket      │     │   driven by keys.txt+AHK,      │
│  ├ CraftWorker[A] ──────────► │ ──► │   gclick, gtype)               │
│  └ CraftWorker[B] ──────────► │     └──────────────────────────────┘
└───────────────────────────────┘     GUEST B (clone, dom "iksar_buddy2")
        each Worker owns one Guest(dom)  └─ (identical setup)
```

- `Guest(dom, width, height)` — the **reusable core**: wraps screenshot→crop,
  `gclick` (resolution-correct), `gtype`, key-inject (per-dom `keys.txt`+`ibkey`),
  `gexec` PowerShell. This is the host-helpers generalized to take a domain instead of
  hardcoding `DOM="iksar_buddy"`. **The healer can later adopt the same abstraction.**
- `CraftWorker` — one per enabled guest: the sense→decide→press craft loop + writ
  driver, as an asyncio task. Reports state to Telemetry.
- We keep `shared/protocol.py`-style discipline only where it earns it; no wire frames
  needed in-process.

We match the healer's **patterns** (config, telemetry, dashboard, host-side primitives,
opsec, chat-safety) and deliberately **simplify the transport** (Claude's call as code
SME; stated per CLAUDE.md "inform, don't ask").

## 3. The two-VM problem (the real infra work)

This is the part that needs owner input — flagged in §12.

- **Clone source:** `iksar_buddy` (the lean healer image) per the owner. A clone gives
  an identical Windows + EQ2 install → **one calibration profile serves both guests**
  (the whole point of cloning vs a fresh build).
- **No GPU for crafting.** Only one 4070 exists and it's passed to `iksar_buddy`; you
  can't pass it to two guests. **Crafting is a static 2D UI — it needs no GPU.** The
  clone runs pure software/virtio-gpu. Recommended: a **GPU-less profile for both
  craft guests** during craft sessions, so the 4070 stays free for the server's AI
  work (directly honors the CLAUDE.md "keep the host's stack intact" rule).
- **Where do they run?** Genuine open decision (§12):
  - **(A) On the live server (10.0.0.16):** clone `iksar_buddy` in place. Con: shares
    the box with the 4070 AI work + healer; two more 8-vCPU guests is real contention.
  - **(B) On this workstation (`JASON-MAIN-CACHY`):** 31G RAM (17G free), 298G disk,
    already has libvirt + a legacy `eq2-gm-vm`. Copy the lean `iksar_buddy.qcow2` over
    (or trim `eq2-gm-vm`) and run both craft guests here. **Recommended** — keeps the
    server untouched; crafting is light and latency-tolerant.
- **Resources per craft guest (proposed, GPU-less):** 4–6 vCPU, 6 GB RAM, virtio-gpu
  at a **fixed 1920×1080**, never-sleep, locked UI scale. Two of these fit comfortably
  on the workstation. Pin to distinct core sets so they don't fight (e.g. guest A
  cpu0–5, guest B cpu6–11 on the 13700K P-cores; leave E-cores for the host).
- **Naming (opsec):** `iksar_buddy` (existing) + `iksar_buddy2`. Keys/scheduled-task
  names inside each guest stay `ib` / `ibkey` (separate Windows installs → no clash).
- **Credentials:** owner sets up the **second EQ2 login** in the clone (he volunteered).
  Two crafting accounts/toons running in parallel.

## 4. Sensor model (host-side, per guest)

All detection is `virsh screenshot <dom>` → crop with `magick` → analyze. Mirrors
`agent/host_sensor.py`. Every coord/region/template below is **per a fixed 1920×1080
guest** and is **calibrated fresh** — the old dino coords were 2560×1440 and do not
transfer; only the *logic* transfers.

1. **Reaction-event region (PRIMARY, fast loop).** A small region where the craft
   "event" icons appear. Template-match (opencv `matchTemplate`, host-side) against
   per-class reaction templates → press the matching counter art's key. Confidence
   gate (~0.8). This is the latency-sensitive path; poll as fast as one screenshot
   allows (target 5–10 Hz; EQ2 event windows are a few seconds). *Upgrade path if a
   full screenshot/poll is too slow: tap the SPICE stream for a region-only fast
   capture instead of full-frame `virsh screenshot`.*
2. **Durability/progress mode pixel.** One pixel that reads "high/progress" vs
   "low/durability" → choose which art-set to run between events.
3. **Begin / Retry button.** Pixel fingerprint (+ template fallback) → the craft-loop
   heartbeat: Begin starts the next item, Retry repeats the current recipe.
4. **Power (mana) gate pixel.** Below color → pause, fire the power ability, wait,
   refocus the craft window.
5. **Quest-journal OCR (writs).** `magick` preprocess + host `tesseract` → parse
   required `{recipe: count}` (ported regex). Owner confirms the parsed list before run.
6. **EQ2 craft log (confirmation, DEFERRED-ish).** `gexec` PowerShell tail for
   "you created…" / "recipe scribed" lines → authoritative completion + dedup (the old
   `to_add.txt` wanted this; better than pixel-only completion). Add once the pixel
   loop works.

## 5. Crafting decision loop (per worker)

Ported from the dino, owner refines the art→key map (he's the SME on the kit):

```
ensure craft window focused (click craft_reaction_focus point)
loop until crafts_done == target or stopped:
  if Begin/Retry visible:        click it, ENTER/confirm, focus reaction button
  while crafting (not complete):
     if power low:               fire power ability, wait, refocus
     if reaction event detected: press its counter-art key (fast region poll)
     else (gap):                 press progress arts (mode high) or durability arts (mode low)
     if complete (Retry/Begin):  crafts_done++; break
```

- **Chat-safety still gates every keypress** (PROJECT.md §6.2). Crafting is solo at a
  station so the risk is lower than mid-group, but a stray `1`–`6` typed into chat is
  still an opsec leak. Reuse the healer's fail-closed guard (`game_present` AND chat
  input not active) and the AHK modifier-clear hardening. *Plus* the focus discipline:
  click a reaction button so arts land in the craft window, not the hotbar/chat.
- **Reaction keys vs hotbar collision** is the known healer footgun (Ctrl+# paged the
  hotbar). Craft arts are bare `1`–`6` in the craft window — verify they don't collide
  with the owner's hotbar paging; owner maps them in config.

## 6. Writ / batch driver

`{recipe: count}` from OCR (or a pasted/loaded list) → for each recipe: clear search,
`gtype` the name (parens handled), ENTER, `gclick` the first result, refocus, then run
the §5 loop `count` times. Progress (item i/N, craft j/count) streams to the dashboard.
Mark items done as their log-confirmation lands to prevent re-crafting on a restart.

## 7. Configuration (`config/forge/`, YAML, hot-reload)

- `stations.yaml` — the two bot slots: `{ slot, dom, width, height, enabled,
  power_key, account_label }`. This is what makes it multi-guest.
- `craft.yaml` — the **calibration + kit profile**, shared by both guests (identical
  clones): art→key map (progress arts, durability arts, reaction-event→key), mode
  pixel, power-gate pixel, begin/retry fingerprints, reaction region + template dir,
  search/result/clear/focus click points, journal OCR region.
- Reaction templates: `config/forge/templates/<class>/{1,2,3}.png` — recaptured at
  1920×1080 via a calibration helper (below).
- Reuse `brain/config.py`'s hot-reload `Config` (point `IB_CONFIG_DIR` or add a forge
  loader) so the owner edits YAML and ibf picks it up live.

## 8. Dashboard (:18081, themeable)

Healer dashboard tech (FastAPI + websocket + the existing `themes.css`). Two
side-by-side **bot panels**, each:
- enable toggle, trade-class + recipe/writ selector, target count, **Start / Stop**.
- live state (idle / selecting / crafting / waiting-power / done), recipe, crafts
  done/target, reaction-hits counter, last-event, power-gate indicator, chat-safe chip.
- a **console log** (the "console window" the owner asked for — one per bot).
- **⧉ console** button → native SPICE viewer for that guest (reuse `ib-console`,
  parameterized per dom — it currently hardcodes the healer tunnel).
- Graphs where they earn it: crafts/hour, reaction hit-rate, durability-mode time-share.

## 9. Calibration tooling

Because clones are identical, calibrate **once**:
- A `forge/calibrate.py` helper: `virsh screenshot` the guest, let the owner mark the
  reaction region / mode pixel / begin-retry / power pixel / search+result click points
  on a still, and **capture reaction templates** (crop the event icons). Writes
  `craft.yaml` + `templates/`. Reuse `burst_capture.sh` for grabbing the short-lived
  event icons.
- **Fix `gclick` resolution scaling first:** `host-helpers/gclick.py` hardcodes
  `W,H=1024,768`; clicks on a 1920×1080 guest land wrong. The new `Guest` click path
  must divide by the *true* guest resolution. (Crafting is click-heavy, unlike the
  healer which clicks only via OCR helpers — so this matters now.)

## 10. Repo layout (proposed)

```
forge/
├── __main__.py        # ibf entrypoint: title 'ibf', start workers + dashboard
├── guest.py           # Guest(dom,w,h): screenshot/crop, gclick, gtype, key-inject, gexec
├── sensors.py         # reaction match, mode/power/begin-retry pixels, journal OCR
├── worker.py          # CraftWorker: the §5 craft loop + §6 writ driver, per guest
├── recipes.py         # OCR parse + recipe-list file parse (ported regex)
├── calibrate.py       # §9 calibration helper
├── telemetry.py       # per-worker status feed (mirror brain/telemetry.py)
└── web/               # FastAPI app + static (fork healer's, two-panel layout)
config/forge/
├── stations.yaml
├── craft.yaml
└── templates/<class>/{1,2,3}.png
infra/host/ib-forge.service          # systemd unit (workstation or server)
infra/vm/iksar_buddy2.xml            # clone domain def (GPU-less)
```

## 11. Phased roadmap (fail fast — thin vertical slice first)

0. **Scaffold + Guest core.** `forge/` package, `Guest(dom)` generalizing the
   host-helpers (parameterized domain + correct click scaling), config loader,
   dashboard shell with two (mostly empty) bot panels. Prove screenshot+crop+click+type
   round-trips to one guest.
1. **Single craft, one guest (THE SLICE).** Calibrate begin/retry, mode pixel, power
   pixel, reaction region+templates. Run one recipe N times end-to-end on the dashboard.
   This is the breathing spine — don't widen before it works.
2. **Reaction loop hardening.** Tune poll rate, confidence, focus discipline, power
   gate, chat-safety gate. Measure reaction hit-rate.
3. **Two guests.** Bring up `iksar_buddy2` (clone), second bot slot, run both at once.
   Two consoles, two command sets — the owner's core ask.
4. **Writs.** Journal OCR → confirm → batch craft a full writ. Log-confirmed completion + dedup.
5. **Movement + writs (stretch).** WASD nudges (`hold_w_0.3` AHK form already exists),
   `/waypoint`-style navigation between station and writ-giver, accept/turn-in via the
   healer's OCR-and-click accept pattern. Loop writs hands-off.
6. **Polish.** Themes, graphs, config UI, restart-safe progress, opsec/title audit.

## 12. Open items (owner input)

- **Where the craft guests run:** workstation (recommended) vs live server. → §3.
- **GPU-less profile OK for crafting?** (recommended — frees the 4070). → §3.
- **Clone go-ahead + second EQ2 login creds** in `iksar_buddy2` (owner volunteered).
- **Resource budget** per craft guest (proposed 4–6 vCPU / 6 GB / 1920×1080).
- **Craft art keybinds** (owner SME): which keys are progress arts, durability arts,
  and which counter art answers each reaction event. Fills `craft.yaml`.
- **Trade classes in scope** first (the dino supported tailor/armorer/weapon/scribe/
  woodworker/carpenter/provisioner/jeweler/sage/alchemist).

## 13. First actions next session

1. Decide §12 location + resource profile (one short exchange).
2. Scaffold `forge/` + `Guest` core; fix `gclick` scaling; round-trip test to
   `iksar_buddy`.
3. Stand up the clone `iksar_buddy2` (GPU-less) once owner okays + adds creds.
4. Calibrate one guest; land the Phase-1 single-craft slice live.
