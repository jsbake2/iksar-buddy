#!/usr/bin/env python3
"""Import an OgreMapper map into our harvest grid format.

OgreMapper ships maps as a zip (or raw .lso) — a LavishNav serialization: per waypoint a
char-sized box (6 float32 min/max XYZ; center = midpoint) + named 2-way connections. Ogre reads
the same EQ2 client memory we do, so the coordinates ARE our x/z — no remapping. This pulls the
points + connectivity (which is wall-aware) into our NavGraph JSON and registers a friendly name.

Usage:
  python -m harvest.ogre_import steppes.zip --name "Ogre — The Thundering Steppes" \
         --zone "The Thundering Steppes" [--data-dir ~/ib-data/harvest]
  (--zone defaults from --name; --suffix overrides the connection suffix, default = .lso stem)
"""
import argparse, json, re, struct, sys, tempfile, zipfile
from pathlib import Path


def parse_lso(data: bytes, suffix: str):
    """LavishNav .lso bytes -> ({name:(x,z)}, {name:set(neighbour names)})."""
    n = len(data)
    suf = "." + suffix
    pts, adj, cur, i = {}, {}, None, 0
    while i < n - 2:
        ln = struct.unpack_from("<H", data, i)[0]
        if 1 <= ln <= 64 and i + 2 + ln <= n and all(32 <= c < 127 for c in data[i + 2:i + 2 + ln]):
            s = data[i + 2:i + 2 + ln].decode("latin1")
            j = i + 2 + ln
            if s.endswith(suf):                              # a connection reference
                if cur is not None:
                    adj.setdefault(cur, set()).add(s[:-len(suf)])
                i = j
                continue
            if j + 24 <= n:
                f = struct.unpack_from("<6f", data, j)
                wx, wz = f[3] - f[0], f[5] - f[2]            # box width/depth = the char box (~2)
                if (all(x == x and -100000 < x < 100000 for x in f)
                        and 0.3 < wx < 12 and 0.3 < wz < 12):
                    pts[s] = ((f[0] + f[3]) / 2.0, (f[2] + f[5]) / 2.0)
                    adj.setdefault(s, set())
                    cur = s
                    i = j + 24
                    continue
            i = j                                            # region class / flag string
        else:
            i += 1
    return pts, adj


def to_navgraph(pts: dict, adj: dict, zone: str) -> dict:
    names = list(pts.keys())
    idx = {nm: k for k, nm in enumerate(names)}
    return {"zone": zone,
            "pts": [[round(pts[nm][0], 2), round(pts[nm][1], 2)] for nm in names],
            "adj": [sorted({idx[t] for t in adj.get(nm, ()) if t in idx and t != nm}) for nm in names]}


def _read_lso(src: Path) -> tuple[bytes, str]:
    """Return (.lso bytes, stem). Accepts a .lso or a .zip containing one."""
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".lso"))
            return z.read(name), Path(name).stem
    return src.read_bytes(), src.stem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="OgreMapper .zip or .lso")
    ap.add_argument("--name", required=True, help="friendly grid name shown in the picker")
    ap.add_argument("--zone", default="", help="EQ2 zone name (default: derived from --name)")
    ap.add_argument("--suffix", default="", help="connection suffix (default: the .lso filename stem)")
    ap.add_argument("--data-dir", default=str(Path.home() / "ib-data" / "harvest"))
    a = ap.parse_args()
    raw, stem = _read_lso(Path(a.src))
    suffix = a.suffix or stem
    zone = a.zone or re.sub(r"^Ogre\s*[—-]\s*", "", a.name).strip()
    pts, adj = parse_lso(raw, suffix)
    if not pts:
        sys.exit(f"no points parsed — wrong --suffix? (tried '.{suffix}')")
    g = to_navgraph(pts, adj, zone)
    fn = "graph_" + re.sub(r"[^A-Za-z0-9]+", "_", a.name).strip("_") + ".json"
    out = Path(a.data_dir); out.mkdir(parents=True, exist_ok=True)
    (out / fn).write_text(json.dumps(g))
    mp = out / "grid_meta.json"
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    meta.setdefault("names", {})[fn] = a.name
    mp.write_text(json.dumps(meta, indent=1))
    edges = sum(len(x) for x in g["adj"]) // 2
    print(f"imported '{a.name}': {len(g['pts'])} pts, {edges} edges -> {out / fn}")


if __name__ == "__main__":
    main()
