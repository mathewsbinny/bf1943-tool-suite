"""NavKit's builder (navbuild), a stage's sectors: the sector windows and tiles, areas (connected pieces of a tile),
their boundaries traced into portals, border portals and outlines with 45-degree bridges, and the neighbour links
between sectors (pipeline steps 2 to 5 in navbuild's docstring).

Names used throughout the package: a sector is (X, Y), its 100 m square less the level's sector offsets; gi0, gj0 are
the global sample indices of its local sample (0, 0) and ox, oz its corner in metres (sector_origin_index); i, j are
local sample indices and gi, gj global ones; k is a kept surface (an index into the samples) and di, dj a step from
one column to the next."""
import math
from collections import defaultdict, deque
from collections.abc import Container, Iterable, Sequence
from typing import TYPE_CHECKING, cast

import kymesh

from .config import (AREA_LINK, BISECT_AREAS, BRIDGE_UNION, MAX_AREA, ONE_WAY_PORTALS, SYMMETRIC_PORTALS,
    TILE_20M_SECTORS)

if TYPE_CHECKING:
    from .config import (AreaDict, BorderRun, BuildState, CellRef, EdgeMap, Grid, OneWay, Resolver, SectorInfo,
        SectorKey, StageResult, Traced, Vec3)
    from .inputs import Level

    type Bridge = tuple[int, int, int, int]                 # (i, j, di, dj): a 45-degree bridge from cell (i, j)
    type BridgeSeg = tuple[int, Vec3, Vec3]                 # (bridge, (ax, az, ya), (bx, bz, yb)) in local units


def sector_origin_index(level: 'Level', X: int, Y: int, xoff: int, zoff: int) -> tuple[int, int, float, float]:
    """Global sample index of the sector's local sample (0, 0)."""
    s, pitch = level.s, level.grid.pitch
    ox, oz = (X + xoff) * 100.0, (Y + zoff) * 100.0
    return int(round((ox - s.min_x) / pitch)), int(round((oz - s.min_z) / pitch)), ox, oz


def tiling(sid: int, grid: 'Grid') -> tuple[int, int]:
    """(samples per tile, tiles per axis) of a sector on the stage's grid."""
    return (25, 6) if TILE_20M_SECTORS and sid in TILE_20M_SECTORS else (grid.tile, grid.tiles)


def sector_window(X: int, Y: int, level: 'Level', state: 'BuildState') -> tuple[range, range]:
    """MIXED_OVERLAP: a sector's window, grown by the settings' mixed_overlap (m) on each side whose neighbour is
    built on the other grid (a 0.2 m sector beside a 0.8 m one, and the other way): the two grids' cells then overlap
    where a seam's edges cross, and each grid shows the ground the other leaves out there (2026-10-26: on Coral Sea's
    mixed seams, line of sight found neither grid's cells for a stretch of 0.2-0.6 m). The grid's window on both axes
    when off. state: the build's BuildState (its normal and detailed sectors)."""
    grid, overlap = level.grid, level.settings.mixed_overlap
    if not overlap:
        return (grid.window, grid.window)
    fine = grid.detailed
    grow = int(math.ceil(overlap / grid.pitch - 1e-9))
    detailed = state.detailed_sectors

    def side(neighbour: 'SectorKey') -> int:
        other_grid = neighbour in state.normal_sectors and (neighbour in detailed) != fine
        return grow if other_grid and ((X, Y) in detailed) == fine else 0
    lo_x, hi_x = side((X - 1, Y)), side((X + 1, Y))
    lo_z, hi_z = side((X, Y - 1)), side((X, Y + 1))
    window = grid.window
    return (range(window[0] - lo_x, window[-1] + 1 + hi_x), range(window[0] - lo_z, window[-1] + 1 + hi_z))


def build_areas(level: 'Level', gi0: int, gj0: int, start: Sequence[int] = (-1, -1), tile: int | None = None,
                tiles: int | None = None, win: tuple[range, range] | None = None) -> 'list[AreaDict]':
    """Areas of one sector: list of dicts {tile: (tx, tz), cells: [(i, j, k)]} (local i, j). start: the local sample
    index where the tiles begin on x and z (-1, or 0 on a side with no neighbouring sector - EDGE_SECTOR_TILES).
    tile, tiles: the level's grid's by default; win: the sector's window (x range, z range of local samples), the
    grid's window on both axes by default."""
    grid = level.grid
    tile = grid.tile if tile is None else tile
    tiles = grid.tiles if tiles is None else tiles
    range_i, range_j = win or (grid.window, grid.window)
    cells_by_tile = defaultdict(list)
    for j in range_j:
        for i in range_i:
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


def components(level: 'Level', members: dict[int, tuple[int, int]], gi0: int, gj0: int) -> 'list[list[CellRef]]':
    """8-connected components (AREA_LINK rule) of the given surfaces {k: (i, j)}. An area is single-layer in plan: a
    surface whose column the component already holds (a second floor reached round a corner) starts its own."""
    seen, pieces = set(), []
    for first in members:
        if first in seen:
            continue
        piece, queue = [], deque([first])
        seen.add(first)
        cols = {members[first]: first}
        while queue:
            k = queue.popleft()
            i, j = members[k]
            piece.append((i, j, k))
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj, rule=AREA_LINK)
                if n is not None and n in members and n not in seen and members[n] == (i + di, j + dj) \
                        and members[n] not in cols:
                    if level.settings.area_cliff and _cliff(level, n, members[n], cols):
                        continue
                    seen.add(n)
                    cols[members[n]] = n
                    queue.append(n)
        pieces.append(piece)
    return pieces


def _cliff(level: 'Level', n: int, ij: tuple[int, int], cols: dict[tuple[int, int], int]) -> bool:
    """AREA_CLIFF: surface n at column ij would sit beside a cell of the area higher or lower than a 45-degree slope
    plus the step, and at least the settings' area_cliff m, away - the area reached it round a ramp (a bridge deck
    beside the ground under it: Wake Island's com_bridge_t07, cells 6.3 m apart side by side, 2026-10-26). A height
    grid cannot hold such an edge inside one area, so the surface goes to an area of its own."""
    y, step, pitch = level.y, level.step, level.grid.pitch
    cliff = cast(float, level.settings.area_cliff)       # set: components calls _cliff only then
    i, j = ij
    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        other = cols.get((i + di, j + dj))
        if other is not None and abs(y[n] - y[other]) > max(cliff, math.hypot(di, dj) * pitch + step):
            return True
    if level.settings.area_cliff_reach >= 2:
        # two cells away, straight or diagonal, with the cell between them not in the area: the per-cell height grid
        # carries the far cell's height across the empty one (Iwo Jima 0a0a: a strip of the floor 1.56 m below, one
        # empty row from the upper floor on both sides, 2026-10-29); the limit is a 45-degree slope over the two cells
        # plus the step
        for di, dj in ((2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (2, -2), (-2, 2), (-2, -2)):
            other = cols.get((i + di, j + dj))
            if other is None or (i + di // 2, j + dj // 2) in cols:
                continue
            if abs(y[n] - y[other]) > max(cliff, math.hypot(di, dj) * pitch + step):
                return True
    return False


def bisect(level: 'Level', comp: 'list[CellRef]', gi0: int, gj0: int) -> 'list[list[CellRef]]':
    lo_i, hi_i = min(c[0] for c in comp), max(c[0] for c in comp)
    lo_j, hi_j = min(c[1] for c in comp), max(c[1] for c in comp)
    if hi_i - lo_i + 1 <= MAX_AREA and hi_j - lo_j + 1 <= MAX_AREA:
        return [comp]
    if hi_i - lo_i >= hi_j - lo_j:
        mid = (lo_i + hi_i + 1) // 2
        halves = ([c for c in comp if c[0] < mid], [c for c in comp if c[0] >= mid])
    else:
        mid = (lo_j + hi_j + 1) // 2
        halves = ([c for c in comp if c[1] < mid], [c for c in comp if c[1] >= mid])
    out = []
    for half in halves:
        for sub in components(level, {k: (i, j) for i, j, k in half}, gi0, gj0):
            out.extend(bisect(level, sub, gi0, gj0))
    return out


def clip_bridges(bsegs: 'Iterable[BridgeSeg]', bridges: 'Sequence[Bridge]') -> 'list[BridgeSeg]':
    """Outline of overlapping 45-degree bridges is the boundary of their union: where edges of two bridges
    cross (one cell with diagonal-only neighbours on both sides of an empty cell), split them at the crossing
    and drop the pieces lying inside the other bridge's strip (half-width 1/sqrt 2 sample about the diagonal)."""
    def inside(px: float, pz: float, bridge: int) -> bool:
        i, j, di, dj = bridges[bridge]
        rx, rz = px - i, pz - j
        along = (rx * di + rz * dj) / 2.0                   # 0..1 between the two cell centres
        perp = abs(rx * dj - rz * di) / math.sqrt(2.0)
        return 0.0 < along < 1.0 and perp < 1.0 / math.sqrt(2.0) - 1e-6

    out = list(bsegs)
    changed = True
    while changed:
        changed = False
        for seg_u in range(len(out)):
            for seg_v in range(seg_u + 1, len(out)):
                (bridge_u, p0, p1), (bridge_v, q0, q1) = out[seg_u], out[seg_v]
                if bridge_u == bridge_v:
                    continue
                cross = (p1[0] - p0[0]) * (q1[1] - q0[1]) - (p1[1] - p0[1]) * (q1[0] - q0[0])
                if abs(cross) < 1e-9:
                    continue
                frac_u = ((q0[0] - p0[0]) * (q1[1] - q0[1]) - (q0[1] - p0[1]) * (q1[0] - q0[0])) / cross
                frac_v = ((q0[0] - p0[0]) * (p1[1] - p0[1]) - (q0[1] - p0[1]) * (p1[0] - p0[0])) / cross
                if not (1e-6 < frac_u < 1 - 1e-6 and 1e-6 < frac_v < 1 - 1e-6):
                    continue
                pieces = []
                for (bridge, end0, end1, frac), other in (((bridge_u, p0, p1, frac_u), bridge_v),
                                                          ((bridge_v, q0, q1, frac_v), bridge_u)):
                    cut = (end0[0] + (end1[0] - end0[0]) * frac, end0[1] + (end1[1] - end0[1]) * frac,
                           end0[2] + (end1[2] - end0[2]) * frac)
                    for piece0, piece1 in ((end0, cut), (cut, end1)):
                        if not inside((piece0[0] + piece1[0]) / 2, (piece0[1] + piece1[1]) / 2, other):
                            pieces.append((bridge, piece0, piece1))
                out = [seg for index, seg in enumerate(out) if index not in (seg_u, seg_v)] + pieces
                changed = True
                break
            if changed:
                break
    return out


def bridge_union(bridges: 'Sequence[Bridge]', cell_k: dict[tuple[int, int], int], level: 'Level'
                 ) -> 'tuple[set[tuple[str, float, int]], list[tuple[Vec3, Vec3]]]':
    """BRIDGE_UNION: the outline of an area's 45-degree bridges as the boundary of their union, exactly. A bridge
    between diagonal-only cells A (i, j) and B (i + di, j + dj) covers half of each of the two empty cells beside the
    shared corner - per empty cell, the two quarter triangles (base: a cell side, apex: the centre) whose bases touch
    that corner. Over the set of covered quarters of the empty cells, a half-diagonal (cell corner to centre) shared
    by two quarters is interior; the others are the bridges' outline - halves of one diagonal joined. A side of an
    area cell is covered (no outline there) where the empty cell's quarter on it is. Returns (covered sides in
    trace_edges' keys, [((ax, az, ya), (bx, bz, yb))] in local sample units). With non-overlapping bridges this is
    exactly the two diagonals per bridge; overlapping ones (chains, a cell shared by two corners) close where
    clip_bridges left open ends (1.7-3.1 % of areas on sp_004_b / sp_01, 2026-10-23)."""
    # (empty cell ei, ej, base side (sx, sz)) -> (bridge index, ya, yb, side towards A, side towards B)
    quarters = {}
    order = []
    yoff = level.settings.y_offset
    for bridge, (i, j, di, dj) in enumerate(bridges):
        ya = level.y[cell_k[(i, j)]] + yoff
        yb = level.y[cell_k[(i + di, j + dj)]] + yoff
        # empty cell (i, j + dj): its sides towards A (0, -dj) and towards B (di, 0); empty cell (i + di, j): towards
        # A (-di, 0) and towards B (0, dj) - the order clip_bridges emitted them in
        for (ei, ej), toward_a, toward_b in (((i, j + dj), (0, -dj), (di, 0)), ((i + di, j), (-di, 0), (0, dj))):
            for side in (toward_a, toward_b):
                quarter = (ei, ej, side)
                if quarter not in quarters:
                    quarters[quarter] = (bridge, ya, yb, toward_a, toward_b)
                    order.append(quarter)
    # the two corners of a quarter's base, in half units
    def corners(ei: int, ej: int, side: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
        sx, sz = side
        if sx:
            return ((2 * ei + sx, 2 * ej - 1), (2 * ei + sx, 2 * ej + 1))
        return ((2 * ei - 1, 2 * ej + sz), (2 * ei + 1, 2 * ej + sz))
    count = defaultdict(int)
    for (ei, ej, side) in quarters:
        for corner in corners(ei, ej, side):
            count[(corner, ei, ej)] += 1            # half-diagonal from the corner to the centre of (ei, ej)
    drop = set()
    for (ei, ej, side) in quarters:
        sx, sz = side
        drop.add(('x', ei + sx * 0.5, ej) if sx else ('z', ej + sz * 0.5, ei))
    segs, done, emitted = [], set(), set()
    for (ei, ej, side) in order:
        bridge, ya, yb, toward_a, toward_b = quarters[(ei, ej, side)]
        if (ei, ej, bridge) in done:
            continue
        done.add((ei, ej, bridge))
        # this bridge's diagonal through the cell: from A's outer corner to B's outer corner, through the centre
        corners_a = [c for c in corners(ei, ej, toward_a)
                     if count[(c, ei, ej)] == 1 and (ei, ej, toward_a) in quarters and (c, ei, ej) not in emitted]
        corners_b = [c for c in corners(ei, ej, toward_b)
                     if count[(c, ei, ej)] == 1 and (ei, ej, toward_b) in quarters and (c, ei, ej) not in emitted]
        emitted.update((c, ei, ej) for c in corners_a + corners_b)
        centre = (float(ei), float(ej), (ya + yb) / 2.0)
        points_a = [(c[0] / 2.0, c[1] / 2.0, ya) for c in corners_a]
        points_b = [(c[0] / 2.0, c[1] / 2.0, yb) for c in corners_b]
        if len(points_a) == 1 and len(points_b) == 1:
            segs.append((points_a[0], points_b[0]))
        else:
            segs.extend((point, centre) for point in points_a)
            segs.extend((centre, point) for point in points_b)
    return drop, segs


def trace(level: 'Level', sector: 'SectorKey', areas: 'Sequence[AreaDict]', area_of: dict[int, int], gi0: int, gj0: int,
          ox: float, oz: float) -> 'list[Traced]':
    """Boundary edges of every area -> merged runs. Returns per area: portals, borders, outlines. One sector on its
    own: one-way portal pieces are kept ('keep') or become outline (otherwise) - build() decides them level-wide
    ('restore', restore_one_way)."""
    per_area, one_way = trace_edges(level, areas, area_of, gi0, gj0)
    drop_one_way(per_area, one_way, keep=set(range(len(one_way))) if ONE_WAY_PORTALS == 'keep' else set())
    return trace_emit(level, areas, per_area, gi0, gj0, ox, oz)


def trace_edges(level: 'Level', areas: 'Sequence[AreaDict]', area_of: dict[int, int], gi0: int, gj0: int,
                win: tuple[range, range] | None = None) -> 'tuple[list[EdgeMap], list[OneWay]]':
    """Boundary edges of every area: (per area {(kind, target, axis, line, dir): [(along, y)]}, one-way candidates).
    Portals are symmetric (SYMMETRIC_PORTALS): every portal edge A -> B also stands as B -> A, replacing an outline
    edge B had there - neighbour() picks the nearest surface in height, so with two surfaces in a column the two
    sides could disagree and leave a one-way portal (22 % of them on sp_004_b before). Where the target's side is
    its own interior (a second surface of the target in the source's column) the piece cannot be mirrored: it is a
    one-way candidate (source area, target area, key, along) for drop_one_way."""
    per_area = []
    one_way = []
    mirror = defaultdict(dict)       # area -> {(source area, axis, line, dir, along): y of its own cell there}
    # per area: {(axis, line, dir, along): (kind 'I' interior / 'B' / 'C' / 'E', target)}
    side_kind = []
    yoff = level.settings.y_offset
    range_i, range_j = win or (level.grid.window, level.grid.window)
    for ai, area in enumerate(areas):
        edges = defaultdict(list)    # (kind, target, axis, line, dir) -> [(along, y0, y1)]
        sides = {}
        own = {c[2] for c in area['cells']}
        for i, j, k in area['cells']:
            y_here = level.y[k] + yoff
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                side = ('x', i + di * 0.5, di, j) if di else ('z', j + dj * 0.5, dj, i)
                # a side is interior when any connected surface across it is in this area (two connected
                # surfaces in one column - stair edge, split-off layer - must not leave a stray portal inside)
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj, prefer=own)
                ni, nj = i + di, j + dj
                if n is not None and ni in range_i and nj in range_j:
                    target_area = area_of.get(n)
                    if target_area == ai:
                        sides[side] = ('I', None)
                        continue
                    kind, target = ('B', target_area) if target_area is not None else ('E', None)
                elif n is not None:
                    kind, target = 'C', (di, dj)
                else:
                    kind, target = 'E', None
                if di:   # vertical line x = const
                    key = (kind, target, 'x', i + di * 0.5, di)
                    edges[key].append((j, y_here))
                else:
                    key = (kind, target, 'z', j + dj * 0.5, dj)
                    edges[key].append((i, y_here))
                sides[side] = (kind, target)
                if kind == 'B':
                    axis, line, along = ('x', i + di * 0.5, j) if di else ('z', j + dj * 0.5, i)
                    mirror[target][(ai, axis, line, -(di or dj), along)] = level.y[n] + yoff
        per_area.append(edges)
        side_kind.append(sides)
    if SYMMETRIC_PORTALS:
        # only where the target's side is plain outline: an interior side (the column holds a surface of the target
        # too) or a side holding another portal stays as it is - a portal there would break the area's outline
        for target_area, need in mirror.items():
            edges = per_area[target_area]
            for (src, axis, line, direction, along), y in need.items():
                there = side_kind[target_area].get((axis, line, direction, along), ('E', None))
                if there == ('B', src):                  # a portal back already
                    continue
                if there[0] != 'E':
                    key = ('B', target_area, axis, line, -direction)
                    if any(entry[0] == along for entry in per_area[src].get(key, [])):
                        one_way.append((src, target_area, key, along))
                    continue
                for key in [k for k in edges if k[0] == 'E' and k[2] == axis and k[3] == line and k[4] == direction]:
                    edges[key] = [entry for entry in edges[key] if entry[0] != along]
                edges[('B', src, axis, line, direction)].append((along, y))
                side_kind[target_area][(axis, line, direction, along)] = ('B', src)
    return per_area, one_way


def drop_one_way(per_area: 'list[EdgeMap]', one_way: 'Sequence[OneWay]', keep: Container[int]) -> None:
    """One-way candidates (trace_edges) not in keep (indices) become outline on their source area."""
    for index, (src, _t, key, along) in enumerate(one_way):
        if index in keep:
            continue
        src_edges = per_area[src]
        moved = [r for r in src_edges.get(key, []) if r[0] == along]
        src_edges[key] = [r for r in src_edges.get(key, []) if r[0] != along]
        src_edges[('E', None, key[2], key[3], key[4])].extend(moved)


def restore_one_way(sectors: 'dict[SectorKey, tuple[list[EdgeMap], list[OneWay]]]',
                    shared: 'Iterable[tuple[tuple[SectorKey, int], tuple[SectorKey, int]]]'
                    ) -> 'dict[SectorKey, set[int]]':
    """ONE_WAY_PORTALS 'restore': which one-way candidates to keep. sectors: {sector: (per_area, one_way)} from
    trace_edges; shared: pairs ((sector, area), (sector, area)) joined across sectors (one surface in both - the
    sectors' two-sample overlap). Areas are joined through every portal piece that is not a candidate and through
    shared; then, pair by pair, a pair's candidates are kept only when its two areas are not joined yet (keeping them
    joins them). Returns {sector: set of kept candidate indices}."""
    parent = {}

    def find(x: 'tuple[SectorKey, int]') -> 'tuple[SectorKey, int]':
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(area_a: 'tuple[SectorKey, int]', area_b: 'tuple[SectorKey, int]') -> None:
        root_a, root_b = find(area_a), find(area_b)
        if root_a != root_b:
            parent[root_a] = root_b
    for sec, (per_area, one_way) in sectors.items():
        candidates = {(src, key, along) for src, _t, key, along in one_way}
        for ai, edges in enumerate(per_area):
            find((sec, ai))
            for key, rows in edges.items():
                if key[0] == 'B' and any((ai, key, r[0]) not in candidates for r in rows):
                    union((sec, ai), (sec, key[1]))
    for area_a, area_b in shared:
        union(area_a, area_b)
    keep = defaultdict(set)
    by_pair = defaultdict(list)
    for sec, (_per_area, one_way) in sectors.items():
        for n, (src, target, _key, _along) in enumerate(one_way):
            by_pair[(sec, src, target)].append(n)
    for (sec, src, target), indices in sorted(by_pair.items()):
        if find((sec, src)) != find((sec, target)):
            keep[sec].update(indices)
            union((sec, src), (sec, target))
    return keep


def trace_emit(level: 'Level', areas: 'Sequence[AreaDict]', per_area: 'list[EdgeMap]', gi0: int, gj0: int, ox: float,
               oz: float) -> 'list[Traced]':
    """Per area (portals, borders, outline) from its boundary edges: runs merged, 45-degree bridges."""
    result = []
    pitch, yoff = level.grid.pitch, level.settings.y_offset
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
                    k_diag = cell_k[(i + di, j + dj)]
                    if any(level.neighbour(side_k, gi0 + side_i, gj0 + side_j, step_i, step_j) is not None
                           for side_k, side_i, side_j, step_i, step_j in (
                               (k, i, j, di, 0), (k, i, j, 0, dj),
                               (k_diag, i + di, j + dj, -di, 0), (k_diag, i + di, j + dj, 0, -dj))):
                        continue
                    bridges.append((i, j, di, dj))
        portals, borders, outline = [], [], []
        if BRIDGE_UNION:
            drop, dsegs = bridge_union(bridges, cell_k, level)
            for (ax, az, ya), (bx, bz, yb) in dsegs:
                outline.append([(ox + ax * pitch, ya, oz + az * pitch), (ox + bx * pitch, yb, oz + bz * pitch)])
        else:
            drop = set()
            for i, j, di, dj in bridges:
                # unit outline edges around the corner point (i + di/2, j + dj/2)
                drop.update({('x', i + di * 0.5, j), ('z', j + dj * 0.5, i), ('x', i + di * 0.5, j + dj),
                             ('z', j + dj * 0.5, i + di)})
            bsegs = []                                      # (bridge, (ax, az, ya), (bx, bz, yb)) local units
            for bi, (i, j, di, dj) in enumerate(bridges):
                cx, cz = i + di * 0.5, j + dj * 0.5         # shared corner (local sample units)
                ya = level.y[cell_k[(i, j)]] + yoff
                yb = level.y[cell_k[(i + di, j + dj)]] + yoff
                for (ax, az), (bx, bz) in (((cx - di, cz), (cx, cz + dj)), ((cx, cz - dj), (cx + di, cz))):
                    bsegs.append((bi, (ax, az, ya), (bx, bz, yb)))
            for _bi, (ax, az, ya), (bx, bz, yb) in clip_bridges(bsegs, bridges):
                outline.append([(ox + ax * pitch, ya, oz + az * pitch), (ox + bx * pitch, yb, oz + bz * pitch)])
        for key in list(edges):
            if key[0] == 'E':
                axis, line = key[2], key[3]
                edges[key] = [row for row in edges[key] if (axis, line, row[0]) not in drop]
        for (kind, target, axis, line, _d), runs in edges.items():
            runs.sort()
            start = 0
            for end in range(1, len(runs) + 1):
                if end == len(runs) or runs[end][0] != runs[end - 1][0] + 1:
                    lo_along, hi_along = runs[start][0] - 0.5, runs[end - 1][0] + 0.5
                    ya, yb = runs[start][1], runs[end - 1][1]
                    if axis == 'x':
                        p0 = (ox + line * pitch, ya, oz + lo_along * pitch)
                        p1 = (ox + line * pitch, yb, oz + hi_along * pitch)
                    else:
                        p0 = (ox + lo_along * pitch, ya, oz + line * pitch)
                        p1 = (ox + hi_along * pitch, yb, oz + line * pitch)
                    if kind == 'B':
                        portals.append(kymesh.Portal(p0, p1, target))
                    elif kind == 'C':
                        borders.append((kymesh.BorderPortal(p0, p1, (float(target[0]), 0.0, float(target[1]))),
                                        runs[start][0], runs[end - 1][0], line))
                    else:
                        outline.append([p0, p1])
                    start = end
        result.append((portals, borders, outline))
    return result


def sector_links(X: int, Y: int, areas: 'Sequence[AreaDict]', mesh_areas: Sequence[kymesh.Area], resolve: 'Resolver'
                 ) -> list[kymesh.NeighbourLink]:
    """The neighbour links of a sector: for every border portal (C), the area across it - resolve(other, area,
    border) gives its (tile x, tile z, layer) in the other sector, or None."""
    links = defaultdict(lambda: defaultdict(list))
    for ai, area in enumerate(areas):
        for border_index, border in enumerate(area['borders']):
            portal = border[0]
            di, dj = int(portal.direction[0]), int(portal.direction[2])
            other = (X + di, Y + dj)
            hit = resolve(other, area, border)
            if hit is None:
                continue
            record_offset = 0x50 + len(mesh_areas[ai].portals) * 0x20 + border_index * 0x2C
            links[other][hit].append((ai, record_offset))
    mesh_links = []
    for other in sorted(links):
        other_id = ((other[0] & 0xFF) << 8) | (other[1] & 0xFF)
        objects = [kymesh.LinkObject(tile_ref, refs) for tile_ref, refs in sorted(links[other].items())]
        mesh_links.append(kymesh.NeighbourLink(other_id, objects))
    return mesh_links


def border_cell(area: 'AreaDict', border: 'BorderRun') -> tuple[int, int, int | None, int, int]:
    """(i, j, k) of the area's cell just inside a border portal (the middle of its run), and the step (di, dj)."""
    portal, run_start, run_end, line = border
    di, dj = int(portal.direction[0]), int(portal.direction[2])
    mid = (run_start + run_end) // 2
    i, j = (int(line + 0.5 * di), mid) if di else (mid, int(line + 0.5 * dj))
    k = next((c[2] for c in area['cells'] if (c[0], c[1]) == (i - di, j - dj)), None)
    return i - di, j - dj, k, di, dj


def same_grid_resolver(level: 'Level', info: 'dict[SectorKey, SectorInfo]', X: int, Y: int) -> 'Resolver':
    """resolve for sector_links within one stage: the connected surface across, in the other sector's areas."""
    gi0, gj0, _ox, _oz = info[(X, Y)]['origin']

    def resolve(other: 'SectorKey', area: 'AreaDict', border: 'BorderRun') -> tuple[int, int, int] | None:
        if other not in info:
            return None
        i, j, k_here, di, dj = border_cell(area, border)
        if k_here is None:
            return None
        n = level.neighbour(k_here, gi0 + i, gj0 + j, di, dj)
        other_info = info[other]
        if n is None or n not in other_info['area_of']:
            return None
        target_area = other_info['areas'][other_info['area_of'][n]]
        return (target_area['tile'][0], target_area['tile'][1], target_area['layer'])
    return resolve


def cross_grid_resolver(here: 'StageResult', there: 'StageResult', X: int, Y: int) -> 'Resolver':
    """resolve for sector_links across two stages (a detailed sector next to a normal one): the cell just across
    the border, in world coordinates, looked up on the other stage's grid - its kept surface nearest in height
    (within 0.8 m) that belongs to the other sector's areas."""
    here_level, here_info = here['level'], here['info'][(X, Y)]
    _gi0, _gj0, ox, oz = here_info['origin']
    pitch_here, there_level, pitch_there = here['pitch'], there['level'], there['pitch']
    there_samples = there_level.s

    def resolve(other: 'SectorKey', area: 'AreaDict', border: 'BorderRun') -> tuple[int, int, int] | None:
        if other not in there['info']:
            return None
        i, j, k_here, di, dj = border_cell(area, border)
        if k_here is None:
            return None
        x = ox + i * pitch_here + di * max(pitch_here, pitch_there)
        z = oz + j * pitch_here + dj * max(pitch_here, pitch_there)
        y = here_level.y[k_here]
        other_info = there['info'][other]
        gi = int(round((x - there_samples.min_x) / pitch_there))
        gj = int(round((z - there_samples.min_z) / pitch_there))
        candidates = [k for k in there_level.surfaces(gi, gj)
                      if k in other_info['area_of'] and abs(there_level.y[k] - y) <= 0.8]
        if not candidates:
            return None
        n = min(candidates, key=lambda k: abs(there_level.y[k] - y))
        target_area = other_info['areas'][other_info['area_of'][n]]
        return (target_area['tile'][0], target_area['tile'][1], target_area['layer'])
    return resolve
