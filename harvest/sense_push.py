r"""Persistent in-guest sensor (runs INSIDE the VM, C:\ib\py\python.exe).

Why: the dashboard used to spawn a fresh python + re-attach pymem every 1.5s poll — the
5ms memory read was buried under ~hundreds of ms of interpreter/import/attach startup plus
the guest-exec round-trip. This process attaches ONCE and stays hot, reading at ~8 Hz and
PUSHING state to the host dashboard over outbound HTTP (same pattern as the reflex agent —
no inbound port, opsec-clean). The host serves the pushed state instantly.

Read-only (pos/heading/zone/nodes); no input, no window — safe to run anytime.
"""
from __future__ import annotations
import struct, time, math, json, urllib.request

PROC = "EverQuest2.exe"
POS_OFF = 0x1822b68
HDG_OFF = 0x1822b74
ZONE_PTR = 0x1826998
NODE_LO = 0x177bf00
NODE_HI = 0x177c100
HOST = "http://10.0.0.16:18082/api/ingest"
HZ = 8.0


def attach_live():
    """Attach to the REAL in-world EQ2 (valid player position), skipping zombie helpers."""
    import pymem, pymem.process
    last = None
    for _ in range(40):
        try:
            pm = pymem.Pymem(PROC)
            base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
            x = pm.read_float(base + POS_OFF); z = pm.read_float(base + POS_OFF + 8)
            if abs(x) > 1 and abs(x) < 1e5 and abs(z) < 1e5:
                return pm, base
            last = "pos 0,0,0 (zombie?)"
        except Exception as e:
            last = str(e)
        time.sleep(0.5)
    raise RuntimeError(f"no live EQ2: {last}")


def compass(h):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((h + 22.5) % 360 // 45)]


def read_nodes(pm, base, px, py, pz):
    mod_end = base + 0x1c00000
    out = []
    try:
        data = pm.read_bytes(base + NODE_LO, NODE_HI - NODE_LO)
    except Exception:
        return out
    for o in range(0, len(data) - 8, 8):
        ptr = struct.unpack_from("<Q", data, o)[0]
        if not (0x10000000000 < ptr < 0x7ff000000000):
            continue
        try:
            vt = struct.unpack("<Q", pm.read_bytes(ptr, 8))[0]
            if not (base <= vt < mod_end and 0x1490000 <= vt - base <= 0x14f0000):
                continue
            x, y, z = struct.unpack("<fff", pm.read_bytes(ptr + 0x60, 12))
        except Exception:
            continue
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        if abs(y - py) > 40:
            continue
        d = math.hypot(x - px, z - pz)
        if d > 220:
            continue
        out.append({"xyz": [round(x, 1), round(y, 1), round(z, 1)], "dist": round(d, 1)})
    out.sort(key=lambda n: n["dist"])
    return out


def read_state(pm, base):
    a = base + POS_OFF
    px = pm.read_float(a); py = pm.read_float(a + 4); pz = pm.read_float(a + 8)
    h = pm.read_float(base + HDG_OFF) % 360.0
    out = {"ok": True, "pos": [round(px, 3), round(py, 3), round(pz, 3)],
           "heading": round(h, 1), "compass": compass(h),
           "nodes": read_nodes(pm, base, px, py, pz), "monsters": None}
    try:
        zp = struct.unpack("<Q", pm.read_bytes(base + ZONE_PTR, 8))[0]
        zb = pm.read_bytes(zp, 64); ze = zb.find(b"\x00")
        out["zone"] = zb[:ze].decode("latin-1") if 0 < ze < 60 else None
    except Exception:
        out["zone"] = None
    return out


def push(state):
    data = json.dumps(state).encode()
    req = urllib.request.Request(HOST, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1.0).read()
    except Exception:
        pass


def main():
    pm = base = None
    period = 1.0 / HZ
    while True:
        t0 = time.time()
        try:
            if pm is None:
                pm, base = attach_live()
            st = read_state(pm, base)
            push(st)
        except Exception as e:
            pm = base = None                 # EQ2 died/relaunched — re-attach next loop
            push({"ok": False, "err": str(e)})
            time.sleep(1.0)
        dt = time.time() - t0
        if dt < period:
            time.sleep(period - dt)


if __name__ == "__main__":
    main()
