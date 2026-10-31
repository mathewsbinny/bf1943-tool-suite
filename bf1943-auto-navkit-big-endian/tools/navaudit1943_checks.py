"""The checks of navaudit1943.py (audit): a BF1943 build against its level's own data and its own consistency.

  1 required positions   capture areas (HotZone outlines), bases, soldier spawns and alternates, vehicle spawns
                         (ground, boats, aircraft, AA guns and mounted MGs), repair stations: walkable ground of the
                         build on the position's own floor within OBJECTIVE_REACH; a vehicle: walkable ground within
                         its own EntryRadius of one of its entry points (the blueprint's PlayerEntryComponentData),
                         else within VEHICLE_REACH (BOAT_REACH for a boat in the water); a base's marker is reported
                         only; the walkable share of each capture area
  2 reachability         every infantry spawn to every capture area and back on the route graph; every island
                         classified (main, vehicle-only zone, separate landmass, raised surface, gap candidate)
  3 file consistency     hpdcheck; portals two-way; border portals paired; one node per position; border-node owners;
                         crossings (touching ground of one floor on two islands)
  4 resolution           0.8 m and 0.2 m walkable ground compared in the detailed sectors
  5 layers               every surface within LAYER_WINDOW of each required position's floor: kept or not, and why
  6 water and shore      the wading band down to WATER_DEPTH; walkable ground under deeper water; jetties and piers
  7 rules                where each rule changed the kept ground (the rule-off masks), and near-misses within 20 %
  8 playable area        walkable ground inside and outside the combat area
"""
import collections
import json
import math
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-spawn-map'), os.path.join(SUITE, 'bf1943-fbrb-tool'),
                os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tools')]
import navaudit1943 as A  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import navbuild  # noqa: E402  (this variant's: core navdata puts the little-endian tools first on sys.path)
import soldier1943  # noqa: E402
from bfbc2core import game1943, materialmap1943, terrain  # noqa: E402

GROUND = {'willys-mb', 'kurogane', 'm4-sherman', 'type97-chiha'}
BOATS = {'lcvp-landingcraft'}
AIRCRAFT = {'corsair', 'zero_fighter'}
STATIONARY = {'aa_bofors', '30cal_tripod', '30cal_windowmount'}
BOAT_REACH = 6.0          # m: an LCVP in the water is boarded from ground this close (it is about 11 m long)
VEHICLE_REACH = 4.0       # m (3-D, spawn point to walkable ground): the fallback for a vehicle whose blueprint gives no
                          # entry point (a vehicle is judged first by its PlayerEntryComponentData's EntryRadius, see
                          # vehicle_entries): half a vehicle plus a step, chosen here. Mounted guns without entry
                          # points use OBJECTIVE_REACH: the gunner stands at the gun
MOUNT_DROP = 2.0          # m: a mounted gun's gunner stands on a floor up to this far below the gun's spawn point
AIRBORNE = 3.0           # m: a spawn this far above its floor is in the air, not on the ground
USABLE_SHARE = 0.25       # a capture area with less of its land on the main network than this is listed
NEAR = 0.2                # near-miss band: within 20 % of a threshold
CLUSTER_CELL = 2.0        # m: rule-firing and difference clusters are grouped on this grid
PIER_RX = re.compile(r'pier|jetty|dock|landing_?stage|boardwalk|bridge', re.I)


# ------------------------------------------------------------------------------------------------ geometry helpers

def in_poly(px, pz, poly):
    """Even-odd point-in-polygon on xz for arrays of points; poly (n, >=2) with x, z in columns 0 and -1."""
    px, pz = np.asarray(px, float), np.asarray(pz, float)
    x, z = poly[:, 0], poly[:, -1]
    x2, z2 = np.roll(x, -1), np.roll(z, -1)
    out = np.zeros(px.shape, bool)
    for a, b, c, d in zip(x, z, x2, z2):
        if b == d:
            continue
        cond = (b > pz) != (d > pz)
        xi = a + (pz - b) * (c - a) / (d - b)
        out ^= cond & (px < xi)
    return out


def poly_area(poly):
    x, z = poly[:, 0], poly[:, -1]
    return 0.5 * abs(float(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))))


def seg_dist(px, pz, a, b):
    """Distances from (px, pz) to segments a -> b ((n, 2) arrays on xz)."""
    d = b - a
    L = (d ** 2).sum(1)
    t = np.clip(((px - a[:, 0]) * d[:, 0] + (pz - a[:, 1]) * d[:, 1]) / np.maximum(L, 1e-12), 0, 1)
    return np.hypot(a[:, 0] + t * d[:, 0] - px, a[:, 1] + t * d[:, 1] - pz)


def label(mask):
    """8-connected components of a 2-D bool array: (labels with -1 off the mask, count). Label propagation with
    pointer jumping; no scipy here."""
    h, w = mask.shape
    lab = np.where(mask, np.arange(h * w).reshape(h, w), h * w)
    big = h * w
    for _ in range(100000):
        m = lab.copy()
        p = np.pad(lab, 1, constant_values=big)
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                if di or dj:
                    np.minimum(m, p[1 + dj:1 + dj + h, 1 + di:1 + di + w], out=m)
        m = np.where(mask, m, big)
        flat = m.ravel()
        # pointer jumping: follow each label to its own label's label
        for _ in range(4):
            ok = flat < big
            flat[ok] = flat[flat[ok]]
        m = flat.reshape(h, w)
        if np.array_equal(m, lab):
            break
        lab = m
    roots, inv = np.unique(lab[mask], return_inverse=True)
    out = np.full((h, w), -1, int)
    out[mask] = inv
    return out, len(roots)


def clusters_xz(x, z, weight=None, cell=CLUSTER_CELL, top=None):
    """Points grouped into 8-connected clusters on a cell grid: [{'centre', 'cells', 'points', 'bbox'}], largest
    first."""
    x, z = np.asarray(x, float), np.asarray(z, float)
    if not len(x):
        return []
    i = np.floor(x / cell).astype(int)
    j = np.floor(z / cell).astype(int)
    i0, j0 = i.min(), j.min()
    W, H = i.max() - i0 + 1, j.max() - j0 + 1
    mask = np.zeros((H, W), bool)
    mask[j - j0, i - i0] = True
    lab, n = label(mask)
    pl = lab[j - j0, i - i0]
    cnt = np.bincount(pl, minlength=n)
    sx, sz = np.bincount(pl, x, n), np.bincount(pl, z, n)
    cells = np.bincount(lab[lab >= 0], minlength=n)
    lo_x, lo_z = np.full(n, np.inf), np.full(n, np.inf)
    hi_x, hi_z = np.full(n, -np.inf), np.full(n, -np.inf)
    np.minimum.at(lo_x, pl, x)
    np.minimum.at(lo_z, pl, z)
    np.maximum.at(hi_x, pl, x)
    np.maximum.at(hi_z, pl, z)
    order = np.argsort(-cnt, kind='stable')
    if top:
        order = order[:top]
    return [{'centre': [round(float(sx[k] / cnt[k]), 1), round(float(sz[k] / cnt[k]), 1)], 'points': int(cnt[k]),
             'cells': int(cells[k]), 'bbox': [round(float(lo_x[k]), 1), round(float(lo_z[k]), 1),
                                              round(float(hi_x[k]), 1), round(float(hi_z[k]), 1)]} for k in order]


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


# ------------------------------------------------------------------------------------------------ loading

def load(lv, log=print):
    import spawnmap
    from bfbc2core import navdata
    from bfbc2core import level1943
    from bfbc2core import water as water_mod
    p = A.level_paths(lv)
    st = A.current_build(lv)       # the checked build of the current preparation only (2026-10-30 review)
    game = game1943.find_game(st.get('game'))         # the copy the level was prepared from (2026-10-30: the default)
    g = game1943.Game(game, lv)
    hf_name = f'terrains/{lv}/{lv}.heightfield-000.res'
    hf = terrain.read(nj.prepared_heightfield(st, lv, g.data(hf_name)))    # the prepared one (second review)
    mm = materialmap1943.for_level(g, lv, hf)
    soldier1943.configure()
    navdata.Y_OFFSET = soldier1943.HEIGHT / 2          # BF1943 builds store nodes and outlines 0.9 m up
    t = time.time()
    nav = navdata.load(p['hpd'], hf, cache_dir=os.path.join(p['audit'], 'cache'))
    log(f'{lv}: build read ({time.time() - t:.0f} s): {len(nav.areas)} areas, {len(nav.node_pos)} nodes')
    exact = navbuild.read_cells(p['hpd'])
    log(f'{lv}: ' + ('cells file of the build read (area heights exact)' if exact is not None else
                     'no cells file for this build: area heights from the reader cells'))
    lvl = level1943.load(lv, game=game, log=lambda *a: None)
    m = spawnmap.spawn_map(game, lv)
    wpath = os.path.join(p['objects'], 'water_mesh.res')
    water = water_mod.read_mesh(wpath) if soldier1943.has_water(st) and os.path.isfile(wpath) else None
    masks = os.path.join(p['audit'], 'masks')
    grids = {}
    hashes = A.Hashes(lv)
    # every mask must be of the samples and build on disk (their SHA-256, stored in it): 2026-10-30 review, a base mask
    # was checked only by its surface count and the rule-off masks not at all
    for which, src in (('normal', 'samples.bin'), ('detailed', 'samples_02.bin')):
        if which == 'detailed' and not A.detailed_current(st):
            log(f'{lv}: the build has no 0.2 m samples of this preparation: no detailed grid')
            continue
        path = os.path.join(masks, f'{which}_base.npz')
        if not os.path.isfile(path):
            log(f'{lv}: no {which} base mask ({path}): navaudit1943.py masks {lv}')
            continue
        why = A.mask_stale(path, src, hashes)
        if why:
            raise nj.NavKitError(f'{why}: make the masks again (navaudit1943.py masks {lv})')
        z = np.load(path)
        s = A.load_samples(os.path.join(p['samples'], src))
        if int(z['surfaces']) != len(s.y):
            raise nj.NavKitError(f'{path}: made from another sampling run ({int(z["surfaces"])} surfaces, now {len(s.y)})')
        G = Grid(s, z['keep'], hf, water, '0.2 m' if which == 'detailed' else '0.8 m')
        for v in A.VARIANTS:
            vp = os.path.join(masks, f'{which}_{v}.npz')
            if v != 'base' and os.path.isfile(vp):
                why = A.mask_stale(vp, src, hashes)
                if why:
                    log(f'{why}: left out (navaudit1943.py masks {lv} --variants {v})')
                    continue
                G.variants[v] = np.load(vp)['keep']
        grids[which] = G
    if 'normal' not in grids:
        raise nj.NavKitError(f'{lv}: no normal base mask of this build: navaudit1943.py masks {lv}')
    xoff, zoff = st['sector_offsets']
    dset = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()}
    built = {a.sector for a in nav.areas if a.detailed}
    dset_built = {((s_ >> 8) & 0xFF, s_ & 0xFF) for s_ in built}
    boxes = json.load(open(os.path.join(p['objects'], 'object_boxes.json'), encoding='utf-8'))
    inputs = {}
    for name, path in (('hpd', p['hpd']), ('samples.bin', os.path.join(p['samples'], 'samples.bin')),
                       ('samples_02.bin', os.path.join(p['samples'], 'samples_02.bin')),
                       ('object_boxes.json', os.path.join(p['objects'], 'object_boxes.json')),
                       ('ladders.json', os.path.join(p['objects'], 'ladders.json')),
                       ('water_mesh.res', wpath)):
        if os.path.isfile(path):
            inputs[name] = {'path': path, 'bytes': os.path.getsize(path), 'sha256': A.sha256(path)}
    inputs['game heightfield'] = {'name': hf_name, 'sha256': __import__('hashlib').sha256(g.data(hf_name)).hexdigest()}
    return dict(lv=lv, p=p, st=st, game=game, g=g, hf=hf, mm=mm, nav=Nav(nav, exact), lvl=lvl, map=m, water=water,
                entity_by_guid={e.guid.lower(): e for e in lvl.entities},
                grids=grids, xoff=xoff, zoff=zoff, dset=dset, dset_built=dset_built, boxes=boxes, inputs=inputs)


def sector_of(C, x, z):
    return int(math.floor(x / 100.0)) - C['xoff'], int(math.floor(z / 100.0)) - C['zoff']


def grid_at(C, x, z):
    if 'detailed' in C['grids'] and sector_of(C, x, z) in C['dset_built']:
        return C['grids']['detailed']
    return C['grids']['normal']


def objects_at(C, x, y, z, grow=0.5):
    out = []
    for b in C['boxes']:
        lo, hi = b['min'], b['max']
        if lo[0] - grow <= x <= hi[0] + grow and lo[2] - grow <= z <= hi[2] + grow and lo[1] - 3 <= y <= hi[1] + 3:
            out.append(b['bp'].split('/')[-1])
    return sorted(set(out))[:8]


# ------------------------------------------------------------------------------------------------ 1 + 5: positions

def required_positions(C):
    """[{kind, name, team, position, ...}] from the spawn map and the level (BF1943 data only)."""
    out = []
    m, lvl = C['map'], C['lvl']
    hot = [s for s in lvl.shapes if lvl.entities[s.entity].short_type == 'HotZoneEntityData' and s.kind == 'area']
    for o in m['objectives']:
        pos = o['position']
        poly = None
        for s in hot:
            if in_poly([pos[0]], [pos[2]], s.points)[0]:
                poly = s.points
                break
        out.append(dict(kind=o['role'], name=o['name'], team=o.get('initial_owner'), position=pos,
                        capture_radius=o.get('capture_radius'), capture_area=poly))
        for s in o['spawns']:
            if s['kind'] == 'soldier':
                for k, q in enumerate([s['position']] + [a for a in s.get('alternates', []) if isinstance(a, list)]):
                    out.append(dict(kind='soldier spawn', name=s['name'] + (f' alt {k}' if k else ''), team=s.get('team'),
                                    position=q, objective=o['name']))
            else:
                out.append(dict(kind='vehicle spawn', name=s['name'], vehicle=s.get('vehicle'), team=s.get('team'),
                                position=s['position'], objective=o['name'], guid=s.get('guid')))
    for s in m.get('unowned_soldier_spawns', []):
        out.append(dict(kind='soldier spawn', name=s['name'], team=s.get('team'), position=s['position'], objective=None))
    for s in m.get('unowned_vehicle_spawns', []):
        if s.get('vehicle') in (None, 'null') and all(abs(v) < 1e-6 for v in s['position']):
            continue                     # unused templates at the origin with no vehicle (Coral Sea)
        out.append(dict(kind='vehicle spawn', name=s['name'], vehicle=s.get('vehicle'), team=s.get('team'),
                        position=s['position'], objective=None, guid=s.get('guid')))
    for e in lvl.entities:
        if e.short_type == 'RepairStationEntityData' and e.position is not None:
            sh = [s for s in lvl.shapes if s.entity == e.index]
            out.append(dict(kind='repair station', name=f'repair {e.index}', team=e.team, position=[float(v) for v in e.position],
                            capture_area=sh[0].points if sh else None))
    hz_used = {id(r['capture_area']) for r in out if r['kind'] in ('flag', 'base') and r.get('capture_area') is not None}
    for s in hot:
        if id(s.points) not in hz_used:
            c = s.points.mean(0)
            out.append(dict(kind='hot zone without a flag', name=f'hotzone {s.entity}', team=None,
                            position=[float(c[0]), float(c[1]), float(c[2])], capture_area=s.points))
    for r in out:
        if r['kind'] == 'vehicle spawn':
            v = r.get('vehicle') or ''
            r['class'] = ('ground vehicle' if v in GROUND else 'boat' if v in BOATS else 'aircraft' if v in AIRCRAFT
                          else 'stationary weapon' if v in STATIONARY else 'other')
            r['entries'] = vehicle_entries(C, r.get('guid'))
    # entities left at the origin are unused placeholders (Coral Sea: 3 flags, 2 soldier spawns, 4 repair stations,
    # 16 vehicle spawns), not positions in the level: listed apart, not checked
    C['placeholders'] = [{k: v for k, v in r.items() if k != 'capture_area'} for r in out
                         if all(abs(v) < 1e-6 for v in r['position'])]
    return [r for r in out if not all(abs(v) < 1e-6 for v in r['position'])]


def floor_of(G, x, y, z):
    """The surface a position stands on: the highest at most 0.5 m above it (spawns sit on or just above their
    floor), else the lowest surface of the column. (surface index, height) or (None, None)."""
    c = G.col(x, z)
    if c is None:
        return None, None, None
    idx = list(G.surfaces(c))
    if not idx:
        return c, None, None
    below = [n for n in idx if G.s.y[n] <= y + 0.5]
    n = max(below, key=lambda n: G.s.y[n]) if below else min(idx, key=lambda n: G.s.y[n])
    # a position under the terrain (Wake Island's Beach alternate 3: 0.92 m under a stony slope, inside a boulder's
    # buried part) stands on the terrain over it, not on what is buried under it
    cx, cz = G.centre(c)
    t = float(G.hf.height(cx, cz))
    if y < t:
        on_t = [m for m in idx if abs(G.s.y[m] - t) < 0.06]
        if on_t:
            n = on_t[0]
    return c, n, float(G.s.y[n])


def nearest_cell_3d(C, p, r=8.0, main_only=False):
    """(distance, cell) of the build's walkable cell nearest a point, measured to a standing soldier's middle (ground +
    HEIGHT / 2); cells within r horizontally (and on the main network with main_only). Cells are the 0.8 m display
    grid: about 0.4 m coarse."""
    nav = C['nav'].nav
    x, y, z = p
    near = (np.abs(nav.cell_xz[:, 0] - x) <= r) & (np.abs(nav.cell_xz[:, 1] - z) <= r)
    if main_only:
        if '_cell_island' not in C:
            C['_cell_island'] = np.array([a.island for a in nav.areas])[nav.cell_area]
        near &= C['_cell_island'] == C['nav'].main
    sel = np.nonzero(near)[0]
    if not len(sel):
        return None, None
    d = np.sqrt((nav.cell_xz[sel, 0] - x) ** 2 + (nav.cell_xz[sel, 1] - z) ** 2
                + (nav.cell_y[sel] + soldier1943.HEIGHT / 2 - y) ** 2)
    k = int(np.argmin(d))
    return float(d[k]), int(sel[k])


def fine_entry(C, entries):
    """(distance, point, area info) of the nearest kept sample surface within an entry point's radius (to a standing
    soldier's middle) that lies inside a built area of its floor; main-network ground first. None when there is none."""
    nv = C['nav']
    best = None
    for ent in entries:
        x, y, z = ent['point']
        G = grid_at(C, x, z)
        s = G.s
        n = int(math.ceil(ent['radius'] / s.pitch))
        ci, cj = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        cand = []
        for j in range(cj - n, cj + n + 1):
            for i in range(ci - n, ci + n + 1):
                if not (0 <= i < s.nx and 0 <= j < s.nz):
                    continue
                for k in G.surfaces(j * s.nx + i):
                    if G.keep[k]:
                        px, py, pz = s.min_x + i * s.pitch, float(s.y[k]), s.min_z + j * s.pitch
                        d = math.sqrt((px - x) ** 2 + (pz - z) ** 2 + (py + soldier1943.HEIGHT / 2 - y) ** 2)
                        if d <= ent['radius']:
                            cand.append((d, (px, py, pz)))
        for d, (px, py, pz) in sorted(cand):
            same, _ = nv.locate(px, pz, py, r=0.5)
            if same is not None and same[0] <= 0.05:
                info = nv.area_info(same, py)
                key = (not info['main'], d)
                if best is None or key < best[0]:
                    best = (key, d, (px, py, pz), info)
                break
    return None if best is None else best[1:]


def vehicle_entries(C, spawn_guid):
    """The entry points of the vehicle a spawn makes, in the world: [{'point', 'radius', 'order'}] from the vehicle
    blueprint's PlayerEntryComponentData (Transform, EntryRadius, EntryOrderNumber), placed by the spawn's transform.
    [] when the blueprint has none. Entry points of parts that move (turrets) are taken at their rest transform."""
    from bfbc2core import dbx
    cache = C.setdefault('_entries', {})
    e = C['entity_by_guid'].get((spawn_guid or '').lower())
    if e is None or e.matrix is None:
        return []
    v = e.element.child('Vehicle')
    ref = v.attr('ref') if v is not None else None
    if not ref or ref == 'null':
        return []
    part = ref.rsplit('/', 1)[0]
    if part not in cache:
        out = []
        data = C['g'].data(part + '.dbx')
        if data:
            for inst in dbx.parse(data).instances:
                if 'PlayerEntryComponentData' not in (inst.attr('type') or ''):
                    continue
                vals = {(c.attr('name') or c.tag): c for c in inst.children or ()}
                try:
                    m = [float(t) for t in dbx.value(vals['Transform']).split('/')]
                    radius = float(dbx.value(vals['EntryRadius']))
                    order = int(dbx.value(vals.get('EntryOrderNumber'))) if vals.get('EntryOrderNumber') is not None else 0
                except (KeyError, ValueError, TypeError):
                    continue
                out.append((np.array(m[12:15]), radius, order))
        cache[part] = out
    M = np.asarray(e.matrix, float)
    return [{'point': (local @ M[:3, :3] + M[3, :3]).tolist(), 'radius': radius, 'order': order}
            for local, radius, order in cache[part]]


def check_position(C, r):
    x, y, z = r['position']
    G = grid_at(C, x, z)
    c, n, fy = floor_of(G, x, y, z)
    res = {k: v for k, v in r.items() if k != 'capture_area'}
    res['grid'] = G.label
    if fy is None:
        res.update(status='fail', reason='no sampled surface under the position', floor_y=None)
        return res
    res['floor_y'] = round(fy, 2)
    res['height_above_floor'] = round(y - fy, 2)
    reach = A.OBJECTIVE_REACH
    floor_for_nav = fy
    if r.get('class') == 'boat' and G.top[c] > fy + 0.3:
        # an LCVP in the water: boarded from ground within BOAT_REACH that is at most WATER_DEPTH under the water
        reach = BOAT_REACH
        floor_for_nav = float(G.top[c]) - soldier1943.WATER_DEPTH / 2
        res['afloat'] = True
    nv = C['nav']
    dy = A.FLOOR_DY if not res.get('afloat') else soldier1943.WATER_DEPTH / 2 + 0.6
    same, anyf = nv.locate(x, z, floor_for_nav, r=30.0, dy=dy)
    res['nearest_same_floor'] = nv.area_info(same, fy)
    res['nearest_any_floor'] = nv.area_info(anyf, fy)
    col = G.describe(c, fy - A.LAYER_WINDOW, fy + A.LAYER_WINDOW, C['mm'])
    res['column'] = col
    res['objects'] = objects_at(C, x, fy, z)
    if r.get('class') == 'aircraft' and y - fy > AIRBORNE:
        res.update(status='not required', reason=f'an aircraft spawned {y - fy:.1f} m above its floor (in the air)')
        return res
    if r['kind'] == 'base':
        # a base cannot be captured: its soldier spawns are the requirement, its marker is reported only
        res['status'] = 'info (base marker)'
        res['reason'] = (f'marker {y - fy:.1f} m above the surface under it; nearest area on any floor '
                         f'{anyf[0]:.1f} m away, {anyf[2] - fy:+.1f} m' if anyf else 'no area within 30 m')
        return res
    if r['kind'] == 'vehicle spawn' and r.get('entries'):
        # entered from walkable ground within the vehicle's own EntryRadius of one of its entry points (the game's
        # PlayerEntryComponentData); Wake Island's air-raid bunker is entered from outside its sealed room
        best = None
        for main_only in (True, False):             # main-network ground within the radius first: a bot must reach it
            best = None
            for ent in r['entries']:
                d, k = nearest_cell_3d(C, ent['point'], main_only=main_only)
                if k is not None and (best is None or d - ent['radius'] < best[0] - best[2]['radius']):
                    best = (d, k, ent)
            if best is not None and best[0] <= best[2]['radius']:
                break
        if best is not None:
            d, k, ent = best
            isl = nv.nav.areas[nv.nav.cell_area[k]].island
            res['entry'] = {'point': [round(v, 2) for v in ent['point']], 'radius': ent['radius'],
                            'seat': ent['order'], 'nearest_walkable': [round(float(nv.nav.cell_xz[k, 0]), 2),
                                                                      round(float(nv.nav.cell_y[k]), 2),
                                                                      round(float(nv.nav.cell_xz[k, 1]), 2)],
                            'distance': round(d, 2), 'island': int(isl), 'main': isl == nv.main}
            if d <= ent['radius']:
                res['status'] = 'ok' if isl == nv.main else 'ok (not main network)'
                res['note'] = f"entered within its EntryRadius {ent['radius']:g} m: walkable ground {d:.2f} m from the entry point"
                return res
            # the display cells are 0.8 m apart: for a small radius (mounted MGs: 1 m), the kept samples themselves,
            # each confirmed inside a built area
            fine = fine_entry(C, r['entries'])
            if fine is not None:
                d2, pt, info = fine
                res['entry'].update(nearest_walkable=[round(v, 2) for v in pt], distance=round(d2, 2),
                                    island=info['island'], main=info['main'])
                res['status'] = 'ok' if info['main'] else 'ok (not main network)'
                res['note'] = (f"entered within its EntryRadius {res['entry']['radius']:g} m: walkable ground {d2:.2f} m "
                               f"from the entry point (kept samples)")
                return res
            res['status'] = 'fail'
            res['reason'] = (f"no walkable ground within its EntryRadius {ent['radius']:g} m of an entry point "
                             f"(nearest {d:.2f} m)")
            return res
    if r.get('class') == 'stationary weapon':
        # the gunner stands at the gun, on its floor or below it: a window mount's gun sits in the aperture 1.3-1.6 m
        # over the bunker floor (Wake Island's beach and airfield bunkers, mil_bunker_t01v01)
        g_same, _ = nv.locate(x, z, fy - (MOUNT_DROP - A.FLOOR_DY) / 2, r=30.0, dy=(MOUNT_DROP + A.FLOOR_DY) / 2)
        if g_same is not None and g_same[0] <= A.OBJECTIVE_REACH and (same is None or same[0] > A.OBJECTIVE_REACH):
            res['gunner_floor'] = nv.area_info(g_same, fy)
            res['status'] = 'ok' if res['gunner_floor']['main'] else 'ok (not main network)'
            res['note'] = f"gunner's floor {g_same[2] - fy:+.2f} m below the mount, {g_same[0]:.1f} m away"
            return res
    vehicle = r['kind'] == 'vehicle spawn' and r.get('class') != 'stationary weapon'
    if vehicle and not res.get('afloat'):
        best = None
        for rec in (same, anyf):
            if rec is not None:
                d3 = math.hypot(rec[0], rec[2] - fy)
                best = d3 if best is None else min(best, d3)
        res['walkable_3d_distance'] = None if best is None else round(best, 2)
        if best is not None and best <= VEHICLE_REACH:
            info = res['nearest_same_floor'] if same is not None and math.hypot(same[0], same[2] - fy) <= VEHICLE_REACH \
                else res['nearest_any_floor']
            res['status'] = 'ok' if info['main'] else 'ok (not main network)'
            return res
        reach = VEHICLE_REACH
    if same is not None and same[0] <= reach:
        res['status'] = 'ok'
        if same[0] > 0:
            res['note'] = f'on the floor of an area {same[0]:.1f} m away'
        if not res['nearest_same_floor']['main'] and r['kind'] not in ('repair station',):
            res['status'] = 'ok (not main network)'
        return res
    res['status'] = 'fail'
    floor = next((s_ for s_ in col['surfaces'] if abs(s_['y'] - round(fy, 2)) < 0.011), None)
    why = []
    if same is not None:
        why.append(f'nearest area on its floor is {same[0]:.1f} m away (reach {reach:g} m)')
    else:
        why.append('no area on its floor within 30 m')
    if anyf is not None and anyf[0] <= reach:
        why.append(f'an area on another floor is {anyf[0]:.1f} m away, {anyf[2] - fy:+.2f} m')
    if floor is not None:
        if floor['kept']:
            why.append('its floor surface is kept walkable, but no area covers it (lost when areas were built)')
        else:
            why.append('floor surface not walkable: ' + '; '.join(floor['why_not']))
    res['reason'] = '; '.join(why)
    return res


def capture_share(C, r):
    """Walkable share of a capture area (or repair station): its land, and how much of it the main network covers."""
    poly = r.get('capture_area')
    if poly is None:
        rad = r.get('capture_radius') or 0
        if rad <= 0:
            return None
        t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        poly = np.stack([r['position'][0] + rad * np.cos(t), np.full(48, r['position'][1]), r['position'][2] + rad * np.sin(t)], 1)
        src = f'capture radius {rad:g} m'
    else:
        src = 'HotZone outline'
    x0, z0 = poly[:, 0].min(), poly[:, 2].min()
    x1, z1 = poly[:, 0].max(), poly[:, 2].max()
    step = 0.8
    X, Z = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(z0, z1 + step, step))
    X, Z = X.ravel(), Z.ravel()
    ins = in_poly(X, Z, poly)
    X, Z = X[ins], Z[ins]
    t = C['hf'].height(X, Z)
    G = C['grids']['normal']
    tops = np.array([G.top[c] if c is not None else -np.inf for c in (G.col(a, b) for a, b in zip(X, Z))])
    land = t >= tops - soldier1943.WATER_DEPTH
    nv = C['nav']
    cx, cz = nv.nav.cell_xz[:, 0], nv.nav.cell_xz[:, 1]
    near = (cx >= x0) & (cx <= x1) & (cz >= z0) & (cz <= z1)
    idx = np.nonzero(near)[0]
    inside = idx[in_poly(cx[idx], cz[idx], poly)]
    isl = np.array([nv.nav.areas[a].island for a in nv.nav.cell_area[inside]], int)
    main = int((isl == nv.main).sum())
    land_m2 = float(land.sum()) * step * step
    area = poly_area(poly)
    out = {'source': src, 'area_m2': round(area, 0), 'land_m2': round(land_m2, 0),
           'walkable_m2': round(len(inside) * 0.64, 0), 'main_network_m2': round(main * 0.64, 0),
           'other_islands': {int(k): int(v) for k, v in collections.Counter(isl[isl != nv.main]).most_common(5)}}
    if r['kind'] == 'flag' or land_m2 >= 0.25 * area:
        # on land: the main network's share of the land (a flag must be held from the network the bots walk)
        out['share'] = round(main * 0.64 / land_m2, 3) if land_m2 else None
        out['share_of'] = 'land, main network'
    else:
        # over water (a carrier's deck): the share of the outline covered by walkable ground on any island
        out['share'] = round(len(inside) * 0.64 / area, 3) if area else None
        out['share_of'] = 'outline, any island (over water)'
    if r['kind'] == 'base':
        out['status'] = 'info (base)'
    else:
        out['status'] = 'ok' if out['share'] is not None and out['share'] >= USABLE_SHARE else 'fail'
    return out


# ------------------------------------------------------------------------------------------------ 2: reachability

def landmass(C, cell=4.0):
    """Land a soldier can wade to: terrain at most WATER_DEPTH under the water, labelled into connected pieces on a
    4 m grid. Returns (label grid, x0, z0, cell)."""
    hf = C['hf']
    x0, z0 = float(hf.min[0]), float(hf.min[2])
    n = int((hf.max[0] - hf.min[0]) / cell)
    X, Z = np.meshgrid(x0 + (np.arange(n) + 0.5) * cell, z0 + (np.arange(n) + 0.5) * cell)
    T = hf.height(X, Z)
    level = sea_level(C)
    land = T >= level - soldier1943.WATER_DEPTH
    lab, k = label(land)
    return lab, x0, z0, cell


def sea_level(C):
    w = C['water']
    if w is None or not len(w[0]):
        # no water mesh: navkit.json's sea level, or without water the exporters' (2026-10-30: float(None) raised, then
        # -1e9 put the audit's sea elsewhere than the export's)
        return soldier1943.sea_level(C['st'], C['hf'])
    return float(np.median(w[0][:, 1]))


def landmass_at(LM, x, z):
    lab, x0, z0, cell = LM
    i = np.clip(((np.asarray(x) - x0) / cell).astype(int), 0, lab.shape[1] - 1)
    j = np.clip(((np.asarray(z) - z0) / cell).astype(int), 0, lab.shape[0] - 1)
    return lab[j, i]


def classify_islands(C, LM, positions):
    nv = C['nav']
    nav = nv.nav
    hf = C['hf']
    cells_isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    t = hf.height(nav.cell_xz[:, 0], nav.cell_xz[:, 1])
    elev = nav.cell_y - t
    lm = landmass_at(LM, nav.cell_xz[:, 0], nav.cell_xz[:, 1])
    main_lm = collections.Counter(lm[cells_isl == nv.main]).most_common(1)[0][0]
    level = sea_level(C)
    req = collections.defaultdict(list)
    for r in positions:
        a = r.get('nearest_same_floor')
        if a and a['distance'] <= max(A.OBJECTIVE_REACH, BOAT_REACH if r.get('afloat') else 0) and a['island'] >= 0:
            req[a['island']].append(f"{r['kind']}: {r['name']}")
    npos = nav.node_pos
    nt = hf.height(npos[:, 0], npos[:, 2])
    nlm = landmass_at(LM, npos[:, 0], npos[:, 2])
    out = []
    for k, isl in enumerate(nav.islands):
        sel = cells_isl == k
        if sel.any():
            xs, zs, ys = nav.cell_xz[sel, 0], nav.cell_xz[sel, 1], nav.cell_y[sel]
            tt, ll = t[sel], lm[sel]
        else:
            # areas too narrow to hold a 0.8 m cell: the island's route nodes stand for it
            ns = nav.node_island == k
            xs, zs, ys = npos[ns, 0], npos[ns, 2], nv.node_ground[ns]
            tt, ll = nt[ns], nlm[ns]
        e = ys - tt
        on_main_land = float((ll == main_lm).mean())
        over_water = float((tt < level - soldier1943.WATER_DEPTH).mean())
        raised = float((e > 1.5).mean())
        d = dict(island=k, nodes=isl.nodes, cells=int(sel.sum()), area_m2=round(float(sel.sum()) * 0.64, 0),
                 centre=[round(float(xs.mean()), 1), round(float(ys.mean()), 1), round(float(zs.mean()), 1)],
                 bbox=[round(float(xs.min()), 1), round(float(zs.min()), 1), round(float(xs.max()), 1), round(float(zs.max()), 1)],
                 on_main_landmass=round(on_main_land, 2), over_deep_water=round(over_water, 2),
                 raised_share=round(raised, 2), mean_height_above_terrain=round(float(e.mean()), 1),
                 required=req.get(k, []))
        cx, cy, cz = d['centre']
        d['objects'] = objects_at(C, cx, cy, cz, grow=2.0)
        if k == nv.main:
            d['kind'] = 'main'
        elif over_water > 0.5:
            d['kind'] = 'vehicle-only zone'
            d['linked_by'] = vehicle_link(C, d, positions)
        elif on_main_land < 0.5:
            d['kind'] = 'separate landmass (reef or islet: boat, or swimming)'
            d['linked_by'] = vehicle_link(C, d, positions)
        elif raised > 0.8 and not d['required']:
            d['kind'] = 'raised surface, no access'
        else:
            d['kind'] = 'gap candidate'
        out.append(d)
    return out


def vehicle_link(C, isl, positions):
    """What would join a vehicle-only zone to the rest: the vehicle spawns on or beside it."""
    x0, z0, x1, z1 = isl['bbox']
    vs = [r for r in positions if r['kind'] == 'vehicle spawn' and x0 - 10 <= r['position'][0] <= x1 + 10
          and z0 - 10 <= r['position'][2] <= z1 + 10]
    kinds = collections.Counter(r.get('class') for r in vs)
    out = []
    if kinds.get('boat'):
        out.append(f"boat (landing craft spawns: {kinds['boat']})")
    if kinds.get('aircraft'):
        out.append(f"plane (aircraft spawns: {kinds['aircraft']})")
    if not out:
        out.append('no vehicle spawn on it: a boat landing or a parachute drop from a plane')
    return out


def reachability(C, positions):
    nv = C['nav']
    flags = [r for r in positions if r['kind'] == 'flag']
    targets = {}
    for f in flags:
        poly = f.get('_poly')
        P = nv.nav.node_pos
        if poly is not None:
            ins = in_poly(P[:, 0], P[:, 2], poly)
        else:
            rad = max(f.get('capture_radius') or 0, 5.0)
            ins = np.hypot(P[:, 0] - f['position'][0], P[:, 2] - f['position'][2]) <= rad
        targets[f['name']] = np.nonzero(ins)[0]
    rows = []
    for r in positions:
        if r['kind'] != 'soldier spawn':
            continue
        a = r.get('nearest_same_floor')
        if r.get('floor_y') is None:
            rows.append(dict(spawn=r['name'], joined=False, reason='no floor'))
            continue
        x, _, z = r['position']
        n = nv.join(x, z, r['floor_y'], island=a['island'] if a else None)
        if n is None:
            rows.append(dict(spawn=r['name'], objective=r.get('objective'), joined=False,
                             reason='no route node within 15 m on its floor'))
            continue
        fw, bw = nv.reach(n), nv.reach(n, backward=True)
        res = dict(spawn=r['name'], objective=r.get('objective'), joined=True, node=n,
                   island=int(nv.nav.node_island[n]), main=int(nv.nav.node_island[n]) == nv.main, to={}, back={})
        for name, tg in targets.items():
            res['to'][name] = bool(len(tg) and fw[tg].any())
            res['back'][name] = bool(len(tg) and bw[tg].any())
        rows.append(res)
    return {'targets': {k: int(len(v)) for k, v in targets.items()}, 'spawns': rows}


# ------------------------------------------------------------------------------------------------ 3: consistency

def consistency(C):
    import hpdcheck
    import hpdtool
    import kymesh
    p = C['p']
    out = {'hpdcheck': hpdcheck.check(p['hpd'])}
    f = hpdtool.parse(open(p['hpd'], 'rb').read())
    one_way, dup_nodes, self_loops = [], [], []
    owners = collections.defaultdict(list)
    for s in f.sectors:
        mesh = kymesh.parse_mesh(s.mesh)
        for ai, a in enumerate(mesh.areas):
            for pt in a.portals:
                t = mesh.areas[pt.target] if 0 <= pt.target < len(mesh.areas) else None
                key = {(round(pt.p0[0], 2), round(pt.p0[2], 2)), (round(pt.p1[0], 2), round(pt.p1[2], 2))}
                back = t is not None and any(q.target == ai and {(round(q.p0[0], 2), round(q.p0[2], 2)),
                                                                 (round(q.p1[0], 2), round(q.p1[2], 2))} == key
                                             for q in t.portals)
                if not back:
                    one_way.append(f'sector {s.id:04x} area {ai} -> {pt.target} at '
                                   f'({pt.p0[0]:.1f}, {pt.p0[1]:.1f}, {pt.p0[2]:.1f})-({pt.p1[0]:.1f}, {pt.p1[2]:.1f})')
        seen = collections.defaultdict(list)
        for nd in s.nodes:
            seen[tuple(round(v, 3) for v in nd.pos)].append(nd.id & 0xFFFFFFFF)
            if nd.id & 0x80000000:
                owners[nd.id & 0xFFFFFFFF].append((s.id, not (nd.c >> 16) & 1))
        for pos, ids in seen.items():
            if len(set(ids)) > 1:
                dup_nodes.append(f'sector {s.id:04x}: {len(ids)} nodes at {pos}')
        for e in s.edges:
            if e.src == e.dst:
                self_loops.append(f'sector {s.id:04x}: edge {e.src} -> itself')
    # border portals are not matched piece by piece: see crossings (each side splits them its own way)
    own = collections.Counter(sum(1 for _s, o in v if o) for v in owners.values())
    out.update({
        'portals one-way': one_way,
        'nodes sharing a position': dup_nodes, 'self-loop edges': self_loops,
        'border node owners (copies without bit 16, per border id)': {str(k): v for k, v in sorted(own.items())},
        'border nodes': len(owners),
    })
    return out


def crossings(C):
    """Where walkable ground of one floor touches another area, can the route graph get across? Build cells side by
    side (0.8 m, within the step) in different areas whose areas lie on different islands; each such place is then
    tested on the kept samples: a flood under the build's own step (0.37 m on 0.2 m grids, LINK_STEP 0.6 m on 0.8 m)
    from the smaller island's nodes that reaches ground under the other island's nodes is a real break (a seam or
    portal the graph does not cross); otherwise the two sides are not joined on the ground either (a pocket the 0.8 m
    seeds kept that the 0.2 m samples wall off, or the reader giving a node-less area a neighbour's island).
    The exact-match count of unpaired border portals (consistency) is no test: each side places its border portals 1.2
    (0.8 m) or 0.3 m (0.2 m) inside its own edge and splits them differently - DICE's files show 21-32 % uncovered."""
    from collections import deque
    nv = C['nav']
    nav = nv.nav
    area_isl = np.array([a.island for a in nav.areas])
    area_sec = np.array([a.sector for a in nav.areas])
    key = collections.defaultdict(list)
    for k, (x, z) in enumerate(nav.cell_xz):
        key[(int(round(x / 0.8)), int(round(z / 0.8)))].append(k)
    groups = collections.defaultdict(lambda: {'pairs': 0, 'seam': False, 'xyz': []})
    for (i, j), ks in key.items():
        for di, dj in ((1, 0), (0, 1)):
            for k in ks:
                for m in key.get((i + di, j + dj), ()):
                    ia, ib = area_isl[nav.cell_area[k]], area_isl[nav.cell_area[m]]
                    if ia == ib or abs(nav.cell_y[k] - nav.cell_y[m]) > soldier1943.STEP:
                        continue
                    g = groups[tuple(sorted((int(ia), int(ib))))]
                    g['pairs'] += 1
                    g['seam'] |= bool(area_sec[nav.cell_area[k]] != area_sec[nav.cell_area[m]])
                    g['xyz'].append((float(nav.cell_xz[k, 0]), float(nav.cell_y[k]), float(nav.cell_xz[k, 1])))
    ground = nv.node_ground
    out = []
    for isl, g in groups.items():
        c = np.mean(g['xyz'], 0)
        small, big = sorted(isl, key=lambda i: nav.islands[i].nodes)
        G = grid_at(C, c[0], c[2])
        s, keep = G.s, G.keep
        step = soldier1943.STEP if s.pitch < 0.5 else 0.6

        def surf(x, y, z):
            cc = G.col(x, z)
            if cc is None:
                return None
            ks = [k for k in G.surfaces(cc) if keep[k] and abs(s.y[k] - y) < 0.5]
            return min(ks, key=lambda k: abs(s.y[k] - y)) if ks else None
        near = lambda w: [k for k in np.nonzero(nav.node_island == w)[0]
                          if math.hypot(nav.node_pos[k, 0] - c[0], nav.node_pos[k, 2] - c[2]) < 30]
        starts = {surf(nav.node_pos[k, 0], ground[k], nav.node_pos[k, 2]) for k in near(small)} - {None}
        goals = {surf(nav.node_pos[k, 0], ground[k], nav.node_pos[k, 2]) for k in near(big)} - {None}
        seen, q, hit = set(starts), deque(starts), False
        col_of = G.col_of()
        while q and not hit:
            k = q.popleft()
            cc = col_of[k]
            i, j = cc % s.nx, cc // s.nx
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    a_, b_ = i + di, j + dj
                    if not (di or dj) or not (0 <= a_ < s.nx and 0 <= b_ < s.nz):
                        continue
                    if math.hypot(s.min_x + a_ * s.pitch - c[0], s.min_z + b_ * s.pitch - c[2]) > 30:
                        continue
                    for n in G.surfaces(b_ * s.nx + a_):
                        if keep[n] and n not in seen and abs(s.y[n] - s.y[k]) <= step:
                            seen.add(n)
                            q.append(n)
                            hit |= n in goals
        out.append({'centre': [round(float(v), 1) for v in c], 'cell_pairs': g['pairs'], 'across_a_seam': g['seam'],
                    'grid': G.label, 'islands': [int(i) for i in isl],
                    'island_nodes': [nav.islands[i].nodes for i in isl], 'main': nv.main in isl,
                    'verdict': 'real break' if hit else 'not joined on the ground'})
    out.sort(key=lambda d: (d['verdict'] != 'real break', -min(d['island_nodes'])))
    return out


# ------------------------------------------------------------------------------------------------ 4: resolution

def ground_heights(G, cols):
    """Per column (of cols): the kept surface nearest the terrain within 1 m (NaN when none) and the number of
    kept surfaces."""
    s = G.s
    st, en = s.start[cols], s.start[cols + 1]
    n = en - st
    rep = np.repeat(np.arange(len(cols)), n)
    idx = (np.repeat(st, n) + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))).astype(np.int64)
    x = s.min_x + (cols % s.nx) * s.pitch
    z = s.min_z + (cols // s.nx) * s.pitch
    t = G.hf.height(x, z)
    y = s.y[idx]
    kept = G.keep[idx]
    d = np.abs(y - t[rep])
    h = np.full(len(cols), np.nan)
    ok = np.nonzero(kept & (d <= 1.0))[0]
    if len(ok):
        o = ok[np.lexsort((d[ok], rep[ok]))]           # by column, nearest the terrain first
        firsts = o[np.r_[True, rep[o][1:] != rep[o][:-1]]]
        h[rep[firsts]] = y[firsts]
    cnt = np.bincount(rep[kept], minlength=len(cols))
    return h, cnt, t


def resolution(C):
    if 'detailed' not in C['grids']:
        return {'skipped': 'no detailed mask'}
    G8, G2 = C['grids']['normal'], C['grids']['detailed']
    s8, s2 = G8.s, G2.s
    out = {'sectors': []}
    diffs8, diffs2, conn = [], [], []
    for (X, Z) in sorted(C['dset_built']):
        bx = (X + C['xoff']) * 100.0
        bz = (Z + C['zoff']) * 100.0
        i0 = int(math.ceil((bx - s8.min_x) / s8.pitch - 1e-6))
        i1 = int(math.floor((bx + 100 - s8.min_x) / s8.pitch - 1e-6))
        j0 = int(math.ceil((bz - s8.min_z) / s8.pitch - 1e-6))
        j1 = int(math.floor((bz + 100 - s8.min_z) / s8.pitch - 1e-6))
        # a sector at the sampled box's edge reaches past it (the box is the land plus a margin)
        i0, i1, j0, j1 = max(i0, 0), min(i1, s8.nx - 1), max(j0, 0), min(j1, s8.nz - 1)
        if i0 > i1 or j0 > j1:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        c8 = (jj * s8.nx + ii).ravel()
        h8, n8, t8 = ground_heights(G8, c8)
        x8 = s8.min_x + ii.ravel() * s8.pitch
        z8 = s8.min_z + jj.ravel() * s8.pitch
        frac = np.zeros(len(c8))
        any2 = np.zeros(len(c8))
        for di in (-2, -1, 0, 1):
            for dj in (-2, -1, 0, 1):
                i2 = np.round((x8 - s2.min_x) / s2.pitch).astype(int) + di
                j2 = np.round((z8 - s2.min_z) / s2.pitch).astype(int) + dj
                ok = (i2 >= 0) & (i2 < s2.nx) & (j2 >= 0) & (j2 < s2.nz)
                c2 = np.where(ok, j2 * s2.nx + i2, 0)
                h2, _n2, _ = ground_heights(G2, c2)
                h2 = np.where(ok, h2, np.nan)
                any2 += ~np.isnan(h2)
                frac += ~np.isnan(h2) & (np.isnan(h8) | (np.abs(h2 - np.nan_to_num(h8)) <= 0.3))
        frac /= 16.0
        any2 /= 16.0
        only8 = ~np.isnan(h8) & (frac < 0.25)
        only2 = np.isnan(h8) & (any2 >= 0.75)
        H, W = jj.shape
        for name, mask, store in (('0.8 m only', only8, diffs8), ('0.2 m only', only2, diffs2)):
            M = mask.reshape(H, W)
            # more than a cell row: what survives removing one row from every side
            er = M.copy()
            er[1:, :] &= M[:-1, :]
            er[:-1, :] &= M[1:, :]
            er[:, 1:] &= M[:, :-1]
            er[:, :-1] &= M[:, 1:]
            if not er.any():
                continue
            lab, n = label(er)
            for k in range(n):
                sel = (lab == k).ravel()
                cx, cz = float(x8[sel].mean()), float(z8[sel].mean())
                store.append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', centre=[round(cx, 1), round(cz, 1)],
                                  core_cells=int(sel.sum()), core_m2=round(float(sel.sum()) * 0.64, 1),
                                  terrain_y=round(float(t8[sel].mean()), 1)))
        # connectivity of the ground layer: pieces one run joins and the other separates
        m8 = (~np.isnan(h8)).reshape(H, W)
        m2 = (any2 >= 0.5).reshape(H, W)
        l8, n8c = label(m8)
        l2, n2c = label(m2)
        both = m8 & m2
        pairs = set(zip(l8[both].tolist(), l2[both].tolist()))
        by2 = collections.defaultdict(set)
        by8 = collections.defaultdict(set)
        for a_, b_ in pairs:
            by2[b_].add(a_)
            by8[a_].add(b_)
        size8 = np.bincount(l8[m8], minlength=n8c)
        size2 = np.bincount(l2[m2], minlength=n2c)
        for b_, s8set in by2.items():
            big = [a_ for a_ in s8set if size8[a_] >= 25]
            if len(big) > 1:
                sel = (l2 == b_).ravel()
                conn.append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', kind='0.2 m joins pieces 0.8 m separates',
                                 pieces=len(big), centre=[round(float(x8[sel].mean()), 1), round(float(z8[sel].mean()), 1)]))
        for a_, s2set in by8.items():
            big = [b_ for b_ in s2set if size2[b_] >= 25]
            if len(big) > 1:
                sel = (l8 == a_).ravel()
                conn.append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', kind='0.8 m joins pieces 0.2 m separates',
                                 pieces=len(big), centre=[round(float(x8[sel].mean()), 1), round(float(z8[sel].mean()), 1)]))
        out['sectors'].append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', ground_08=int(m8.sum()),
                                   ground_02=int(m2.sum()), only08=int(only8.sum()), only02=int(only2.sum())))
    for lst in (diffs8, diffs2):
        for d in lst:
            x, z = d['centre']
            d['objects'] = objects_at(C, x, d['terrain_y'], z, grow=1.0)
            c8 = G8.col(x, z)
            d['column_08'] = G8.describe(c8, d['terrain_y'] - 1.5, d['terrain_y'] + 2.5, C['mm'])['surfaces'] if c8 is not None else None
            c2 = G2.col(x, z)
            d['column_02'] = G2.describe(c2, d['terrain_y'] - 1.5, d['terrain_y'] + 2.5, C['mm'])['surfaces'] if c2 is not None else None
        lst.sort(key=lambda d: -d['core_cells'])
    out['walkable at 0.8 m, not at 0.2 m'] = diffs8
    out['walkable at 0.2 m, not at 0.8 m'] = diffs2
    out['connectivity'] = conn
    return out


# ------------------------------------------------------------------------------------------------ 6: water

def water_checks(C):
    out = {}
    nv = C['nav']
    nav = nv.nav
    level = sea_level(C)
    out['sea_level'] = round(level, 2)
    # ground under more than 1.5 m of water marked walkable (cells on terrain)
    G = C['grids']['normal']
    cols = [G.col(x, z) for x, z in nav.cell_xz]
    top = np.array([G.top[c] if c is not None else -np.inf for c in cols])
    depth = top - nav.cell_y
    deep = depth > 1.5
    out['walkable cells under more than 1.5 m of water'] = int(deep.sum())
    out['deep walkable clusters'] = clusters_xz(nav.cell_xz[deep, 0], nav.cell_xz[deep, 1], top=20)
    wade = (depth > 0) & (depth <= 1.5)
    out['walkable cells in the wading band (0-1.5 m)'] = int(wade.sum())
    # the wading band: terrain columns 0..WATER_DEPTH under the water, as the samples see them
    res = {}
    for which, G in C['grids'].items():
        s = G.s
        col = G.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        sel = np.nonzero(np.isfinite(G.top[col]) & (G.top[col] > s.y) & (G.top[col] - s.y <= soldier1943.WATER_DEPTH))[0]
        if which == 'normal' and 'detailed' in C['grids']:
            secs = np.stack([np.floor(cx[sel] / 100.0).astype(int) - C['xoff'], np.floor(cz[sel] / 100.0).astype(int) - C['zoff']], 1)
            det = np.array([tuple(v) in C['dset_built'] for v in secs]) if len(secs) else np.zeros(0, bool)
            sel = sel[~det]
        t = G.hf.height(cx[sel], cz[sel])
        terr = np.abs(s.y[sel] - t) < 0.06
        sel = sel[terr]
        flat = s.normal[sel, 1] >= math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01
        clear = (s.flags[sel] & 1) == 0
        cand = sel[flat & clear]
        lost = cand[~G.keep[cand]]
        res[G.label] = {'band surfaces (terrain, 0-1.4 m deep)': int(len(sel)), 'flat and clear': int(len(cand)),
                        'kept': int(G.keep[cand].sum()), 'not kept': int(len(lost)),
                        'not kept clusters': clusters_xz(cx[lost], cz[lost], top=15)}
        kept_deep = np.nonzero(G.keep & np.isfinite(G.top[col]) & (G.top[col] - s.y > soldier1943.WATER_DEPTH))[0]
        res[G.label]['kept surfaces deeper than 1.4 m'] = int(len(kept_deep))
        res[G.label]['kept deep clusters'] = clusters_xz(cx[kept_deep], cz[kept_deep], top=15)
    out['wading band'] = res
    # jetties, piers, bridges: the build's islands on them
    piers = []
    cells_isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    for b in C['boxes']:
        if not PIER_RX.search(b['bp']):
            continue
        lo, hi = b['min'], b['max']
        sel = (nav.cell_xz[:, 0] >= lo[0]) & (nav.cell_xz[:, 0] <= hi[0]) & (nav.cell_xz[:, 1] >= lo[2]) & \
              (nav.cell_xz[:, 1] <= hi[2]) & (nav.cell_y >= lo[1] - 0.5) & (nav.cell_y <= hi[1] + 0.5)
        isl = collections.Counter(cells_isl[sel].tolist())
        piers.append(dict(object=b['bp'].split('/')[-1], centre=[round((lo[0] + hi[0]) / 2, 1), round(hi[1], 1), round((lo[2] + hi[2]) / 2, 1)],
                          cells=int(sel.sum()), on_main=int(isl.get(nv.main, 0)),
                          other_islands={int(k): v for k, v in isl.items() if k != nv.main}))
    out['piers, jetties, bridges'] = piers
    return out


# ------------------------------------------------------------------------------------------------ 7: rules

def rules(C, positions):
    out = {'objective floor': 'no such rule in the BF1943 pipeline: navbuild.ORDER_NODES adds nodes at AI order '
                              'positions and BF1943 levels have none (AreaOrders absent); the spawn seeds '
                              '(soldier_spawns.json) only seed the flood'}
    req = np.array([r['position'] for r in positions], float) if positions else np.zeros((0, 3))
    for which, G in C['grids'].items():
        s = G.s
        col = G.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        for v, kv in G.variants.items():
            diff = np.nonzero(kv != G.keep)[0]
            removed = diff[kv[diff] & ~G.keep[diff]]          # walkable without the rule: the rule removed them
            added = diff[~kv[diff] & G.keep[diff]]            # walkable only with the rule: the rule added them
            rec = {'removed': int(len(removed)), 'added': int(len(added))}
            for name, sel in (('removed', removed), ('added', added)):
                cl = clusters_xz(cx[sel], cz[sel], top=12)
                for c_ in cl:
                    x, z = c_['centre']
                    if len(req):
                        d = np.hypot(req[:, 0] - x, req[:, 2] - z)
                        k = int(np.argmin(d))
                        c_['nearest_required'] = f"{positions[k]['kind']}: {positions[k]['name']} ({d[k]:.0f} m)"
                rec[name + ' clusters'] = cl
                if len(req) and len(sel):
                    near = []
                    for r in positions:
                        x, y, z = r['position']
                        m_ = (np.abs(cx[sel] - x) <= 20) & (np.abs(cz[sel] - z) <= 20) & (np.abs(s.y[sel] - y) <= 5)
                        if m_.any():
                            near.append(f"{r['kind']}: {r['name']}: {int(m_.sum())} surfaces within 20 m")
                    rec[name + ' near required positions'] = near
            out[f'{v} ({G.label})'] = rec
    # near-misses: within 20 % of each threshold, on surfaces that pass every other surface test
    nm = {}
    for which, G in C['grids'].items():
        s = G.s
        col = G.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        y = s.y.astype(float)
        clear = (s.flags & 1) == 0
        ang = np.degrees(np.arccos(np.clip(s.normal[:, 1], -1, 1)))
        # clearance: the next surface up in the column (stored top down)
        above = np.full(len(y), np.inf)
        first = np.zeros(len(y), bool)
        first[s.start[:-1][np.diff(s.start) > 0]] = True
        above[1:] = np.where(first[1:], np.inf, y[:-1])
        clr = above - y
        depth = np.where(np.isfinite(G.top[col]), G.top[col] - y, -np.inf)
        rec = {}
        lo_, hi_ = soldier1943.SLOPE_MAX * (1 - NEAR), soldier1943.SLOPE_MAX * (1 + NEAR)
        sel = np.nonzero(clear & (ang >= lo_) & (ang <= hi_))[0]
        rec[f'slope {lo_:.0f}-{hi_:.0f} deg (limit {soldier1943.SLOPE_MAX:g})'] = _near(G, sel, ang[sel] <= soldier1943.SLOPE_MAX + 0.6, cx, cz)
        H = soldier1943.HEIGHT
        sel = np.nonzero(clear & (clr >= H * (1 - NEAR)) & (clr <= H * (1 + NEAR)))[0]
        rec[f'headroom {H * (1 - NEAR):.2f}-{H * (1 + NEAR):.2f} m (limit {H:g})'] = _near(G, sel, clr[sel] >= H, cx, cz)
        W = soldier1943.WATER_DEPTH
        sel = np.nonzero(clear & (depth >= W * (1 - NEAR)) & (depth <= W * (1 + NEAR)))[0]
        rec[f'water depth {W * (1 - NEAR):.2f}-{W * (1 + NEAR):.2f} m (limit {W:g})'] = _near(G, sel, depth[sel] <= W, cx, cz)
        t = G.hf.height(cx, cz)
        und = t - y
        sel = np.nonzero(clear & (und >= 0.5 * (1 - NEAR)) & (und <= 0.5 * (1 + NEAR)))[0]
        rec['under the terrain 0.40-0.60 m (limit 0.5)'] = _near(G, sel, und[sel] <= 0.5, cx, cz)
        rec['step (sills, stairs)'] = step_near(G)
        nm[G.label] = rec
    out['near-misses'] = nm
    out['ladders'] = ladder_report(C)
    return out


def _near(G, sel, inside_limit, cx, cz):
    k = sel[inside_limit]
    o = sel[~inside_limit]
    return {'inside the limit': int(len(k)), 'kept of those': int(G.keep[k].sum()),
            'just past the limit': int(len(o)), 'past the limit, clusters': clusters_xz(cx[o], cz[o], top=8)}


def step_near(G):
    """Neighbouring surfaces (8 directions) on one floor whose height difference is within 20 % of the step: kept
    ground beside ground the step rule left out (dy just over STEP) and kept pairs just under it."""
    s = G.s
    nx, nz = s.nx, s.nz
    STEP = soldier1943.STEP
    lo, hi = STEP * (1 - NEAR), STEP * (1 + NEAR)
    col = G.col_of()
    kept = np.nonzero(G.keep)[0]
    clear = (s.flags & 1) == 0
    slope_ok = s.normal[:, 1] >= math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01
    over, under = [], 0
    ky = s.y[kept]
    kc = col[kept]
    for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1), (-1, 0), (0, -1), (-1, -1), (-1, 1)):
        i = kc % nx + di
        j = kc // nx + dj
        ok = (i >= 0) & (i < nx) & (j >= 0) & (j < nz)
        nc = np.where(ok, j * nx + i, 0)
        st, en = s.start[nc], s.start[nc + 1]
        for q in range(int((en - st).max()) if len(st) else 0):
            n = st + q
            v = ok & (n < en)
            n = np.where(v, n, 0)
            dy = s.y[n] - ky
            m_ = v & (np.abs(dy) >= lo) & (np.abs(dy) <= hi)
            past = m_ & (np.abs(dy) > STEP) & ~G.keep[n] & clear[n] & slope_ok[n]
            over.append(n[past])
            under += int((m_ & (np.abs(dy) <= STEP) & G.keep[n]).sum())
    over = np.unique(np.concatenate(over)) if over else np.zeros(0, int)
    cx = s.min_x + (col[over] % nx) * s.pitch
    cz = s.min_z + (col[over] // nx) * s.pitch
    return {'kept pairs stepping %.2f-%.2f m' % (lo, STEP): under // 2,
            'left out, %.2f-%.2f m above or below kept ground' % (STEP, hi): int(len(over)),
            'left out, clusters': clusters_xz(cx, cz, top=12)}


def ladder_report(C):
    nv = C['nav']
    nav = nv.nav
    out = []
    lad = json.load(open(os.path.join(C['p']['objects'], 'ladders.json'), encoding='utf-8'))
    le = nav.ladder_edge if nav.ladder_edge is not None else np.zeros(len(nav.edges), bool)
    E = nav.edges[le]
    for L in lad:
        lo, hi = L['lo'], L['hi']
        cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        foot, top = lo[1], hi[1]
        sf, af = nv.locate(cx, cz, foot, r=3.0, dy=1.0)
        st_, at_ = nv.locate(cx, cz, top, r=3.0, dy=1.5)
        links = 0
        for a, b in E:
            pa, pb = nav.node_pos[a], nav.node_pos[b]
            if min(math.hypot(pa[0] - cx, pa[2] - cz), math.hypot(pb[0] - cx, pb[2] - cz)) <= 4.0:
                links += 1
        sec = {sector_of(C, lo[0], lo[2]), sector_of(C, hi[0], hi[2])}
        out.append(dict(ladder=L['bp'].split('/')[-1], centre=[round(cx, 1), round(foot, 1), round(cz, 1)], top=round(top, 1),
                        foot_area=nv.area_info(sf, foot), top_area=nv.area_info(st_, top), ladder_edges_near=links,
                        ends_in_two_sectors=len(sec) > 1,
                        near_miss=bool(sf and A.OBJECTIVE_REACH * 0.6 <= sf[0] <= 1.8) or bool(st_ and 1.2 <= st_[0] <= 1.8)))
    return out


# ------------------------------------------------------------------------------------------------ 8: playable area

def playable(C):
    lvl = C['lvl']
    nv = C['nav']
    nav = nv.nav
    combat = [(s.kind, s.points) for s in lvl.shapes if lvl.entities[s.entity].short_type == 'CombatAreaTriggerEntityData']
    areas = [p for k, p in combat if k == 'area']
    outer = [p for k, p in combat if k == 'outer area']
    out = {'combat areas': len(areas), 'outer areas': len(outer)}
    if not areas:
        return out
    x, z = nav.cell_xz[:, 0], nav.cell_xz[:, 1]
    inside = np.zeros(len(x), bool)
    for p in areas:
        inside |= in_poly(x, z, p)
    hf = C['hf']
    lvl_sea = sea_level(C)
    # land a soldier can stand on: terrain at most WATER_DEPTH under the sea, on the build's own 0.8 m cell grid; the
    # walkable share counts each such column once (stacked floors and object tops are reported apart)
    land = set()
    for p in areas:
        X, Z = np.meshgrid(np.arange(math.floor(p[:, 0].min() / 0.8), math.ceil(p[:, 0].max() / 0.8) + 1) * 0.8,
                           np.arange(math.floor(p[:, 2].min() / 0.8), math.ceil(p[:, 2].max() / 0.8) + 1) * 0.8)
        X, Z = X.ravel(), Z.ravel()
        ins = in_poly(X, Z, p) & (hf.height(X, Z) >= lvl_sea - soldier1943.WATER_DEPTH)
        land.update(zip(np.round(X[ins] / 0.8).astype(int).tolist(), np.round(Z[ins] / 0.8).astype(int).tolist()))
    t = hf.height(x, z)
    on_ground = inside & (np.abs(nav.cell_y - t) < 0.3)
    walk_cols = set(zip(np.round(x[on_ground] / 0.8).astype(int).tolist(), np.round(z[on_ground] / 0.8).astype(int).tolist()))
    cells_isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    main_cols = set(zip(np.round(x[on_ground & (cells_isl == nv.main)] / 0.8).astype(int).tolist(),
                        np.round(z[on_ground & (cells_isl == nv.main)] / 0.8).astype(int).tolist()))
    out.update({
        'combat area m2': [round(poly_area(p), 0) for p in areas],
        'outer area m2': [round(poly_area(p), 0) for p in outer],
        'land inside (terrain down to the wading depth), m2': round(len(land) * 0.64, 0),
        'walkable ground inside (one per column), m2': round(len(walk_cols & land) * 0.64, 0),
        'main network ground inside, m2': round(len(main_cols & land) * 0.64, 0),
        'walkable share of the land inside': round(len(walk_cols & land) / len(land), 3) if land else None,
        'main network share of the land inside': round(len(main_cols & land) / len(land), 3) if land else None,
        'walkable on objects and stacked floors inside, m2': round(float((inside & ~on_ground).sum()) * 0.64, 0),
        'walkable outside, m2': round(float((~inside).sum()) * 0.64, 0),
        'walkable outside, clusters': clusters_xz(x[~inside], z[~inside], top=15),
    })
    if areas and outer and max(poly_area(p) for p in areas) < 0.05 * max(poly_area(p) for p in outer):
        out['note'] = ("the combat areas are small outlines inside the outer area (Coral Sea: four of about 11,500 m2, "
                       "at the carriers) - not the playable boundary; what they mean is not known")
    for c_ in out['walkable outside, clusters']:
        cx, cz = c_['centre']
        sel = (~inside) & (np.abs(x - cx) < 50) & (np.abs(z - cz) < 50)
        if sel.any():
            c_['islands'] = {int(k): int(v) for k, v in collections.Counter(cells_isl[sel]).most_common(3)}
            c_['objects'] = objects_at(C, cx, float(np.median(nav.cell_y[sel])), cz, grow=5.0)
    return out


# ------------------------------------------------------------------------------------------------ run

def run(lv, json_out=None, log=print):
    t0 = time.time()
    C = load(lv, log)
    positions = required_positions(C)
    for r in positions:
        if r.get('capture_area') is not None:
            r['_poly'] = r['capture_area']
    checked = []
    for r in positions:
        res = check_position(C, r)
        if r['kind'] in ('flag', 'base', 'repair station', 'hot zone without a flag'):
            res['capture_share'] = capture_share(C, r)
        if r['kind'] == 'repair station' and res.get('capture_share'):
            # a repair station is its outline (vehicles are repaired inside it); its entity's point can sit above the
            # ground (Guadalcanal: 4.4 m) - reported, not the test
            res['marker_status'], res['marker_reason'] = res['status'], res.get('reason')
            res['status'] = res['capture_share']['status']
            res['reason'] = (f"outline {res['capture_share']['share']:.0%} walkable ({res['capture_share']['share_of']})"
                             if res['capture_share']['share'] is not None else 'outline: no ground')
        res['_poly'] = r.get('_poly')
        checked.append(res)
    log(f'{lv}: 1 positions ({time.time() - t0:.0f} s)')
    LM = landmass(C)
    islands = classify_islands(C, LM, checked)
    reach = reachability(C, checked)
    log(f'{lv}: 2 reachability ({time.time() - t0:.0f} s)')
    cons = consistency(C)
    cons['crossings'] = crossings(C)
    log(f'{lv}: 3 consistency ({time.time() - t0:.0f} s)')
    resol = resolution(C)
    log(f'{lv}: 4 resolution ({time.time() - t0:.0f} s)')
    water = water_checks(C)
    log(f'{lv}: 6 water ({time.time() - t0:.0f} s)')
    rl = rules(C, checked)
    log(f'{lv}: 7 rules ({time.time() - t0:.0f} s)')
    play = playable(C)
    log(f'{lv}: 8 playable area ({time.time() - t0:.0f} s)')
    for r in checked:
        r.pop('_poly', None)
    report = {'level': lv, 'date': time.strftime('%Y-%m-%d %I:%M %p'), 'inputs': C['inputs'],
              'settings': {'OBJECTIVE_REACH': A.OBJECTIVE_REACH, 'FLOOR_DY': A.FLOOR_DY, 'LAYER_WINDOW': A.LAYER_WINDOW,
                           'BOAT_REACH': BOAT_REACH, 'USABLE_SHARE': USABLE_SHARE, 'soldier': {
                               'radius': soldier1943.RADIUS, 'step': soldier1943.STEP, 'height': soldier1943.HEIGHT,
                               'slope_max': soldier1943.SLOPE_MAX, 'water_depth': soldier1943.WATER_DEPTH}},
              'masks': {w: sorted(G.variants) for w, G in C['grids'].items()},
              'placeholders at the origin (not checked)': C['placeholders'],
              '1 positions': checked, '2 reachability': reach, '2 islands': islands, '3 consistency': cons,
              '4 resolution': resol, '6 water': water, '7 rules': rl, '8 playable area': play}
    path = json_out or os.path.join(C['p']['audit'], 'audit.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(report, f, indent=1, default=_json)
    md = os.path.splitext(path)[0] + '.md'
    with open(md, 'w', encoding='utf-8', newline='\n') as f:
        f.write(markdown(json.loads(json.dumps(report, default=_json))))
    log(f'{lv}: wrote {path} and {md} ({time.time() - t0:.0f} s)')
    return report


def _cols(col):
    """A sample column as one line per surface."""
    if not col:
        return []
    out = [f"    column {col['grid']} at ({col['column'][0]}, {col['column'][1]}): terrain {col['terrain_y']}, "
           f"water {col['water_top']}"]
    for s in col['surfaces']:
        extra = [f"slope {s['slope_deg']}", f"clearance {s['clearance']}"]
        if s.get('material'):
            extra.append(s['material'])
        if s['water_depth']:
            extra.append(f"{s['water_depth']} m under water")
        if s['below_terrain']:
            extra.append(f"{s['below_terrain']} m under the terrain")
        if s['blocked']:
            extra.append('capsule blocked')
        if s.get('rules'):
            extra.append('; '.join(f'{k} rule {v}' for k, v in s['rules'].items()))
        verdict = 'kept' if s['kept'] else 'not kept: ' + '; '.join(s['why_not'])
        out.append(f"      y {s['y']:8.2f}  {', '.join(extra)}  -> {verdict}")
    return out


def _fmt(p):
    return f"({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f})" if len(p) == 3 else f"({p[0]:.1f}, {p[1]:.1f})"


def markdown(R):
    L = [f"# Navigation audit: {R['level']}", '', f"{R['date']}. Settings: {json.dumps(R['settings'])}.",
         f"Masks: {json.dumps(R['masks'])}.", '', '## Inputs', '']
    for k, v in R['inputs'].items():
        L.append(f"- {k}: `{v.get('sha256', '')[:16]}...` {v.get('path') or v.get('name')}")
    P = R['1 positions']
    L += ['', '## 1. Required positions', '']
    cnt = collections.Counter((p['kind'], p.get('class') or '', p['status']) for p in P)
    L += ['| kind | class | status | count |', '|---|---|---|---|']
    L += [f'| {a} | {b} | {c} | {n} |' for (a, b, c), n in sorted(cnt.items())]
    ph = R.get('placeholders at the origin (not checked)') or []
    if ph:
        L += ['', f"Placeholders at the origin, not checked: {len(ph)} ("
              + ', '.join(f'{k}: {n}' for k, n in collections.Counter(p['kind'] for p in ph).items()) + ').']
    L += ['', '### Failures', '']
    for p in P:
        if p['status'] != 'fail':
            continue
        ns, na = p.get('nearest_same_floor'), p.get('nearest_any_floor')
        L.append(f"- **{p['kind']} {p.get('class') or ''} {p['name']}** at {_fmt(p['position'])}, floor {p.get('floor_y')} "
                 f"({p.get('grid')}): {p.get('reason')}")
        if ns:
            L.append(f"    nearest area on its floor: {ns['distance']} m, layer {ns['layer']}, sector {ns['sector']}, "
                     f"island {ns['island']} ({ns['island_nodes']} nodes{', main' if ns['main'] else ''})")
        if na:
            L.append(f"    nearest area on any floor: {na['distance']} m, {na['dy']:+} m, layer {na['layer']}, "
                     f"island {na['island']} ({na['island_nodes']} nodes)")
        if p.get('objects'):
            L.append(f"    objects: {', '.join(p['objects'])}")
        L += _cols(p.get('column'))
    L += ['', '### Capture areas and repair stations', '', '| position | source | area m2 | land m2 | main m2 | share | of | status |',
          '|---|---|---|---|---|---|---|---|']
    for p in P:
        c = p.get('capture_share')
        if c:
            L.append(f"| {p['kind']} {p['name']} | {c['source']} | {c['area_m2']} | {c['land_m2']} | {c['main_network_m2']} | "
                     f"{c['share']} | {c['share_of']} | {c['status']} |")
    rs = R['2 reachability']
    L += ['', '## 2. Reachability', '', f"Capture-area target nodes: {json.dumps(rs['targets'])}.", '']
    bad = [s for s in rs['spawns'] if not s.get('joined') or not all(s['to'].values()) or not all(s['back'].values())]
    L.append(f"{len(rs['spawns'])} infantry spawn points; {len(bad)} without a route to and from every capture area:")
    for s in bad:
        miss = [k for k, v in s.get('to', {}).items() if not v or not s['back'].get(k)]
        L.append(f"- {s['spawn']}: island {s.get('island')}, main {s.get('main')}; {s.get('reason') or 'no route: ' + ', '.join(miss)}")
    I = R['2 islands']
    L += ['', '### Islands', '']
    L.append(', '.join(f'{k}: {n}' for k, n in collections.Counter(i['kind'] for i in I).items()))
    for title, kinds in (('Gap candidates', ('gap candidate',)), ('Vehicle-only zones and separate landmasses',
                                                                  ('vehicle-only zone', 'separate landmass (reef or islet: boat, or swimming)')),
                         ('Raised surfaces without access', ('raised surface, no access',))):
        L += ['', f'#### {title}', '']
        for i in I:
            if i['kind'] in kinds:
                L.append(f"- island {i['island']}: {i['nodes']} nodes, {i['area_m2']} m2, centre {_fmt(i['centre'])}, "
                         f"{i['mean_height_above_terrain']} m above the terrain; objects {', '.join(i['objects'][:4]) or '-'}"
                         + (f"; required: {', '.join(i['required'])}" if i['required'] else '')
                         + (f"; linked by: {', '.join(i['linked_by'])}" if i.get('linked_by') else ''))
    c = R['3 consistency']
    L += ['', '## 3. File consistency', '']
    for k, v in c['hpdcheck'].items():
        L.append(f"- hpdcheck {k}: {len(v)}" + (f" (e.g. {v[0]})" if v else ''))
    for k in ('portals one-way', 'nodes sharing a position', 'self-loop edges'):
        L.append(f"- {k}: {len(c[k])}" + ''.join(f'\n    {x}' for x in c[k][:10]))
    L.append(f"- border nodes: {c['border nodes']}; owners per border id: {json.dumps(c['border node owners (copies without bit 16, per border id)'])}")
    cr = c.get('crossings') or []
    real = [d for d in cr if d['verdict'] == 'real break']
    L += ['', f"### Crossings: {len(cr)} places where touching ground of one floor lies on two islands; "
          f"{len(real)} real breaks (kept ground joins them under the build's step)", '']
    for d in cr:
        L.append(f"- {d['verdict']}: {_fmt(d['centre'])}, {d['cell_pairs']} cell pairs, "
                 f"{'across a sector seam' if d['across_a_seam'] else 'inside a sector'} ({d['grid']}), islands of "
                 f"{' and '.join(str(n) for n in d['island_nodes'])} nodes{' (one is the main network)' if d['main'] else ''}")
    r4 = R['4 resolution']
    L += ['', '## 4. Resolution (0.8 m against 0.2 m, detailed sectors)', '']
    if 'skipped' in r4:
        L.append(r4['skipped'])
    else:
        for k in ('walkable at 0.8 m, not at 0.2 m', 'walkable at 0.2 m, not at 0.8 m'):
            L += ['', f'### {k}: {len(r4[k])} places wider than a cell row', '']
            for d in r4[k][:40]:
                L.append(f"- sector {d['sector']} at ({d['centre'][0]}, {d['terrain_y']}, {d['centre'][1]}): {d['core_m2']} m2 core; "
                         f"objects {', '.join(d['objects'][:4]) or '-'}")
                L += _cols({'grid': '0.8 m', 'column': d['centre'], 'terrain_y': d['terrain_y'], 'water_top': None,
                            'surfaces': d['column_08'] or []})[1:]
        L += ['', f"### Connectivity: {len(r4['connectivity'])} places", '']
        L += [f"- sector {d['sector']} at {_fmt(d['centre'])}: {d['kind']} ({d['pieces']} pieces)" for d in r4['connectivity'][:40]]
    L += ['', '## 5. Layers at required positions', '']
    for p in P:
        col = p.get('column')
        if col and sum(1 for s in col['surfaces']) > 1:
            L.append(f"- {p['kind']} {p['name']} at {_fmt(p['position'])}: {p['status']}; area used: "
                     + (f"layer {p['nearest_same_floor']['layer']} at {p['nearest_same_floor']['ground_y']}" if p.get('nearest_same_floor') else 'none'))
            L += _cols(col)
    w = R['6 water']
    L += ['', '## 6. Water and shore', '', f"Sea level {w['sea_level']}. Walkable cells under more than 1.5 m of water: "
          f"{w['walkable cells under more than 1.5 m of water']}; in the wading band: {w['walkable cells in the wading band (0-1.5 m)']}."]
    for c_ in w['deep walkable clusters']:
        L.append(f"- deep: {c_['points']} cells around {_fmt(c_['centre'])}")
    for g, v in w['wading band'].items():
        L.append(f"- {g}: band surfaces {v['band surfaces (terrain, 0-1.4 m deep)']}, flat and clear {v['flat and clear']}, kept "
                 f"{v['kept']}, not kept {v['not kept']}; kept deeper than 1.4 m: {v['kept surfaces deeper than 1.4 m']}")
        for c_ in v['not kept clusters'][:10]:
            L.append(f"    not kept: {c_['points']} surfaces around {_fmt(c_['centre'])}")
    L.append('')
    for pz in w['piers, jetties, bridges']:
        L.append(f"- {pz['object']} at {_fmt(pz['centre'])}: {pz['cells']} cells, {pz['on_main']} on the main network, other islands {pz['other_islands']}")
    rl = R['7 rules']
    L += ['', '## 7. Rules', '', f"- objective floor: {rl['objective floor']}"]
    for k, v in rl.items():
        if k in ('objective floor', 'near-misses', 'ladders'):
            continue
        L.append(f"- {k}: removed {v['removed']}, added {v['added']}")
        for nm in ('removed', 'added'):
            for c_ in v.get(nm + ' clusters', [])[:6]:
                L.append(f"    {nm}: {c_['points']} surfaces around {_fmt(c_['centre'])} - {c_.get('nearest_required', '')}")
            for x in v.get(nm + ' near required positions', [])[:12]:
                L.append(f"    {nm} near {x}")
    L += ['', '### Near-misses (within 20 % of a threshold)', '']
    for g, rec in rl['near-misses'].items():
        for k, v in rec.items():
            L.append(f"- {g} {k}: {json.dumps({a: b for a, b in v.items() if 'clusters' not in a})}")
            for a, b in v.items():
                if 'clusters' in a:
                    for c_ in b[:5]:
                        L.append(f"    {c_['points']} around {_fmt(c_['centre'])}")
    L += ['', '### Ladders', '']
    for d in rl['ladders']:
        L.append(f"- {d['ladder']} at {_fmt(d['centre'])} to {d['top']}: foot area {d['foot_area'] and d['foot_area']['distance']}, "
                 f"top area {d['top_area'] and d['top_area']['distance']}, ladder edges near {d['ladder_edges_near']}"
                 + (', ends in two sectors' if d['ends_in_two_sectors'] else '') + (', near-miss' if d['near_miss'] else ''))
    pa = R['8 playable area']
    L += ['', '## 8. Playable area', '']
    L += [f"- {k}: {v}" for k, v in pa.items() if k != 'walkable outside, clusters']
    for c_ in pa.get('walkable outside, clusters', []):
        L.append(f"    outside: {c_['points']} cells around {_fmt(c_['centre'])}; islands {c_.get('islands')}; {', '.join(c_.get('objects', [])[:4])}")
    return '\n'.join(L) + '\n'


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(type(o))
