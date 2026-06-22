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

POS_OFF = 0x1822b68            # [EverQuest2.exe + POS_OFF] = float32 X,Y,Z (validated)
PROC = "EverQuest2.exe"


def _pm():
    import pymem, pymem.process
    pm = pymem.Pymem(PROC)
    mod = pymem.process.module_from_name(pm.process_handle, PROC)
    return pm, mod.lpBaseOfDll, mod.SizeOfImage


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
    # spawn-list-derived fields — pending RE (see HARVEST.md)
    out["heading"] = None
    out["spawns"] = None        # {harvestables:[], monsters:[], players:[]}
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
