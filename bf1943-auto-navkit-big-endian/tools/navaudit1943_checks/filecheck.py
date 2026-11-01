"""navaudit1943_checks, check 3: file consistency and crossings."""
import collections
import math

import numpy as np

import soldier1943  # noqa: E402

from .loading import grid_at


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
