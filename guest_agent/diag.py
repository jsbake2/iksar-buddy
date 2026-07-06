r"""RE diagnostics for the in-guest agent (REFACTOR P3.2 — split out of
harvest_agent.py; all code verbatim).

The three memory-scan diagnostic mains (--diag / --diag2 / --dump) used to crack
node vtables and struct layouts. Owner stands on/near an object in-world, fires
one of these, reads gdbg.log. Not part of any production loop.
"""
from __future__ import annotations
import ctypes
import math, struct

try:
    from offsets import POS_OFF
    from agentio import _dbg
    from eq2mem import (_MBI, NODE_POS, NODE_RADIUS, NODE_VTS, _live_eq2,
                        _node_cache, _read_cstr, read_actors, read_node_array)
except ImportError:
    from guest_agent.offsets import POS_OFF
    from guest_agent.agentio import _dbg
    from guest_agent.eq2mem import (_MBI, NODE_POS, NODE_RADIUS, NODE_VTS, _live_eq2,
                                    _node_cache, _read_cstr, read_actors, read_node_array)


def diag_scan_main():
    """Dump exactly what the PRODUCTION read_node_array sees: exact-vtable node objects (NODE_VTS,
    pos @ +0x60), nearest-first. Owner stands on a node -> nearest entry should be ~0-2m. Verifies
    the detector with no guessing. Writes to gdbg.log."""
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _dbg("DIAG: no live EQ2"); return
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    _dbg(f"DIAG === player @ {px:.1f},{py:.1f},{pz:.1f}  vts={[hex(v) for v in NODE_VTS]} pos+{NODE_POS:#x} ===")
    _node_cache["ts"] = 0.0                       # force a fresh scan
    nodes = read_node_array(pm, base)
    actors = read_actors(pm, base)
    _dbg(f"DIAG nodes ({len(nodes)} within {NODE_RADIUS:.0f}m)  [nearAct = dist to nearest actor]:")
    for x, z in nodes[:40]:
        d = math.hypot(x - px, z - pz)
        na = min((math.hypot(x - a[0], z - a[1]) for a in actors), default=9e9)
        flag = "  <-- ON AN ACTOR (mob?)" if na < 2.0 else ""
        _dbg(f"  node d={d:6.1f} @ {x:8.1f},{z:8.1f}   nearAct={na:5.1f}{flag}")
    _dbg(f"DIAG actors ({len(actors)} non-self within {NODE_RADIUS:.0f}m):")
    for a in sorted(actors, key=lambda a: math.hypot(a[0] - px, a[1] - pz))[:20]:
        _dbg(f"  actor d={math.hypot(a[0]-px, a[1]-pz):6.1f} @ {a[0]:8.1f},{a[1]:8.1f}")
    _dbg("DIAG === done ===")


def diag_wide_main(rad=10.0):
    """Find ANY object whose world pos (@+0x1a0) is within `rad` m of the player, regardless of
    vtable range. Reports each object's vtable (relative to base). Used to capture the vtable of a
    node type the narrow scan misses (e.g. bush/shrubbery) — owner stands ON it, we read what's
    under him. Tries a few likely pos offsets too in case bush nodes store pos elsewhere."""
    import numpy as np
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _dbg("DIAG2: no live EQ2"); return
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    _dbg(f"DIAG2 === player @ {px:.1f},{py:.1f},{pz:.1f} rad={rad} ===")
    # accept any qword that points into the module's vtable band (covers node 0x149xxxx..
    # actor 0x178xxxx and a margin) — that's an object header (vtable at offset 0).
    vlo = base + 0x1000000; vhi = base + 0x1900000
    POS_OFFS = (0x1a0, 0x60, 0x1f0, 0x90)         # try several struct layouts
    VQ = ctypes.windll.kernel32.VirtualQueryEx; VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    hits = []   # (dist, pos_off, vt_rel, x, z)
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
            if len(buf) >= 0x200:
                arr = np.frombuffer(buf[:(len(buf) // 8) * 8], dtype="<u8")
                for i in np.where((arr >= vlo) & (arr < vhi))[0]:
                    o = int(i) * 8
                    for po in POS_OFFS:
                        if o + po + 12 > len(buf):
                            continue
                        x, y, z = struct.unpack_from("<fff", buf, o + po)
                        if (math.isfinite(x) and math.isfinite(z) and math.isfinite(y)
                                and abs(y - py) < 30):
                            d = math.hypot(x - px, z - pz)
                            if d < rad:
                                hits.append((d, po, int(arr[i]) - base, round(x, 1), round(z, 1)))
        addr = b0 + sz if sz else addr + 0x10000
    hits.sort()
    # drop the player's own object stack (everything basically at his feet), dedup by (posOff,vt)
    # keeping the NEAREST instance, so distinct nearby objects (the node) stand out.
    best = {}
    for d, po, vt, x, z in hits:
        if d < 1.5:                          # player self-cluster
            continue
        key = (po, vt)
        if key not in best or d < best[key][0]:
            best[key] = (d, x, z)
    rows = sorted((d, po, vt, x, z) for (po, vt), (d, x, z) in best.items())
    _dbg(f"DIAG2 distinct objects 1.5..{rad}m ({len(rows)}):")
    for d, po, vt, x, z in rows[:40]:
        _dbg(f"  obj d={d:5.1f} posOff=+{po:#05x} vt=+{vt:#08x} @ {x:8.1f},{z:8.1f}")
    _dbg("DIAG2 === done ===")


def diag_dump_main():
    """Dump the FULL object the player is standing on (nearest node-vtable object) so we can find a
    field that distinguishes a harvest NODE from a mob wearing the same vtable. Stand ON a node ->
    capture A; stand ON/next to the mis-detected mob -> capture B; diff the two. For each 8-byte
    field we print the raw qword, the float interpretation, and — if the qword points at readable
    memory — any ASCII string there (the object's NAME is the likely discriminator). Writes gdbg.log."""
    import numpy as np
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _dbg("DUMP: no live EQ2"); return
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    vts = set(base + v for v in NODE_VTS)
    VQ = ctypes.windll.kernel32.VirtualQueryEx; VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    best = None                            # (dist, objAddr)
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
                for i in np.where(np.isin(arr, np.array(sorted(vts), dtype="<u8")))[0]:
                    o = int(i) * 8
                    if o + NODE_POS + 12 > len(buf):
                        continue
                    x, y, z = struct.unpack_from("<fff", buf, o + NODE_POS)
                    if math.isfinite(x) and math.isfinite(z) and abs(y - py) < 25:
                        d = math.hypot(x - px, z - pz)
                        if best is None or d < best[0]:
                            best = (d, b0 + o)
        addr = b0 + sz if sz else addr + 0x10000
    if best is None:
        _dbg(f"DUMP: no node-vtable object found near player @ {px:.1f},{pz:.1f}"); return
    d, obj = best
    _dbg(f"DUMP === nearest node-vtable obj @ {obj:#x} (d={d:.1f}m) player @ {px:.1f},{py:.1f},{pz:.1f} ===")
    try:
        raw = pm.read_bytes(obj, 0x400)
    except Exception as e:
        _dbg(f"DUMP: read fail {e}"); return
    for off in range(0, 0x400, 8):
        q = struct.unpack_from("<Q", raw, off)[0]
        f0, f1 = struct.unpack_from("<ff", raw, off)
        i0, i1 = struct.unpack_from("<ii", raw, off)
        note = ""
        # vtable / pointer? show it relative to base + try to read a string there or one hop in
        if 0x10000000000 < q < 0x7ff000000000:
            rel = q - base
            note = f" ptr(base+{rel:#x})" if 0 <= rel < 0x2000000 else " ptr"
            s = _read_cstr(pm, q)
            if not s:
                try:                       # many EQ2 name fields are ptr->ptr->char
                    s = _read_cstr(pm, struct.unpack_from("<Q", pm.read_bytes(q, 8), 0)[0])
                except Exception:
                    s = ""
            if s:
                note += f'  STR="{s}"'
        elif abs(f0) > 1e-6 and abs(f0) < 1e6 and f0 == f0:
            note = f" f0={f0:.3f}"
        _dbg(f"  +{off:#05x}  q={q:#018x}  i=({i0},{i1}) {note}")
    _dbg("DUMP === done ===")
