"""NavKit's builder (navbuild), the route graph of a sector: nodes at area centres, portals and borders (and
coverage, order and ladder nodes), walk edges along straight walkable lines, groups joined through string-pulled
waypoints (join_groups), and a sector's final node and edge tables (finalize_graph) (pipeline step 6)."""
import heapq
import math
from collections import defaultdict, deque
from collections.abc import Callable, Container, Iterable, Sequence
from typing import TYPE_CHECKING, cast

import numpy as np

import hpdtool

from .config import (BORDER_OWNER, DEDUP_GRAPH_NODES, DETOUR_LINK_ALL, DETOUR_MAX, DETOUR_PASSES, DETOUR_REPAIR,
    DETOUR_TRIGGER, DIST_EDGE_MAX, GABRIEL_EDGES, NODE_COVER, ORDER_MIN_ISLAND, ORDER_NODE_MIN, ORDER_NODES,
    SORT_NODES_BY_ID, BuildState)

if TYPE_CHECKING:
    from .config import AreaDict, BorderNode, GraphPair, GraphPoint, JoinScope, NewId, Points, SectorGraph, Traced
    from .inputs import Level


def line_walkable(level: 'Level', k0: int, start: tuple[int, int], end: tuple[int, int], gi0: int, gj0: int, k1: int
                  ) -> bool:
    """Straight line from local sample start to end (i, j) over connected kept surfaces, starting on k0, ending on
    k1."""
    (i0, j0), (i1, j1) = start, end
    steps = max(abs(i1 - i0), abs(j1 - j0))
    k, cur_i, cur_j = k0, i0, j0
    for t in range(1, steps + 1):
        next_i = i0 + round((i1 - i0) * t / steps)
        next_j = j0 + round((j1 - j0) * t / steps)
        di, dj = next_i - cur_i, next_j - cur_j
        if di or dj:
            next_k = level.neighbour(k, gi0 + cur_i, gj0 + cur_j, di, dj)
        else:
            continue
        if next_k is None:
            return False
        k, cur_i, cur_j = next_k, next_i, next_j
    return k == k1 or abs(level.y[k] - level.y[k1]) <= 0.6


def _walkable_between(level: 'Level', a: 'GraphPoint', b: 'GraphPoint', gi0: int, gj0: int) -> bool:
    """line_walkable from route node a to route node b, each (id, pos, surface k, local i, local j)."""
    return line_walkable(level, a[2], (a[3], a[4]), (b[3], b[4]), gi0, gj0, b[2])


def _string_pull(level: 'Level', path: list[int], cell_ij: dict[int, tuple[int, int]], gi0: int, gj0: int
                 ) -> list[int]:
    """String pulling: the surfaces of path (a walk from surface to surface) where its straight walkable legs (each at
    most DIST_EDGE_MAX) meet - the first, then from each the farthest one a leg reaches."""
    pitch = level.grid.pitch
    way = [path[0]]
    pull_from = 0
    while pull_from < len(path) - 1:
        pull_to = len(path) - 1
        while pull_to > pull_from + 1 and not (
                math.dist(cell_ij[path[pull_from]], cell_ij[path[pull_to]]) * pitch <= DIST_EDGE_MAX and
                line_walkable(level, path[pull_from], cell_ij[path[pull_from]], cell_ij[path[pull_to]], gi0, gj0,
                              path[pull_to])):
            pull_to -= 1
        way.append(path[pull_to])
        pull_from = pull_to
    return way


def join_groups(level: 'Level', areas: 'Sequence[AreaDict]', pts: 'list[GraphPoint]', pairs: 'list[GraphPair]',
                gi0: int, gj0: int, ox: float, oz: float, new_id: 'NewId', join: 'JoinScope | None' = None
                ) -> tuple[dict[int, int], dict[int, tuple[int, int]]]:
    """Connect a sector graph's remaining groups: grid search (8-connected, step rule, within the sector's
    window) from one group to the nearest node of another, then string-pull the path into straight walkable legs
    with waypoint nodes. pts and pairs grow in place. join: the JoinScope of the sector (its window, and where the
    flood may go past it); without one, the grid's window only. Returns (node_at, cell_ij) for the passes after it."""
    grid, yoff = level.grid, level.settings.y_offset
    pitch = grid.pitch
    parent = list(range(len(pts)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for node_a, node_b, _ in pairs:
        parent[find(node_a)] = find(node_b)
    node_at = {}
    for idx, point in enumerate(pts):
        node_at.setdefault(point[2], idx)
    cell_ij = {c[2]: (c[0], c[1]) for area in areas for c in area['cells']}
    for point in pts:
        cell_ij.setdefault(point[2], (point[3], point[4]))
    # the flood's bounds: the sector's window, and the settings' join_margin beyond it (ground joined only round a
    # corner of the neighbour sector - Coral Sea's strip beside the South Base bunker, closed inside 0b0a's window and
    # joined 1 m past it, 2026-10-26)
    margin = int(math.ceil((level.settings.join_margin or 0.0) / pitch - 1e-9))
    range_i, range_j = join.window if join is not None and join.window else (grid.window, grid.window)
    lo_i, hi_i, lo_j, hi_j = range_i[0] - margin, range_i[-1] + margin, range_j[0] - margin, range_j[-1] + margin

    def beyond_ok(i: int, j: int) -> bool:
        # past the window only into a sector of this stage (this grid's pitch): a 0.2 m flood into a 0.8 m sector
        # would walk on samples the 0.8 m sector's cells do not show
        if join is None:
            return False
        X, Y = join.sector
        sector = (X + i // grid.sector_samples, Y + j // grid.sector_samples)
        return sector in join.sectors and (sector in join.detailed_sectors) == grid.detailed
    changed = True
    while changed:
        changed = False
        groups = defaultdict(list)
        for idx in range(len(pts)):
            groups[find(idx)].append(idx)
        if len(groups) < 2:
            break
        for root, members in sorted(groups.items(), key=lambda group: len(group[1])):
            if find(members[0]) != root:
                continue
            prev: dict[int, int | None] = {pts[member][2]: None for member in members}
            queue = deque(pts[member][2] for member in members)
            target = None
            while queue and target is None:
                k = queue.popleft()
                i, j = cell_ij[k]
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        if not (di or dj) or not (lo_i <= i + di <= hi_i and lo_j <= j + dj <= hi_j):
                            continue
                        if not (i + di in range_i and j + dj in range_j) and not beyond_ok(i + di, j + dj):
                            continue
                        n = level.neighbour(k, gi0 + i, gj0 + j, di, dj)
                        if n is None or n in prev:
                            continue
                        prev[n] = k
                        cell_ij.setdefault(n, (i + di, j + dj))
                        if n in node_at and find(node_at[n]) != root:
                            target = n
                            break
                        queue.append(n)
                    if target is not None:
                        break
            if target is None:
                continue
            path = [target]
            while prev[path[-1]] is not None:
                path.append(cast(int, prev[path[-1]]))   # not None: the loop's test
            path.reverse()                       # from a member's surface to the target's surface
            way = _string_pull(level, path, cell_ij, gi0, gj0)
            chain = []
            for k in way:
                if k in node_at:
                    chain.append(node_at[k])
                else:
                    i, j = cell_ij[k]
                    pts.append((new_id(), (ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch), k, i, j))
                    parent.append(len(pts) - 1)
                    node_at[k] = len(pts) - 1
                    chain.append(len(pts) - 1)
            for u, v in zip(chain, chain[1:]):
                pairs.append((u, v, math.dist(pts[u][1], pts[v][1])))
                parent[find(u)] = find(v)
            changed = True
    return node_at, cell_ij


def build_graph(level: 'Level', areas: 'Sequence[AreaDict]', area_of: dict[int, int], traced: 'Sequence[Traced]',
                borders: 'Sequence[BorderNode]', gi0: int, gj0: int, ox: float, oz: float, new_id: 'NewId',
                orders: 'Points' = (), ladders: Sequence[tuple[int, int]] = (), join: 'JoinScope | None' = None,
                state: BuildState | None = None) -> 'tuple[list[GraphPoint], list[GraphPair]]':
    """A sector's route graph: (pts, pairs). join: its JoinScope (join_groups); state: the build's BuildState (order
    nodes and repair counts are added to it)."""
    state = state if state is not None else BuildState()
    grid, yoff = level.grid, level.settings.y_offset
    pitch = grid.pitch
    pts: list[GraphPoint] = []    # (id, pos, k, local i, local j)
    # one node per surface: an area-centre or portal node whose surface already holds a node (another area's or
    # portal's, or a border node) is not added - DICE's route graphs have no two nodes at one position except a
    # border node in both of its sectors (sp_004_b: 1,515 shared positions, all border pairs with one id); NavKit
    # had 377 duplicate pairs joined by zero-length edges (2026-10-20)
    taken = {k for _nid, _pos, k in borders} if DEDUP_GRAPH_NODES else set()

    def add(i: int, j: int, k: int) -> None:
        if i not in grid.window or j not in grid.window:
            # a cell of a window grown past a mixed seam (MIXED_OVERLAP): there for the cells the runtime reads, not
            # for nodes - ground joined to the sector only through the other grid's side made islands of 2-5 nodes
            # (Guadalcanal 1, Iwo Jima 3 seam breaks, 2026-10-26)
            return
        if DEDUP_GRAPH_NODES:
            if k in taken:
                return
            taken.add(k)
        pts.append((new_id(), (ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch), k, i, j))
    for area in areas:
        if len(area['cells']) < 4:
            continue
        centre_i = sum(c[0] for c in area['cells']) / len(area['cells'])
        centre_j = sum(c[1] for c in area['cells']) / len(area['cells'])
        i, j, k = min(area['cells'], key=lambda c: (c[0] - centre_i) ** 2 + (c[1] - centre_j) ** 2)
        add(i, j, k)
    cell_of = {c[2]: (c[0], c[1]) for area in areas for c in area['cells']}
    for ai, (portals, _b, _o) in enumerate(traced):
        for portal in portals:
            if portal.target < ai:
                continue          # one node per portal pair
            mid_x, mid_z = (portal.p0[0] + portal.p1[0]) / 2, (portal.p0[2] + portal.p1[2]) / 2
            i, j, k = min(areas[ai]['cells'],
                          key=lambda c: (ox + c[0] * pitch - mid_x) ** 2 + (oz + c[1] * pitch - mid_z) ** 2)
            add(i, j, k)
    for nid, pos, k in borders:
        # the node's own surface cell (a node carried from another pitch's build sits up to CARRY_RADIUS from it;
        # walkable lines must start on that cell, not on the cell under the position)
        i, j = cell_of.get(k) or (int(round((pos[0] - ox) / pitch)), int(round((pos[2] - oz) / pitch)))
        pts.append((nid, pos, k, i, j))
    cover = NODE_COVER.get('detailed' if grid.detailed else 'normal')
    if cover:
        add_cover_nodes(level, areas, pts, add, ox, oz, cover)
    # edges: pairs within DIST_EDGE_MAX whose straight line is walkable
    bucket_size = DIST_EDGE_MAX
    buckets = defaultdict(list)
    for idx, point in enumerate(pts):
        buckets[(int(point[1][0] // bucket_size), int(point[1][2] // bucket_size))].append(idx)
    pairs: list[GraphPair] = []
    for idx, point in enumerate(pts):
        bucket_x, bucket_z = int(point[1][0] // bucket_size), int(point[1][2] // bucket_size)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other in buckets.get((bucket_x + dx, bucket_z + dz), ()):
                    if other <= idx:
                        continue
                    dist = math.dist(point[1], pts[other][1])
                    if dist <= DIST_EDGE_MAX and _walkable_between(level, point, pts[other], gi0, gj0):
                        pairs.append((idx, other, dist))
    if GABRIEL_EDGES == 'all' or (GABRIEL_EDGES == 'detailed' and grid.detailed):
        pairs = gabriel_prune(pts, pairs)
    for k_foot, k_top in ladders:
        # a ladder link: a node on the ground at its foot and at its top, joined directly (the climb is no walkable
        # line); the runtime's mover walks the bot into the ladder, which takes any soldier who walks into it
        ends = []
        for k in (k_foot, k_top):
            idx = next((other for other, point in enumerate(pts) if point[2] == k), None)
            if idx is None:
                i, j = cell_of[k]
                pts.append((new_id(), (ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch), k, i, j))
                idx = len(pts) - 1
            ends.append(idx)
        if ends[0] != ends[1]:               # a short ladder whose foot and top found one surface: no self edge
            pairs.append((ends[0], ends[1], math.dist(pts[ends[0]][1], pts[ends[1]][1])))
    # connect remaining groups (join_groups)
    node_at, cell_ij = join_groups(level, areas, pts, pairs, gi0, gj0, ox, oz, new_id, join)
    if DETOUR_REPAIR:
        for repair_pass in range(DETOUR_PASSES):
            added = repair_detours(level, pts, pairs, node_at, cell_ij, gi0, gj0, ox, oz, new_id)
            state.repair_stats[repair_pass] = state.repair_stats.get(repair_pass, 0) + added
    if ORDER_NODES and orders:
        added = add_order_nodes(level, pts, pairs, node_at, area_of, orders, gi0, gj0, ox, oz, new_id, state.order_ids)
        state.order_stats['added'] = state.order_stats.get('added', 0) + added
    return pts, pairs


def add_cover_nodes(level: 'Level', areas: 'Sequence[AreaDict]', pts: 'list[GraphPoint]',
                    add: Callable[[int, int, int], None], ox: float, oz: float, cover: float) -> None:
    """NODE_COVER: in every area of 4+ cells, add route nodes (through add, so one per surface) at the cell farthest
    from all nodes on the same floor (|dy| < 2.5 m) until no cell is farther than cover metres."""
    pitch, yoff = level.grid.pitch, level.settings.y_offset
    for area in areas:
        if len(area['cells']) < 4:
            continue
        cells_xyz = np.array([(ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch) for i, j, k in area['cells']])
        lo_x, hi_x = cells_xyz[:, 0].min() - cover, cells_xyz[:, 0].max() + cover
        lo_z, hi_z = cells_xyz[:, 2].min() - cover, cells_xyz[:, 2].max() + cover
        nearby = [point[1] for point in pts if lo_x <= point[1][0] <= hi_x and lo_z <= point[1][2] <= hi_z]
        nodes_xyz = np.array(nearby, float).reshape(-1, 3)
        dist = np.full(len(cells_xyz), np.inf)
        for node in nodes_xyz:
            same = np.abs(cells_xyz[:, 1] - node[1]) < 2.5
            flat = np.hypot(cells_xyz[:, 0] - node[0], cells_xyz[:, 2] - node[2])
            dist = np.where(same, np.minimum(dist, flat), dist)
        while True:
            farthest = int(np.argmax(dist))
            if dist[farthest] <= cover:
                break
            i, j, k = area['cells'][farthest]
            count_before = len(pts)
            add(i, j, k)
            placed = cells_xyz[farthest]
            dist[farthest] = 0.0                               # placed (or the surface already held a node)
            if len(pts) > count_before:
                same = np.abs(cells_xyz[:, 1] - placed[1]) < 2.5
                flat = np.hypot(cells_xyz[:, 0] - placed[0], cells_xyz[:, 2] - placed[2])
                dist = np.where(same, np.minimum(dist, flat), dist)


def gabriel_prune(pts: 'Sequence[GraphPoint]', pairs: 'Iterable[GraphPair]') -> 'list[GraphPair]':
    """The pairs that are Gabriel edges of the sector's nodes (no other node within 2.5 m of height inside the circle
    with the pair as diameter), plus the shortest dropped pairs needed to keep every group of nodes the full pair set
    joins joined (so pruning never splits a route)."""
    positions = np.array([point[1] for point in pts], float)
    keep, rest = [], []
    for node_a, node_b, dist in pairs:
        mid = (positions[node_a] + positions[node_b]) / 2
        radius2 = (dist / 2) ** 2 - 1e-9
        in_circle = ((positions[:, 0] - mid[0]) ** 2 + (positions[:, 2] - mid[2]) ** 2) < radius2
        near = (np.abs(positions[:, 1] - mid[1]) < 2.5) & in_circle
        near[[node_a, node_b]] = False
        (rest if near.any() else keep).append((node_a, node_b, dist))
    parent = list(range(len(pts)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for node_a, node_b, _d in keep:
        parent[find(node_a)] = find(node_b)
    for node_a, node_b, dist in sorted(rest, key=lambda e: e[2]):
        if find(node_a) != find(node_b):
            parent[find(node_a)] = find(node_b)
            keep.append((node_a, node_b, dist))
    return keep


def prune_order_nodes(graphs: 'list[SectorGraph]', state: BuildState) -> 'list[SectorGraph]':
    """Order nodes in a route island under ORDER_MIN_ISLAND nodes lose their edges (finalize_graph then
    drops them as unlinked); other nodes are untouched. An order node that joins a small pocket becomes the
    nearest node for points next to the main network, and the runtime takes the nearest node (sp_009: the order
    nodes cut connectivity 4.4 points through such pockets; 2026-10-20). Components over all sectors (border ids
    join them). state: the build's BuildState - its order_ids; the removed ones are recorded in
    state.order_stats['removed'] as (id, position, component size)."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for _sid, _b, _m, pts, pairs, _o in graphs:
        for point in pts:
            find(point[0])
        for node_a, node_b, _d in pairs:
            root_a, root_b = find(pts[node_a][0]), find(pts[node_b][0])
            if root_a != root_b:
                parent[root_a] = root_b
    size = defaultdict(int)
    for x in list(parent):
        size[find(x)] += 1
    if not size:
        return graphs
    drop = {i for i in state.order_ids if size[find(i)] < ORDER_MIN_ISLAND}
    seen = set()
    for _sid, _b, _m, pts, _pairs, _o in graphs:
        for point in pts:
            if point[0] in drop and point[0] not in seen:
                seen.add(point[0])
                rounded = tuple(round(coord, 2) for coord in point[1])
                state.order_stats.setdefault('removed', []).append((point[0], rounded, size[find(point[0])]))
    return [(sid, bounds, mesh, pts,
             [pair for pair in pairs if pts[pair[0]][0] not in drop and pts[pair[1]][0] not in drop], origin)
            for sid, bounds, mesh, pts, pairs, origin in graphs]


def add_order_nodes(level: 'Level', pts: 'list[GraphPoint]', pairs: 'list[GraphPair]', node_at: dict[int, int],
                    area_of: Container[int], orders: 'Points', gi0: int, gj0: int, ox: float, oz: float,
                    new_id: 'NewId', order_ids: set[int]) -> int:
    """A route node at each AI order position (AreaGeometryEntityData) on this sector's walkable ground whose nearest
    route node on the same floor is farther than ORDER_NODE_MIN, linked to every node within DIST_EDGE_MAX it reaches
    in a straight walkable line. The runtime resolves a go-to point to the nearest gathered node (R = 15 m, the first
    candidate accepted; 0x0105ED80) - at sp_004_b's stairs top NavKit's nearest node was 3.6-6.3 m from the order
    points, DICE's 0.2 m, and those requests failed (2026-10-20). The new nodes' ids are added to order_ids. Returns
    the number of nodes added."""
    s = level.s
    pitch, yoff = level.grid.pitch, level.settings.y_offset
    added = 0
    for x, y, z in orders:
        gi, gj = int(round((x - s.min_x) / pitch)), int(round((z - s.min_z) / pitch))
        candidates = [k for k in level.surfaces(gi, gj) if k in area_of and abs(level.y[k] - y) <= 1.0]
        if not candidates:
            continue
        k = min(candidates, key=lambda cand: abs(level.y[cand] - y))
        if k in node_at:
            continue
        pos = (ox + (gi - gi0) * pitch, float(level.y[k]) + yoff, oz + (gj - gj0) * pitch)
        near = [math.hypot(point[1][0] - pos[0], point[1][2] - pos[2])
                for point in pts if abs(point[1][1] - pos[1]) < 1.5]
        if near and min(near) <= ORDER_NODE_MIN:
            continue
        new = len(pts)
        pts.append((new_id(), pos, k, gi - gi0, gj - gj0))
        order_ids.add(pts[new][0])
        node_at[k] = new
        added += 1
        for other in range(new):
            dist = math.dist(pos, pts[other][1])
            if dist <= DIST_EDGE_MAX and _walkable_between(level, pts[new], pts[other], gi0, gj0):
                pairs.append((new, other, dist))
    return added


def repair_detours(level: 'Level', pts: 'list[GraphPoint]', pairs: 'list[GraphPair]', node_at: dict[int, int],
                   cell_ij: dict[int, tuple[int, int]], gi0: int, gj0: int, ox: float, oz: float,
                   new_id: 'NewId') -> int:
    """Detour repair: for node pairs of the sector within DIST_EDGE_MAX whose graph route is longer than
    DETOUR_TRIGGER x their straight distance, while a walk over kept surfaces of at most DETOUR_MAX x the straight
    distance exists, add waypoint nodes along the string-pulled walk, linked along the chain (and to the nodes around
    them by the edge rule only with DETOUR_LINK_ALL).
    DICE's routes run 1.02-1.11x the straight distance where NavKit's failed go-to requests ran 3.2-6.3x
    (sp_004_b stairs top, 2026-10-20). Returns the number of nodes added."""
    grid, yoff = level.grid, level.settings.y_offset
    pitch = grid.pitch
    adj = defaultdict(dict)
    for node_a, node_b, dist in pairs:
        adj[node_a][node_b] = adj[node_b][node_a] = min(dist, adj[node_a].get(node_b, 1e18))

    def routes(src: int, limit: float) -> dict[int, float]:
        best = {src: 0.0}
        heap = [(0.0, src)]
        while heap:
            dist, node = heapq.heappop(heap)
            if dist > best[node] or dist > limit:
                continue
            for nbr, length in adj[node].items():
                if dist + length < best.get(nbr, 1e18):
                    best[nbr] = dist + length
                    heapq.heappush(heap, (dist + length, nbr))
        return best

    def walk(k0: int, k1: int, limit: float) -> list[int] | None:
        """Shortest 8-connected walk over kept surfaces (step rule) from k0 to k1, at most limit metres; list of
        surfaces or None."""
        best = {k0: 0.0}
        prev: dict[int, int | None] = {k0: None}
        heap = [(0.0, k0)]
        while heap:
            dist, k = heapq.heappop(heap)
            if k == k1:
                path = [k]
                while prev[path[-1]] is not None:
                    path.append(cast(int, prev[path[-1]]))   # not None: the loop's test
                return path[::-1]
            if dist > best[k]:
                continue
            i, j = cell_ij[k]
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if not (di or dj) or (i + di) not in grid.window or (j + dj) not in grid.window:
                        continue
                    n = level.neighbour(k, gi0 + i, gj0 + j, di, dj)
                    if n is None:
                        continue
                    next_dist = dist + (1.4142 if di and dj else 1.0) * pitch
                    if next_dist <= limit and next_dist < best.get(n, 1e18):
                        best[n], prev[n] = next_dist, k
                        cell_ij.setdefault(n, (i + di, j + dj))
                        heapq.heappush(heap, (next_dist, n))
        return None

    def link(u: int) -> None:
        for other in range(len(pts)):
            if other == u or other in adj[u]:
                continue
            dist = math.dist(pts[u][1], pts[other][1])
            if dist <= DIST_EDGE_MAX and _walkable_between(level, pts[u], pts[other], gi0, gj0):
                pairs.append((u, other, dist))
                adj[u][other] = adj[other][u] = dist

    added = 0
    original_count = len(pts)
    for first in range(original_count):
        best = routes(first, DETOUR_TRIGGER * DIST_EDGE_MAX)
        for second in range(first + 1, original_count):
            straight = math.dist(pts[first][1], pts[second][1])
            if straight > DIST_EDGE_MAX or straight < 1e-3:
                continue
            route = best.get(second, 1e18)
            if route <= DETOUR_TRIGGER * straight:
                continue
            # the walk itself within DETOUR_MAX x straight
            path = walk(pts[first][2], pts[second][2], DETOUR_MAX * straight)
            if path is None:
                continue
            way = _string_pull(level, path, cell_ij, gi0, gj0)
            chain = []
            for k in way:
                if k in node_at:
                    chain.append(node_at[k])
                else:
                    i, j = cell_ij[k]
                    pts.append((new_id(), (ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch), k, i, j))
                    node_at[k] = len(pts) - 1
                    chain.append(len(pts) - 1)
                    added += 1
            for u, v in zip(chain, chain[1:]):
                if v not in adj[u]:
                    dist = math.dist(pts[u][1], pts[v][1])
                    pairs.append((u, v, dist))
                    adj[u][v] = adj[v][u] = dist
            if DETOUR_LINK_ALL:
                for u in chain:
                    if u >= original_count:
                        link(u)
            best = routes(first, DETOUR_TRIGGER * DIST_EDGE_MAX)
    return added


def finalize_graph(pts: 'Sequence[GraphPoint]', pairs: 'Iterable[GraphPair]', linked_ids: Container[int],
                   origin: tuple[float, float] | None = None) -> tuple[list[hpdtool.Node], list[hpdtool.Edge]]:
    """Nodes and edges of a sector. Nodes without an edge in any sector (lone nodes on slivers and tiny islands
    the group joining cannot reach; border ids count edges of both sectors) are dropped - DICE's data has none."""
    keep = [idx for idx, point in enumerate(pts) if point[0] in linked_ids]
    if SORT_NODES_BY_ID:
        # DICE writes a sector's nodes sorted by id as an unsigned 32-bit value - ordinary nodes, then border nodes
        # (flag 80000000h) - in 40 of 40 sp_004_b sectors (masked to 31 bits: 0 of 40); the runtime binary-searches
        # these arrays for a path's start / destination ids (0x0108D5E0 / 0x0108D550). NavKit interleaved border
        # nodes (0 of 40 sorted) - 2026-10-20
        keep.sort(key=lambda idx: pts[idx][0] & 0xFFFFFFFF)
    index_of = {old: position for position, old in enumerate(keep)}
    out_edges = defaultdict(list)
    for node_a, node_b, dist in pairs:
        if node_a in index_of and node_b in index_of:
            # the cost from the positions as stored (32-bit floats), as every DICE edge is: from the unrounded
            # positions, 138 of sp_004_b's 60,142 costs came out one unit off
            dist = math.dist(*(tuple(float(np.float32(coord)) for coord in pts[i][1]) for i in (node_a, node_b)))
            cost = 0x80000000 | int(math.floor(dist * 16))
            out_edges[index_of[node_a]].append((cost, index_of[node_b]))
            out_edges[index_of[node_b]].append((cost, index_of[node_a]))
    nodes, edges = [], []
    for position, idx in enumerate(keep):
        nid, pos = pts[idx][0], pts[idx][1]
        nodes.append(hpdtool.Node(nid, pos, 0x80000000, DIST_EDGE_MAX if position == 0 else 0.0, 0, 0, 0))
    for idx in range(len(nodes)):
        first = len(edges)
        for cost, dst in sorted(out_edges[idx], key=lambda e: e[1]):
            edges.append(hpdtool.Edge(cost, idx, dst))
        nodes[idx].a = first + 1 if out_edges[idx] else 0     # DICE: 0 = no outgoing edges
    def foreign(node: hpdtool.Node) -> bool:
        # bit 16 of c: a border node's copy in a sector that does not own it. DICE: one copy per border id is the
        # owner (bit clear) - the sector whose 100 m square holds the node (1,379 of 1,445 sp_004_b ids), the
        # others set; ordinary nodes never. NavKit set the bit on every border copy, so no border node had an
        # owner (2026-10-20)
        if not node.id & 0x80000000:
            return False
        if not BORDER_OWNER or origin is None:
            return True
        ox, oz = origin
        return not (ox <= node.pos[0] < ox + 100 and oz <= node.pos[2] < oz + 100)
    n = len(nodes)
    by_x = sorted(range(n), key=lambda position: nodes[position].pos[0])
    by_y = sorted(range(n), key=lambda position: nodes[position].pos[1])
    by_z = sorted(range(n), key=lambda position: nodes[position].pos[2])
    for position in range(n):
        nodes[position].b = by_x[position] | (by_y[position] << 16)
        nodes[position].c = by_z[position] | (0x10000 if foreign(nodes[position]) else 0)
    return nodes, edges
