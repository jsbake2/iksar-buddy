r"""EQ2 client-memory reading: pymem attach, player state, heap scans for
harvest nodes / actors, the mob blacklist and the RE study corpus (REFACTOR
P3.2 — split out of harvest_agent.py; all code verbatim).

Client-side only (own process memory) per the project's hard rule.
"""
from __future__ import annotations
import json, math, struct, time

import ctypes
from ctypes import wintypes
import ctypes.wintypes as _wt

import pymem, pymem.process

# Offsets come from the ONE shared module (REFACTOR P0.4); deploy pushes it
# alongside this file as C:\ib\agent\offsets.py.
try:
    from offsets import HDG_OFF, POS_OFF, PROC  # in-guest sibling
    import win_input
except ImportError:
    from guest_agent.offsets import HDG_OFF, POS_OFF, PROC
    from guest_agent import win_input

_u = win_input._u


def _live_eq2():
    """Return (hwnd, pid, pm, base) for the REAL in-world EverQuest2 — found by attaching
    pymem to the owning process and checking for a valid player position. Robust to empty
    window titles and the crashed/zombie helper procs (pos 0,0,0)."""
    wins = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if _u.IsWindowVisible(h):
            r = wintypes.RECT()
            _u.GetWindowRect(h, ctypes.byref(r))
            if (r.right - r.left) > 200 and (r.bottom - r.top) > 150:
                wins.append((h, win_input._win_pid(h)))
        return True
    _u.EnumWindows(cb, 0)
    seen = {}
    for h, pid in wins:
        if pid in seen:
            continue
        try:
            pm = pymem.Pymem(); pm.open_process_from_id(pid)
            base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
            x = pm.read_float(base + POS_OFF); z = pm.read_float(base + POS_OFF + 8)
            ok = abs(x) > 1 and abs(x) < 1e5 and abs(z) < 1e5
            seen[pid] = (ok, pm, base)
            if ok:
                return h, pid, pm, base
        except Exception:
            seen[pid] = (False, None, None)
    return None, None, None, None


def pm_open():
    pm = pymem.Pymem(PROC)
    base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
    return pm, base


def state(pm, base):
    a = base + POS_OFF
    x = pm.read_float(a); z = pm.read_float(a + 8)
    h = pm.read_float(base + HDG_OFF) % 360.0
    return x, z, h


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", _wt.DWORD), ("__a1", _wt.DWORD),
                ("RegionSize", ctypes.c_ulonglong), ("State", _wt.DWORD),
                ("Protect", _wt.DWORD), ("Type", _wt.DWORD), ("__a2", _wt.DWORD)]


NODE_CLASSES = [(0x14eb850, 0x60), (0x14a3238, 0x40), (0x14a32d8, 0x40),
                (0x1493c58, 0x40), (0x149b2f8, 0x40)]


def scan_nodes(pm, base, px, pz, radius=160.0):
    """Inline node-candidate scan (union of harvest-node vtable classes). Runs while the bot
    is stationary between nodes, so it never makes movement jerky."""
    VQ = ctypes.windll.kernel32.VirtualQueryEx
    VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    pats = {struct.pack("<Q", base + vt): po for vt, po in NODE_CLASSES}
    out = []
    seen = set()
    addr = 0
    mbi = _MBI()
    while addr < 0x7fffffffffff:
        if not VQ(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        sz = mbi.RegionSize
        if mbi.State == 0x1000 and (mbi.Protect & 0xff) == 0x04 and 0 < sz <= 256 * 1024 * 1024:
            try:
                buf = pm.read_bytes(mbi.BaseAddress, sz)
            except Exception:
                buf = b""
            for patt, po in pats.items():
                i = buf.find(patt)
                while i != -1:
                    oa = mbi.BaseAddress + i
                    if oa not in seen and i + po + 12 <= len(buf):
                        seen.add(oa)
                        x, y, z = struct.unpack_from("<fff", buf, i + po)
                        if (math.isfinite(x) and math.isfinite(z) and abs(x) > 5 and abs(z) > 5
                                and abs(x) < 1e5 and abs(z) < 1e5 and abs(y) < 1e4):
                            d = math.hypot(x - px, z - pz)
                            if d < radius:
                                out.append((round(x, 1), round(z, 1), round(d, 1)))
                    i = buf.find(patt, i + 1)
        addr = mbi.BaseAddress + sz if sz else addr + 0x1000
    out.sort(key=lambda n: n[2])
    return out


# The game's live "nearby harvestables" array (module-static). Pointers to harvest-node
# objects (vtable in the 0x149x-0x14ex family); world position at obj+0x60. Found via the
# target-diff: this list is what the gather skill walks, so it's REAL nodes only.
NODE_LO = 0x177bf00
NODE_HI = 0x177c100


# Harvest nodes are render objects with these EXACT vtables — confirmed by standing on each type
# (2026-06-24): wood (felled high plains arbor), ore (wind swept stones) AND bush (high plains
# shrubbery) ALL share 0x14eb830, with a co-located sibling 0x14eba10. World position is at
# obj+0x60 (NOT +0x1a0 — that offset hid the bush nodes). Mobs/NPCs are DIFFERENT vtables, so an
# exact-vtable match needs no actor/monster filter at all — that filter was deleting real nodes.
NODE_VTS = {0x14eb830, 0x14eba10}
NODE_POS = 0x60
ACTOR_VT = 0x1782848             # monsters/NPCs/players actor vtable (pos @ +0x1f0)
ACTOR_POS = 0x1f0
NODE_RADIUS = 110.0
# Node-vs-mob discriminator — SOLVED 2026-06-25 via a /consider-labelled corpus (1 node + 7 mobs,
# all 7 mobs unanimous). These objects all share the HEADER vtable 0x14eb830 at +0x000 (that's why
# matching it alone caught mobs too). The real separator is the MOST-DERIVED CLASS vtable at +0x140:
#   harvest node  -> +0x140 == 0x14eb830   (Harvestable)
#   creature/mob  -> +0x140 == 0x14eba10   (Creature wearing the same header)
# So 0x14eba10 was never a "sibling node vtable" — it's the MOB class vtable. Match the header at
# +0x000, then require +0x140 to be the Harvestable vtable. No heuristics, no blacklist needed.
NODE_HEADER_VT = 0x14eb830      # shared object-header vtable at +0x000 (node AND mob)
NODE_CLASS_OFF = 0x140          # most-derived class vtable lives here
NODE_CLASS_VT = 0x14eb830       # value at +0x140 that marks a real harvest node (mob = 0x14eba10)
MOB_SAME = 3.0                   # a node candidate within this of an actor IS that actor — some mobs
                                 # (skeletons) carry the node vtable; drop them (confirmed 2026-06-24)
ACTOR_BLOCK = 4.5                # softer: a mob this close to a (real) node likely blocks Ctrl+0
_node_cache = {"nodes": [], "actors": [], "ts": 0.0, "px": 0.0, "pz": 0.0}

# Self-correcting mob blacklist (zone-independent). Some creatures wear the node vtable, aren't
# in the actor list, AND stand still — beating every memory filter. The ONLY reliable verdict is
# the game's /consider ('attackable' = mob). When we walk to a candidate and Ctrl+0 cons it as a
# mob, we stamp its cell here; read_node_array then drops candidates near it. So the bot walks to
# any given mob AT MOST ONCE per run, then never again — no reverse-engineering required.
MOB_BL_CELL = 4.0                # blacklist granularity (m) — one cell per confused mob
MOB_BL_TTL = 600.0               # how long a learned mob stays blacklisted (mobs wander/respawn)
_mob_bl: dict = {}               # (cx,cz) -> last-confirmed ts

# Max distance the bot will DETOUR off its current spot to a memory-detected candidate. The node
# vtable is shared with mobs that aren't in the actor list (Commonlands skeletons), so a detected
# position is NOT trustworthy enough to make a long beeline to — that's how the bot ran across the
# zone "approaching every mob as a harvest". We only grab candidates within this radius of where
# the TRAIL already put us; farther nodes are reached when the tour walks us near them. Keeps the
# bot on its recorded route instead of chasing distant blips.
MAX_DETOUR = 11.0


STUDY = r"C:\ib\study.jsonl"     # labelled object snapshots for offline node/mob discriminator RE
_study_n = [0]


def study_capture(pm, addr, label, xz):
    """Append a GROUND-TRUTH-labelled snapshot of a node-vtable object to STUDY (jsonl). label is
    'node' (we harvested it) or 'mob' (Ctrl+0 cons'd it attackable). Off the gather's hot path,
    fully guarded — never let RE logging break harvesting. We dump 0x400 raw bytes + the module
    base so pointer fields can be normalised offline. Capped so a long run can't fill the disk."""
    if addr is None or _study_n[0] >= 400:
        return
    try:
        _hwnd, _pid, _pm, base = None, None, pm, 0
        base = pymem.process.module_from_name(pm.process_handle, "EverQuest2.exe").lpBaseOfDll
        raw = pm.read_bytes(addr, 0x400)
        rec = {"label": label, "xz": [round(xz[0], 1), round(xz[1], 1)], "ts": round(time.time(), 1),
               "addr": addr, "base": base, "hex": raw.hex()}
        with open(STUDY, "a") as f:
            f.write(json.dumps(rec) + "\n")
        _study_n[0] += 1
    except Exception:
        pass


def blacklist_mob(x, z):
    """Mark (x,z) as a confirmed mob so the detector stops offering it as a node."""
    _mob_bl[(round(x / MOB_BL_CELL), round(z / MOB_BL_CELL))] = time.time()


def _is_blacklisted(x, z):
    now = time.time()
    cx, cz = round(x / MOB_BL_CELL), round(z / MOB_BL_CELL)
    for dx in (-1, 0, 1):                         # check the 3x3 neighbourhood so a near-miss hits
        for dz in (-1, 0, 1):
            ts = _mob_bl.get((cx + dx, cz + dz))
            if ts and now - ts < MOB_BL_TTL:
                return True
    return False


def read_node_array(pm, base):
    """Nearby REAL harvest nodes. Heap-scan for objects whose vtable is EXACTLY a node vtable
    (NODE_VTS — wood/ore/bush all share 0x14eb830 + sibling 0x14eba10), reading world pos at
    obj+0x60. Mobs/NPCs are different vtables, so there is NO monster filter to mis-fire and no
    real nodes get dropped. Cached ~6s / until the player moves 30m (a full scan is a few sec)."""
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    now = time.time()
    if (now - _node_cache["ts"] < 6.0
            and math.hypot(px - _node_cache["px"], pz - _node_cache["pz"]) < 30):
        return _node_cache["nodes"]   # scan covers ~110m; don't re-scan every 10m hop (pause-y)
    try:
        import numpy as np
    except Exception:
        return _node_cache["nodes"]
    header_vt = base + NODE_HEADER_VT            # match the shared header at +0x000 (node AND mob)
    node_class = base + NODE_CLASS_VT            # then require the Harvestable class vtable at +0x140
    actvt = base + ACTOR_VT
    VQ = ctypes.windll.kernel32.VirtualQueryEx; VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    cand = {}                                    # rounded (x,z) -> (x,z)  (dedup co-located objs)
    acts = {}                                    # rounded (x,z) -> (x,z)  non-player actor positions
    addr = 0; mbi = _MBI()
    while addr < 0x7fff00000000:
        if not VQ(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            addr += 0x10000; continue
        b0 = mbi.BaseAddress; sz = mbi.RegionSize
        if (mbi.State == 0x1000 and not (mbi.Protect & 0x100)
                and (mbi.Protect & 0xff) in (0x04, 0x02, 0x20)
                and 0x10000000000 < b0 < 0x7ff000000000 and 0 < sz < 0x8000000):
            try:
                buf = pm.read_bytes(b0, sz)
            except Exception:
                buf = b""
            if len(buf) >= 0x80:
                arr = np.frombuffer(buf[:(len(buf) // 8) * 8], dtype="<u8")
                for i in np.where(arr == header_vt)[0]:
                    o = int(i) * 8
                    if o + NODE_CLASS_OFF + 8 > len(buf) or o + NODE_POS + 12 > len(buf):
                        continue
                    # THE discriminator: most-derived class vtable at +0x140. Harvest node ->
                    # 0x14eb830; creature wearing the same header -> 0x14eba10. Drop creatures here,
                    # at the source, so a mob can NEVER become a nav target. (RE-confirmed 7/7 mobs.)
                    if struct.unpack_from("<Q", buf, o + NODE_CLASS_OFF)[0] != node_class:
                        continue
                    x, y, z = struct.unpack_from("<fff", buf, o + NODE_POS)
                    if (math.isfinite(x) and math.isfinite(z) and math.isfinite(y)
                            and abs(y - py) < 25):
                        d = math.hypot(x - px, z - pz)
                        if d < NODE_RADIUS:
                            cand[(round(x), round(z))] = (b0 + o, round(x, 1), round(z, 1))
                for i in np.where(arr == actvt)[0]:          # non-player actors (mobs/NPCs)
                    o = int(i) * 8
                    if o + ACTOR_POS + 12 > len(buf):
                        continue
                    x, y, z = struct.unpack_from("<fff", buf, o + ACTOR_POS)
                    if (math.isfinite(x) and math.isfinite(z) and abs(y - py) < 35):
                        d = math.hypot(x - px, z - pz)
                        if 3.0 < d < NODE_RADIUS:             # >3m skips the player's own actor(s)
                            acts[(round(x), round(z))] = (round(x, 1), round(z, 1))
        addr = b0 + sz if sz else addr + 0x10000
    actors = list(acts.values())
    # The +0x140 class filter (at scan time) already removed every mob, so cand is real Harvestables.
    # Do NOT drop a node just because an actor is near it — a carrion standing ON a node would knock
    # out a perfectly good node we want to Tab-past-and-harvest. (actors[] is still gathered, used by
    # harvest_nearest only to PREFER clear nodes, not to delete blocked ones.)
    survivors = list(cand.values())
    # MOTION filter — harmless backstop: re-read each survivor's pos after a short delay; anything
    # that MOVED is a creature (no class-vtable match should reach here, but belt-and-suspenders).
    # Static harvest nodes never move.
    addrs = {}                                   # (round(x),round(z)) -> object address (for study dumps)
    try:
        time.sleep(0.6)
        kept = []
        for a, x, z in survivors:
            try:
                nx = pm.read_float(a + NODE_POS); nz = pm.read_float(a + NODE_POS + 8)
                if math.isfinite(nx) and math.isfinite(nz) and math.hypot(nx - x, nz - z) > 0.5:
                    continue                         # moved -> mob, drop
            except Exception:
                pass
            kept.append((x, z))
            addrs[(round(x), round(z))] = a
        nodes = kept
    except Exception:
        nodes = [(x, z) for _a, x, z in survivors]
        addrs = {(round(x), round(z)): a for a, x, z in survivors}
    nodes.sort(key=lambda n: math.hypot(n[0] - px, n[1] - pz))
    _node_cache.update(nodes=nodes, actors=actors, ts=time.time(), px=px, pz=pz, addrs=addrs)
    return nodes


def node_addr_at(x, z):
    """Object address of the detected node-candidate nearest (x,z) from the last scan, or None.
    Lets the gather snapshot the exact object it just harvested / hit a mob on (study labelling)."""
    addrs = _node_cache.get("addrs") or {}
    best, ba = None, None
    for (rx, rz), a in addrs.items():
        d = math.hypot(rx - x, rz - z)
        if best is None or d < best:
            best, ba = d, a
    return ba if (best is not None and best <= 3.0) else None


def read_actors(pm, base):
    """Non-player actor (mob/NPC) positions from the most recent node scan. Excludes the player's
    own actor(s) (anything within 3 m of the player at scan time). Call read_node_array first."""
    return _node_cache.get("actors", [])


def _read_cstr(pm, addr, maxlen=64):
    """Read a NUL-terminated ASCII string at addr, '' on failure / non-text."""
    try:
        b = pm.read_bytes(addr, maxlen)
    except Exception:
        return ""
    out = []
    for c in b:
        if c == 0:
            break
        if 32 <= c < 127:
            out.append(chr(c))
        else:
            return ""                      # non-printable -> not a C string
    return "".join(out) if len(out) >= 2 else ""
