"""NavKit's builder (navbuild), links across mixed-pitch seams (a 0.8 m sector beside a 0.2 m one), made after the
two stages are merged: gap nodes where both grids' ground meets the seam with no shared node (seam_gap_nodes), and
walk edges in the 0.8 m sector where the 0.2 m samples join its pieces (seam_fine_links, seam_far_links). Which run is
the build's Settings' (seam_gap_nodes, seam_fine_links, seam_far_links).

Each pass takes the two stages' results (normal: the 0.8 m stage, detailed: the 0.2 m one), by_sec ({sector: merged
graph tuple}, whose node and edge lists it extends in place) and the detailed sectors. Names: a sector's gi0, gj0 are
the global sample indices of its local sample (0, 0) and ox, oz its origin in metres (areas.sector_origin_index); gi,
gj a 0.8 m column and fi, fj a 0.2 m one; k a 0.8 m kept surface and f a 0.2 m one."""
import math
from collections import defaultdict, deque
from collections.abc import Collection, Iterator, Mapping
from typing import TYPE_CHECKING

from .config import CARRY_RADIUS, DETAILED_PITCH, DIST_EDGE_MAX, JoinScope
from .graph import join_groups, line_walkable

if TYPE_CHECKING:
    import navsample

    from .config import (BuildState, GraphPair, GraphPoint, NewId, SectorGraph, SectorInfo, SectorKey, StageResult,
        Vec3)
    from .inputs import Level


SEAM_FINE_BAND = 2.0            # m: seam_fine_links looks this far into the normal sector from a mixed seam


def fine_columns(sa: 'navsample.Samples', sb: 'navsample.Samples', gi: int, gj: int, pa: float, pb: float
                 ) -> list[tuple[int, int]]:
    """The columns of the fine grid sb (pitch pb) whose centres lie in the coarse grid sa's cell (gi, gj) (pitch pa):
    [c - pa/2, c + pa/2) on each axis, (pa / pb)^2 of them, each once. Both seam passes use it (2026-10-30). Before,
    seam_fine_links rounded the points c + (u + 0.5) x pb to the fine grid; those fall on halves, and round-half-even
    took some columns twice and left others out (seam_far_links found it on Iwo Jima's slope, 2026-10-29)."""
    cx, cz = sa.min_x + gi * pa, sa.min_z + gj * pa
    fi0 = int(math.ceil((cx - pa / 2 - sb.min_x) / pb - 1e-6))      # the first fine column on each axis
    fj0 = int(math.ceil((cz - pa / 2 - sb.min_z) / pb - 1e-6))
    per_axis = int(round(pa / pb))
    return [(fi0 + u, fj0 + v) for u in range(per_axis) for v in range(per_axis)]


def _mixed_seams(detailed_secs: 'Collection[SectorKey]', by_sec: 'Mapping[SectorKey, SectorGraph]',
                 normal_info: 'Mapping[SectorKey, SectorInfo]') -> 'Iterator[tuple[SectorKey, SectorKey, int, int]]':
    """Every mixed seam: (detailed sector, the normal sector beside it, dX, dY: the step from the first to the
    second), the detailed sectors in order."""
    for sector in sorted(detailed_secs):
        for dX, dY in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            beside = (sector[0] + dX, sector[1] + dY)
            if beside not in by_sec or beside in detailed_secs or beside not in normal_info:
                continue
            yield sector, beside, dX, dY


def _seam_line(sector: 'SectorKey', dX: int, dY: int, xoff: int, zoff: int) -> tuple[float, float, float]:
    """(x0, z0, seam): the detailed sector's corner in metres, and the seam's x (dX) or z (dY) towards its
    neighbour."""
    x0, z0 = (sector[0] + xoff) * 100.0, (sector[1] + zoff) * 100.0
    seam = (x0 + (100.0 if dX > 0 else 0.0)) if dX else (z0 + (100.0 if dY > 0 else 0.0))
    return x0, z0, seam


def _pieces(level: 'Level', columns: Mapping[int, tuple]) -> dict[int, int]:
    """The surfaces of columns ({surface: (gi, gj, ...)} on the level's grid) in pieces under the level's step rule
    (8-connected): {surface: the first surface of its piece}, in the order of columns."""
    piece = {}
    for first in columns:
        if first in piece:
            continue
        piece[first] = first
        queue = deque([first])
        while queue:
            k = queue.popleft()
            gi, gj = columns[k][:2]
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di or dj:
                        n = level.neighbour(k, gi, gj, di, dj)
                        if n is not None and n in columns and n not in piece:
                            piece[n] = first
                            queue.append(n)
    return piece


def _surface_node(level: 'Level', pts: 'list[GraphPoint]', pairs: 'list[GraphPair]', k: int, gi: int, gj: int,
                  origin: tuple[int, int, float, float], pitch: float, new_id: 'NewId') -> int:
    """The index of the node on surface k in a normal sector's graph: the first there, or a new one (at column gi, gj)
    linked to every node within DIST_EDGE_MAX it walks to in a straight line. origin: the sector's (gi0, gj0, ox,
    oz)."""
    index = next((other for other, point in enumerate(pts) if point[2] == k), None)
    if index is not None:
        return index
    gi0, gj0, ox, oz = origin
    i, j = gi - gi0, gj - gj0
    pos = (ox + i * pitch, float(level.y[k]) + level.settings.y_offset, oz + j * pitch)
    pts.append((new_id(), pos, k, i, j))
    index = len(pts) - 1
    for other, point in enumerate(pts[:-1]):
        dist = math.dist(pos, point[1])
        if dist <= DIST_EDGE_MAX and line_walkable(level, k, (i, j), (point[3], point[4]), gi0, gj0, point[2]):
            pairs.append((index, other, dist))
    return index


def _link_surfaces(level: 'Level', graph: 'SectorGraph', k1: int, k2: int, coarse: Mapping[int, tuple[int, int]],
                   origin: tuple[int, int, float, float], pitch: float, new_id: 'NewId',
                   made: list[tuple[tuple[float, ...], ...]]) -> None:
    """A walk edge between the nodes on surfaces k1 and k2 of a normal sector's graph (each made when missing), its two
    ends' positions recorded in made (BuildState.fine_links or far_links). coarse: {surface: (gi, gj)}."""
    pts, pairs = graph[3], graph[4]
    ends = [_surface_node(level, pts, pairs, k, *coarse[k], origin, pitch, new_id) for k in (k1, k2)]
    pairs.append((ends[0], ends[1], math.dist(pts[ends[0]][1], pts[ends[1]][1])))
    made.append(tuple(tuple(round(v, 2) for v in pts[e][1]) for e in ends))


def seam_fine_links(normal: 'StageResult', detailed: 'StageResult', by_sec: 'dict[SectorKey, SectorGraph]',
                    detailed_secs: 'Collection[SectorKey]', xoff: int, zoff: int, state: 'BuildState') -> int:
    """SEAM_FINE_LINKS: in a normal (0.8 m) sector beside a detailed one, ground the 0.2 m samples join but the 0.8 m
    step rule does not - a lip the coarse grid cannot resolve (Coral Sea 0b0b: a strip at 138.11 and the ground at
    137.58, 0.53 m apart at 0.8 m, joined at 0.2 m by a slope 0.6-1.4 m past the seam; the strip's 0.2 m side was
    closed, the last seam break, 2026-10-26). Within SEAM_FINE_BAND of the seam, the 0.8 m kept surfaces fall into
    pieces under the 0.8 m step rule and the 0.2 m kept surfaces into pieces under the 0.2 m one; where one 0.2 m piece
    stands on two 0.8 m pieces in side-by-side columns, the two 0.8 m surfaces get a node each and a walk edge between
    them in the normal sector's graph (once per pair of pieces). by_sec: {sector: merged graph tuple}, changed in place.
    Each level's step rule is its own grid's (0.8 m for the normal stage's level, 0.2 m for the detailed one's).
    Returns the links made (also in state.fine_links)."""
    coarse_level, fine_level = normal['level'], detailed['level']
    coarse_samples, fine_samples = coarse_level.s, fine_level.s
    coarse_pitch, fine_pitch = normal['pitch'], DETAILED_PITCH
    made = 0
    next_id = [detailed['next_id'] + 500000]

    def new_id() -> int:
        next_id[0] += 1
        return next_id[0]
    for sector, beside, dX, dY in _mixed_seams(detailed_secs, by_sec, normal['info']):
        info = normal['info'][beside]
        area_of = info['area_of']
        x0, z0, seam = _seam_line(sector, dX, dY, xoff, zoff)
        # the 0.8 m surfaces of the normal sector in the band, in pieces under the 0.8 m step rule
        cols = set()
        for along in range(int(100 / coarse_pitch) + 1):
            for into in range(int(SEAM_FINE_BAND / coarse_pitch) + 1):
                x = (seam + dX * into * coarse_pitch) if dX else (x0 + along * coarse_pitch)
                z = (z0 + along * coarse_pitch) if dX else (seam + dY * into * coarse_pitch)
                cols.add((int(round((x - coarse_samples.min_x) / coarse_pitch)),
                          int(round((z - coarse_samples.min_z) / coarse_pitch))))
        coarse = {k: (gi, gj) for gi, gj in cols for k in coarse_level.surfaces(gi, gj) if k in area_of}
        piece = _pieces(coarse_level, coarse)
        if len(set(piece.values())) < 2:
            continue
        # the 0.2 m surfaces under them, in pieces under the 0.2 m step rule; each mapped to the 0.8 m surface
        # of its column within 0.4 m of its height
        fine = {}
        for k, (gi, gj) in coarse.items():
            for fi, fj in fine_columns(coarse_samples, fine_samples, gi, gj, coarse_pitch, fine_pitch):
                for f in fine_level.surfaces(fi, fj):
                    if abs(float(fine_level.y[f]) - float(coarse_level.y[k])) <= 0.4:
                        fine.setdefault(f, (fi, fj, k))
        fine_piece = _pieces(fine_level, fine)
        # a fine piece on two coarse pieces: link a side-by-side pair of their surfaces, once per pair
        touched = defaultdict(set)
        for f, root in fine_piece.items():
            touched[root].add(fine[f][2])
        done = set()
        for _root, surfaces in sorted(touched.items()):
            for k1 in sorted(surfaces):
                for k2 in sorted(surfaces):
                    piece1, piece2 = piece[k1], piece[k2]
                    if piece1 >= piece2 or (piece1, piece2) in done:
                        continue
                    (gi1, gj1), (gi2, gj2) = coarse[k1], coarse[k2]
                    if max(abs(gi1 - gi2), abs(gj1 - gj2)) != 1:
                        continue
                    done.add((piece1, piece2))
                    _link_surfaces(coarse_level, by_sec[beside], k1, k2, coarse, info['origin'], coarse_pitch, new_id,
                                   state.fine_links)
                    made += 1
    return made


def seam_far_links(normal: 'StageResult', detailed: 'StageResult', by_sec: 'dict[SectorKey, SectorGraph]',
                   detailed_secs: 'Collection[SectorKey]', xoff: int, zoff: int, state: 'BuildState') -> int:
    """SEAM_FAR_LINKS: SEAM_FINE_LINKS for 0.8 m pieces two columns apart (2026-10-29). Iwo Jima 0b0d: a strip of four
    0.8 m surfaces 0.8-2.4 m past the seam and the main network's area beside it, joined by a 30-degree slope of 0.2 m
    kept ground (steps of 0.20 m at most), with no 0.8 m surface kept in the columns between (their one 0.8 m sample
    lies on a steep facet), so no two side-by-side columns ever met and the strip stayed an island of 3 nodes.
    Within SEAM_FINE_BAND of the seam and one column past it, the 0.8 m kept surfaces fall into pieces under the 0.8 m
    step rule; the 0.2 m kept surfaces under those columns (within 0.4 m of the 0.8 m surface) and under the columns
    with no 0.8 m surface (all of them) fall into pieces under the 0.2 m step rule. Two 0.8 m surfaces of different
    pieces, two columns apart (one of them in the band), that one 0.2 m piece stands on get a walk edge when the build's
    own line rule (line_walkable) walks the 0.2 m samples from one to the other; once per pair of pieces. The side-by-
    side pairs stay SEAM_FINE_LINKS' (it runs first, unchanged). by_sec: {sector: merged graph tuple}, changed in place.
    Each level's step rule is its own grid's. Returns the links made (also in state.far_links)."""
    coarse_level, fine_level = normal['level'], detailed['level']
    coarse_samples, fine_samples = coarse_level.s, fine_level.s
    coarse_pitch, fine_pitch = normal['pitch'], DETAILED_PITCH
    made = 0
    next_id = [detailed['next_id'] + 600000]

    def new_id() -> int:
        next_id[0] += 1
        return next_id[0]

    def under(gi: int, gj: int) -> list[tuple[int, int]]:
        return fine_columns(coarse_samples, fine_samples, gi, gj, coarse_pitch, fine_pitch)
    for sector, beside, dX, dY in _mixed_seams(detailed_secs, by_sec, normal['info']):
        info = normal['info'][beside]
        area_of = info['area_of']
        x0, z0, seam = _seam_line(sector, dX, dY, xoff, zoff)
        band_cols = int(SEAM_FINE_BAND / coarse_pitch)
        cols, band = set(), set()
        for along in range(int(100 / coarse_pitch) + 1):
            for into in range(band_cols + 2):
                x = (seam + dX * into * coarse_pitch) if dX else (x0 + along * coarse_pitch)
                z = (z0 + along * coarse_pitch) if dX else (seam + dY * into * coarse_pitch)
                col = (int(round((x - coarse_samples.min_x) / coarse_pitch)),
                       int(round((z - coarse_samples.min_z) / coarse_pitch)))
                cols.add(col)
                if into <= band_cols:
                    band.add(col)
        coarse = {k: (gi, gj) for gi, gj in cols for k in coarse_level.surfaces(gi, gj) if k in area_of}
        piece = _pieces(coarse_level, coarse)
        if len(set(piece.values())) < 2:
            continue
        fine = {}
        for k, (gi, gj) in coarse.items():
            for fi, fj in under(gi, gj):
                for f in fine_level.surfaces(fi, fj):
                    if abs(float(fine_level.y[f]) - float(coarse_level.y[k])) <= 0.4:
                        fine.setdefault(f, (fi, fj, k))
        # every other 0.2 m kept surface under the columns carries the flood but no 0.8 m surface: those under
        # a column with no 0.8 m surface, and those further than 0.4 m from it (on a 30-degree slope a 0.8 m
        # column spans 0.46 m of rise, so the 0.4 m match alone breaks the slope into pieces)
        for gi, gj in sorted(cols):
            for fi, fj in under(gi, gj):
                for f in fine_level.surfaces(fi, fj):
                    fine.setdefault(f, (fi, fj, None))
        fine_piece = _pieces(fine_level, fine)
        # per fine piece, per coarse surface it stands on: the fine surface nearest that column's centre
        touched = defaultdict(dict)
        for f, root in fine_piece.items():
            fi, fj, k = fine[f]
            if k is None:
                continue
            gi, gj = coarse[k]
            dist2 = (fine_samples.min_x + fi * fine_pitch - coarse_samples.min_x - gi * coarse_pitch) ** 2 + \
                (fine_samples.min_z + fj * fine_pitch - coarse_samples.min_z - gj * coarse_pitch) ** 2
            if k not in touched[root] or dist2 < touched[root][k][1]:
                touched[root][k] = (f, dist2)
        done = set()
        for _root, nearest in sorted(touched.items()):
            for k1 in sorted(nearest):
                for k2 in sorted(nearest):
                    piece1, piece2 = piece[k1], piece[k2]
                    if piece1 >= piece2 or (piece1, piece2) in done:
                        continue
                    (gi1, gj1), (gi2, gj2) = coarse[k1], coarse[k2]
                    if max(abs(gi1 - gi2), abs(gj1 - gj2)) != 2 or \
                            (coarse[k1] not in band and coarse[k2] not in band):
                        continue
                    f1, f2 = nearest[k1][0], nearest[k2][0]
                    ok = line_walkable(fine_level, f1, fine[f1][:2], fine[f2][:2], 0, 0, f2)
                    if not ok:
                        continue
                    done.add((piece1, piece2))
                    _link_surfaces(coarse_level, by_sec[beside], k1, k2, coarse, info['origin'], coarse_pitch, new_id,
                                   state.far_links)
                    made += 1
    return made


SEAM_REACH = 1.0                # seam_gap_nodes: each grid's ground for a gap node lies within this of the seam (m)


def seam_gap_nodes(normal: 'StageResult', detailed: 'StageResult', by_sec: 'dict[SectorKey, SectorGraph]',
                   detailed_secs: 'Collection[SectorKey]', xoff: int, zoff: int, state: 'BuildState'
                   ) -> tuple[int, int, int]:
    """SEAM_GAP_NODES: the shared nodes of a mixed-pitch seam come from the normal build (one per BORDER_NODE_SPACING
    of its ground along the seam) and are carried to the detailed one; detailed ground at the seam that no carried node
    reaches - a 0.2 m opening the 0.8 m run places no node near, a carried node dropped for want of ground within
    CARRY_RADIUS, or a copy that joined no node of its sector - was not joined across the seam at all (Wake Island's
    Japanese carrier, Coral Sea's islets: 2026-10-24). Along every mixed seam, each stretch where both grids have
    kept ground of their own sector within SEAM_REACH of the seam, within 0.6 m of each other's height, and no shared
    node with edges in both sectors lies within CARRY_RADIUS + 0.8 m of it (and 0.6 m of its height), gets one shared
    node on the seam (the stretch's middle first), joined into both
    sector graphs by walkable lines (join_groups when none reaches). by_sec: {sector: merged graph tuple}, changed in
    place. Returns (nodes added, stretches found, nodes that joined only the detailed side)."""
    coarse_level, fine_level = normal['level'], detailed['level']
    yoff, fine_step = fine_level.settings.y_offset, fine_level.settings.detailed_link_step
    next_id = [detailed['next_id']]

    def new_id() -> int:
        next_id[0] += 1
        return next_id[0]

    def linked_shared(sec: 'SectorKey') -> 'dict[int, Vec3]':
        pts, pairs = by_sec[sec][3], by_sec[sec][4]
        has_edge = set()
        for u, v, _dist in pairs:
            has_edge.update((u, v))
        return {pts[q][0]: pts[q][1] for q in has_edge if pts[q][0] & 0x80000000}

    def ground(level: 'Level', info: 'dict[SectorKey, SectorInfo]', sec: 'SectorKey', x: float, z: float,
               inward: tuple[int, int], step: float) -> list[tuple[int, int, int]]:
        """Kept surfaces of the sector's own areas at the column nearest the seam (within SEAM_REACH inward)."""
        s = level.s
        area_of = info[sec]['area_of']
        for into in range(int(SEAM_REACH / step + 1e-6) + 1):
            px, pz = x + inward[0] * into * step, z + inward[1] * into * step
            gi, gj = int(round((px - s.min_x) / step)), int(round((pz - s.min_z) / step))
            surfaces = [k for k in level.surfaces(gi, gj) if k in area_of]
            if surfaces:
                return [(k, gi, gj) for k in surfaces]
        return []

    def attach(level: 'Level', info: 'dict[SectorKey, SectorInfo]', sec: 'SectorKey', nid: int, pos: 'Vec3', k: int,
               gi: int, gj: int) -> bool:
        """The shared node on surface k of the sector's graph, linked by walkable lines (on the level's own grid)."""
        gi0, gj0, ox, oz = info[sec]['origin']
        pts, pairs = by_sec[sec][3], by_sec[sec][4]
        if any(point[2] == k for point in pts):                     # one node per surface
            return False
        i, j = gi - gi0, gj - gj0
        pts.append((nid, pos, k, i, j))
        own = len(pts) - 1
        for other, point in enumerate(pts[:-1]):
            dist = math.dist(pos, point[1])
            if dist <= DIST_EDGE_MAX and line_walkable(level, k, (i, j), (point[3], point[4]), gi0, gj0, point[2]):
                pairs.append((own, other, dist))
        if not any(own in (u, v) for u, v, _dist in pairs):
            join = JoinScope(sec, set(info), info[sec].get('win'), state.detailed_sectors)
            join_groups(level, info[sec]['areas'], pts, pairs, gi0, gj0, ox, oz, new_id, join)
        return any(own in (u, v) for u, v, _dist in pairs)

    added = runs_found = one_side = 0
    coarse_pitch, fine_pitch = normal['pitch'], DETAILED_PITCH
    for sector, beside, dX, dY in _mixed_seams(detailed_secs, by_sec, normal['info']):
        x0, z0 = (sector[0] + xoff) * 100.0, (sector[1] + zoff) * 100.0
        # the seam line, the direction along it, and inward (into the detailed sector, into the normal one)
        if dX:
            seam_x = x0 + (100.0 if dX > 0 else 0.0)
            along = [(seam_x, z0 + t * fine_pitch) for t in range(int(100 / fine_pitch))]
        else:
            seam_z = z0 + (100.0 if dY > 0 else 0.0)
            along = [(x0 + t * fine_pitch, seam_z) for t in range(int(100 / fine_pitch))]
        into_detailed, into_normal = (-dX, -dY), (dX, dY)
        shared = {nid: pos for nid, pos in linked_shared(sector).items() if nid in linked_shared(beside)}
        # stretches of fine ground (a step of at most fine_step from one seam column to the next) that coarse ground
        # meets: per seam column t, item (t, x, z, fine y, (f, fi, fj), (k, gi, gj))
        runs, active = [], []
        for t, (x, z) in enumerate(along):
            fine_ground = ground(fine_level, detailed['info'], sector, x, z, into_detailed, fine_pitch)
            coarse_ground = ground(coarse_level, normal['info'], beside, x, z, into_normal, coarse_pitch)
            still_active = []
            for f, fi, fj in fine_ground:
                y_fine = float(fine_level.y[f])
                matches = [m for m in coarse_ground if abs(float(coarse_level.y[m[0]]) - y_fine) <= 0.6]
                if not matches:
                    continue
                match = min(matches, key=lambda m: abs(float(coarse_level.y[m[0]]) - y_fine))
                item = (t, x, z, y_fine, (f, fi, fj), match)
                for run in active:
                    if run[-1][0] == t - 1 and abs(run[-1][3] - y_fine) <= fine_step:
                        run.append(item)
                        still_active.append(run)
                        active.remove(run)
                        break
                else:
                    run = [item]
                    runs.append(run)
                    still_active.append(run)
            active = still_active
        for run in runs:
            # a shared node with edges in both sectors near the stretch: within CARRY_RADIUS + 0.8 m, 0.6 m in height
            covered = any(
                min(math.hypot(node_xyz[0] - entry[1], node_xyz[2] - entry[2]) for entry in run) <= CARRY_RADIUS + 0.8
                and min(abs(node_xyz[1] - yoff - entry[3]) for entry in run) <= 0.6
                for node_xyz in shared.values())
            if covered:
                continue
            runs_found += 1
            mid = len(run) // 2
            for entry in sorted(run, key=lambda entry: abs(entry[0] - run[mid][0])):
                _t, x, z, y_fine, (f, fi, fj), (k, gi, gj) = entry
                nid = 0x80000000 | new_id()
                pos = (x, y_fine + yoff, z)
                if not attach(fine_level, detailed['info'], sector, nid, pos, f, fi, fj):
                    continue
                if attach(coarse_level, normal['info'], beside, nid, pos, k, gi, gj):
                    added += 1
                else:
                    one_side += 1
                break
    return added, runs_found, one_side
