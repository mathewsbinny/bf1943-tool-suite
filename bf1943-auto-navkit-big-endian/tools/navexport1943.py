r"""NavKit (big-endian): a BF1943 navigation build exported to RSNV, the navigation runtime's per-level file
(core/bfbc2core/rsnv.py; operation-rising-sun research/NAVIGATION_RUNTIME_DESIGN.md, sections 1.2-1.4: exporter v1).

  navexport1943.py <level> [--out <file.rsnv>] [--big-endian] [--check]

Reads the checked build (<level>.hpd), its jump links (jump_links.json), the level's ladders (ladders.json), sea level
(navkit.json) and vehicle spawns (the level's own data), and writes work\levels\<level>\navkit\<level>.rsnv
(little-endian; --big-endian for an in-guest build). --check reads it back and validates it (rsnv.World.validate) and
compares its graph with the build's (navdata).

What it holds:
  - the route graph: nodes (ground heights), edges with integer costs (1/16 m, as stored) and kinds - walk, ladder
    (up / down: a NavKit ladder link, navdata.ladder_edge) and jump (the edges of jumplinks1943's links);
  - areas with bitmaps of their walkable cells at their own pitch (0.2 m in detailed sectors, 0.8 m elsewhere),
    rasterised from their outlines, portals and border portals (even-odd, as navdiff.area_cells, at the area's pitch);
    a 4 m height grid per area; per sector, 25 x 25 buckets of 4 m listing the areas over each;
  - portals between areas (both directions; border portals resolved to the neighbour sector's area across them);
  - ladders and jump links (LADR), vehicle entry points (OFFM: board a vehicle, or mount a gun, within EntryRadius of an
    entry point; core vehicles1943.py), islands (ISLD: the main network; carriers by the water under them);
  - provenance in META: the source files' hashes, NavKit's soldier, the exporter version.
"""
import argparse
import collections
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tools')]
import soldier1943  # noqa: E402
from bfbc2core import game1943, navdata, rsnv, terrain, vehicles1943  # noqa: E402

EXPORTER_VERSION = '1.2 (2026-10-25: height grid of 1 m blocks at 0.2 m pitch, per cell at 0.8 m; tight walk edges flagged)'
HEIGHT_CELLS = {0.2: 5, 0.4: 2, 0.8: 1}   # cells per height-grid block, by pitch
COARSE_PITCH = 0.8        # m: the bitmap pitch of the build's 0.8 m areas (0.4 was tried 2026-10-25: no better)
END_MATCH = 1.0           # m: a ladder's / jump link's end and its route node lie at most this far apart
AREA_GROUND_TOP = 1.52    # m: an area's stored max_y is its highest ground plus this (navbuild.AREA_Y_MAX)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def rasterise(area, pitch, y_offset, heightfield):
    """The area's walkable cells at its pitch: (ii, jj) sample indices (centre = index x pitch) and ground heights.
    Samples inside the boundary (even-odd on xz), the height from the nearest boundary point less y_offset, or the
    terrain's where that point lies on the terrain (navdata._ground's rule)."""
    segs = [(np.asarray(l[k], float), np.asarray(l[k + 1], float)) for l in area.outline for k in range(len(l) - 1)]
    segs += [(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1, *_ in area.portals]
    segs += [(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1 in area.borders]
    if not segs:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    a = np.array([s[0] for s in segs])
    b = np.array([s[1] for s in segs])
    xs, zs = np.concatenate([a[:, 0], b[:, 0]]), np.concatenate([a[:, 2], b[:, 2]])
    i0, i1 = int(math.floor(xs.min() / pitch)) - 1, int(math.ceil(xs.max() / pitch)) + 1
    j0, j1 = int(math.floor(zs.min() / pitch)) - 1, int(math.ceil(zs.max() / pitch)) + 1
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    ii, jj = ii.ravel(), jj.ravel()
    px, pz = ii * pitch + pitch * 1.37e-3, jj * pitch + pitch * 0.71e-3      # off the 45-degree bridges' corners
    inside = np.zeros(len(ii), bool)
    for k in range(0, len(a), 256):
        ax, az, bx, bz = a[k:k + 256, 0], a[k:k + 256, 2], b[k:k + 256, 0], b[k:k + 256, 2]
        cond = (az[None, :] > pz[:, None]) != (bz[None, :] > pz[:, None])
        with np.errstate(divide='ignore', invalid='ignore'):
            xint = ax[None, :] + (pz[:, None] - az[None, :]) * (bx - ax)[None, :] / (bz - az)[None, :]
        inside ^= np.count_nonzero(cond & (px[:, None] < xint), axis=1) % 2 == 1
    ii, jj = ii[inside], jj[inside]
    if not len(ii):
        return ii, jj, np.zeros(0)
    pts = np.concatenate([a, b])
    est, near = np.empty(len(ii)), np.empty((len(ii), 2))
    for k in range(0, len(ii), 2048):
        sx, sz = ii[k:k + 2048] * pitch, jj[k:k + 2048] * pitch
        q = np.argmin((sx[:, None] - pts[None, :, 0]) ** 2 + (sz[:, None] - pts[None, :, 2]) ** 2, axis=1)
        est[k:k + 2048] = pts[q, 1] - y_offset
        near[k:k + 2048] = pts[q][:, [0, 2]]
    xz = np.stack([ii * pitch, jj * pitch], 1)
    y = navdata._ground(xz, est, heightfield, near)
    # in a large 0.8 m area on sloped terrain the nearest boundary point can be 20 m away and its height metres off
    # (navdata's 1.5 m rule then keeps the estimate: 457 of Wake's nodes fell outside their area). Where that point lies
    # on the terrain, a cell whose terrain height is inside the area's own ground range (min_y .. max_y - 1.52, DICE's
    # convention) takes the terrain's height
    t = heightfield.height(xz[:, 0], xz[:, 1])
    h = navdata.GRID / 2
    ring = np.stack([heightfield.height(near[:, 0] + dx, near[:, 1] + dz) for dx, dz in ((0, 0), (h, 0), (-h, 0), (0, h), (0, -h))])
    near_on_t = (est >= ring.min(0) - navdata.ON_TERRAIN) & (est <= ring.max(0) + navdata.ON_TERRAIN)
    in_range = (t >= area.min_y - 0.2) & (t <= area.max_y - AREA_GROUND_TOP + 0.2)
    y = np.where(near_on_t & in_range, t, y)
    return ii, jj, y


def export(lv, out=None, big_endian=False, log=print):
    W = game1943.level_work(lv)
    hpd = os.path.join(W, 'navkit', f'{lv}.hpd')
    if not os.path.isfile(hpd):
        raise FileNotFoundError(f'{hpd}: build the level first (navkit.py build {lv})')
    st = json.load(open(os.path.join(W, 'navkit', 'navkit.json'), encoding='utf-8'))
    xoff, zoff = st['sector_offsets']
    sea = float(st.get('sea_level', 0.0))
    game = game1943.find_game(None)
    g = game1943.Game(game, lv)
    hf_name = f'terrains/{lv}/{lv}.heightfield-000.res'
    hf = terrain.read(g.data(hf_name))
    soldier1943.configure()
    yoff = soldier1943.HEIGHT / 2
    navdata.Y_OFFSET = yoff
    nav = navdata.load(hpd, hf)
    log(f'{lv}: {len(nav.areas)} areas, {len(nav.node_pos)} nodes, {len(nav.edges)} edges')

    # sectors and their areas, in the build's order
    sec_ids = []
    for a in nav.areas:
        if not sec_ids or sec_ids[-1] != a.sector:
            sec_ids.append(a.sector)
    sec_index = {s: k for k, s in enumerate(sec_ids)}
    first_area = {}
    for k, a in enumerate(nav.areas):
        first_area.setdefault(a.sector, k)

    # areas: bitmaps at their pitch, height grids
    bmap, hght, area_rows, area_cells = bytearray(), [], [], []
    for k, a in enumerate(nav.areas):
        pitch = 0.2 if a.pitch < 0.5 else COARSE_PITCH
        ii, jj, y = rasterise(a, pitch, yoff, hf)
        if len(ii):
            ci0, cj0 = int(ii.min()), int(jj.min())
            w_, h_ = int(ii.max()) - ci0 + 1, int(jj.max()) - cj0 + 1
            miny, maxy = float(y.min()), float(y.max())
        else:
            ci0 = cj0 = 0
            w_ = h_ = 0
            miny, maxy = a.min_y, a.max_y
        row = (w_ + 7) >> 3
        bits = np.zeros((h_, row * 8), np.uint8)
        if len(ii):
            bits[jj - cj0, ii - ci0] = 1
        packed = np.packbits(bits.reshape(h_, row, 8)[:, :, ::-1], axis=-1).reshape(-1) if h_ else np.zeros(0, np.uint8)
        x0, z0 = ci0 * pitch, cj0 * pitch
        # height grid: blocks of HEIGHT_CELLS[pitch] x that many cells (1 m at 0.2 m, each cell at 0.8 m), each the
        # mean of its cells; readers interpolate between block centres (rsnv.World.ground)
        cpb = HEIGHT_CELLS[pitch]
        hw = max(1, -(-max(w_, 1) // cpb))
        hh = max(1, -(-max(h_, 1) // cpb))
        grid = np.full((hh, hw), 0xFFFF, np.int64)
        if len(ii):
            gi = (ii - ci0) // cpb
            gj = (jj - cj0) // cpb
            sums = collections.defaultdict(list)
            for p, q, v in zip(gi, gj, y):
                sums[(p, q)].append(v)
            for (p, q), v in sums.items():
                grid[q, p] = min(int(round((np.mean(v) - miny) * 100)), 0xFFFE)
        pmm = int(round(pitch * 1000))
        area_rows.append([x0, z0, x0 + (w_ - 1) * pitch, z0 + (h_ - 1) * pitch, miny, maxy, len(bmap), w_, h_,
                          0, 0, sec_index[a.sector], 0, 0, 0, 0, pmm, len(hght), hw, hh, cpb * pmm])
        bmap += packed.tobytes()
        hght += grid.ravel().tolist()
        area_cells.append((ii, jj, y, pitch))
    log(f'{lv}: bitmaps {len(bmap) / 1e6:.2f} MB, {sum(len(c[0]) for c in area_cells):,} cells')

    # nodes: ground heights, their areas
    gpos = nav.node_pos.copy()
    gpos[:, 1] -= yoff
    cellkey = {}
    for k, (ii, jj, y, pitch) in enumerate(area_cells):
        for i, j, v in zip(ii.tolist(), jj.tolist(), y.tolist()):
            cellkey.setdefault((round(pitch, 1), i, j), []).append((k, v))
    node_area = np.full(len(gpos), 0xFFFF, np.int64)
    for n, (x, y, z) in enumerate(gpos):
        best = None
        for pitch in sorted({0.2, COARSE_PITCH}):
            ci, cj = int(round(x / pitch)), int(round(z / pitch))
            for di in (0, 1, -1, 2, -2):
                for dj in (0, 1, -1, 2, -2):
                    for k, v in cellkey.get((pitch, ci + di, cj + dj), ()):
                        if abs(v - y) <= 1.0:
                            d = math.hypot(di, dj) * pitch + abs(v - y)
                            if best is None or d < best[0]:
                                best = (d, k)
        if best is not None:
            node_area[n] = best[1]

    # edges: CSR by source, kinds
    E = nav.edges
    order = np.lexsort((E[:, 1], E[:, 0]))
    E = E[order]
    cost = np.round(nav.edge_cost[order] * 16).astype(np.int64)
    kind = np.zeros(len(E), np.int64)
    if nav.ladder_edge is not None:
        lad = nav.ladder_edge[order]
        up = gpos[E[:, 1], 1] > gpos[E[:, 0], 1]
        kind[lad & up] = rsnv.EDGE_LADDER_UP
        kind[lad & ~up] = rsnv.EDGE_LADDER_DOWN
    jp = os.path.join(W, 'navkit', 'jump_links.json')
    jumps = json.load(open(jp, encoding='utf-8')) if os.path.isfile(jp) else []
    ladders = json.load(open(os.path.join(W, 'objects', 'ladders.json'), encoding='utf-8'))

    def nearest_node(p, maxd=END_MATCH):
        d = np.linalg.norm(gpos - np.asarray(p, float), axis=1)
        k = int(np.argmin(d))
        return (k, float(d[k])) if d[k] <= maxd else (None, float(d[k]))
    pairs = {}
    for k_e, (a_, b_) in enumerate(E):
        pairs[(int(a_), int(b_))] = k_e
    jump_ends = []
    for jl in jumps:
        na, _ = nearest_node(jl['platform'])
        nb, _ = nearest_node(jl['deck'])
        jump_ends.append((na, nb))
        for u, v in ((na, nb), (nb, na)):
            if u is not None and v is not None and (u, v) in pairs:
                kind[pairs[(u, v)]] = rsnv.EDGE_JUMP
    first_edge = np.searchsorted(E[:, 0], np.arange(len(gpos) + 1))
    log(f'{lv}: edges by kind ' + str({rsnv.EDGE_NAMES[k]: int(v) for k, v in zip(*np.unique(kind, return_counts=True))}))

    # islands
    isl = nav.node_island.astype(np.int64)
    t = hf.height(gpos[:, 0], gpos[:, 2])
    over_deep = t < sea - soldier1943.WATER_DEPTH
    island_rows = []
    for k_i, I in enumerate(nav.islands):
        members = np.nonzero(isl == k_i)[0]
        flags = (rsnv.ISLD_MAIN if k_i == 0 else 0) | (rsnv.ISLD_CARRIER if len(members) and over_deep[members].mean() > 0.5 else 0)
        island_rows.append((len(members), int(members[0]) if len(members) else 0, flags, 0, 0))

    # node flags
    depth = sea - gpos[:, 1]
    nflags = np.zeros(len(gpos), np.int64)
    nflags[(depth > 0) & (depth <= 0.5)] |= rsnv.NAUX_SHALLOW
    nflags[(depth > 0.5) & (depth <= soldier1943.WATER_DEPTH + 0.05)] |= rsnv.NAUX_WADE
    for k_e in np.nonzero((kind == rsnv.EDGE_LADDER_UP) | (kind == rsnv.EDGE_LADDER_DOWN))[0]:
        nflags[E[k_e, 0]] |= rsnv.NAUX_LADDER_END
    for k_e in np.nonzero(kind == rsnv.EDGE_JUMP)[0]:
        nflags[E[k_e, 0]] |= rsnv.NAUX_JUMP_END

    # areas: island (most of their nodes, else the reader's), nodes by area
    anod = []
    by_area = collections.defaultdict(list)
    for n, a_ in enumerate(node_area):
        if a_ != 0xFFFF:
            by_area[int(a_)].append(n)
    for k, row in enumerate(area_rows):
        ns = by_area.get(k, [])
        row[12] = collections.Counter(isl[ns].tolist()).most_common(1)[0][0] if ns else max(nav.areas[k].island, 0)
        row[14], row[15] = len(anod), len(ns)
        anod += ns

    # portals: inside sectors by target index; border portals to the neighbour sector's area across
    port = []
    per_area = collections.defaultdict(list)
    for k, a in enumerate(nav.areas):
        base = first_area[a.sector]
        for p0, p1, target in a.portals:
            per_area[k].append((p0, p1, base + int(target)))
        for p0, p1 in a.borders:
            m = (np.asarray(p0) + np.asarray(p1)) / 2
            d = np.asarray(p1) - np.asarray(p0)
            nrm = np.array([-d[2], 0.0, d[0]])
            nrm /= max(np.linalg.norm(nrm), 1e-9)
            for sgn in (1, -1):
                q = m + sgn * 0.6 * nrm
                hit = None
                for pitch in sorted({0.2, COARSE_PITCH}):
                    for kk, v in cellkey.get((pitch, int(round(q[0] / pitch)), int(round(q[2] / pitch))), ()):
                        if nav.areas[kk].sector != a.sector and abs(v - (m[1] - yoff)) <= 1.0:
                            hit = kk
                            break
                    if hit is not None:
                        break
                if hit is not None:
                    per_area[k].append((p0, p1, hit))
                    break
    for k in range(len(nav.areas)):
        area_rows[k][9] = len(port)
        area_rows[k][10] = len(per_area[k])
        for p0, p1, target in per_area[k]:
            port.append((float(p0[0]), float(p0[2]), float(p1[0]), float(p1[2]), float((p0[1] + p1[1]) / 2 - yoff),
                         k, int(target)))

    # sectors and their 4 m buckets
    sect, gbkt, glst = [], [], []
    for s in sec_ids:
        X, Z = (s >> 8) & 0xFF, s & 0xFF
        sx0, sz0 = (X + xoff) * 100.0, (Z + zoff) * 100.0
        ks = [k for k, a in enumerate(nav.areas) if a.sector == s]
        pitch_code = 0 if nav.areas[ks[0]].pitch < 0.5 else 1
        first_bucket = len(gbkt)
        buckets = [[] for _ in range(rsnv.BUCKETS * rsnv.BUCKETS)]
        for k in ks:
            r = area_rows[k]
            if r[7] == 0:
                continue
            b0 = max(int((r[0] - sx0) // rsnv.BUCKET), 0)
            b1 = min(int((r[2] - sx0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            c0 = max(int((r[1] - sz0) // rsnv.BUCKET), 0)
            c1 = min(int((r[3] - sz0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            for bj in range(c0, c1 + 1):
                for bi in range(b0, b1 + 1):
                    buckets[bj * rsnv.BUCKETS + bi].append(k)
        for lst in buckets:
            lst.sort(key=lambda k: area_rows[k][4])
            gbkt.append((len(glst), len(lst)))
            glst += lst
        sect.append((s, 0, pitch_code, sx0, sz0, sx0 + 100.0, sz0 + 100.0, first_bucket, ks[0], len(ks), 0))

    # ladders and jump links
    ladr = []
    for k, L in enumerate(ladders):
        lo, hi = L['lo'], L['hi']
        foot = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]
        top = [(lo[0] + hi[0]) / 2, hi[1], (lo[2] + hi[2]) / 2]
        fn, _ = nearest_node(foot, 3.0)
        tn, _ = nearest_node(top, 3.0)
        ladr.append((*foot, *top, 0.0, fn if fn is not None else 0xFFFFFFFF, tn if tn is not None else 0xFFFFFFFF, k))
    for k, (jl, (na, nb)) in enumerate(zip(jumps, jump_ends)):
        dx, dz = jl['deck'][0] - jl['platform'][0], jl['deck'][2] - jl['platform'][2]
        ladr.append((*jl['platform'], *jl['deck'], math.atan2(dx, dz), na if na is not None else 0xFFFFFFFF,
                     nb if nb is not None else 0xFFFFFFFF, (1 << 24) | k))

    # vehicle entry points
    from bfbc2core import level1943
    lvl = level1943.load(lv, game=game, log=lambda *a_: None)
    offm = []
    for e in lvl.entities:
        if e.category != 'vehicle spawn' or e.position is None:
            continue
        part = vehicles1943.vehicle_partition(e)
        vname = part.rsplit('/', 1)[-1] if part else ''
        vcls = vehicles1943.vehicle_class(vname)
        team = {1: 1, 2: 2}.get(e.team, 3)
        for ent in vehicles1943.entry_points(g, e):
            n, _ = nearest_node([ent['point'][0], ent['point'][1] - yoff, ent['point'][2]], ent['radius'] + 2.0)
            kind_o = rsnv.OFFM_MOUNT_STATIC if vcls == 'stationary weapon' else rsnv.OFFM_BOARD_VEHICLE
            offm.append((kind_o, team, vehicles1943.CLASSES.index(vcls), n if n is not None else 0xFFFFFFFF,
                         *ent['point'], 0.0, ent['radius'], e.index))

    node_rows = [(float(x), float(y), float(z), int(first_edge[n])) for n, (x, y, z) in enumerate(gpos)]
    node_rows.append((0.0, 0.0, 0.0, int(first_edge[-1])))
    naux = [(int(node_area[n]), sec_index.get(int(nav.node_sector[n]), 0xFFFF), int(isl[n]), int(nflags[n]))
            for n in range(len(gpos))]
    edge_rows = [(int(b_) | (int(kind[k]) << 24), min(int(cost[k]), 0xFFFF), 0) for k, (a_, b_) in enumerate(E)]
    inputs = {'hpd': hpd, 'jump_links.json': jp, 'ladders.json': os.path.join(W, 'objects', 'ladders.json'),
              'navkit.json': os.path.join(W, 'navkit', 'navkit.json')}
    meta = {'exporter': EXPORTER_VERSION, 'level': lv,
            'sources': {k: {'path': v, 'sha256': sha256(v)} for k, v in inputs.items() if os.path.isfile(v)},
            'game heightfield': {'name': hf_name, 'sha256': hashlib.sha256(g.data(hf_name)).hexdigest()},
            'soldier': {'radius': soldier1943.RADIUS, 'step': soldier1943.STEP, 'height': soldier1943.HEIGHT,
                        'slope_max': soldier1943.SLOPE_MAX, 'water_depth': soldier1943.WATER_DEPTH},
            'counts': {'nodes': len(gpos), 'edges': len(E), 'areas': len(nav.areas), 'portals': len(port),
                       'ladders': len(ladders), 'jump links': len(jumps), 'vehicle entries': len(offm),
                       'islands': len(island_rows)}}
    sections = {'SECT': sect, 'NODE': node_rows, 'NAUX': naux, 'EDGE': edge_rows,
                'AREA': [tuple(r) for r in area_rows], 'PORT': port, 'BMAP': bytes(bmap), 'HGHT': [(v,) for v in hght],
                'GBKT': gbkt, 'GLST': [(v,) for v in glst], 'ANOD': [(v,) for v in anod], 'LADR': ladr, 'OFFM': offm,
                'ISLD': island_rows, 'META': json.dumps(meta, indent=1).encode('utf-8')}
    out = out or os.path.join(W, 'navkit', f'{lv}{".be" if big_endian else ""}.rsnv')
    header = {'level': lv, 'sea_level': sea, 'radius': soldier1943.RADIUS, 'height': soldier1943.HEIGHT,
              'step': soldier1943.STEP, 'water_depth': soldier1943.WATER_DEPTH, 'hpd_sha256': meta['sources']['hpd']['sha256']}
    size = rsnv.write(out, sections, header, big_endian)
    # tight edges: walk edges with no line of sight either way over the cells just written (rsnv.World.los, the
    # runtime's rule): the graph links them, the cells leave no room (a slit, an obstacle's corner). Flagged so a
    # soldier's search can price them (EDGEF_TIGHT); the file is written again with the flags.
    w = rsnv.load(out)
    tight = set()
    for k, (a_, b_) in enumerate(E):
        if int(kind[k]) != rsnv.EDGE_WALK or k in tight:
            continue
        pa, pb = tuple(float(v) for v in w.node_pos[a_]), tuple(float(v) for v in w.node_pos[b_])
        if not w.los(pa, pb) and not w.los(pb, pa):
            tight.add(k)
            tight.update(j for j in range(int(first_edge[b_]), int(first_edge[b_ + 1])) if int(E[j][1]) == int(a_))
    sections['EDGE'] = [(t, c, rsnv.EDGEF_TIGHT if k in tight else 0) for k, (t, c, _f) in enumerate(edge_rows)]
    meta['counts']['tight walk edges'] = len(tight)
    sections['META'] = json.dumps(meta, indent=1).encode('utf-8')
    size = rsnv.write(out, sections, header, big_endian)
    walk = sum(1 for k in range(len(E)) if int(kind[k]) == rsnv.EDGE_WALK)
    log(f'{lv}: wrote {out} ({size / 1e6:.2f} MB; {len(port)} portals, {len(ladr)} ladders and jump links, '
        f'{len(offm)} vehicle entries, {len(island_rows)} islands; {len(tight)} of {walk} walk edges tight)')
    return out, nav


def check(path, nav, log=print):
    """Reads the file back: validate(), and its graph against the build's (navdata)."""
    w = rsnv.load(path)
    problems = list(w.validate())
    n = len(nav.node_pos)
    if len(w.node_pos) != n:
        problems.append(f'{len(w.node_pos)} nodes, the build has {n}')
    else:
        d = np.abs(w.node_pos[:, [0, 2]] - nav.node_pos[:, [0, 2]]).max()
        if d > 1e-3:
            problems.append(f'node positions differ by up to {d:.4f} m')
        src = np.repeat(np.arange(n), np.diff(w.node_first_edge))
        got = sorted(zip(src.tolist(), w.edge_target.tolist(), w.edge_cost.tolist()))
        want = sorted(zip(nav.edges[:, 0].tolist(), nav.edges[:, 1].tolist(),
                          np.round(nav.edge_cost * 16).astype(int).tolist()))
        if got != want:
            problems.append('edges or costs differ from the build')
    unplaced = int((w.node_area == 0xFFFF).sum())
    log(f'check: {len(problems)} problem(s); nodes without an area {unplaced} of {len(w.node_pos)}')
    for p in problems:
        log('  ' + p)
    return problems, w


def main(argv):
    ap = argparse.ArgumentParser(prog='navexport1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--out')
    ap.add_argument('--big-endian', action='store_true')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv[1:])
    path, nav = export(a.level, a.out, a.big_endian)
    if a.check:
        problems, _ = check(path, nav)
        return 1 if problems else 0
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navexport1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
