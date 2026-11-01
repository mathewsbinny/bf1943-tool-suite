"""navaudit1943_checks: the level - one grid's samples and masks (Grid), why a surface is not kept, the build's
graph (Nav)."""
import collections
import math

import numpy as np

import navaudit1943 as A  # noqa: E402
import soldier1943  # noqa: E402

from .geometry import seg_dist


# ------------------------------------------------------------------------------------------------ the level

class Grid:
    """One sampling run with its kept mask: surfaces per column, the build's verdict, water over every column."""

    def __init__(self, s, keep, hf, water, label_):
        import navbuild
        self.s, self.keep, self.hf, self.label = s, keep, hf, label_
        self.top = navbuild.water_tops(s, water[0], water[1]).astype(np.float32) if water is not None \
            else np.full(s.nx * s.nz, -np.inf, np.float32)
        self.variants = {}

    def col(self, x, z):
        s = self.s
        i, j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            return None
        return j * s.nx + i

    def centre(self, c):
        s = self.s
        return s.min_x + (c % s.nx) * s.pitch, s.min_z + (c // s.nx) * s.pitch

    def surfaces(self, c):
        return range(self.s.start[c], self.s.start[c + 1])

    def col_of(self):
        if not hasattr(self, '_col_of'):
            self._col_of = np.repeat(np.arange(self.s.nx * self.s.nz), np.diff(self.s.start))
        return self._col_of

    def describe(self, c, lo=-1e9, hi=1e9, mm=None):
        """The column's surfaces between heights lo and hi, top down: the sample columns of the report."""
        s = self.s
        x, z = self.centre(c)
        t = float(self.hf.height(x, z))
        top = float(self.top[c])
        idx = list(self.surfaces(c))
        out = []
        for q, n in enumerate(idx):
            y = float(s.y[n])
            if not (lo <= y <= hi):
                continue
            above = float(s.y[idx[q - 1]]) if q > 0 else None        # surfaces are stored top down
            ang = math.degrees(math.acos(max(-1.0, min(1.0, float(s.normal[n, 1])))))
            on_terrain = abs(y - t) < 0.06
            d = {'y': round(y, 2), 'slope_deg': round(ang, 1), 'blocked': bool(s.flags[n] & 1),
                 'kept': bool(self.keep[n]), 'clearance': None if above is None else round(above - y, 2),
                 'terrain': on_terrain, 'below_terrain': round(t - y, 2) if y < t - 0.06 else 0.0,
                 'water_depth': round(top - y, 2) if top > -1e30 and top > y else 0.0}
            if on_terrain and mm is not None:
                d['material'] = mm.name_at(x, z)[0]
            rule = [v for v, kv in self.variants.items() if kv[n] != self.keep[n]]
            if rule:
                d['rules'] = {v: ('removed it' if self.variants[v][n] else 'added it') for v in rule}
            d['why_not'] = None if d['kept'] else why_not(d)
            out.append(d)
        return {'grid': self.label, 'column': [round(x, 2), round(z, 2)], 'terrain_y': round(t, 2),
                'water_top': None if top < -1e30 else round(top, 2), 'surfaces': out}


def why_not(d):
    r = []
    if d['blocked']:
        r.append('capsule blocked')
    if d['slope_deg'] > soldier1943.SLOPE_MAX + 0.6:
        r.append(f'steeper than {soldier1943.SLOPE_MAX:g} deg')
    if d['clearance'] is not None and 0.5 <= d['clearance'] < soldier1943.HEIGHT:
        r.append('headroom under 1.8 m')
    if d['water_depth'] > soldier1943.WATER_DEPTH:
        r.append('deeper than 1.4 m under water')
    if d['below_terrain'] > 0.5:
        r.append('more than 0.5 m under the terrain')
    for v, what in (d.get('rules') or {}).items():
        if what == 'removed it':
            r.append(f'{v} rule removed it')
    return r or ['passes the surface tests; not reached by the flood from the seeds (or an object rule: forbid, '
                 'exclude, dynamic)']


class Nav:
    """The build: areas with exact outlines (point in area, distance to area), cells, route graph."""

    def __init__(self, nav, exact=None):
        self.nav = nav
        # the build's own cells (navbuild's <level>.cells.npz) per area: x, z, ground height - exact, where the
        # reader's cells (navdata, 0.8 m, heights from the nearest outline point) can be metres off on rock and slopes
        # (Coral Sea's South Base flag, 2026-10-26)
        self.exact = None
        if exact is not None and len(exact) == len(nav.areas):
            self.exact = [(np.asarray(i_, float) * p_, np.asarray(j_, float) * p_, np.asarray(y_, float))
                          for _s, p_, i_, j_, y_ in exact]
        self.segs, self.box = [], []
        for a in nav.areas:
            sg = []
            for line in a.outline:
                line = np.asarray(line, float)
                for k in range(len(line) - 1):
                    sg.append((line[k], line[k + 1]))
            for p0, p1, *_ in a.portals:
                sg.append((np.asarray(p0, float), np.asarray(p1, float)))
            for p0, p1 in a.borders:
                sg.append((np.asarray(p0, float), np.asarray(p1, float)))
            S = np.array(sg, float).reshape(-1, 2, 3) if sg else np.zeros((0, 2, 3))
            self.segs.append(S)
            self.box.append((S[:, :, 0].min(), S[:, :, 2].min(), S[:, :, 0].max(), S[:, :, 2].max()) if len(S) else None)
        self.bucket = collections.defaultdict(list)
        for k, b in enumerate(self.box):
            if b is None:
                continue
            for i in range(int(b[0] // 10), int(b[2] // 10) + 1):
                for j in range(int(b[1] // 10), int(b[3] // 10) + 1):
                    self.bucket[(i, j)].append(k)
        self.cell_key = collections.defaultdict(list)
        g = 0.8
        for n, (x, z) in enumerate(nav.cell_xz):
            self.cell_key[(int(round(x / g)), int(round(z / g)))].append(n)
        main = max(range(len(nav.islands)), key=lambda i: nav.islands[i].nodes) if nav.islands else -1
        self.main = main
        n = len(nav.node_pos)
        e = nav.edges
        order = np.argsort(e[:, 0], kind='stable')
        self.fwd_dst = e[order, 1]
        self.fwd_start = np.searchsorted(e[order, 0], np.arange(n + 1))
        order = np.argsort(e[:, 1], kind='stable')
        self.bwd_dst = e[order, 0]
        self.bwd_start = np.searchsorted(e[order, 1], np.arange(n + 1))
        self.node_ground = nav.node_pos[:, 1] - soldier1943.HEIGHT / 2

    def area_height(self, k, x, z):
        """The area's ground height near (x, z): its nearest cell's (the build's own cells where the build has its
        cells file, else the reader's 0.8 m cells), else its nearest outline point's."""
        if self.exact is not None and len(self.exact[k][0]):
            cx, cz, cy = self.exact[k]
            return float(cy[int(np.argmin((cx - x) ** 2 + (cz - z) ** 2))])
        best = None
        for di in range(-3, 4):
            for dj in range(-3, 4):
                for n in self.cell_key.get((int(round(x / 0.8)) + di, int(round(z / 0.8)) + dj), ()):
                    if self.nav.cell_area[n] == k:
                        d = (self.nav.cell_xz[n, 0] - x) ** 2 + (self.nav.cell_xz[n, 1] - z) ** 2
                        if best is None or d < best[0]:
                            best = (d, float(self.nav.cell_y[n]))
        if best is not None:
            return best[1]
        S = self.segs[k]
        P = S.reshape(-1, 3)
        q = np.argmin((P[:, 0] - x) ** 2 + (P[:, 2] - z) ** 2)
        return float(P[q, 1]) - soldier1943.HEIGHT / 2

    def candidates(self, x, z, r):
        out = set()
        for i in range(int((x - r) // 10), int((x + r) // 10) + 1):
            for j in range(int((z - r) // 10), int((z + r) // 10) + 1):
                out.update(self.bucket.get((i, j), ()))
        return out

    def locate(self, x, z, floor_y, r=30.0, dy=None):
        """Areas near (x, z): the nearest on the floor (|area ground - floor_y| <= dy) and the nearest on any floor.
        Distance 0 inside the area's outline."""
        dy = A.FLOOR_DY if dy is None else dy
        same = anyf = None
        for k in self.candidates(x, z, r):
            S = self.segs[k]
            b = self.box[k]
            if x < b[0] - r or x > b[2] + r or z < b[1] - r or z > b[3] + r:
                continue
            a2, b2 = S[:, 0][:, [0, 2]], S[:, 1][:, [0, 2]]
            cond = (a2[:, 1] > z) != (b2[:, 1] > z)
            with np.errstate(divide='ignore', invalid='ignore'):
                xi = a2[:, 0] + (z - a2[:, 1]) * (b2[:, 0] - a2[:, 0]) / (b2[:, 1] - a2[:, 1])
            inside = bool(np.count_nonzero(cond & (x < xi)) % 2)
            d = 0.0 if inside else float(seg_dist(x, z, a2, b2).min())
            if d > r:
                continue
            h = self.area_height(k, x, z)
            rec = (d, k, h)
            if anyf is None or (d, abs(h - floor_y)) < (anyf[0], abs(anyf[2] - floor_y)):
                anyf = rec
            if abs(h - floor_y) <= dy and (same is None or d < same[0]):
                same = rec
        return same, anyf

    def area_info(self, rec, floor_y=None):
        if rec is None:
            return None
        d, k, h = rec
        a = self.nav.areas[k]
        isl = a.island
        return {'distance': round(d, 2), 'area': k, 'sector': f'{a.sector:04x}', 'layer': a.layer,
                'pitch': round(a.pitch, 2), 'ground_y': round(h, 2),
                'dy': None if floor_y is None else round(h - floor_y, 2), 'island': isl,
                'island_nodes': self.nav.islands[isl].nodes if isl >= 0 else None, 'main': isl == self.main}

    def join(self, x, z, ground_y, island=None, r=15.0, dy=2.0):
        """The nearest route node on the ground's floor (and on the island, when given)."""
        P = self.nav.node_pos
        d = np.hypot(P[:, 0] - x, P[:, 2] - z)
        ok = (d <= r) & (np.abs(self.node_ground - ground_y) <= dy)
        if island is not None:
            ok2 = ok & (self.nav.node_island == island)
            if ok2.any():
                ok = ok2
        if not ok.any():
            return None
        return int(np.argmin(np.where(ok, d, np.inf)))

    def reach(self, start, backward=False):
        n = len(self.nav.node_pos)
        seen = np.zeros(n, bool)
        seen[start] = True
        st, dst = (self.bwd_start, self.bwd_dst) if backward else (self.fwd_start, self.fwd_dst)
        q = [start]
        while q:
            u = q.pop()
            for v in dst[st[u]:st[u + 1]]:
                if not seen[v]:
                    seen[v] = True
                    q.append(int(v))
        return seen
