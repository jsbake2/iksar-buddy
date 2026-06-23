"""Host-side DENSE graph recorder. Run on the host while the owner walks the harvest loop in
game; it polls the dashboard's live position (the in-guest sensor's push) and drops a graph
point every ~3 m, connecting them OgreNav-style.

Saves the graph to disk on EVERY new point (incremental) — so the walk can never be lost, and
the file can be read at any moment. Stops cleanly on SIGTERM or SIGINT.

  python -u -m harvest.record_graph /tmp/graph.json     # stop with: kill -TERM <pid>

Then deploy the saved file to C:\\ib\\graph.json in the VM for the agent to path with.
"""
from __future__ import annotations
import sys, time, json, signal, urllib.request

from harvest.nav_graph import Graph

STATE_URL = "http://127.0.0.1:18082/api/state"
STEP = 3.0          # metres between recorded points (dense -> short, wall-safe hops)
LINK = 6.0          # cross-link to ANY earlier point within this many metres. Bigger than the
                    # ~3 m step so when you COVER an area (lawn-mower passes ~4-5 m apart) the
                    # adjacent passes connect laterally -> a real navigable MESH, not parallel
                    # lanes. Keep walkable-only; a thin wall walked on both sides could bridge.

_stop = False


def _onsig(*_):
    global _stop
    _stop = True


def _state():
    with urllib.request.urlopen(STATE_URL, timeout=2.0) as r:
        return json.loads(r.read()).get("state", {})


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/graph.json"
    signal.signal(signal.SIGTERM, _onsig)
    signal.signal(signal.SIGINT, _onsig)
    g = Graph()
    last_n = 0
    print(f"RECORDING -> {out}. Walk the full loop at a normal pace. Stop with SIGTERM/Ctrl-C.",
          flush=True)
    while not _stop:
        try:
            st = _state()
            p = st.get("pos")
            if p and abs(p[0]) > 1:                     # valid in-world position (x,y,z)
                if g.zone is None and st.get("zone"):
                    g.zone = st["zone"]
                g.add_point(p[0], p[2], step=STEP, link=LINK)
                if len(g) != last_n:
                    last_n = len(g)
                    g.save(out)                         # INCREMENTAL save — never lose the walk
                    print(f"  pts={last_n:4d}  ({p[0]:.0f}, {p[2]:.0f})  {st.get('compass')}",
                          flush=True)
        except Exception as e:
            print("  poll err:", e, flush=True)
        time.sleep(0.4)
    g.save(out)
    edges = sum(len(a) for a in g.adj) // 2
    print(f"\nSAVED {len(g)} points / {edges} edges -> {out}  (zone: {g.zone})", flush=True)


if __name__ == "__main__":
    main()
