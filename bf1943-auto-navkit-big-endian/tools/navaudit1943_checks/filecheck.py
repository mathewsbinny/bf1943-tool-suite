"""navaudit1943_checks, check 3: file consistency and crossings.

consistency reads the build's .hpd (hpdtool, kymesh) for one-way portals, nodes sharing a position, self-loop edges
and border-node owners, with hpdcheck's findings; crossings finds where touching walkable ground of one floor lies on
two islands and tests each place on the kept samples (a real break, or not joined on the ground).
"""
import collections
import math
from collections import deque
from typing import TYPE_CHECKING, Any, cast

import numpy as np

import soldier1943  # noqa: E402

from .loading import grid_at

if TYPE_CHECKING:
    from .loading import AuditContext


def _rounded_ends(portal: Any) -> set[tuple[float, float]]:
    """A portal's two ends on xz, rounded to 1 cm: the key a portal and its way back share."""
    return {(round(portal.p0[0], 2), round(portal.p0[2], 2)), (round(portal.p1[0], 2), round(portal.p1[2], 2))}


# ------------------------------------------------------------------------------------------------ 3: consistency

def consistency(ctx: 'AuditContext') -> dict[str, Any]:
    """hpdcheck's findings, one-way portals, nodes sharing a position, self-loop edges and border-node owners."""
    import hpdcheck
    import hpdtool
    import kymesh
    paths = ctx['p']
    out: dict[str, Any] = {'hpdcheck': hpdcheck.check(paths['hpd'])}
    hpd = hpdtool.parse(open(paths['hpd'], 'rb').read())
    one_way, dup_nodes, self_loops = [], [], []
    owners = collections.defaultdict(list)          # border id -> [(sector, owns it: bit 16 clear)]
    for sector in hpd.sectors:
        mesh = kymesh.parse_mesh(sector.mesh)
        for area_index, area in enumerate(mesh.areas):
            for portal in area.portals:
                target = mesh.areas[portal.target] if 0 <= portal.target < len(mesh.areas) else None
                key = _rounded_ends(portal)
                back = target is not None and any(other.target == area_index and _rounded_ends(other) == key
                                                   for other in target.portals)
                if not back:
                    p0, p1 = portal.p0, portal.p1
                    one_way.append(f'sector {sector.id:04x} area {area_index} -> {portal.target} at '
                                   f'({p0[0]:.1f}, {p0[1]:.1f}, {p0[2]:.1f})-({p1[0]:.1f}, {p1[2]:.1f})')
        seen = collections.defaultdict(list)        # rounded position -> node ids
        for node in sector.nodes:
            seen[tuple(round(v, 3) for v in node.pos)].append(node.id & 0xFFFFFFFF)
            if node.id & 0x80000000:
                owners[node.id & 0xFFFFFFFF].append((sector.id, not (node.c >> 16) & 1))
        for pos, ids in seen.items():
            if len(set(ids)) > 1:
                dup_nodes.append(f'sector {sector.id:04x}: {len(ids)} nodes at {pos}')
        for edge in sector.edges:
            if edge.src == edge.dst:
                self_loops.append(f'sector {sector.id:04x}: edge {edge.src} -> itself')
    # border portals are not matched piece by piece: see crossings (each side splits them its own way)
    owner_counts = collections.Counter(sum(1 for _sector, owns in copies if owns) for copies in owners.values())
    owners_per_id = {str(k): v for k, v in sorted(owner_counts.items())}
    out.update({
        'portals one-way': one_way,
        'nodes sharing a position': dup_nodes, 'self-loop edges': self_loops,
        'border node owners (copies without bit 16, per border id)': owners_per_id,
        'border nodes': len(owners),
    })
    return out


def crossings(ctx: 'AuditContext') -> list[dict[str, Any]]:
    """Where walkable ground of one floor touches another area, can the route graph get across? Build cells side by
    side (0.8 m, within the step) in different areas whose areas lie on different islands; each such place is then
    tested on the kept samples: a flood under the build's own step (0.37 m on 0.2 m grids, LINK_STEP 0.6 m on 0.8 m)
    from the smaller island's nodes that reaches ground under the other island's nodes is a real break (a seam or
    portal the graph does not cross); otherwise the two sides are not joined on the ground either (a pocket the 0.8 m
    seeds kept that the 0.2 m samples wall off, or the reader giving a node-less area a neighbour's island).
    The exact-match count of unpaired border portals (consistency) is no test: each side places its border portals 1.2
    (0.8 m) or 0.3 m (0.2 m) inside its own edge and splits them differently - DICE's files show 21-32 % uncovered."""
    nav_index = ctx['nav']
    nav = nav_index.nav
    area_isl = np.array([area.island for area in nav.areas])
    area_sec = np.array([area.sector for area in nav.areas])
    cells_at = collections.defaultdict(list)        # 0.8 m grid position -> cells
    for k, (x, z) in enumerate(nav.cell_xz):
        cells_at[(int(round(x / 0.8)), int(round(z / 0.8)))].append(k)
    # (island, island) -> the touching cell pairs: how many, whether across a sector seam, where
    groups: collections.defaultdict[tuple[int, ...], dict[str, Any]] = collections.defaultdict(
        lambda: {'pairs': 0, 'seam': False, 'xyz': []})
    for (i, j), cells in cells_at.items():
        for di, dj in ((1, 0), (0, 1)):
            for k in cells:
                for m in cells_at.get((i + di, j + dj), ()):
                    island_a, island_b = area_isl[nav.cell_area[k]], area_isl[nav.cell_area[m]]
                    if island_a == island_b or abs(nav.cell_y[k] - nav.cell_y[m]) > soldier1943.STEP:
                        continue
                    group = groups[tuple(sorted((int(island_a), int(island_b))))]
                    group['pairs'] += 1
                    group['seam'] |= bool(area_sec[nav.cell_area[k]] != area_sec[nav.cell_area[m]])
                    group['xyz'].append((float(nav.cell_xz[k, 0]), float(nav.cell_y[k]), float(nav.cell_xz[k, 1])))
    ground = nav_index.node_ground
    out = []
    for isl, group in groups.items():
        centre = np.mean(group['xyz'], 0)
        small, big = sorted(isl, key=lambda i: nav.islands[i].nodes)
        grid = grid_at(ctx, centre[0], centre[2])
        s, keep = grid.s, grid.keep
        step = soldier1943.STEP if s.pitch < 0.5 else 0.6

        def surf(x: float, y: float, z: float) -> int | None:
            """The kept surface of the column at (x, z) nearest y, within 0.5 m."""
            column = grid.col(x, z)
            if column is None:
                return None
            near_y = [k for k in grid.surfaces(column) if keep[k] and abs(s.y[k] - y) < 0.5]
            return min(near_y, key=lambda k: abs(s.y[k] - y)) if near_y else None

        def near(island: int) -> list[np.intp]:
            """The island's route nodes within 30 m of the place."""
            return [k for k in np.nonzero(nav.node_island == island)[0]
                    if math.hypot(nav.node_pos[k, 0] - centre[0], nav.node_pos[k, 2] - centre[2]) < 30]
        starts = {surf(nav.node_pos[k, 0], ground[k], nav.node_pos[k, 2]) for k in near(small)} - {None}
        goals = {surf(nav.node_pos[k, 0], ground[k], nav.node_pos[k, 2]) for k in near(big)} - {None}
        seen, queue, hit = set(starts), deque(starts), False
        col_of = grid.col_of()
        while queue and not hit:
            k = queue.popleft()
            column = cast(int, col_of[k])           # one surface's column
            i, j = column % s.nx, column // s.nx
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    ni, nj = i + di, j + dj
                    if not (di or dj) or not (0 <= ni < s.nx and 0 <= nj < s.nz):
                        continue
                    if math.hypot(s.min_x + ni * s.pitch - centre[0], s.min_z + nj * s.pitch - centre[2]) > 30:
                        continue
                    for n in grid.surfaces(nj * s.nx + ni):
                        if keep[n] and n not in seen and abs(s.y[n] - s.y[k]) <= step:
                            seen.add(n)
                            queue.append(n)
                            hit |= n in goals
        out.append({'centre': [round(float(v), 1) for v in centre], 'cell_pairs': group['pairs'],
                    'across_a_seam': group['seam'], 'grid': grid.label, 'islands': [int(i) for i in isl],
                    'island_nodes': [nav.islands[i].nodes for i in isl], 'main': nav_index.main in isl,
                    'verdict': 'real break' if hit else 'not joined on the ground'})
    out.sort(key=lambda record: (record['verdict'] != 'real break', -min(record['island_nodes'])))
    return out
