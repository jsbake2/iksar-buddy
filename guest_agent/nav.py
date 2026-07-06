r"""Waypoint-graph navigation for the in-guest agent (REFACTOR P3.2 — split out
of harvest_agent.py; all code verbatim).

OgreNav-style movement: follow the dense recorded graph around walls, leave the
path only for the final off-path hop to a node, progress-based stuck detection,
and the settle-before-harvest stop. See nav_graph.py for the graph + A*.
"""
from __future__ import annotations
import math, time

try:
    import nav_graph                       # dense waypoint graph + A* (deployed alongside us)
except Exception:
    nav_graph = None

try:
    from agentio import _check_stop, _dbg
    from win_input import _u, focus_eq2, _jump
    from eq2mem import state
except ImportError:
    from guest_agent.agentio import _check_stop, _dbg
    from guest_agent.win_input import _u, focus_eq2, _jump
    from guest_agent.eq2mem import state

GRAPH_FILE = r"C:\ib\graph.json"   # dense recorded waypoint graph (OgreNav-style wall avoidance)
ROAM = 28.0                        # max off-path deviation: leave the graph to grab a node up to
                                   # this far from the nearest graph point (tighter -> stays on
                                   # the MAPPED mesh, never beelines into unmapped areas/walls)

GRACE = 2.5          # metres — close enough to harvest
FACE_TOL = 22.0      # degrees — "generally facing"
TURN_BRAKE = 8.0     # release turn slightly early; momentum carries it in
STRAFE_BAND = (6.0, FACE_TOL)   # trim lateral with strafe inside this |diff|
TIMEOUT = 25.0
# Right arrow INCREASES heading (calibrated); to cut a +diff we press Right.
TURN_FOR_POS_DIFF = "right"
TURN_FOR_NEG_DIFF = "left"


def _settle(pm, base, keys, timeout=3.0):
    """Release ALL keys and wait until the character is FULLY stopped before harvesting — EQ2
    will not harvest while you're moving, and nav leaves momentum/drift. (owner: stop completely
    then harvest.) Returns once two reads in a row show < 0.05 m of movement."""
    keys.release_all()
    last = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        x, z, _h = state(pm, base)
        if last is not None and math.hypot(x - last[0], z - last[1]) < 0.05:
            break
        last = (x, z)
    keys.release_all()
    time.sleep(0.2)                              # tiny extra beat for the client to register idle


def signed_diff(bearing, h):
    return (bearing - h + 540) % 360 - 180


def nav(pm, base, hwnd, tx, tz, keys, grace=GRACE):
    """Returns (ok, dist, stuck). STUCK = no PROGRESS toward the target over ~2.5s, even if
    position is changing (owner: bouncing up/down a barrier is stuck too) — so we watch the
    distance-to-target trend, not raw movement, and bail instead of grinding a wall."""
    focus_eq2(hwnd)
    t0 = time.time()
    last_focus = 0.0
    last_hb = time.time()
    hist = []                                    # (t, dist-to-target)
    while time.time() - t0 < TIMEOUT:
        _check_stop()                            # halt mid-nav if the STOP flag appears
        now = time.time()
        if now - last_focus > 1.0:               # re-assert focus periodically
            if _u.GetForegroundWindow() != hwnd:
                keys.set(set()); focus_eq2(hwnd)
            last_focus = now
        x, z, h = state(pm, base)
        d = math.hypot(tx - x, tz - z)
        if now - last_hb > 6.0:                   # travel heartbeat: keeps the log alive on long
            _dbg(f"  ..travel {x:.0f},{z:.0f} -> {tx:.0f},{tz:.0f} d={d:.0f}")   # trips so the
            last_hb = now                         # watchdog doesn't read a long goto as a stall
        if d < grace:
            keys.release_all()
            return True, d, False
        # progress-based stuck check: keep ~2.5s of distance history
        hist.append((now, d))
        hist = [(t, dd) for t, dd in hist if now - t < 2.6]
        if hist and now - hist[0][0] >= 2.2 and d >= hist[0][1] - 1.2:
            # <1.2 m of progress in 2.2 s while trying to move => stuck on a barrier
            keys.set({"s"}); time.sleep(0.5)      # back off a touch
            keys.release_all()
            return False, d, True
        bearing = math.degrees(math.atan2(tx - x, tz - z)) % 360
        diff = signed_diff(bearing, h)               # -180..180, + = target is to our right
        ad = abs(diff)
        dr = math.radians(diff)
        want = set()
        # SIMPLE: point heading at the node, then walk straight in (owner's model). No strafe.
        if ad > 20:
            # not pointed at it -> TURN IN PLACE until facing (clean pivot, no forward = no arc)
            want.add(TURN_FOR_POS_DIFF if diff > 0 else TURN_FOR_NEG_DIFF)
        else:
            # facing it -> drive straight forward; small turn nudge only if heading drifts
            want.add("w")
            if ad > 7:
                want.add(TURN_FOR_POS_DIFF if diff > 0 else TURN_FOR_NEG_DIFF)
        keys.set(want)
        time.sleep(0.03)
    keys.release_all()
    return False, math.hypot(tx - x, tz - z), False    # timed out (not flagged stuck)


def load_graph():
    """Load the dense recorded waypoint graph, or None if not recorded yet / too small."""
    if nav_graph is None:
        return None
    try:
        g = nav_graph.Graph.load(GRAPH_FILE)
        return g if len(g) >= 2 else None
    except Exception:
        return None


def reachable(graph, tx, tz):
    """Nodes live OFF the path. Only skip ones absurdly far from the walked loop (> ROAM from the
    nearest graph point) — those are almost certainly across a wall. Everything else we go grab."""
    if graph is None:
        return True                                    # no graph yet -> don't filter
    _, d = graph.nearest(tx, tz)
    return d <= ROAM


def _nav_unstuck(pm, base, hwnd, keys, tx, tz, grace):
    """nav() to (tx,tz); if it jams on a barrier, run the unstuck ladder (jump + back off) and
    retry ONCE. Returns (ok, dist_left, stuck)."""
    ok, d, stuck = nav(pm, base, hwnd, tx, tz, keys, grace=grace)
    if stuck:
        keys.release_all(); _jump()
        keys.set({"s"}); time.sleep(0.4); keys.release_all()
        ok, d, stuck = nav(pm, base, hwnd, tx, tz, keys, grace=grace)
    return ok, d, stuck


def goto(pm, base, hwnd, keys, tx, tz, graph, grace=GRACE):
    """Travel to (tx,tz) AROUND walls: graph-route to the nearest graph point to the target, then
    LEAVE the path and straight-hop the rest of the way to the node (nodes are off-path). The next
    goto re-enters the path automatically (it routes from wherever we end up). Falls back to
    straight nav with no graph. Returns (ok, dist_left, stuck)."""
    if graph is None:
        return nav(pm, base, hwnd, tx, tz, keys, grace=grace)
    x, z, _ = state(pm, base)
    for wx, wz in graph.route(x, z, tx, tz):           # 1) follow the path around walls
        _check_stop()
        cx, cz, _ = state(pm, base)
        if math.hypot(wx - cx, wz - cz) < 2.5:
            continue                                   # already at/past this graph point
        ok, d, stuck = _nav_unstuck(pm, base, hwnd, keys, wx, wz, grace=2.2)
        if stuck:
            keys.release_all()
            return False, math.hypot(tx - cx, tz - cz), True
    keys.release_all()
    return _nav_unstuck(pm, base, hwnd, keys, tx, tz, grace)   # 2) off-path hop to the exact node
