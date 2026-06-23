"""Dense waypoint GRAPH + A* — the OgreNav/ISXEQ2 wall-avoidance model in pure Python.

We do NOT path through unmapped space (that's what ran Fury into walls). Instead a human walks
the zone once, we drop a point every few metres and connect each to the previous point (+ any
prior point he physically stood near), forming a graph of KNOWN-WALKABLE space. To travel
anywhere we A* over that graph and walk the points in order — so every hop is between two spots
a human already walked between. No navmesh, no collision data, no game API.

Used host-side to BUILD the graph (record_graph.py) and in-guest by the agent to PATH (imported
by harvest_agent.py). Pure stdlib so it imports fine inside the VM's python.
"""
from __future__ import annotations
import json, math, heapq


class Graph:
    def __init__(self, zone=None):
        self.zone = zone
        self.pts: list[tuple] = []     # [(x, z), ...] world coords (EQ2 ground plane = X/Z)
        self.adj: list[list] = []      # adjacency: adj[i] = [neighbour indices]

    # ---- persistence ----
    @classmethod
    def load(cls, path):
        with open(path) as f:
            d = json.load(f)
        g = cls(d.get("zone"))
        g.pts = [tuple(p) for p in d.get("pts", [])]
        g.adj = [list(a) for a in d.get("adj", [])]
        return g

    def save(self, path):
        with open(path, "w") as f:
            json.dump({"zone": self.zone,
                       "pts": [list(p) for p in self.pts], "adj": self.adj}, f)

    def __len__(self):
        return len(self.pts)

    # ---- build ----
    def _link(self, i, j):
        if i == j:
            return
        if j not in self.adj[i]:
            self.adj[i].append(j)
        if i not in self.adj[j]:
            self.adj[j].append(i)

    def add_point(self, x, z, step=3.0, link=6.0):
        """Add (x,z) only if it's >= `step` m from the last point. Connect it to the previous
        point (always) and to any earlier point within `link` m — i.e. spots he physically
        re-walked (closes loops/intersections). Both endpoints are places a human stood, so the
        edge is real-walkable, not an invented through-wall shortcut. Returns the point index."""
        p = (round(x, 1), round(z, 1))
        if self.pts and math.hypot(p[0] - self.pts[-1][0], p[1] - self.pts[-1][1]) < step:
            return len(self.pts) - 1
        idx = len(self.pts)
        self.pts.append(p)
        self.adj.append([])
        if idx > 0:
            self._link(idx, idx - 1)
        for j in range(idx - 1):
            if math.hypot(p[0] - self.pts[j][0], p[1] - self.pts[j][1]) <= link:
                self._link(idx, j)
        return idx

    # ---- query ----
    def nearest(self, x, z):
        """(index, distance) of the graph point closest to (x,z); (-1, inf) if empty."""
        best, bd = -1, float("inf")
        for i, (px, pz) in enumerate(self.pts):
            d = math.hypot(x - px, z - pz)
            if d < bd:
                bd, best = d, i
        return best, bd

    def astar(self, si, gi):
        """List of point indices from si to gi inclusive, or [] if unreachable."""
        if si < 0 or gi < 0:
            return []
        if si == gi:
            return [si]
        pts = self.pts

        def h(i):
            return math.hypot(pts[i][0] - pts[gi][0], pts[i][1] - pts[gi][1])

        openh = [(h(si), 0.0, si)]
        came = {si: None}
        gscore = {si: 0.0}
        while openh:
            _, gc, u = heapq.heappop(openh)
            if u == gi:
                path = []
                while u is not None:
                    path.append(u)
                    u = came[u]
                return path[::-1]
            if gc > gscore.get(u, float("inf")):
                continue
            for v in self.adj[u]:
                nd = gc + math.hypot(pts[u][0] - pts[v][0], pts[u][1] - pts[v][1])
                if nd < gscore.get(v, float("inf")):
                    gscore[v] = nd
                    came[v] = u
                    heapq.heappush(openh, (nd + h(v), nd, v))
        return []

    def route(self, x0, z0, x1, z1):
        """Ordered [(x,z), ...] to walk from (x0,z0) to (x1,z1) via the graph; [] if no path."""
        s, _ = self.nearest(x0, z0)
        g, _ = self.nearest(x1, z1)
        return [self.pts[i] for i in self.astar(s, g)]
