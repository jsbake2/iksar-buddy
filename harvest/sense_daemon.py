r"""In-guest SENSOR DAEMON — runs persistently inside the VM (C:\ib\py\python.exe).

Decouples the slow whole-heap memory scan from the fast act loop (owner's requirement:
"the memory scan needs to be a thread so we can be harvesting while it runs"). Two cadences:

  - FAST (~4 Hz): player position + heading — a couple of cheap reads.
  - SLOW (background thread, continuous): nearby nodes (union of harvest-node vtable
    classes) + actors (mobs/NPCs/players, vtable 0x1782848) — the ~5-6s whole-heap sweep.

Writes a single atomic cache file C:\ib\sense.json that the bot/host reads instantly, so
nav + harvest never wait on the sweep. Title 'ibsense' (setproctitle if available).
Run once at bot start (Start-Process / scheduled task); it self-restarts its scan thread.
"""
from __future__ import annotations
import json, os, struct, threading, time
import ctypes, ctypes.wintypes as w

PROC = "EverQuest2.exe"
POS_OFF = 0x1822b68
HDG_OFF = 0x1822b74
ACTOR_VT = 0x1782848            # mobs/NPCs/players; world pos at obj+0x20
# harvest-node vtable family (per-type), (vtable_off, pos_off) — confirmed via despawn diffs
NODE_CLASSES = [(0x14eb850, 0x60), (0x14a3238, 0x40), (0x14a32d8, 0x40),
                (0x1493c58, 0x40), (0x149b2f8, 0x40)]
OUT = r"C:\ib\sense.json"
RADIUS = 200.0

_cache = {"player": None, "nodes": [], "mobs": [], "scan_ts": 0, "scan_ms": 0}
_lock = threading.Lock()


def _pm():
    import pymem, pymem.process
    pm = pymem.Pymem(PROC)
    mod = pymem.process.module_from_name(pm.process_handle, PROC)
    return pm, mod.lpBaseOfDll, mod.SizeOfImage


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", w.DWORD), ("__a1", w.DWORD),
                ("RegionSize", ctypes.c_ulonglong), ("State", w.DWORD),
                ("Protect", w.DWORD), ("Type", w.DWORD), ("__a2", w.DWORD)]


def _regions(h):
    VQ = ctypes.windll.kernel32.VirtualQueryEx
    VQ.restype = ctypes.c_size_t
    addr = 0
    mbi = MBI()
    while addr < 0x7fffffffffff:
        if not VQ(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        sz = mbi.RegionSize
        if mbi.State == 0x1000 and (mbi.Protect & 0xff) == 0x04 and 0 < sz <= 256 * 1024 * 1024:
            yield mbi.BaseAddress, sz
        addr = mbi.BaseAddress + sz if sz else addr + 0x1000


def _ok(x, y, z):
    import math
    return (all(map(math.isfinite, (x, y, z))) and abs(x) > 5 and abs(z) > 5
            and abs(x) < 1e5 and abs(z) < 1e5 and abs(y) < 1e4)


def _scan_loop():
    import math
    while True:
        t0 = time.time()
        try:
            pm, base, _ = _pm()
            px = pm.read_float(base + POS_OFF)
            pz = pm.read_float(base + POS_OFF + 8)
            actor_pat = struct.pack("<Q", base + ACTOR_VT)
            node_pats = {struct.pack("<Q", base + vt): po for vt, po in NODE_CLASSES}
            nodes, mobs, seen = [], [], set()
            for b, sz in _regions(pm.process_handle):
                try:
                    buf = pm.read_bytes(b, sz)
                except Exception:
                    continue
                # actors
                i = buf.find(actor_pat)
                while i != -1:
                    if b + i not in seen and i + 0x2c <= len(buf):
                        seen.add(b + i)
                        x, y, z = struct.unpack_from("<fff", buf, i + 0x20)
                        if _ok(x, y, z):
                            d = math.hypot(x - px, z - pz)
                            if d < RADIUS:
                                mobs.append({"a": hex(b + i), "xyz": [round(x, 1), round(y, 1), round(z, 1)], "d": round(d, 1)})
                    i = buf.find(actor_pat, i + 1)
                # nodes
                for patt, po in node_pats.items():
                    i = buf.find(patt)
                    while i != -1:
                        if b + i not in seen and i + po + 12 <= len(buf):
                            seen.add(b + i)
                            x, y, z = struct.unpack_from("<fff", buf, i + po)
                            if _ok(x, y, z):
                                d = math.hypot(x - px, z - pz)
                                if d < RADIUS:
                                    nodes.append({"a": hex(b + i), "xyz": [round(x, 1), round(y, 1), round(z, 1)], "d": round(d, 1)})
                        i = buf.find(patt, i + 1)
            mobs.sort(key=lambda m: m["d"]); nodes.sort(key=lambda n: n["d"])
            with _lock:
                _cache["nodes"] = nodes[:60]
                _cache["mobs"] = mobs[:60]
                _cache["scan_ts"] = time.time()
                _cache["scan_ms"] = int((time.time() - t0) * 1000)
        except Exception as e:
            with _lock:
                _cache["scan_err"] = str(e)
            time.sleep(1.0)


def main():
    try:
        import setproctitle
        setproctitle.setproctitle("ibsense")
    except Exception:
        pass
    threading.Thread(target=_scan_loop, daemon=True).start()
    while True:
        try:
            pm, base, _ = _pm()
            a = base + POS_OFF
            player = {"ok": True,
                      "pos": [round(pm.read_float(a), 3), round(pm.read_float(a + 4), 3), round(pm.read_float(a + 8), 3)],
                      "hdg": round(pm.read_float(base + HDG_OFF) % 360, 1)}
        except Exception as e:
            player = {"ok": False, "err": str(e)}
        with _lock:
            _cache["player"] = player
            _cache["ts"] = time.time()
            snap = dict(_cache)
        try:
            tmp = OUT + ".tmp"
            with open(tmp, "w") as f:
                json.dump(snap, f)
            os.replace(tmp, OUT)
        except Exception:
            pass
        time.sleep(0.25)


if __name__ == "__main__":
    main()
