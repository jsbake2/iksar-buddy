r"""Harvest work loops for the in-guest agent (REFACTOR P3.2 — split out of
harvest_agent.py; all code verbatim).

The log-driven harvest verbs live here: the combat-log regexes + background
combat watcher, the acquire-then-deplete harvest() state machine, and the three
top-level loops (loop_main, route_loop_main, gather_loop_main).
"""
from __future__ import annotations
import json, math, os, re, threading, time
from pathlib import Path

import pymem
import pymem.process

try:
    from offsets import POS_OFF, PROC
    from agentio import _DBG, _check_stop, _dbg, _status
    from win_input import _u, focus_eq2, harvest_key, tab_key, target_key
    from eq2mem import (ACTOR_BLOCK, _live_eq2, node_addr_at, read_actors,
                        read_node_array, scan_nodes, state, study_capture)
    from nav import _nav_unstuck, _settle, goto, load_graph, nav, reachable
except ImportError:
    from guest_agent.offsets import POS_OFF, PROC
    from guest_agent.agentio import _DBG, _check_stop, _dbg, _status
    from guest_agent.win_input import _u, focus_eq2, harvest_key, tab_key, target_key
    from guest_agent.eq2mem import (ACTOR_BLOCK, _live_eq2, node_addr_at, read_actors,
                                    read_node_array, scan_nodes, state, study_capture)
    from guest_agent.nav import _nav_unstuck, _settle, goto, load_graph, nav, reachable

import glob as _glob
_LOGDIR = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
           r"\EverQuest II\logs\Wuoshi")


def _freshest_log():
    """The active character's log = the most-recently-written eq2log_*.txt. Auto-adapts to
    whoever's logged in (Trailmix, Furyflatulence, ...) instead of a hardcoded name."""
    fs = _glob.glob(os.path.join(_LOGDIR, "eq2log_*.txt"))
    return max(fs, key=os.path.getmtime) if fs else os.path.join(_LOGDIR, "eq2log_Furyflatulence.txt")


LOG = _freshest_log()
HARV = re.compile(r"You (?:mine|forage|gather|fell|trap|acquire|catch|chop|cut) \d+ .*? from the (.+?)\.")
FAIL = re.compile(r"(?:fail(?:ed)? to (?:gather|harvest|mine|forage|trap|acquire|catch|fell|chop)"
                  r"|did not (?:find|gather|harvest))", re.I)   # node STILL there -> retry
RARE = re.compile(r"You have found a rare item")
# Ctrl+0 = /consider. Only ATTACKABLE creatures (mobs) con; harvest nodes do not. Gives the
# mob name too. Used as a gate so the bot never wastes pulls on a badger. (owner: testing aid)
CONSIDER = re.compile(r"You consider (?:an? |the )?(.+?)\s*\.\.\.", re.I)
NOTARGET = re.compile(r"no eligible target", re.I)         # gather found nothing harvestable
NOT_ATTACKABLE = re.compile(r"not attackable", re.I)       # /consider says it's a node, not a mob
# damage TO the player = we're under attack -> flee, never stand and die
RE_DMG = re.compile(r"(?:hits YOU|YOU take \d+|tries to \w+ YOU|\bMaul\b.*YOU|"
                    r"crush(?:es)? YOU|slash(?:es)? YOU|pierc(?:es)? YOU|burn(?:s)? YOU"
                    r"|has killed you)", re.I)
FAR = re.compile(r"too far away", re.I)         # gather locked a node but out of range

# --- combat watcher (background): sets a flag from the log so the act loop can flee fast ---
_combat = {"hit": False, "ts": 0.0}
_combat_stop = False


def _combat_watch():
    off = _log_len()
    while not _combat_stop:
        try:
            new = _log_since(off); off = _log_len()
            if RE_DMG.search(new):
                _combat["hit"] = True; _combat["ts"] = time.time()
        except Exception:
            pass
        time.sleep(0.5)


def _log_len():
    try: return os.path.getsize(LOG)
    except OSError: return 0


def _log_since(off):
    try:
        with open(LOG, "r", errors="replace") as f:
            f.seek(off)
            return f.read()
    except OSError:
        return ""


def _wait_harvest(off, window=5.0):
    """Watch the log after a HARVEST press. Returns (status, name, rare):
    ok=harvested (node), fail=failed-but-still-there, toofar=out of range, notarget=nothing
    harvestable on the current target (creature/empty), none=no line within the window."""
    rare = False; t = time.time()
    while time.time() - t < window:
        time.sleep(0.25)
        new = _log_since(off)
        if RARE.search(new): rare = True
        m = HARV.search(new)
        if m: return ("ok", m.group(1), rare)
        if FAIL.search(new): return ("fail", None, rare)
        if FAR.search(new): return ("toofar", None, rare)
        if NOTARGET.search(new): return ("notarget", None, rare)
    return ("none", None, rare)


def harvest(hwnd):
    """Acquire a NODE as the current target, then deplete it on the HELD target so we never
    re-target and lose it. EQ2 has no 'target nearest harvestable' — only target-nearest-
    non-player, which also grabs creatures. So:
      1. Ctrl+0 (target nearest non-player + /consider). 'not attackable' => node (locked);
         attackable => a creature, step off it with Tab.
      2. If not yet on a node, Tab through the nearby non-players, probing each with a HARVEST
         press — a node harvests, a creature/empty target does nothing — until one harvests.
      3. HARVEST the held node to depletion (3 pulls / bountiful = done). No re-targeting.
    """
    focus_eq2(hwnd)
    succ = 0; rare = False; node = None; debug = []
    have_node = False
    acq = None          # what the FIRST acquire considered: 'mob' (attackable) / 'node' / None.
                        # The caller uses 'mob' to BLACKLIST this spot — the candidate we walked
                        # to is a creature wearing the node vtable, so never approach it again.

    # ---- acquire: make a NODE the current target ----
    _check_stop()
    coff = _log_len(); target_key(); time.sleep(0.9); cnew = _log_since(coff)
    debug.append("T:" + cnew[-200:].replace("\n", " | "))
    cm = CONSIDER.search(cnew)
    if NOTARGET.search(cnew) and not cm:
        return {"node": None, "harvests": 0, "rare": False, "done": "gone", "acq": acq, "debug": debug}
    if cm and not NOT_ATTACKABLE.search(cnew):
        acq = "mob"
        tab_key(); time.sleep(0.4)                 # nearest non-player is a creature -> step past it
    elif cm:
        acq = "node"
        node = cm.group(1).strip(); have_node = True   # /consider says node ('not attackable')

    # ---- probe the non-player ring with harvest presses until one is a node ----
    # A mob/empty target gives "too far"/"no eligible" -> Tab PAST it; only a real node harvests.
    # (Don't bail on the first "too far" — that was a mob sitting on the node blocking everything.)
    # TAB PAST MOBS to the node (owner SME): in mob-dense desert a node has a carrion PACK on it, so
    # Ctrl+0 grabs a hound and Tab must ring through several creatures before the harvestable. Probe
    # each Tab target with a QUICK harvest test (a node harvests within ~2s; a mob/empty fails fast
    # via 'no eligible'/'too far'), up to PROBE_TABS rings. 'Try briefly, then skip' (owner): if no
    # node turns up in the ring, bail and move to the next node — never grind a blocked/empty spot.
    PROBE_TABS = 10
    if not have_node:
        for _ in range(PROBE_TABS):
            _check_stop()
            off = _log_len(); harvest_key(); res = _wait_harvest(off, window=2.0)
            rare = rare or res[2]
            if res[0] == "ok":
                succ += 1; node = res[1]; have_node = True; break
            tab_key(); time.sleep(0.25)            # not harvestable here -> Tab to the next target
        if not have_node:
            return {"node": node, "harvests": succ, "rare": rare, "acq": acq,
                    "done": ("mob_blocked" if succ == 0 else "depleted"), "debug": debug}

    # ---- deplete: harvest the HELD node target (3 pulls, bountiful counts as one) ----
    for _ in range(12):
        if succ >= 3:
            break
        _check_stop()
        off = _log_len(); harvest_key(); res = _wait_harvest(off)
        if res[2]: rare = True
        if res[0] == "ok":
            succ += 1; node = res[1]
        elif res[0] == "fail":
            continue                               # still there -> harvest again, same target
        elif res[0] == "toofar":
            return {"node": node, "harvests": succ, "rare": rare, "acq": acq,
                    "done": "toofar", "debug": debug}
        else:
            break                                  # target gone -> depleted
    return {"node": node, "harvests": succ, "rare": rare, "acq": acq,
            "done": ("depleted" if succ else "gone"), "debug": debug}


def loop_main(keys, max_nodes):
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _status(json.dumps({"ok": False, "err": "live EQ2 not found"}))
        return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    visited = set()
    progress = {"loop": True, "nodes_done": [], "harvests_total": 0}
    for it in range(max_nodes):
        x, z, _ = state(pm, base)
        cands = scan_nodes(pm, base, x, z)
        tgt = None
        for cx, cz, cd in cands:
            if cd < 3.5:
                continue
            if (round(cx / 3), round(cz / 3)) in visited:
                continue
            tgt = (cx, cz); break
        if not tgt:
            progress["stop"] = "no fresh node candidates"; break
        progress["going_to"] = [tgt[0], tgt[1], it + 1]
        _status(json.dumps(progress))
        ok, d, _ = nav(pm, base, hwnd, tgt[0], tgt[1], keys)
        keys.release_all()
        hv = harvest(hwnd) if ok else {"harvests": 0, "done": "nav_fail"}
        visited.add((round(tgt[0] / 3), round(tgt[1] / 3)))
        progress["nodes_done"].append({"xz": tgt, "nav_dist": round(d, 1),
                                       "harvests": hv.get("harvests", 0),
                                       "node": hv.get("node"), "rare": hv.get("rare"),
                                       "result": hv.get("done")})
        progress["harvests_total"] += hv.get("harvests", 0)
        _status(json.dumps(progress))
    progress["finished"] = True
    _status(json.dumps(progress))


ROUTE = r"C:\ib\route.json"
_scan_cache = {"nodes": [], "ts": 0}
_scan_lock = threading.Lock()
_scan_stop = False


def _scan_thread():
    """Background node scanner (the owner's 'scan must be a thread' requirement). Own pymem
    handle; scans continuously so a waypoint read is INSTANT — never waits on the sweep."""
    try:
        pm = pymem.Pymem(PROC)
        base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
    except Exception:
        return
    while not _scan_stop:
        try:
            px = pm.read_float(base + POS_OFF); pz = pm.read_float(base + POS_OFF + 8)
            if abs(px) < 1:                 # attached to a zombie; re-open
                pm = pymem.Pymem(PROC)
                base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
                continue
            nodes = scan_nodes(pm, base, px, pz, radius=60.0)
            with _scan_lock:
                _scan_cache["nodes"] = nodes
                _scan_cache["ts"] = time.time()
        except Exception:
            time.sleep(0.5)


def route_loop_main(keys, laps):
    global _scan_stop
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _status(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    route = json.loads(Path(ROUTE).read_text())
    wps = route["waypoints"]
    threading.Thread(target=_scan_thread, daemon=True).start()
    prog = {"route": route.get("name"), "lap": 0, "wp": 0, "harvests_total": 0, "events": []}
    try:
        for lap in range(laps):
            prog["lap"] = lap + 1
            for wi, wp in enumerate(wps):
                prog["wp"] = wi + 1
                _status(json.dumps(prog))
                nav(pm, base, hwnd, wp[0], wp[1], keys); keys.release_all()
                # QUICK node sweep around this waypoint from the background cache (instant)
                with _scan_lock:
                    cands = list(_scan_cache["nodes"])
                wx, wz, _ = state(pm, base)
                near = [(cx, cz) for (cx, cz, _) in cands
                        if math.hypot(cx - wx, cz - wz) < 25][:8]
                tried = set()
                for cx, cz in near:
                    key = (round(cx / 2), round(cz / 2))
                    if key in tried:
                        continue
                    tried.add(key)
                    nav(pm, base, hwnd, cx, cz, keys); keys.release_all()
                    hv = harvest(hwnd)
                    if hv.get("harvests"):
                        prog["harvests_total"] += hv["harvests"]
                        prog["events"].append({"lap": lap + 1, "wp": wi + 1,
                                               "node": hv.get("node"), "n": hv["harvests"],
                                               "rare": hv.get("rare")})
                        _status(json.dumps(prog))
        prog["finished"] = True
        _status(json.dumps(prog))
    finally:
        _scan_stop = True
        keys.release_all()


def _tour_anchors(graph):
    """Coarse list of (x,z) anchors to drive the loop, IN WALK ORDER. From the graph if recorded
    (every Nth point ~ a tour of the walked loop), else the legacy sparse route.json waypoints."""
    if graph is not None and len(graph) >= 2:
        step = max(1, len(graph.pts) // 40)            # ~40 anchors spread along the loop
        return [tuple(p) for p in graph.pts[::step]]
    try:
        wps = json.loads(Path(ROUTE).read_text())["waypoints"]
        if len(wps) > 2 and math.hypot(wps[0][0] - wps[-1][0], wps[0][1] - wps[-1][1]) < 3:
            wps = wps[:-1]
        return [tuple(w) for w in wps]
    except Exception:
        return []


def gather_loop_main(keys, laps):
    global _combat_stop
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _status(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    graph = load_graph()                               # dense recorded waypoint graph (or None)
    anchors = _tour_anchors(graph)
    # SURVIVAL: watch the log for damage to us in the background
    _combat["hit"] = False; _combat_stop = False
    threading.Thread(target=_combat_watch, daemon=True).start()
    x0, z0, _ = state(pm, base)
    safe = (x0, z0); flees = [0]
    done = {}                                          # node-cell -> last-visited ts; expires so a
    DONE_TTL = 240.0                                   # respawned node gets re-harvested on a long run
    prog = {"mode": "gather_loop", "graph": bool(graph), "anchors": len(anchors),
            "lap": 0, "wp": 0, "harvests_total": 0, "events": [], "named_nodes": []}

    def flee_if_combat():
        """Under attack -> run to the last clear spot to break aggro. Never stand and die."""
        if not _combat["hit"]:
            return False
        _combat["hit"] = False; flees[0] += 1; prog["fled"] = flees[0]
        prog["status"] = "FLEEING combat"; _status(json.dumps(prog))
        keys.release_all()
        nav(pm, base, hwnd, safe[0], safe[1], keys, grace=3.0); keys.release_all()
        time.sleep(2.5); _combat["hit"] = False
        prog["status"] = "resumed"
        return True

    def harvest_nearest():
        """Harvest sensed nodes NEAREST-FIRST. Nodes are OFF the path: graph-route to the closest
        graph point then straight-hop out to the node, harvest, and the next pass re-enters the
        path automatically. Skip nodes farther than ROAM off the walked loop (walled off)."""
        nonlocal safe
        misses = 0
        blocked = 0          # consecutive mob_blocked — skeletons squatting on nodes (NOT in the
        for _ in range(40):  # actor list, so the proximity filter misses them); bail the area at 3
            _check_stop()
            if flee_if_combat():
                return                          # fled — bail this batch, caller moves on
            x, z, _h = state(pm, base)
            # Only nodes near the MAPPED mesh (reachable) — never beeline into unmapped areas/walls.
            _now = time.time()
            # Nearest reachable REAL node — no distance cap. The detector now returns only
            # Harvestables (mobs filtered by the +0x140 class vtable), so every candidate is a real
            # node and beelining to the nearest one is always correct. (The old MAX_DETOUR cap was a
            # band-aid for when mobs leaked into this list; it just starved the bot once that was fixed.)
            cand = sorted((n for n in read_node_array(pm, base)
                           if _now - done.get((round(n[0] / 3), round(n[1] / 3)), 0) > DONE_TTL
                           and reachable(graph, n[0], n[1])),
                          key=lambda n: math.hypot(n[0] - x, n[1] - z))
            if not cand:
                _dbg("hn: no reachable node -> travel"); return
            # Prefer CLEAR nodes — skip ones with a mob squatting on them (a non-player actor within
            # ACTOR_BLOCK m). Only fall back to guarded nodes when nothing clear is reachable, so we
            # don't waste trips on skeleton-camp nodes we can't acquire (Ctrl+0 grabs the mob).
            # Detection already dropped candidates that ARE mobs (skeletons on the node vtable).
            # Here just PREFER nodes with no mob within ACTOR_BLOCK (so Ctrl+0 grabs the node, not a
            # mob beside it); fall back to the rest if none are clear. Camp-bail (3x mob_blocked)
            # backstops dense camps where every node has a wanderer next to it.
            actors = read_actors(pm, base)
            clear = [n for n in cand
                     if not any(math.hypot(n[0] - a[0], n[1] - a[1]) < ACTOR_BLOCK for a in actors)]
            cand = clear or cand
            tx, tz = cand[0]                     # the NEAREST reachable node to us
            d0 = math.hypot(tx - x, tz - z)
            prog["status"] = "to nearest node"; prog["target"] = [tx, tz]
            _status(json.dumps(prog))
            _dbg(f"hn: {len(cand)} cand; go {tx:.0f},{tz:.0f} d0={d0:.0f}")
            # Go STRAIGHT to it (smooth: face once, walk in). The reachability gate already keeps
            # targets near the mapped mesh, so straight-line stays on walkable ground; unstuck
            # handles the odd bump. (Graph routing zigzagged through dense mesh points = jerky.)
            ok, dist_left, stuck = _nav_unstuck(pm, base, hwnd, keys, tx, tz, grace=1.0)
            keys.release_all()
            done[(round(tx / 3), round(tz / 3))] = time.time()   # visited (expires after DONE_TTL)
            _dbg(f"  -> dist_left={dist_left:.1f} stuck={stuck}")
            if dist_left > 3.5:
                misses += 1
                if misses >= 3:                  # 3 unreachable in a row -> stop grinding, relocate
                    prog["status"] = "nodes unreachable here — relocating"
                    _status(json.dumps(prog))
                    return
                continue                         # couldn't get within ~3m -> skip, next nearest
            misses = 0
            if not _combat["hit"]:
                safe = (tx, tz)
            prog["status"] = "settling to harvest"; _status(json.dumps(prog))
            _dbg("  settle..")                   # markers: pinpoint a freeze in settle vs harvest
            _settle(pm, base, keys)              # STOP COMPLETELY before harvesting (owner rule)
            _dbg("  harvest()..")
            hv = harvest(hwnd)
            _dbg(f"  harvest() ret {hv.get('done')}")
            tries = 0                            # held node drifted out of ~2m range -> hug & retry
            while hv.get("done") == "toofar" and tries < 2:
                tries += 1
                _dbg(f"  toofar -> hug closer + settle, retry {tries}")
                nav(pm, base, hwnd, tx, tz, keys, grace=0.5); keys.release_all()
                _settle(pm, base, keys)
                hv = harvest(hwnd)
            # Skeleton wandered onto a REAL node (owner: wait/retry — it'll move off). Linger a few
            # seconds and retry; the node is static, the mob isn't. Don't abandon a real node to a
            # passing wanderer.
            if hv.get("done") in ("mob_blocked", "mob"):
                prog["status"] = "mob on node — waiting for it to wander"
                _status(json.dumps(prog))
                _dbg("  mob on node -> wait 3.5s for it to wander, retry once")
                time.sleep(3.5)
                _check_stop()
                _settle(pm, base, keys)
                hv = harvest(hwnd)
            _dbg(f"  HARVEST done={hv.get('done')} n={hv.get('harvests')} node={hv.get('node')} "
                 f"dbg={hv.get('debug')}")
            # RE corpus: label this object by the /consider verdict (acq), NOT by harvest success —
            # a node we cons 'not attackable' is a confirmed node even if the pull was 'too far'. Far
            # higher yield than harvest-only, and the ground truth is the game's own classification.
            if hv.get("acq") in ("node", "mob"):
                study_capture(pm, node_addr_at(tx, tz), hv["acq"], (tx, tz))
            if hv.get("harvests"):
                blocked = 0
                prog["harvests_total"] += hv["harvests"]
                prog["events"].append({"node": hv.get("node"), "n": hv["harvests"],
                                       "rare": hv.get("rare"), "at": [tx, tz]})
                if hv.get("node"):
                    prog["named_nodes"].append({"xz": [round(tx, 1), round(tz, 1)], "name": hv["node"]})
                _status(json.dumps(prog))
            elif hv.get("done") in ("mob_blocked", "mob"):
                blocked += 1
                # A REAL node we couldn't grab right now (a mob on it we couldn't Tab past, or it
                # despawned). The candidate is a real Harvestable (+0x140 filter), so do NOT blacklist
                # it — just let the DONE_TTL (240s) skip it for now; it's retried later once the mob
                # has wandered off ('try briefly, then skip' — owner). No permanent suppression.
                prog.setdefault("mobs_skipped", []).append({"mob": hv.get("node"), "at": [tx, tz]})
                _status(json.dumps(prog))
                if blocked >= 3:                 # several blocked in a row here -> bail, advance tour
                    _dbg("  too many blocked -> bail this area, advance tour")
                    return
            else:
                blocked = 0                       # gone/depleted/toofar — not a mob, keep going

    try:
        try:
            os.remove(_DBG)
        except OSError:
            pass
        _dbg(f"=== gather start: {len(anchors)} anchors, graph={bool(graph)} ===")
        # RIGHT WHEN WE START: grab the nearest reachable node(s) to where we stand.
        harvest_nearest()
        # then tour the loop (graph-routed travel), harvesting nearest-first at each anchor.
        for lap in range(laps):
            prog["lap"] = lap + 1
            if not anchors:
                break
            x0, z0, _ = state(pm, base)
            start = min(range(len(anchors)),
                        key=lambda i: math.hypot(anchors[i][0] - x0, anchors[i][1] - z0))
            tour = anchors[start:] + anchors[:start]    # start at the nearest anchor, walk in order
            for wi, anc in enumerate(tour):
                _check_stop()
                if flees[0] >= 8:
                    prog["stop"] = "too much combat — bailed"; break
                prog["wp"] = wi + 1; prog["status"] = "travel (path)"
                _status(json.dumps(prog))
                flee_if_combat()
                goto(pm, base, hwnd, keys, anc[0], anc[1], graph); keys.release_all()
                if not _combat["hit"]:
                    safe = (anc[0], anc[1])             # reached clear -> new safe anchor
                harvest_nearest()
            if prog.get("stop"):
                break
        prog["finished"] = True; _status(json.dumps(prog))
    finally:
        _combat_stop = True
        keys.release_all()
