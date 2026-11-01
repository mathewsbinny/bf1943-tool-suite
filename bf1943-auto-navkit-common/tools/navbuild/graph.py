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


def line_walkable(level: 'Level', k0: int, a: tuple[int, int], b: tuple[int, int], gi0: int, gj0: int, k1: int) -> bool:
    """Straight line from local sample a to b (i, j) over connected kept surfaces, starting on k0, ending on k1."""
    (i0, j0), (i1, j1) = a, b
    n = max(abs(i1 - i0), abs(j1 - j0))
    k, ci, cj = k0, i0, j0
    for t in range(1, n + 1):
        ti = i0 + round((i1 - i0) * t / n)
        tj = j0 + round((j1 - j0) * t / n)
        di, dj = ti - ci, tj - cj
        if di or dj:
            nk = level.neighbour(k, gi0 + ci, gj0 + cj, di, dj)
        else:
            continue
        if nk is None:
            return False
        k, ci, cj = nk, ti, tj
    return k == k1 or abs(level.y[k] - level.y[k1]) <= 0.6


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
    for a_, b_, _ in pairs:
        parent[find(a_)] = find(b_)
    node_at = {}
    for idx, p in enumerate(pts):
        node_at.setdefault(p[2], idx)
    cell_ij = {c[2]: (c[0], c[1]) for a in areas for c in a['cells']}
    for p in pts:
        cell_ij.setdefault(p[2], (p[3], p[4]))
    # the flood's bounds: the sector's window, and the settings' join_margin beyond it (ground joined only round a
    # corner of the neighbour sector - Coral Sea's strip beside the South Base bunker, closed inside 0b0a's window and
    # joined 1 m past it, 2026-10-26)
    m_ = int(math.ceil((level.settings.join_margin or 0.0) / pitch - 1e-9))
    wi, wj = join.window if join is not None and join.window else (grid.window, grid.window)
    lo_i, hi_i, lo_j, hi_j = wi[0] - m_, wi[-1] + m_, wj[0] - m_, wj[-1] + m_

    def beyond_ok(i: int, j: int) -> bool:
        # past the window only into a sector of this stage (this grid's pitch): a 0.2 m flood into a 0.8 m sector
        # would walk on samples the 0.8 m sector's cells do not show
        if join is None:
            return False
        X, Y = join.sector
        t = (X + i // grid.sector_samples, Y + j // grid.sector_samples)
        return t in join.sectors and (t in join.detailed_sectors) == grid.detailed
    changed = True
    while changed:
        changed = False
        groups = defaultdict(list)
        for idx in range(len(pts)):
            groups[find(idx)].append(idx)
        if len(groups) < 2:
            break
        for root, members in sorted(groups.items(), key=lambda g: len(g[1])):
            if find(members[0]) != root:
                continue
            prev: dict[int, int | None] = {pts[m][2]: None for m in members}
            q = deque(pts[m][2] for m in members)
            target = None
            while q and target is None:
                k = q.popleft()
                i, j = cell_ij[k]
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        if not (di or dj) or not (lo_i <= i + di <= hi_i and lo_j <= j + dj <= hi_j):
                            continue
                        if not (i + di in wi and j + dj in wj) and not beyond_ok(i + di, j + dj):
                            continue
                        n = level.neighbour(k, gi0 + i, gj0 + j, di, dj)
                        if n is None or n in prev:
                            continue
                        prev[n] = k
                        cell_ij.setdefault(n, (i + di, j + dj))
                        if n in node_at and find(node_at[n]) != root:
                            target = n
                            break
                        q.append(n)
                    if target is not None:
                        break
            if target is None:
                continue
            path = [target]
            while prev[path[-1]] is not None:
                path.append(cast(int, prev[path[-1]]))   # not None: the loop's test
            path.reverse()                       # from a member's surface to the target's surface
            # string pulling
            way = [path[0]]
            a = 0
            while a < len(path) - 1:
                b = len(path) - 1
                while b > a + 1 and not (math.dist(cell_ij[path[a]], cell_ij[path[b]]) * pitch <= DIST_EDGE_MAX and
                                         line_walkable(level, path[a], cell_ij[path[a]], cell_ij[path[b]], gi0, gj0, path[b])):
                    b -= 1
                way.append(path[b])
                a = b
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
    for a in areas:
        if len(a['cells']) < 4:
            continue
        ci = sum(c[0] for c in a['cells']) / len(a['cells'])
        cj = sum(c[1] for c in a['cells']) / len(a['cells'])
        i, j, k = min(a['cells'], key=lambda c: (c[0] - ci) ** 2 + (c[1] - cj) ** 2)
        add(i, j, k)
    cell_of = {c[2]: (c[0], c[1]) for a in areas for c in a['cells']}
    for ai, (portals, _b, _o) in enumerate(traced):
        for p in portals:
            if p.target < ai:
                continue          # one node per portal pair
            mx, mz = (p.p0[0] + p.p1[0]) / 2, (p.p0[2] + p.p1[2]) / 2
            best = min(areas[ai]['cells'], key=lambda c: (ox + c[0] * pitch - mx) ** 2 + (oz + c[1] * pitch - mz) ** 2)
            i, j, k = best
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
    cell = DIST_EDGE_MAX
    buckets = defaultdict(list)
    for idx, p in enumerate(pts):
        buckets[(int(p[1][0] // cell), int(p[1][2] // cell))].append(idx)
    pairs: list[GraphPair] = []
    for idx, p in enumerate(pts):
        gx, gz = int(p[1][0] // cell), int(p[1][2] // cell)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for q in buckets.get((gx + dx, gz + dz), ()):
                    if q <= idx:
                        continue
                    d = math.dist(p[1], pts[q][1])
                    if d <= DIST_EDGE_MAX and line_walkable(level, p[2], (p[3], p[4]), (pts[q][3], pts[q][4]), gi0, gj0, pts[q][2]):
                        pairs.append((idx, q, d))
    if GABRIEL_EDGES == 'all' or (GABRIEL_EDGES == 'detailed' and grid.detailed):
        pairs = gabriel_prune(pts, pairs)
    for kf, kt in ladders:
        # a ladder link: a node on the ground at its foot and at its top, joined directly (the climb is no walkable
        # line); the runtime's mover walks the bot into the ladder, which takes any soldier who walks into it
        ends = []
        for k in (kf, kt):
            idx = next((q for q, p in enumerate(pts) if p[2] == k), None)
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
        for p in range(DETOUR_PASSES):
            added = repair_detours(level, pts, pairs, node_at, cell_ij, gi0, gj0, ox, oz, new_id)
            state.repair_stats[p] = state.repair_stats.get(p, 0) + added
    if ORDER_NODES and orders:
        added = add_order_nodes(level, pts, pairs, node_at, area_of, orders, gi0, gj0, ox, oz, new_id, state.order_ids)
        state.order_stats['added'] = state.order_stats.get('added', 0) + added
    return pts, pairs


def add_cover_nodes(level: 'Level', areas: 'Sequence[AreaDict]', pts: 'list[GraphPoint]',
                    add: Callable[[int, int, int], None], ox: float, oz: float, cover: float) -> None:
    """NODE_COVER: in every area of 4+ cells, add route nodes (through add, so one per surface) at the cell farthest from
    all nodes on the same floor (|dy| < 2.5 m) until no cell is farther than cover metres."""
    pitch, yoff = level.grid.pitch, level.settings.y_offset
    for a in areas:
        if len(a['cells']) < 4:
            continue
        C = np.array([(ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch) for i, j, k in a['cells']])
        x0, x1, z0, z1 = C[:, 0].min() - cover, C[:, 0].max() + cover, C[:, 2].min() - cover, C[:, 2].max() + cover
        P = np.array([p[1] for p in pts if x0 <= p[1][0] <= x1 and z0 <= p[1][2] <= z1], float).reshape(-1, 3)
        d = np.full(len(C), np.inf)
        for q in P:
            same = np.abs(C[:, 1] - q[1]) < 2.5
            d = np.where(same, np.minimum(d, np.hypot(C[:, 0] - q[0], C[:, 2] - q[2])), d)
        while True:
            w = int(np.argmax(d))
            if d[w] <= cover:
                break
            i, j, k = a['cells'][w]
            n = len(pts)
            add(i, j, k)
            q = C[w]
            d[w] = 0.0                               # placed (or the surface already held a node)
            if len(pts) > n:
                same = np.abs(C[:, 1] - q[1]) < 2.5
                d = np.where(same, np.minimum(d, np.hypot(C[:, 0] - q[0], C[:, 2] - q[2])), d)


def gabriel_prune(pts: 'Sequence[GraphPoint]', pairs: 'Iterable[GraphPair]') -> 'list[GraphPair]':
    """The pairs that are Gabriel edges of the sector's nodes (no other node within 2.5 m of height inside the circle
    with the pair as diameter), plus the shortest dropped pairs needed to keep every group of nodes the full pair set
    joins joined (so pruning never splits a route)."""
    P = np.array([p[1] for p in pts], float)
    keep, rest = [], []
    for a_, b_, d in pairs:
        m = (P[a_] + P[b_]) / 2
        r2 = (d / 2) ** 2 - 1e-9
        near = (np.abs(P[:, 1] - m[1]) < 2.5) & (((P[:, 0] - m[0]) ** 2 + (P[:, 2] - m[2]) ** 2) < r2)
        near[[a_, b_]] = False
        (rest if near.any() else keep).append((a_, b_, d))
    parent = list(range(len(pts)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a_, b_, _d in keep:
        parent[find(a_)] = find(b_)
    for a_, b_, d in sorted(rest, key=lambda e: e[2]):
        if find(a_) != find(b_):
            parent[find(a_)] = find(b_)
            keep.append((a_, b_, d))
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
        for p in pts:
            find(p[0])
        for a_, b_, _d in pairs:
            ra, rb = find(pts[a_][0]), find(pts[b_][0])
            if ra != rb:
                parent[ra] = rb
    size = defaultdict(int)
    for x in list(parent):
        size[find(x)] += 1
    if not size:
        return graphs
    drop = {i for i in state.order_ids if size[find(i)] < ORDER_MIN_ISLAND}
    seen = set()
    for _sid, _b, _m, pts, _pairs, _o in graphs:
        for p in pts:
            if p[0] in drop and p[0] not in seen:
                seen.add(p[0])
                state.order_stats.setdefault('removed', []).append((p[0], tuple(round(v, 2) for v in p[1]),
                                                                    size[find(p[0])]))
    return [(sid, b, m, pts, [q for q in pairs if pts[q[0]][0] not in drop and pts[q[1]][0] not in drop], o)
            for sid, b, m, pts, pairs, o in graphs]


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
        cands = [k for k in level.surfaces(gi, gj) if k in area_of and abs(level.y[k] - y) <= 1.0]
        if not cands:
            continue
        k = min(cands, key=lambda q: abs(level.y[q] - y))
        if k in node_at:
            continue
        pos = (ox + (gi - gi0) * pitch, float(level.y[k]) + yoff, oz + (gj - gj0) * pitch)
        near = [math.hypot(p[1][0] - pos[0], p[1][2] - pos[2]) for p in pts if abs(p[1][1] - pos[1]) < 1.5]
        if near and min(near) <= ORDER_NODE_MIN:
            continue
        u = len(pts)
        pts.append((new_id(), pos, k, gi - gi0, gj - gj0))
        order_ids.add(pts[u][0])
        node_at[k] = u
        added += 1
        for q in range(u):
            d = math.dist(pos, pts[q][1])
            if d <= DIST_EDGE_MAX and line_walkable(level, k, (gi - gi0, gj - gj0), (pts[q][3], pts[q][4]), gi0, gj0, pts[q][2]):
                pairs.append((u, q, d))
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
    for a_, b_, d in pairs:
        adj[a_][b_] = adj[b_][a_] = min(d, adj[a_].get(b_, 1e18))

    def routes(src: int, limit: float) -> dict[int, float]:
        best = {src: 0.0}
        q = [(0.0, src)]
        while q:
            d, u = heapq.heappop(q)
            if d > best[u] or d > limit:
                continue
            for v, w in adj[u].items():
                if d + w < best.get(v, 1e18):
                    best[v] = d + w
                    heapq.heappush(q, (d + w, v))
        return best

    def walk(k0: int, k1: int, limit: float) -> list[int] | None:
        """Shortest 8-connected walk over kept surfaces (step rule) from k0 to k1, at most limit metres; list of
        surfaces or None."""
        best = {k0: 0.0}
        prev: dict[int, int | None] = {k0: None}
        q = [(0.0, k0)]
        while q:
            d, k = heapq.heappop(q)
            if k == k1:
                path = [k]
                while prev[path[-1]] is not None:
                    path.append(cast(int, prev[path[-1]]))   # not None: the loop's test
                return path[::-1]
            if d > best[k]:
                continue
            i, j = cell_ij[k]
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if not (di or dj) or (i + di) not in grid.window or (j + dj) not in grid.window:
                        continue
                    n = level.neighbour(k, gi0 + i, gj0 + j, di, dj)
                    if n is None:
                        continue
                    nd = d + (1.4142 if di and dj else 1.0) * pitch
                    if nd <= limit and nd < best.get(n, 1e18):
                        best[n], prev[n] = nd, k
                        cell_ij.setdefault(n, (i + di, j + dj))
                        heapq.heappush(q, (nd, n))
        return None

    def link(u: int) -> None:
        for q_ in range(len(pts)):
            if q_ == u or q_ in adj[u]:
                continue
            d = math.dist(pts[u][1], pts[q_][1])
            if d <= DIST_EDGE_MAX and line_walkable(level, pts[u][2], (pts[u][3], pts[u][4]), (pts[q_][3], pts[q_][4]),
                                                    gi0, gj0, pts[q_][2]):
                pairs.append((u, q_, d))
                adj[u][q_] = adj[q_][u] = d

    added = 0
    n0 = len(pts)
    for a in range(n0):
        best = routes(a, DETOUR_TRIGGER * DIST_EDGE_MAX)
        for b in range(a + 1, n0):
            straight = math.dist(pts[a][1], pts[b][1])
            if straight > DIST_EDGE_MAX or straight < 1e-3:
                continue
            route = best.get(b, 1e18)
            if route <= DETOUR_TRIGGER * straight:
                continue
            path = walk(pts[a][2], pts[b][2], DETOUR_MAX * straight)   # the walk itself within DETOUR_MAX x straight
            if path is None:
                continue
            way, s = [path[0]], 0
            while s < len(path) - 1:
                e = len(path) - 1
                while e > s + 1 and not (math.dist(cell_ij[path[s]], cell_ij[path[e]]) * pitch <= DIST_EDGE_MAX and
                                         line_walkable(level, path[s], cell_ij[path[s]], cell_ij[path[e]], gi0, gj0, path[e])):
                    e -= 1
                way.append(path[e])
                s = e
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
                    d = math.dist(pts[u][1], pts[v][1])
                    pairs.append((u, v, d))
                    adj[u][v] = adj[v][u] = d
            if DETOUR_LINK_ALL:
                for u in chain:
                    if u >= n0:
                        link(u)
            best = routes(a, DETOUR_TRIGGER * DIST_EDGE_MAX)
    return added


def finalize_graph(pts: 'Sequence[GraphPoint]', pairs: 'Iterable[GraphPair]', linked_ids: Container[int],
                   origin: tuple[float, float] | None = None) -> tuple[list[hpdtool.Node], list[hpdtool.Edge]]:
    """Nodes and edges of a sector. Nodes without an edge in any sector (lone nodes on slivers and tiny islands
    the group joining cannot reach; border ids count edges of both sectors) are dropped - DICE's data has none."""
    keep = [idx for idx, p in enumerate(pts) if p[0] in linked_ids]
    if SORT_NODES_BY_ID:
        # DICE writes a sector's nodes sorted by id as an unsigned 32-bit value - ordinary nodes, then border nodes
        # (flag 80000000h) - in 40 of 40 sp_004_b sectors (masked to 31 bits: 0 of 40); the runtime binary-searches
        # these arrays for a path's start / destination ids (0x0108D5E0 / 0x0108D550). NavKit interleaved border
        # nodes (0 of 40 sorted) - 2026-10-20
        keep.sort(key=lambda idx: pts[idx][0] & 0xFFFFFFFF)
    new = {old: q for q, old in enumerate(keep)}
    out_edges = defaultdict(list)
    for a_, b_, d in pairs:
        if a_ in new and b_ in new:
            # the cost from the positions as stored (32-bit floats), as every DICE edge is: from the unrounded
            # positions, 138 of sp_004_b's 60,142 costs came out one unit off
            d = math.dist(*(tuple(float(np.float32(v)) for v in pts[i][1]) for i in (a_, b_)))
            cost = 0x80000000 | int(math.floor(d * 16))
            out_edges[new[a_]].append((cost, new[b_]))
            out_edges[new[b_]].append((cost, new[a_]))
    nodes, edges = [], []
    for q, idx in enumerate(keep):
        nid, pos = pts[idx][0], pts[idx][1]
        nodes.append(hpdtool.Node(nid, pos, 0x80000000, DIST_EDGE_MAX if q == 0 else 0.0, 0, 0, 0))
    for idx in range(len(nodes)):
        first = len(edges)
        for cost, dst in sorted(out_edges[idx], key=lambda e: e[1]):
            edges.append(hpdtool.Edge(cost, idx, dst))
        nodes[idx].a = first + 1 if out_edges[idx] else 0     # DICE: 0 = no outgoing edges
    def foreign(nd: hpdtool.Node) -> bool:
        # bit 16 of c: a border node's copy in a sector that does not own it. DICE: one copy per border id is the
        # owner (bit clear) - the sector whose 100 m square holds the node (1,379 of 1,445 sp_004_b ids), the
        # others set; ordinary nodes never. NavKit set the bit on every border copy, so no border node had an
        # owner (2026-10-20)
        if not nd.id & 0x80000000:
            return False
        if not BORDER_OWNER or origin is None:
            return True
        ox, oz = origin
        return not (ox <= nd.pos[0] < ox + 100 and oz <= nd.pos[2] < oz + 100)
    n = len(nodes)
    by_x = sorted(range(n), key=lambda q: nodes[q].pos[0])
    by_y = sorted(range(n), key=lambda q: nodes[q].pos[1])
    by_z = sorted(range(n), key=lambda q: nodes[q].pos[2])
    for q in range(n):
        nodes[q].b = by_x[q] | (by_y[q] << 16)
        nodes[q].c = by_z[q] | (0x10000 if foreign(nodes[q]) else 0)
    return nodes, edges
