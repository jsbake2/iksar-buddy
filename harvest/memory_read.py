"""In-guest memory reader (runs INSIDE the VM via C:\\ib\\py\\python.exe).

Prints a JSON line with what we can read from the live EQ2 client. Reads are LOCAL
(pymem) so this is the only place fast enough for the eventual nav loop; the host calls
it via guest-exec for the dashboard for now.

Proven (2026-06-22): player position = module-static [EverQuest2.exe + POS_OFF].
Spawn list (harvestables/monsters/players) is NOT cracked yet — fields are emitted as
null and lit up once the RE lands (see HARVEST.md). The offset is re-derivable after a
client update via `--recalibrate X Y Z` (scan the module range for the /loc triplet).
"""
from __future__ import annotations
import json, sys

# Offsets come from the ONE shared module (REFACTOR P0.4); deploy pushes it
# alongside this file as C:\ib\agent\offsets.py. Re-derive POS via --recalibrate
# after each client update, then edit guest_agent/offsets.py.
try:
    from offsets import HDG_OFF, NODE_HI, NODE_LO, POS_OFF, PROC, ZONE_PTR  # in-guest sibling
except ImportError:
    from guest_agent.offsets import HDG_OFF, NODE_HI, NODE_LO, POS_OFF, PROC, ZONE_PTR


def _pm():
    import pymem, pymem.process
    pm = pymem.Pymem(PROC)
    mod = pymem.process.module_from_name(pm.process_handle, PROC)
    return pm, mod.lpBaseOfDll, mod.SizeOfImage


def _compass(h: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((h + 22.5) % 360 // 45)]


def _node_name(pm, p):
    """Node name via the path node -> [+0x138] -> [+0x78] -> +0x118 (cracked read-only).
    Reads 'bad locale name' when the node is too far for the name to be loaded."""
    import struct
    try:
        a = struct.unpack("<Q", pm.read_bytes(p + 0x138, 8))[0]
        b = struct.unpack("<Q", pm.read_bytes(a + 0x78, 8))[0]
        s = pm.read_bytes(b + 0x118, 64); e = s.find(b"\x00")
        nm = s[:e].decode("latin-1") if 0 < e < 60 else None
        # reject the model-asset path / unloaded placeholder — only real display names
        if (nm and nm.lower() != "bad locale name" and "/" not in nm and "." not in nm
                and "locale" not in nm.lower() and all(97 <= ord(c) <= 122 or c in " '-" for c in nm.lower())):
            return nm
    except Exception:
        pass
    return None


def read_nodes(pm, base, px, py, pz) -> list:
    """Read the live harvestable array -> nearby REAL nodes (sanity-filtered). Fast: one
    static-data read + a deref per slot. Returns [{xyz, dist, name}] sorted by distance."""
    import struct, math
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
        if abs(y - py) > 40:                 # sanity: player elevation (drops garbage)
            continue
        d = math.hypot(x - px, z - pz)
        if d > 220:
            continue
        out.append({"xyz": [round(x, 1), round(y, 1), round(z, 1)], "dist": round(d, 1),
                    "name": _node_name(pm, ptr)})
    out.sort(key=lambda n: n["dist"])
    return out


def read_state() -> dict:
    try:
        pm, base, _ = _pm()
    except Exception as e:                       # EQ2 not running / not attachable
        return {"ok": False, "err": f"attach: {e}"}
    out = {"ok": True}
    try:
        a = base + POS_OFF
        out["pos"] = [round(pm.read_float(a), 3),
                      round(pm.read_float(a + 4), 3),
                      round(pm.read_float(a + 8), 3)]
    except Exception as e:
        out["ok"] = False; out["err"] = f"pos: {e}"
    try:
        h = round(pm.read_float(base + HDG_OFF) % 360.0, 1)   # normalize to 0..360
        out["heading"] = h
        out["compass"] = _compass(h)
    except Exception:
        out["heading"] = None
    # nearby harvestable nodes — CRACKED (live array @ module+0x177bf00, gather-skill list)
    try:
        p = out.get("pos") or [0, 0, 0]
        out["nodes"] = read_nodes(pm, base, p[0], p[1], p[2])
    except Exception as e:
        out["nodes"] = []; out["nodes_err"] = str(e)
    # monsters/players still pending RE (heap spawn-manager, not a static array)
    out["monsters"] = None
    try:
        import struct
        zp = struct.unpack("<Q", pm.read_bytes(base + ZONE_PTR, 8))[0]
        zb = pm.read_bytes(zp, 64); ze = zb.find(b"\x00")
        out["zone"] = zb[:ze].decode("latin-1") if 0 < ze < 60 else None
    except Exception:
        out["zone"] = None
    return out


def recalibrate(x: float, y: float, z: float) -> dict:
    """Find the current module-relative offset of the player position by scanning ONLY the
    module range for the contiguous (x,y,z) float triplet. Use after a client update."""
    import numpy as np
    np.seterr(all="ignore")
    pm, base, size = _pm()
    data = pm.read_bytes(base, size)
    arr = np.frombuffer(data[:(len(data) // 4) * 4], dtype="<f4")
    tol = 0.05
    idx = np.where((np.abs(arr[:-2] - x) < tol) & (np.abs(arr[1:-1] - y) < tol)
                   & (np.abs(arr[2:] - z) < tol))[0]
    offs = [hex(int(i) * 4) for i in idx[:10]]
    return {"ok": bool(len(idx)), "offsets": offs,
            "primary": offs[0] if offs else None}


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--recalibrate":
        print(json.dumps(recalibrate(float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]))))
    else:
        print(json.dumps(read_state()))
