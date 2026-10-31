"""NavKit's builder (navbuild), a stage's sectors: the sector windows and tiles, areas (connected pieces of a tile),
their boundaries traced into portals, border portals and outlines with 45-degree bridges, and the neighbour links
between sectors (pipeline steps 2 to 5 in navbuild's docstring)."""
import math
from collections import defaultdict, deque

import kymesh

from .config import (AREA_LINK, BISECT_AREAS, BRIDGE_UNION, MAX_AREA, ONE_WAY_PORTALS, SYMMETRIC_PORTALS,
    TILE_20M_SECTORS)


def sector_origin_index(level, X, Y, xoff, zoff):
    """Global sample index of the sector's local sample (0, 0)."""
    s, p = level.s, level.grid.pitch
    ox, oz = (X + xoff) * 100.0, (Y + zoff) * 100.0
    return int(round((ox - s.min_x) / p)), int(round((oz - s.min_z) / p)), ox, oz


def tiling(sid, grid):
    """(samples per tile, tiles per axis) of a sector on the stage's grid."""
    return (25, 6) if TILE_20M_SECTORS and sid in TILE_20M_SECTORS else (grid.tile, grid.tiles)


def sector_window(X, Y, level, state):
    """MIXED_OVERLAP: a sector's window, grown by the settings' mixed_overlap (m) on each side whose neighbour is
    built on the other grid (a 0.2 m sector beside a 0.8 m one, and the other way): the two grids' cells then overlap
    where a seam's edges cross, and each grid shows the ground the other leaves out there (2026-10-26: on Coral Sea's
    mixed seams, line of sight found neither grid's cells for a stretch of 0.2-0.6 m). The grid's window on both axes
    when off. state: the build's BuildState (its normal and detailed sectors)."""
    grid, overlap = level.grid, level.settings.mixed_overlap
    if not overlap:
        return (grid.window, grid.window)
    fine = grid.detailed
    e = int(math.ceil(overlap / grid.pitch - 1e-9))
    detailed = state.detailed_sectors

    def side(n):
        return e if n in state.normal_sectors and (n in detailed) != fine and ((X, Y) in detailed) == fine else 0
    lo_x, hi_x = side((X - 1, Y)), side((X + 1, Y))
    lo_z, hi_z = side((X, Y - 1)), side((X, Y + 1))
    w = grid.window
    return (range(w[0] - lo_x, w[-1] + 1 + hi_x), range(w[0] - lo_z, w[-1] + 1 + hi_z))


def build_areas(level, gi0, gj0, start=(-1, -1), tile=None, tiles=None, win=None):
    """Areas of one sector: list of dicts {tile: (tx, tz), cells: [(i, j, k)]} (local i, j). start: the local sample
    index where the tiles begin on x and z (-1, or 0 on a side with no neighbouring sector - EDGE_SECTOR_TILES).
    tile, tiles: the level's grid's by default; win: the sector's window (x range, z range of local samples), the
    grid's window on both axes by default."""
    grid = level.grid
    tile = grid.tile if tile is None else tile
    tiles = grid.tiles if tiles is None else tiles
    wi, wj = win or (grid.window, grid.window)
    cells_by_tile = defaultdict(list)
    for j in wj:
        for i in wi:
            for k in level.surfaces(gi0 + i, gj0 + j):
                tx = min(max(i - start[0], 0) // tile, tiles - 1)
                tz = min(max(j - start[1], 0) // tile, tiles - 1)
                cells_by_tile[(tx, tz)].append((i, j, k))
    areas = []
    for tile in sorted(cells_by_tile):
        members = {k: (i, j) for i, j, k in cells_by_tile[tile]}
        for comp in components(level, members, gi0, gj0):
            for piece in (bisect(level, comp, gi0, gj0) if BISECT_AREAS else [comp]):
                areas.append({'tile': tile, 'cells': piece})
    return areas


def components(level, members, gi0, gj0):
    """8-connected components (AREA_LINK rule) of the given surfaces {k: (i, j)}. An area is single-layer in plan: a
    surface whose column the component already holds (a second floor reached round a corner) starts its own."""
    seen, out = set(), []
    for k0 in members:
        if k0 in seen:
            continue
        comp, q = [], deque([k0])
        seen.add(k0)
        cols = {members[k0]: k0}
        while q:
            k = q.popleft()
            i, j = members[k]
            comp.append((i, j, k))
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj, rule=AREA_LINK)
                if n is not None and n in members and n not in seen and members[n] == (i + di, j + dj) \
                        and members[n] not in cols:
                    if level.settings.area_cliff and _cliff(level, n, members[n], cols):
                        continue
                    seen.add(n)
                    cols[members[n]] = n
                    q.append(n)
        out.append(comp)
    return out


def _cliff(level, n, ij, cols):
    """AREA_CLIFF: surface n at column ij would sit beside a cell of the area higher or lower than a 45-degree slope
    plus the step, and at least the settings' area_cliff m, away - the area reached it round a ramp (a bridge deck
    beside the ground under it: Wake Island's com_bridge_t07, cells 6.3 m apart side by side, 2026-10-26). A height
    grid cannot hold such an edge inside one area, so the surface goes to an area of its own."""
    y, step, pitch = level.y, level.step, level.grid.pitch
    cliff = level.settings.area_cliff
    i, j = ij
    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        m = cols.get((i + di, j + dj))
        if m is not None and abs(y[n] - y[m]) > max(cliff, math.hypot(di, dj) * pitch + step):
            return True
    if level.settings.area_cliff_reach >= 2:
        # two cells away, straight or diagonal, with the cell between them not in the area: the per-cell height grid
        # carries the far cell's height across the empty one (Iwo Jima 0a0a: a strip of the floor 1.56 m below, one
        # empty row from the upper floor on both sides, 2026-10-29); the limit is a 45-degree slope over the two cells
        # plus the step
        for di, dj in ((2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (2, -2), (-2, 2), (-2, -2)):
            m = cols.get((i + di, j + dj))
            if m is None or (i + di // 2, j + dj // 2) in cols:
                continue
            if abs(y[n] - y[m]) > max(cliff, math.hypot(di, dj) * pitch + step):
                return True
    return False


def bisect(level, comp, gi0, gj0):
    i0, i1 = min(c[0] for c in comp), max(c[0] for c in comp)
    j0, j1 = min(c[1] for c in comp), max(c[1] for c in comp)
    if i1 - i0 + 1 <= MAX_AREA and j1 - j0 + 1 <= MAX_AREA:
        return [comp]
    if i1 - i0 >= j1 - j0:
        mid = (i0 + i1 + 1) // 2
        halves = ([c for c in comp if c[0] < mid], [c for c in comp if c[0] >= mid])
    else:
        mid = (j0 + j1 + 1) // 2
        halves = ([c for c in comp if c[1] < mid], [c for c in comp if c[1] >= mid])
    out = []
    for h in halves:
        for sub in components(level, {k: (i, j) for i, j, k in h}, gi0, gj0):
            out.extend(bisect(level, sub, gi0, gj0))
    return out


def clip_bridges(bsegs, bridges):
    """Outline of overlapping 45-degree bridges is the boundary of their union: where edges of two bridges
    cross (one cell with diagonal-only neighbours on both sides of an empty cell), split them at the crossing
    and drop the pieces lying inside the other bridge's strip (half-width 1/sqrt 2 sample about the diagonal)."""
    def inside(px, pz, b):
        i, j, di, dj = bridges[b]
        rx, rz = px - i, pz - j
        along = (rx * di + rz * dj) / 2.0                   # 0..1 between the two cell centres
        perp = abs(rx * dj - rz * di) / math.sqrt(2.0)
        return 0.0 < along < 1.0 and perp < 1.0 / math.sqrt(2.0) - 1e-6

    out = list(bsegs)
    changed = True
    while changed:
        changed = False
        for u in range(len(out)):
            for v in range(u + 1, len(out)):
                (bu, p0, p1), (bv, q0, q1) = out[u], out[v]
                if bu == bv:
                    continue
                d = (p1[0] - p0[0]) * (q1[1] - q0[1]) - (p1[1] - p0[1]) * (q1[0] - q0[0])
                if abs(d) < 1e-9:
                    continue
                t = ((q0[0] - p0[0]) * (q1[1] - q0[1]) - (q0[1] - p0[1]) * (q1[0] - q0[0])) / d
                s = ((q0[0] - p0[0]) * (p1[1] - p0[1]) - (q0[1] - p0[1]) * (p1[0] - p0[0])) / d
                if not (1e-6 < t < 1 - 1e-6 and 1e-6 < s < 1 - 1e-6):
                    continue
                pieces = []
                for (b, a0, a1, f), other in (((bu, p0, p1, t), bv), ((bv, q0, q1, s), bu)):
                    m = (a0[0] + (a1[0] - a0[0]) * f, a0[1] + (a1[1] - a0[1]) * f, a0[2] + (a1[2] - a0[2]) * f)
                    for c0, c1 in ((a0, m), (m, a1)):
                        if not inside((c0[0] + c1[0]) / 2, (c0[1] + c1[1]) / 2, other):
                            pieces.append((b, c0, c1))
                out = [x for w, x in enumerate(out) if w not in (u, v)] + pieces
                changed = True
                break
            if changed:
                break
    return out


def bridge_union(bridges, cell_k, level):
    """BRIDGE_UNION: the outline of an area's 45-degree bridges as the boundary of their union, exactly. A bridge
    between diagonal-only cells A (i, j) and B (i + di, j + dj) covers half of each of the two empty cells beside the
    shared corner - per empty cell, the two quarter triangles (base: a cell side, apex: the centre) whose bases touch
    that corner. Over the set of covered quarters of the empty cells, a half-diagonal (cell corner to centre) shared
    by two quarters is interior; the others are the bridges' outline - halves of one diagonal joined. A side of an
    area cell is covered (no outline there) where the empty cell's quarter on it is. Returns (covered sides in
    trace_edges' keys, [((ax, az, ya), (bx, bz, yb))] in local sample units). With non-overlapping bridges this is
    exactly the two diagonals per bridge; overlapping ones (chains, a cell shared by two corners) close where
    clip_bridges left open ends (1.7-3.1 % of areas on sp_004_b / sp_01, 2026-10-23)."""
    quarters = {}                                   # (cell i, j, base side (sx, sz)) -> (bridge index, ya, yb)
    order = []
    yoff = level.settings.y_offset
    for bi, (i, j, di, dj) in enumerate(bridges):
        ya = level.y[cell_k[(i, j)]] + yoff
        yb = level.y[cell_k[(i + di, j + dj)]] + yoff
        # empty cell (i, j + dj): its sides towards A (0, -dj) and towards B (di, 0); empty cell (i + di, j): towards
        # A (-di, 0) and towards B (0, dj) - the order clip_bridges emitted them in
        for (ei, ej), toward_a, toward_b in (((i, j + dj), (0, -dj), (di, 0)), ((i + di, j), (-di, 0), (0, dj))):
            for side in (toward_a, toward_b):
                q = (ei, ej, side)
                if q not in quarters:
                    quarters[q] = (bi, ya, yb, toward_a, toward_b)
                    order.append(q)
    def corners(ei, ej, side):                      # the two corners of a quarter's base, in half units
        sx, sz = side
        if sx:
            return ((2 * ei + sx, 2 * ej - 1), (2 * ei + sx, 2 * ej + 1))
        return ((2 * ei - 1, 2 * ej + sz), (2 * ei + 1, 2 * ej + sz))
    count = defaultdict(int)
    for (ei, ej, side) in quarters:
        for c in corners(ei, ej, side):
            count[(c, ei, ej)] += 1                 # half-diagonal from corner c to the centre of (ei, ej)
    drop = set()
    for (ei, ej, side) in quarters:
        sx, sz = side
        drop.add(('x', ei + sx * 0.5, ej) if sx else ('z', ej + sz * 0.5, ei))
    segs, done, emitted = [], set(), set()
    for (ei, ej, side) in order:
        bi, ya, yb, toward_a, toward_b = quarters[(ei, ej, side)]
        if (ei, ej, bi) in done:
            continue
        done.add((ei, ej, bi))
        # this bridge's diagonal through the cell: from A's outer corner to B's outer corner, through the centre
        ca = [c for c in corners(ei, ej, toward_a) if count[(c, ei, ej)] == 1 and (ei, ej, toward_a) in quarters
              and (c, ei, ej) not in emitted]
        cb = [c for c in corners(ei, ej, toward_b) if count[(c, ei, ej)] == 1 and (ei, ej, toward_b) in quarters
              and (c, ei, ej) not in emitted]
        emitted.update((c, ei, ej) for c in ca + cb)
        ctr = (float(ei), float(ej), (ya + yb) / 2.0)
        pa = [(c[0] / 2.0, c[1] / 2.0, ya) for c in ca]
        pb = [(c[0] / 2.0, c[1] / 2.0, yb) for c in cb]
        if len(pa) == 1 and len(pb) == 1:
            segs.append((pa[0], pb[0]))
        else:
            segs.extend((p, ctr) for p in pa)
            segs.extend((ctr, p) for p in pb)
    return drop, segs


def trace(level, sector, areas, area_of, gi0, gj0, ox, oz):
    """Boundary edges of every area -> merged runs. Returns per area: portals, borders, outlines. One sector on its
    own: one-way portal pieces are kept ('keep') or become outline (otherwise) - build() decides them level-wide
    ('restore', restore_one_way)."""
    per_area, one_way = trace_edges(level, areas, area_of, gi0, gj0)
    drop_one_way(per_area, one_way, keep=set(range(len(one_way))) if ONE_WAY_PORTALS == 'keep' else set())
    return trace_emit(level, areas, per_area, gi0, gj0, ox, oz)


def trace_edges(level, areas, area_of, gi0, gj0, win=None):
    """Boundary edges of every area: (per area {(kind, target, axis, line, dir): [(along, y)]}, one-way candidates).
    Portals are symmetric (SYMMETRIC_PORTALS): every portal edge A -> B also stands as B -> A, replacing an outline
    edge B had there - neighbour() picks the nearest surface in height, so with two surfaces in a column the two
    sides could disagree and leave a one-way portal (22 % of them on sp_004_b before). Where the target's side is
    its own interior (a second surface of the target in the source's column) the piece cannot be mirrored: it is a
    one-way candidate (source area, target area, key, along) for drop_one_way."""
    per_area = []
    one_way = []
    mirror = defaultdict(dict)       # area -> {(source area, axis, line, dir, along): y of its own cell there}
    side_kind = []                   # per area: {(axis, line, dir, along): (kind 'I' interior / 'B' / 'C' / 'E', target)}
    yoff = level.settings.y_offset
    wi, wj = win or (level.grid.window, level.grid.window)
    for ai, area in enumerate(areas):
        edges = defaultdict(list)    # (kind, target, axis, line, dir) -> [(along, y0, y1)]
        sides = {}
        own = {c[2] for c in area['cells']}
        for i, j, k in area['cells']:
            yk = level.y[k] + yoff
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                side = ('x', i + di * 0.5, di, j) if di else ('z', j + dj * 0.5, dj, i)
                # a side is interior when any connected surface across it is in this area (two connected
                # surfaces in one column - stair edge, split-off layer - must not leave a stray portal inside)
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj, prefer=own)
                ni, nj = i + di, j + dj
                if n is not None and ni in wi and nj in wj:
                    t = area_of.get(n)
                    if t == ai:
                        sides[side] = ('I', None)
                        continue
                    kind, target = ('B', t) if t is not None else ('E', None)
                elif n is not None:
                    kind, target = 'C', (di, dj)
                else:
                    kind, target = 'E', None
                if di:   # vertical line x = const
                    key = (kind, target, 'x', i + di * 0.5, di)
                    edges[key].append((j, yk))
                else:
                    key = (kind, target, 'z', j + dj * 0.5, dj)
                    edges[key].append((i, yk))
                sides[side] = (kind, target)
                if kind == 'B':
                    axis, line, along = ('x', i + di * 0.5, j) if di else ('z', j + dj * 0.5, i)
                    mirror[target][(ai, axis, line, -(di or dj), along)] = level.y[n] + yoff
        per_area.append(edges)
        side_kind.append(sides)
    if SYMMETRIC_PORTALS:
        # only where the target's side is plain outline: an interior side (the column holds a surface of the target
        # too) or a side holding another portal stays as it is - a portal there would break the area's outline
        for t, need in mirror.items():
            edges = per_area[t]
            for (src, axis, line, d, along), y in need.items():
                there = side_kind[t].get((axis, line, d, along), ('E', None))
                if there == ('B', src):                  # a portal back already
                    continue
                if there[0] != 'E':
                    key = ('B', t, axis, line, -d)
                    if any(r[0] == along for r in per_area[src].get(key, [])):
                        one_way.append((src, t, key, along))
                    continue
                for key in [k for k in edges if k[0] == 'E' and k[2] == axis and k[3] == line and k[4] == d]:
                    edges[key] = [r for r in edges[key] if r[0] != along]
                edges[('B', src, axis, line, d)].append((along, y))
                side_kind[t][(axis, line, d, along)] = ('B', src)
    return per_area, one_way


def drop_one_way(per_area, one_way, keep):
    """One-way candidates (trace_edges) not in keep (indices) become outline on their source area."""
    for n, (src, _t, key, along) in enumerate(one_way):
        if n in keep:
            continue
        se = per_area[src]
        ys = [r for r in se.get(key, []) if r[0] == along]
        se[key] = [r for r in se.get(key, []) if r[0] != along]
        se[('E', None, key[2], key[3], key[4])].extend(ys)


def restore_one_way(sectors, shared):
    """ONE_WAY_PORTALS 'restore': which one-way candidates to keep. sectors: {sector: (per_area, one_way)} from
    trace_edges; shared: pairs ((sector, area), (sector, area)) joined across sectors (one surface in both - the
    sectors' two-sample overlap). Areas are joined through every portal piece that is not a candidate and through
    shared; then, pair by pair, a pair's candidates are kept only when its two areas are not joined yet (keeping them
    joins them). Returns {sector: set of kept candidate indices}."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for sec, (per_area, one_way) in sectors.items():
        cand = {(src, key, along) for src, _t, key, along in one_way}
        for ai, edges in enumerate(per_area):
            find((sec, ai))
            for key, rows in edges.items():
                if key[0] == 'B' and any((ai, key, r[0]) not in cand for r in rows):
                    union((sec, ai), (sec, key[1]))
    for a, b in shared:
        union(a, b)
    keep = defaultdict(set)
    by_pair = defaultdict(list)
    for sec, (_per_area, one_way) in sectors.items():
        for n, (src, t, _key, _along) in enumerate(one_way):
            by_pair[(sec, src, t)].append(n)
    for (sec, src, t), ns in sorted(by_pair.items()):
        if find((sec, src)) != find((sec, t)):
            keep[sec].update(ns)
            union((sec, src), (sec, t))
    return keep


def trace_emit(level, areas, per_area, gi0, gj0, ox, oz):
    """Per area (portals, borders, outline) from its boundary edges: runs merged, 45-degree bridges."""
    result = []
    p, yoff = level.grid.pitch, level.settings.y_offset
    for ai, area in enumerate(areas):
        edges = per_area[ai]
        # 45-degree bridges between diagonal-only neighbours of this area
        cellset = {(c[0], c[1]) for c in area['cells']}
        bridges = []
        cell_k = {(c[0], c[1]): c[2] for c in area['cells']}
        for i, j, k in area['cells']:
            for di, dj in ((1, 1), (1, -1)):
                if (i + di, j + dj) in cellset and (i + di, j) not in cellset and (i, j + dj) not in cellset:
                    # only across an open corner: neither cell has a connected surface (another area) beside it
                    # there - otherwise those sides are portals and the strip would overlap the other area
                    kd = cell_k[(i + di, j + dj)]
                    if any(level.neighbour(kk, gi0 + a, gj0 + b, x, z) is not None
                           for kk, a, b, x, z in ((k, i, j, di, 0), (k, i, j, 0, dj),
                                                  (kd, i + di, j + dj, -di, 0), (kd, i + di, j + dj, 0, -dj))):
                        continue
                    bridges.append((i, j, di, dj))
        portals, borders, outline = [], [], []
        if BRIDGE_UNION:
            drop, dsegs = bridge_union(bridges, cell_k, level)
            for (ax, az, ya), (bx, bz, yb) in dsegs:
                outline.append([(ox + ax * p, ya, oz + az * p), (ox + bx * p, yb, oz + bz * p)])
        else:
            drop = set()
            for i, j, di, dj in bridges:
                # unit outline edges around the corner point (i + di/2, j + dj/2)
                drop.update({('x', i + di * 0.5, j), ('z', j + dj * 0.5, i), ('x', i + di * 0.5, j + dj), ('z', j + dj * 0.5, i + di)})
            bsegs = []                                      # (bridge, (ax, az, ya), (bx, bz, yb)) local units
            for bi, (i, j, di, dj) in enumerate(bridges):
                cx, cz = i + di * 0.5, j + dj * 0.5         # shared corner (local sample units)
                ya = level.y[cell_k[(i, j)]] + yoff
                yb = level.y[cell_k[(i + di, j + dj)]] + yoff
                for (ax, az), (bx, bz) in (((cx - di, cz), (cx, cz + dj)), ((cx, cz - dj), (cx + di, cz))):
                    bsegs.append((bi, (ax, az, ya), (bx, bz, yb)))
            for _bi, (ax, az, ya), (bx, bz, yb) in clip_bridges(bsegs, bridges):
                outline.append([(ox + ax * p, ya, oz + az * p), (ox + bx * p, yb, oz + bz * p)])
        for key in list(edges):
            if key[0] == 'E':
                axis, line = key[2], key[3]
                edges[key] = [r for r in edges[key] if (axis, line, r[0]) not in drop]
        for (kind, target, axis, line, _d), runs in edges.items():
            runs.sort()
            start = 0
            for r in range(1, len(runs) + 1):
                if r == len(runs) or runs[r][0] != runs[r - 1][0] + 1:
                    a, b = runs[start][0] - 0.5, runs[r - 1][0] + 0.5
                    ya, yb = runs[start][1], runs[r - 1][1]
                    if axis == 'x':
                        p0 = (ox + line * p, ya, oz + a * p)
                        p1 = (ox + line * p, yb, oz + b * p)
                    else:
                        p0 = (ox + a * p, ya, oz + line * p)
                        p1 = (ox + b * p, yb, oz + line * p)
                    if kind == 'B':
                        portals.append(kymesh.Portal(p0, p1, target))
                    elif kind == 'C':
                        borders.append((kymesh.BorderPortal(p0, p1, (float(target[0]), 0.0, float(target[1]))),
                                        runs[start][0], runs[r - 1][0], line))
                    else:
                        outline.append([p0, p1])
                    start = r
        result.append((portals, borders, outline))
    return result


def sector_links(X, Y, areas, mesh_areas, resolve):
    """The neighbour links of a sector: for every border portal (C), the area across it - resolve(other, area,
    border) gives its (tile x, tile z, layer) in the other sector, or None."""
    links = defaultdict(lambda: defaultdict(list))
    for ai, a in enumerate(areas):
        for ci, b in enumerate(a['borders']):
            bp = b[0]
            di, dj = int(bp.direction[0]), int(bp.direction[2])
            other = (X + di, Y + dj)
            hit = resolve(other, a, b)
            if hit is None:
                continue
            rel = 0x50 + len(mesh_areas[ai].portals) * 0x20 + ci * 0x2C
            links[other][hit].append((ai, rel))
    mesh_links = []
    for other in sorted(links):
        oid = ((other[0] & 0xFF) << 8) | (other[1] & 0xFF)
        mesh_links.append(kymesh.NeighbourLink(oid, [kymesh.LinkObject(h, refs) for h, refs in sorted(links[other].items())]))
    return mesh_links


def border_cell(a, b):
    """(i, j, k) of the area's cell just inside a border portal (the middle of its run), and the step (di, dj)."""
    bp, t0_, t1_, line = b
    di, dj = int(bp.direction[0]), int(bp.direction[2])
    tmid = (t0_ + t1_) // 2
    i, j = (int(line + 0.5 * di), tmid) if di else (tmid, int(line + 0.5 * dj))
    k = next((c[2] for c in a['cells'] if (c[0], c[1]) == (i - di, j - dj)), None)
    return i - di, j - dj, k, di, dj


def same_grid_resolver(level, info, X, Y):
    """resolve for sector_links within one stage: the connected surface across, in the other sector's areas."""
    gi0, gj0, _ox, _oz = info[(X, Y)]['origin']

    def resolve(other, a, b):
        if other not in info:
            return None
        i, j, k_here, di, dj = border_cell(a, b)
        if k_here is None:
            return None
        n = level.neighbour(k_here, gi0 + i, gj0 + j, di, dj)
        oth = info[other]
        if n is None or n not in oth['area_of']:
            return None
        ta = oth['areas'][oth['area_of'][n]]
        return (ta['tile'][0], ta['tile'][1], ta['layer'])
    return resolve


def cross_grid_resolver(here, there, X, Y):
    """resolve for sector_links across two stages (a detailed sector next to a normal one): the cell just across
    the border, in world coordinates, looked up on the other stage's grid - its kept surface nearest in height
    (within 0.8 m) that belongs to the other sector's areas."""
    lv, inf = here['level'], here['info'][(X, Y)]
    _gi0, _gj0, ox, oz = inf['origin']
    p_here, olv, p_there = here['pitch'], there['level'], there['pitch']
    os_ = olv.s

    def resolve(other, a, b):
        if other not in there['info']:
            return None
        i, j, k_here, di, dj = border_cell(a, b)
        if k_here is None:
            return None
        x = ox + i * p_here + di * max(p_here, p_there)
        z = oz + j * p_here + dj * max(p_here, p_there)
        y = lv.y[k_here]
        oth = there['info'][other]
        gi, gj = int(round((x - os_.min_x) / p_there)), int(round((z - os_.min_z) / p_there))
        cand = [k for k in olv.surfaces(gi, gj) if k in oth['area_of'] and abs(olv.y[k] - y) <= 0.8]
        if not cand:
            return None
        n = min(cand, key=lambda k: abs(olv.y[k] - y))
        ta = oth['areas'][oth['area_of'][n]]
        return (ta['tile'][0], ta['tile'][1], ta['layer'])
    return resolve
