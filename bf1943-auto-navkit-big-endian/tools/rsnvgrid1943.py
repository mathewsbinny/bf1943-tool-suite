r"""NavKit (big-endian): an RSNV world (core rsnv.py) from cells on a grid - the vehicle worlds' writer
(groundnav1943.py: jeeps and tanks; seanav1943.py: the landing craft; airnav1943.py: planes).

  sets = [CellSet(pitch, x0, z0, i, j, y, links)]      cells: grid indices, ground heights, 4-neighbour links
  write_world(lv, cls, sets, nodes, edges, offm, out, sector_offsets, header, meta)

A world holds what the soldier's export holds, built from cells instead of a NavKit .hpd:
  - SECT: the 100 m sectors (the soldier build's sector grid: XOffset / ZOffset from navkit.json), one record per
    sector and cell set; `layer` is the class id (vehicleclasses1943.CLASS_IDS);
  - AREA: per sector and cell set, flood-filled pieces of linked cells with at most one cell per column (so the
    bitmap and height grid are unique); bitmaps at the set's pitch, one height block per cell (hstep = pitch);
  - PORT: one record per pair of neighbouring areas (both directions), the shared cell edges' extent;
  - GBKT / GLST: 25 x 25 buckets of 4 m per sector record, areas by their bounding box, lowest first;
  - NODE / NAUX / EDGE / ANOD: route nodes at their cells' centres and ground, edges both ways unless one-way,
    integer costs in 1/16 m (the 3-D length unless given), kinds as given (walk = drive; drop for a launch);
  - ISLD: the graph's islands (MAIN on the largest, flags as given);
  - OFFM: transfers as given (kind, team mask, vehicle class, node, the soldier-side point, yaw, radius, aux);
  - META: exporter version, sources and their hashes, the class's parameters.
"""
import collections
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
from bfbc2core import rsnv  # noqa: E402

EXPORTER_VERSION = '1.0 (2026-10-26: vehicle worlds from cells)'
OFFM_ALIGHT_ZONE = 2         # design 1.3's OFFM kinds beside rsnv.py's BOARD_VEHICLE (1) and MOUNT_STATIC (5)
ALIGHT_TARGET_REACH = 60.0   # m: a ground vehicle's alight node lies within this of its base or flag
ALIGHT_REACH = 6.0           # m from the alight node to the soldier node


def soldier_node(S, p, reach, dy=3.5, main_only=False):
    """The soldier node nearest p horizontally within reach whose ground is within dy of p's height (lowest index on
    a tie; main_only: on the main island), or None - the rule the runtime resolves a transfer with again
    (core rsnvtrip.resolve)."""
    d = np.hypot(S.node_pos[:, 0] - p[0], S.node_pos[:, 2] - p[2])
    ok = (d <= reach) & (np.abs(S.node_pos[:, 1] - p[1]) <= dy)
    if main_only:
        ok &= S.node_island == 0
    if not ok.any():
        return None
    k = np.nonzero(ok)[0]
    return int(k[np.argmin(d[k])])
STRAIGHT = ((1, 0), (-1, 0), (0, 1), (0, -1))


class CellSet:
    """Cells of one grid: indices i, j (centre x0 + i * pitch, z0 + j * pitch), ground y, and links
    {(k, di, dj): n} between straight neighbours (only those are used for areas)."""

    def __init__(self, pitch, x0, z0, i, j, y, links, name=''):
        self.pitch, self.x0, self.z0 = float(pitch), float(x0), float(z0)
        self.i, self.j = np.asarray(i, np.int64), np.asarray(j, np.int64)
        self.y = np.asarray(y, np.float64)
        self.links = links
        self.name = name
        self.n = len(self.i)

    def xz(self, k):
        return self.x0 + int(self.i[k]) * self.pitch, self.z0 + int(self.j[k]) * self.pitch


def grid_links(mask_index, di_dj=STRAIGHT):
    """Links between straight neighbours of a 2-D index grid (-1: no cell): {(k, di, dj): n}."""
    nz, nx = mask_index.shape
    out = {}
    for di, dj in di_dj:
        dst = mask_index[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)]
        src = mask_index[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
        m = (src >= 0) & (dst >= 0)
        for a, b in zip(src[m].tolist(), dst[m].tolist()):
            out[(a, di, dj)] = b
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def areas_of(cs, sector_offsets):
    """[(sector id, [cell indices])] - flood fills of linked cells within each sector, one cell per column."""
    xoff, zoff = sector_offsets
    x = cs.x0 + cs.i * cs.pitch
    z = cs.z0 + cs.j * cs.pitch
    sx = np.floor(x / 100.0).astype(np.int64) - xoff
    sz = np.floor(z / 100.0).astype(np.int64) - zoff
    if cs.n and (sx.min() < 0 or sz.min() < 0 or sx.max() > 255 or sz.max() > 255):
        raise ValueError(f'{cs.name}: cells outside the sector grid (X {sx.min()}..{sx.max()}, Z {sz.min()}..{sz.max()})')
    sec = sx * 256 + sz
    order = np.lexsort((cs.i, cs.j, sec))
    area_of = np.full(cs.n, -1, np.int64)
    out = []
    links = cs.links
    for k0 in order.tolist():
        if area_of[k0] >= 0:
            continue
        s = int(sec[k0])
        aid = len(out)
        cols = {(int(cs.i[k0]), int(cs.j[k0]))}
        members = [k0]
        area_of[k0] = aid
        q = collections.deque([k0])
        while q:
            k = q.popleft()
            for di, dj in STRAIGHT:
                n = links.get((k, di, dj))
                if n is None or area_of[n] >= 0 or sec[n] != s:
                    continue
                c = (int(cs.i[n]), int(cs.j[n]))
                if c in cols:
                    continue
                cols.add(c)
                area_of[n] = aid
                members.append(n)
                q.append(n)
        out.append((s, members))
    return out, area_of


def write_world(lv, cls, sets, nodes, edges, offm, out, sector_offsets, header, meta, island_flags=None,
                node_flags=None, big_endian=False, flag_tight=True, log=print):
    """nodes: [(set index, cell index)]; edges: [(a, b, kind, cost_m or None, both_ways)]; offm: rows as rsnv.SCHEMA
    ['OFFM'] with the node as a node index; header: sea_level, radius, height, step, water_depth, source sha256 (hex);
    island_flags: {island: flags}; node_flags: [u16 per node]. Returns stats."""
    import vehicleclasses1943 as vc
    layer = vc.CLASS_IDS[cls]
    xoff, zoff = sector_offsets
    # areas per set, grouped by sector
    per_set = [areas_of(cs, sector_offsets) for cs in sets]
    sect_keys = sorted({(s, si) for si, (lst, _) in enumerate(per_set) for s, _m in lst})
    area_rows, bmap, hght, area_cells = [], bytearray(), [], []
    area_index = {}                 # (set, local area) -> global area
    sect_rows, gbkt, glst = [], [], []
    by_key = collections.defaultdict(list)
    for si, (lst, _) in enumerate(per_set):
        for la, (s, members) in enumerate(lst):
            by_key[(s, si)].append((la, members))
    for sk, (s, si) in enumerate(sect_keys):
        cs = sets[si]
        first_area = len(area_rows)
        for la, members in by_key[(s, si)]:
            m = np.asarray(members)
            ii, jj, yy = cs.i[m], cs.j[m], cs.y[m]
            ci0, cj0 = int(ii.min()), int(jj.min())
            w_, h_ = int(ii.max()) - ci0 + 1, int(jj.max()) - cj0 + 1
            row = (w_ + 7) >> 3
            bits = np.zeros((h_, row * 8), np.uint8)
            bits[jj - cj0, ii - ci0] = 1
            packed = np.packbits(bits.reshape(h_, row, 8)[:, :, ::-1], axis=-1).reshape(-1)
            miny, maxy = float(yy.min()), float(yy.max())
            grid = np.full((h_, w_), 0xFFFF, np.int64)
            grid[jj - cj0, ii - ci0] = np.minimum(np.round((yy - miny) * 100).astype(np.int64), 0xFFFE)
            pmm = int(round(cs.pitch * 1000))
            x0, z0 = cs.x0 + ci0 * cs.pitch, cs.z0 + cj0 * cs.pitch
            area_index[(si, la)] = len(area_rows)
            area_rows.append([x0, z0, x0 + (w_ - 1) * cs.pitch, z0 + (h_ - 1) * cs.pitch, miny, maxy, len(bmap), w_, h_,
                              0, 0, sk, 0, 0, 0, 0, pmm, len(hght), w_, h_, pmm])
            bmap += packed.tobytes()
            hght += grid.ravel().tolist()
            area_cells.append((si, m))
        X, Z = s // 256, s % 256
        sx0, sz0 = (X + xoff) * 100.0, (Z + zoff) * 100.0
        first_bucket = len(gbkt)
        buckets = [[] for _ in range(rsnv.BUCKETS * rsnv.BUCKETS)]
        for k in range(first_area, len(area_rows)):
            r = area_rows[k]
            half = sets[si].pitch / 2
            b0 = max(int((r[0] - half - sx0) // rsnv.BUCKET), 0)
            b1 = min(int((r[2] + half - sx0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            c0 = max(int((r[1] - half - sz0) // rsnv.BUCKET), 0)
            c1 = min(int((r[3] + half - sz0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            for bj in range(c0, c1 + 1):
                for bi in range(b0, b1 + 1):
                    buckets[bj * rsnv.BUCKETS + bi].append(k)
        for lst in buckets:
            lst.sort(key=lambda k: (area_rows[k][4], k))
            gbkt.append((len(glst), len(lst)))
            glst += lst
        sect_rows.append(((X << 8) | Z, layer, 1 if sets[si].pitch >= 0.5 else 0, sx0, sz0, sx0 + 100.0, sz0 + 100.0,
                          first_bucket, first_area, len(area_rows) - first_area, 0))
    if len(area_rows) >= 0xFFFF:
        raise ValueError(f'{len(area_rows)} areas: more than an RSNV area id holds')
    # global area of every cell
    cell_area = [np.full(cs.n, -1, np.int64) for cs in sets]
    for si, (lst, local) in enumerate(per_set):
        for la in range(len(lst)):
            pass
        g_of_local = np.array([area_index[(si, la)] for la in range(len(lst))], np.int64) if lst else np.zeros(0, np.int64)
        cell_area[si] = g_of_local[local] if len(local) else local
    # portals: neighbouring cells in different areas (same set)
    port_pairs = collections.defaultdict(list)
    for si, cs in enumerate(sets):
        ca = cell_area[si]
        for (k, di, dj), n in cs.links.items():
            if (di, dj) not in STRAIGHT:
                continue
            a, b = int(ca[k]), int(ca[n])
            if a != b and a >= 0 and b >= 0:
                x, z = cs.xz(k)
                port_pairs[(a, b)].append((x + di * cs.pitch / 2, z + dj * cs.pitch / 2, (cs.y[k] + cs.y[n]) / 2))
    per_area = collections.defaultdict(list)
    for (a, b), pts in port_pairs.items():
        P = np.array(pts)
        per_area[a].append((float(P[:, 0].min()), float(P[:, 1].min()), float(P[:, 0].max()), float(P[:, 1].max()),
                            float(P[:, 2].mean()), a, b))
    port = []
    for k in range(len(area_rows)):
        area_rows[k][9] = len(port)
        area_rows[k][10] = len(per_area[k])
        port += sorted(per_area[k], key=lambda r: r[6])
    # nodes and edges
    pos = np.array([[*sets[si].xz(k)[:1], sets[si].y[k], sets[si].xz(k)[1]] for si, k in nodes], np.float64).reshape(-1, 3)
    n_area = np.array([cell_area[si][k] for si, k in nodes], np.int64)
    adj = collections.defaultdict(dict)
    for a, b, kind, cost, both in edges:
        c = cost if cost is not None else float(np.linalg.norm(pos[a] - pos[b]))
        ci = max(1, min(int(round(c * 16)), 0xFFFF))
        adj[a][b] = (kind, ci)
        if both:
            adj[b][a] = (kind, ci)
    edge_rows, first_edge = [], []
    for nd in range(len(nodes)):
        first_edge.append(len(edge_rows))
        for b in sorted(adj[nd]):
            kind, ci = adj[nd][b]
            edge_rows.append((int(b) | (int(kind) << 24), ci, 0))
    first_edge.append(len(edge_rows))
    # islands (undirected)
    lab = np.arange(len(nodes))
    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a in adj:
        for b in adj[a]:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    lab = np.array([find(x) for x in range(len(nodes))], np.int64)
    roots, inv, counts = np.unique(lab, return_inverse=True, return_counts=True)
    order = np.argsort(-counts, kind='stable')
    rank = np.empty(len(roots), np.int64)
    rank[order] = np.arange(len(roots))
    island = rank[inv] if len(nodes) else np.zeros(0, np.int64)
    island_rows = []
    for r in range(len(roots)):
        members = np.nonzero(island == r)[0]
        fl = (rsnv.ISLD_MAIN if r == 0 else 0) | ((island_flags or {}).get(r, 0))
        island_rows.append([len(members), int(members[0]), fl, 0, 0])
    # transfers per island (OFFM sorted by island, then as given)
    offm = sorted(offm, key=lambda r: (int(island[r[3]]) if r[3] != 0xFFFFFFFF else 1 << 30))
    for k, r in enumerate(offm):
        if r[3] == 0xFFFFFFFF:
            continue
        isl = island_rows[int(island[r[3]])]
        if isl[3] == 0:
            isl[4] = k
        isl[3] += 1
    # areas: island and nodes
    anod = []
    by_area = collections.defaultdict(list)
    for nd, a in enumerate(n_area.tolist()):
        if a >= 0:
            by_area[a].append(nd)
    for k, row in enumerate(area_rows):
        ns = by_area.get(k, [])
        row[12] = collections.Counter(island[ns].tolist()).most_common(1)[0][0] if ns else 0xFFFF
        row[14], row[15] = len(anod), len(ns)
        anod += ns
    nflags = node_flags if node_flags is not None else [0] * len(nodes)
    node_rows = [(float(x), float(y), float(z), int(first_edge[n])) for n, (x, y, z) in enumerate(pos)]
    node_rows.append((0.0, 0.0, 0.0, int(first_edge[-1])))
    naux = []
    for nd in range(len(nodes)):
        a = int(n_area[nd])
        naux.append((a if a >= 0 else 0xFFFF, area_rows[a][11] if a >= 0 else 0, int(island[nd]), int(nflags[nd])))
    meta = dict(meta)
    meta.setdefault('exporter', 'rsnvgrid1943 ' + EXPORTER_VERSION)
    meta['level'] = lv
    meta['class'] = cls
    meta['layer'] = layer
    meta['counts'] = {'nodes': len(nodes), 'edges': len(edge_rows), 'areas': len(area_rows), 'portals': len(port),
                      'sectors': len(sect_rows), 'islands': len(island_rows), 'transfers': len(offm),
                      'cells': int(sum(cs.n for cs in sets))}
    sections = {'SECT': sect_rows, 'NODE': node_rows, 'NAUX': naux, 'EDGE': edge_rows,
                'AREA': [tuple(r) for r in area_rows], 'PORT': port, 'BMAP': bytes(bmap), 'HGHT': [(v,) for v in hght],
                'GBKT': gbkt, 'GLST': [(v,) for v in glst], 'ANOD': [(v,) for v in anod],
                'OFFM': [tuple(r) for r in offm], 'ISLD': [tuple(r) for r in island_rows],
                'META': json.dumps(meta, indent=1).encode('utf-8')}
    if not offm:
        del sections['OFFM']
    hdr = {'level': lv, 'sea_level': header['sea_level'], 'radius': header['radius'], 'height': header['height'],
           'step': header['step'], 'water_depth': header['water_depth'], 'hpd_sha256': header.get('sha256', '0' * 8)}
    size = rsnv.write(out, sections, hdr, big_endian)
    if flag_tight:
        # tight edges: drive edges with no line of sight either way under the runtime's rule (rsnv.World.los), as
        # navexport1943 flags the soldier's; the file is written again with the flags
        w = rsnv.load(out)
        src = np.repeat(np.arange(len(nodes)), np.diff(w.node_first_edge))
        tight = set()
        index = {(int(a), int(b)): k for k, (a, b) in enumerate(zip(src.tolist(), w.edge_target.tolist()))}
        f32 = lambda v: tuple(float(np.float32(x)) for x in v)
        for k, (a, b) in enumerate(zip(src.tolist(), w.edge_target.tolist())):
            if int(w.edge_kind[k]) != rsnv.EDGE_WALK or k in tight or a > b and (b, a) in index:
                continue
            pa, pb = f32(w.node_pos[a]), f32(w.node_pos[b])
            if not w.los(pa, pb) and not w.los(pb, pa):
                tight.add(k)
                if (b, a) in index:
                    tight.add(index[(b, a)])
        sections['EDGE'] = [(t, c, rsnv.EDGEF_TIGHT if k in tight else 0) for k, (t, c, _f) in enumerate(edge_rows)]
        meta['counts']['tight edges'] = len(tight)
        sections['META'] = json.dumps(meta, indent=1).encode('utf-8')
        size = rsnv.write(out, sections, hdr, big_endian)
    stats = dict(meta['counts'])
    stats['bytes'] = size
    stats['island sizes'] = [r[0] for r in island_rows[:8]]
    log(f'  RSNV {out}: {size / 1e6:.2f} MB, {len(nodes)} nodes, {len(edge_rows)} edges, {len(area_rows)} areas, '
        f'{len(island_rows)} islands (largest {island_rows[0][0] if island_rows else 0}), {len(offm)} transfers')
    return stats


def export_surfaces(lv, cls, g, kept, table, nodes, edges, seeds, out, st, sources, sea, log=print, soldier=None,
                    objectives=None):
    """groundnav1943's world: the kept surfaces as one cell set, boarding transfers at the class's spawns."""
    import vehicleclasses1943 as vc
    ks = np.nonzero(kept)[0]
    local = np.full(g.n, -1, np.int64)
    local[ks] = np.arange(len(ks))
    links = {}
    for (k, di, dj), n in table.items():
        if (di, dj) in STRAIGHT and kept[k] and kept[n]:
            links[(int(local[k]), di, dj)] = int(local[n])
    cs = CellSet(g.pitch, g.x0, g.z0, g.i[ks], g.j[ks], g.y[ks], links, name=f'{lv} {cls}')
    node_list = [(0, int(local[n])) for n in nodes]
    nidx = {n: k for k, n in enumerate(nodes)}
    edge_list = [(nidx[a], nidx[b], rsnv.EDGE_WALK, None, True) for a, b in edges]
    # node flags: shallow / wading by the water over them
    depth = sea - g.y[np.asarray(nodes, np.int64)] if nodes else np.zeros(0)
    nflags = np.zeros(len(nodes), np.int64)
    nflags[(depth > 0) & (depth <= 0.5)] |= rsnv.NAUX_SHALLOW
    nflags[depth > 0.5] |= rsnv.NAUX_WADE
    # boarding: each spawn of the class, its entry points on the soldier side, the vehicle's own node
    npos = np.array([[*cs.xz(local[n])[:1], g.y[n], cs.xz(local[n])[1]] for n in nodes]).reshape(-1, 3)
    offm, board, alight = [], [], []
    S = rsnv.load(soldier) if soldier and os.path.isfile(soldier) else None
    for s in seeds:
        if s['class'] != cls or not len(npos):
            continue
        d = np.hypot(npos[:, 0] - s['pos'][0], npos[:, 2] - s['pos'][2]) + 2 * np.abs(npos[:, 1] - s['pos'][1])
        nd = int(np.argmin(d))
        team = {1: 1, 2: 2}.get(s['team'], 3)
        ents = s['entries'] or [{'point': s['pos'], 'radius': 4.0, 'order': 0}]
        for e in ents:
            if e.get('order', 0) != 0:
                continue                                     # the driver's seat
            sn = soldier_node(S, e['point'], float(e['radius']) + 2.0) if S is not None else None
            offm.append((rsnv.OFFM_BOARD_VEHICLE, team, vc.CLASS_IDS[cls], nd if d[nd] <= 12.0 else 0xFFFFFFFF,
                         *[float(v) for v in e['point']], float(s['yaw']), float(e['radius']),
                         sn if sn is not None else 0xFFFFFFFF))
            board.append({'vehicle': s['vehicle'], 'spawn': s['index'], 'node': nd, 'distance': round(float(d[nd]), 2),
                          'soldier node': sn, 'soldier island': int(S.node_island[sn]) if sn is not None else None})
    # leaving the vehicle: at the drivable node nearest each base and flag (and each boarding spawn), onto the soldier
    # node nearest it on the main island (the exits are 1.8-2.5 m beside the body [O])
    if S is not None and len(npos):
        targets = [(o.get('name'), o['position']) for o in (objectives or []) if o.get('position')]
        targets += [(f'spawn {b["spawn"]}', npos[b['node']].tolist()) for b in board]
        for name, q in targets:
            d = np.hypot(npos[:, 0] - q[0], npos[:, 2] - q[2])
            for nd in np.argsort(d, kind='stable')[:40].tolist():
                if d[nd] > ALIGHT_TARGET_REACH:
                    break
                sn = soldier_node(S, npos[nd], ALIGHT_REACH, 2.5, main_only=True)
                if sn is not None:
                    sp = S.node_pos[sn]
                    reach = float(np.hypot(sp[0] - npos[nd, 0], sp[2] - npos[nd, 2]))
                    offm.append((OFFM_ALIGHT_ZONE, 3, vc.CLASS_IDS[cls], nd, float(sp[0]), float(sp[1]), float(sp[2]),
                                 0.0, reach, sn))
                    alight.append({'target': name, 'node': nd, 'soldier node': sn, 'from target': round(float(d[nd]), 1)})
                    break
    meta = {'sources': {k: {'path': v, 'sha256': sha256(v)} for k, v in sources.items() if v and os.path.isfile(v)},
            'class parameters': {k: list(v) if isinstance(v, tuple) else v for k, v in vc.CLASSES[cls].items()},
            'boarding': board, 'alighting': alight}
    header = {'sea_level': sea, 'radius': vc.value(cls, 'radius'), 'height': vc.value(cls, 'height'),
              'step': vc.value(cls, 'step'), 'water_depth': vc.value(cls, 'water_depth'),
              'sha256': meta['sources'].get('samples', {}).get('sha256', '0' * 8)}
    return write_world(lv, cls, [cs], node_list, edge_list, offm, out, st['sector_offsets'], header, meta,
                       node_flags=nflags.tolist(), log=log)
