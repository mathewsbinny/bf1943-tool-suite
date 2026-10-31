"""The path test: a route between two points over a path-data file's route graph, or why there is none.

    r = route(nav, start, end)          # nav: navdata.NavData; start, end: world (x, y, z) on the ground
    r.found, r.nodes, r.cost, r.reason, r.start_island, r.end_island, r.gap

The points are joined to the graph at the nearest route node within 15 m horizontally and 4 m in height (as
the studio's spawn check does); the route is the cheapest over the file's own directed edges and their
stored costs (A*, straight-line distance times the graph's least cost per metre as the estimate, so the cheapest
route is found although the stored costs are rounded down). Without a route, the result says which island
each point is on and where the two islands come closest. This plans over the graph only: the game also
smooths the route through the areas, which this does not do (DESIGN.md section 7, question 4).
"""
import heapq
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

JOIN_XZ, JOIN_Y = 15.0, 4.0


@dataclass
class Route:
    found: bool
    nodes: List[int] = field(default_factory=list)        # node indices, start to end
    cost: float = 0.0                                     # sum of the stored edge costs (m)
    length: float = 0.0                                   # 3-D length of the node chain (m)
    straight: float = 0.0                                 # start to end, straight (m)
    start_node: Optional[int] = None
    end_node: Optional[int] = None
    start_join: float = 0.0                               # metres from the point to its node
    end_join: float = 0.0
    start_island: Optional[int] = None
    end_island: Optional[int] = None
    gap: Optional[tuple] = None                           # (node in start island, node in end island, metres)
    reason: str = ''
    expanded: int = 0


def ground_nodes(nav):
    return nav.node_pos - np.array([0, nav.y_offset, 0])


def join(nav, p):
    """(node index, horizontal distance) nearest to p within the join limits, or (None, distance)."""
    if not len(nav.node_pos):
        return None, np.inf
    ground = ground_nodes(nav)
    dist = np.hypot(ground[:, 0] - p[0], ground[:, 2] - p[2])
    dist = np.where(np.abs(ground[:, 1] - p[1]) <= JOIN_Y, dist, np.inf)
    nearest = int(np.argmin(dist))
    if dist[nearest] <= JOIN_XZ:
        return nearest, float(dist[nearest])
    return None, float(dist[nearest])


def _adjacency(nav):
    """(first edge per node, edge targets, edge costs), the edges grouped by their source node; kept on nav."""
    cache = getattr(nav, '_adj', None)
    if cache is None:
        if len(nav.edges):
            order = np.argsort(nav.edges[:, 0], kind='stable')
            sources, targets, costs = nav.edges[order, 0], nav.edges[order, 1], nav.edge_cost[order]
        else:
            sources, targets, costs = np.zeros(0, int), np.zeros(0, int), np.zeros(0)
        first_edge = np.searchsorted(sources, np.arange(len(nav.node_pos) + 1))
        cache = (first_edge, targets, costs)
        nav._adj = cache
    return cache


def cost_scale(nav):
    """The least stored cost per metre of length over the graph's edges, at most 1 (0 when an edge of some length
    costs nothing): the straight-line estimate times this never exceeds a route's cost. 2026-10-30 second review: the
    stored costs are rounded down to 1/16 m, so the plain distance overestimated and a dearer route came back (5.1875
    where one of 5.125 existed)."""
    cache = getattr(nav, '_cost_scale', None)
    if cache is not None:
        return cache
    scale = 1.0
    if len(nav.edges):
        delta = nav.node_pos[nav.edges[:, 1]] - nav.node_pos[nav.edges[:, 0]]
        length = np.linalg.norm(delta, axis=1)
        has_length = length > 0
        if has_length.any():
            least = float((np.asarray(nav.edge_cost, float)[has_length] / length[has_length]).min())
            scale = max(0.0, min(1.0, least * (1.0 - 1e-9)))
    nav._cost_scale = scale
    return scale


def closest_between(nav, ia, ib):
    """The closest pair of nodes of two islands (horizontal metres)."""
    nodes_a = np.nonzero(nav.node_island == ia)[0]
    nodes_b = np.nonzero(nav.node_island == ib)[0]
    if not len(nodes_a) or not len(nodes_b):
        return None
    xz_a, xz_b = nav.node_pos[nodes_a][:, [0, 2]], nav.node_pos[nodes_b][:, [0, 2]]
    best = (None, None, np.inf)
    for chunk in range(0, len(xz_a), 512):                 # 512 nodes of island a at a time against all of b
        part = xz_a[chunk:chunk + 512]
        dist = np.hypot(part[:, None, 0] - xz_b[None, :, 0], part[:, None, 1] - xz_b[None, :, 1])
        i, j = np.unravel_index(int(np.argmin(dist)), dist.shape)
        if dist[i, j] < best[2]:
            best = (int(nodes_a[chunk + i]), int(nodes_b[j]), float(dist[i, j]))
    return best


def route(nav, start, end):
    start, end = np.asarray(start, float), np.asarray(end, float)
    result = Route(False, straight=float(np.linalg.norm(end - start)))
    result.start_node, result.start_join = join(nav, start)
    result.end_node, result.end_join = join(nav, end)
    if result.start_node is None or result.end_node is None:
        which = 'start' if result.start_node is None else 'end'
        nearest = result.start_join if which == 'start' else result.end_join
        result.reason = (f'no route node within {JOIN_XZ:.0f} m of the {which} point '
                         f'(nearest {min(nearest, 9999):.0f} m)')
        return result
    result.start_island = int(nav.node_island[result.start_node])
    result.end_island = int(nav.node_island[result.end_node])
    first_edge, edge_target, edge_cost = _adjacency(nav)
    pos = nav.node_pos
    goal = result.end_node
    scale = cost_scale(nav)                                     # an estimate that never exceeds the stored costs
    best_cost = {result.start_node: 0.0}
    came_from = {}
    heap = [(scale * float(np.linalg.norm(pos[result.start_node] - pos[goal])), 0.0, result.start_node)]
    closed = set()
    while heap:
        _, cost, node = heapq.heappop(heap)
        if node in closed:
            continue
        closed.add(node)
        if node == goal:
            break
        for k in range(first_edge[node], first_edge[node + 1]):
            neighbour = int(edge_target[k])
            new_cost = cost + float(edge_cost[k])
            if new_cost < best_cost.get(neighbour, np.inf):
                best_cost[neighbour] = new_cost
                came_from[neighbour] = node
                estimate = scale * float(np.linalg.norm(pos[neighbour] - pos[goal]))
                heapq.heappush(heap, (new_cost + estimate, new_cost, neighbour))
    result.expanded = len(closed)
    if goal not in closed:
        if result.start_island != result.end_island:
            result.gap = closest_between(nav, result.start_island, result.end_island)
            if result.gap:
                result.reason = (f'the points are on different islands ({result.start_island} and '
                                 f'{result.end_island}); they come closest {result.gap[2]:.1f} m apart')
            else:
                result.reason = f'the points are on different islands ({result.start_island}, {result.end_island})'
        else:
            result.reason = 'same island, but no route along the edges\' directions (one-way edges)'
        return result
    chain = [goal]
    while chain[-1] != result.start_node:
        chain.append(came_from[chain[-1]])
    result.nodes = chain[::-1]
    result.cost = best_cost[goal]
    if len(result.nodes) > 1:
        result.length = float(np.sum(np.linalg.norm(np.diff(pos[result.nodes], axis=0), axis=1)))
    else:
        result.length = 0.0
    result.found = True
    return result
