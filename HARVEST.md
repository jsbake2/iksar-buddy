# HARVEST.md — harvesting bot (client-memory + nav)

Sister tool to the healer (brain) and crafter (forge). 100% **client-side**
([[client-side-only-rule]]): reads the EQ2 **client process memory** + the EQ2 log, acts
via keypresses. Runs on the **GPU VM `iksar_buddy`** (open-world rendering). Login:
meatwad33w → **Furyflatulence**, parked in Thundering Steppes. Dashboard on **:18082** →
**harvest.jsb-emr.us** (Access-gated, Trusted Users — DONE 2026-06-22).

Opsec: process title `ibh`, no `eq2`/`bot` in any visible name.

---

## 1. Architecture (mirrors forge)
- `harvest/__main__.py` — entrypoint, FastAPI on :18082, title `ibh`.
- `harvest/memory.py` — the **memory layer** (read player pos/heading/HP, spawn list, zone),
  signature/offset resolution + a **re-calibrate** tool (offsets drift on client updates).
- `harvest/controller.py` — orchestrates the VM (launch/login via the existing LoginDriver
  with explicit creds — multi-account), the in-guest harvest agent, route running.
- `harvest/routes.py` — per-map route storage (multiple routes per map), record/save/load.
- `harvest/web/app.py` + `static/` — the dashboard (themed like forge).
- in-guest **`harvest_agent`** (runs in the VM like `craft_reflex`): reads memory locally at
  ~10 Hz (pos/spawns), runs navigation + harvest, reports telemetry to the host. Heavy
  reads MUST be in-guest (local pymem); host-side guest-exec is too slow for the nav loop.

## 2. Memory layer — status
- **Player position = `[EverQuest2.exe + 0x1822b68]`** (3× float32 X,Y,Z). Module-static,
  STABLE, validated tracking all moves. THE anchor. (heap actor addresses relocate — unused.)
- **Heading**: from the transform matrix by the actor, or derive from position deltas while
  moving; a module-static heading is findable same as position. TODO.
- **Spawn/actor list** (harvestables, monsters, players, ? nodes): NOT yet cracked. The
  player actor is a heap object (name + transform-matrix + position) that RELOCATES; the
  inline position diverges from the module-static "camera" copy when moving, so the
  slow-scan re-find is fragile. **Finish with Cheat Engine** (freeze the value, pointer-scan
  to a static base, "find what accesses this address" → the spawn manager that walks the
  list). Once the list + a per-entry **type** field are mapped, tasks 1–5 & 10 fall out.
- **HP/mana**: exact ints in the actor struct (also the healer win, [[memory-reading-for-bots]]).
- **Re-calibrate** (client update moves offsets): `/loc` to seed current X,Y,Z, scan ONLY the
  module range (base..+SizeOfImage, ~27 MB, fast) for the contiguous triplet → new offset.
  Proven. Wrap as a dashboard button + a signature fallback.

## 3. Spawn-derived data (all gated on the spawn list)
- **Harvestables** (task 1): position, node type/tier, and a **being-worked / open** flag if
  one exists in the struct (task 4 of the dashboard). Rares-in-node = SERVER-SIDE, NOT
  knowable client-side (only on harvest, via the log).
- **Monsters** (task 3): name, level, position; **aggressive vs friendly** (task 5) — derive
  from the con/level data: EQ2 greys (non-aggro) mobs below a level delta, and some are
  flagged non-aggressive. The con color / faction / a "will aggro" computation is client-side
  (the client colors the name plate). Avoid pathing within aggro radius of any non-grey mob.
- **Players** (task 2 / proximity): PC entries → name, level, class, race, distance. Used for
  the proximity alert AND **node contention** (task 4: don't grab a node another PC is on —
  detect via either the node's being-worked flag OR a PC standing on/very near the node).
- **Zone** (task 4): zone name (UI string in memory — "The Thundering Steppes" visible), and
  **player count** = count PC-type entries in the spawn list (in-range) or a zone roster.

## 3a. Opsec — pause when players are nearby (owner rule)
Friends don't know about the bot. **If a player is "nearby," STOP** (hold position / pause
the route + harvesting) until they leave — both during dev testing AND at runtime.
- **"Nearby" = within ~30 m of Fury** (visual/render range — close enough to watch her move).
  Tunable. This is the OPSEC threshold (pause-on-presence).
- Node **contention** is a tighter ~8 m of the *node* (don't grab a node someone's working).
- Auto-detection needs the spawn list (PC entries + distance) — pending. UNTIL then, dev
  testing must **screenshot-check** for player nameplates/models near Fury and hold if any.
- Runtime: this pause is also the safe default for the in-combat / surprised-by-a-player case.

## 4. Movement (task 7) — high-level route + skeleton + smoothing
- **High-level route**: an ordered loop of waypoints (POIs). Bot follows it continuously.
- **Skeleton-out-to-node**: when a harvestable is within `detour_radius` of the path, leave
  the path, path to the node, harvest, then **rejoin at the nearest POI** (not necessarily
  the one it left) so it never backtracks awkwardly.
- **Smoothing** (owner: no jerky 90° snaps): closed-loop heading control — compute desired
  heading to the next point, **turn gradually** (rate-limited yaw via held turn key, not
  instant), and **blend** forward + strafe so corrections look like a player drifting onto
  line rather than stop-rotate-go. Add small random jitter to timing/path so it's not robotic.
  Arrival tolerance per POI so it doesn't oscillate.
- Stuck handling: if position delta ≈ 0 for `stuck_secs` while trying to move → jump +
  back up + re-orient; escalate to "return to previous POI and resume" (task 9.3).

## 5. Route tool (task 8) — record-a-route
Owner's approach (good — refined): a **"Start Recording"** button on the dashboard. While
recording, the host (or in-guest agent) samples the **module-static position every ~2–3 s**
(or on a distance threshold so dense turns get more points) → POI list. Owner walks the
route, hits **"Stop"**. We **close the loop** by connecting the last POI back to the first
(if they're near) or flag an open route. Stored per-map, multiple routes per map. The
dashboard route selector lists routes for the CURRENT zone (read from memory). Resume after
harvest = rejoin at nearest POI. Distance-based sampling beats fixed-time (denser points on
turns, sparse on straights) — best of both: sample on `max(time, distance)`.

## 6. Alerts (task 9) — mostly log-scrape + position
- **In-combat / need intervention** (9.1): EQ2 log `"... attacks YOU"` / `"YOU are hit by"`
  → alert + stop harvesting/flee logic. (Avoiding aggro up front is the primary defense.)
- **PM alert** (9.2): log `"<Name> tells you, "..."` → alert (chat monitor panel + push).
- **Stuck** (9.3): position delta ≈ 0 over time → auto-unstuck (jump/back/reorient → return
  to previous POI). Alert if it can't recover after N tries.
- **Rare found** (9.4): the rare harvest line in the log (owner: "easily recognizable") → fun
  alert + log to the harvest table flagged rare. (Confirm the exact line by harvesting.)
- **Can't-harvest** (9.5): attempted harvest but no success/"you acquire" and no known reason
  → alert with whatever the log/state shows.

## 7. ? collectible nodes (task 10)
The `?` nodes (collections) are spawns too — a distinct node type in the spawn list. Harvest
= same interact flow. RE the type id + the collectible harvest log line. Pending spawn list.

## 8. Dashboard layout (task 6) — owner's 7 points + real-time monitor
1. character name/selector (login: user/pass/char — multi-account)
2. manual unstuck control
3. zone selector / display (from memory)
4. more manual control inputs (move/turn/stop/harvest/camp)
5. session harvest table (log scrape) — in-session + all-time
6. chat monitor (log scrape — tells/PMs flagged)
7. shared console view/open (same SPICE method as forge/iksar)
Real-time monitor: players nearby (dist/level/class/race) · harvestables (type/dist/open?) ·
nearest monster (aggro?/level/dist) · console preview · harvested-items table (session +
all-time) · char swap · route selector (dynamic per current zone).

## Status (2026-06-22)
DONE: URL+Access, memory-read proven, **player position tracking solid**, login multi-account.
WALL (needs Cheat Engine / more RE): spawn list → blocks harvestables/monsters/players/zone-
count/aggro/?-nodes display. BUILDABLE NOW on what's proven: dashboard skeleton + live
position, route-record tool, log-scrape alerts (combat/PM/rare/can't-harvest), stuck detection.
