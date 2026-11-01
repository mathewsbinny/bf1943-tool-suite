"""NavKit: links and route edges of a navigation side file (DESIGN.md 5.1), written into a path-data file's route
graph (navkit apply, after the build - or on a copy of DICE's file for a DICE base). Input: a parsed pathdata.hpd
(hpdtool.HpdFile) and the side file's operations; output: the file changed in place and one result per operation.

  link-areas a b            a two-way route edge between the nodes nearest a and b (joins two areas / islands)
  route-edge a b add        the same, one-way when "two-way" is false
  route-edge a b remove     removes the edge(s) between the nodes nearest a and b

A point joins the route node nearest to it (within 15 m horizontally and 4 m in height, as the path test
joins points; node heights are ground + 0.95 m). An edge lives in one sector and joins two of its nodes, so
the two nodes are looked for in the same sector: every sector with a node near each point is tried and the
closest pair wins (border nodes, which belong to two sectors, let a link cross a sector edge). Costs are as
the generator writes them: 0x80000000 | floor(3-D distance * 16). Edges stay sorted by source node and each
node's first-edge index (`a`: first outgoing edge + 1, 0 when it has none) is recomputed - the rule every
DICE and NavKit file follows.
"""
import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

import hpdtool

JOIN_XZ, JOIN_Y, Y_OFFSET = 15.0, 4.0, 0.95
GRAPH_OPS = ('link-areas', 'route-edge')

type Operation = dict[str, Any]          # one operation of a navigation side file (core/bfbc2core/navtune.py)
type Result = tuple[int, bool, str]      # (operation index, applied, message)


def cost_tag(p: Sequence[float], q: Sequence[float]) -> int:
    """The generator's edge cost between two points: 0x80000000 | floor(3-D distance * 16)."""
    return 0x80000000 | int(math.floor(math.dist(p, q) * 16))


def _nearest(sector: hpdtool.Sector, point: Sequence[float]) -> tuple[int, float] | None:
    """(local index, horizontal distance) of the sector's node nearest the point within the join limits, or None."""
    if not sector.nodes:
        return None
    pos = np.array([node.pos for node in sector.nodes], dtype=np.float64)
    dist = np.hypot(pos[:, 0] - point[0], pos[:, 2] - point[2])
    dist = np.where(np.abs(pos[:, 1] - Y_OFFSET - point[1]) <= JOIN_Y, dist, np.inf)
    k = int(np.argmin(dist))
    return (k, float(dist[k])) if dist[k] <= JOIN_XZ else None


def find_pair(hpd: hpdtool.HpdFile, a: Sequence[float], b: Sequence[float]) -> tuple[int, int, int] | None:
    """(sector index, local node a, local node b) - the closest pair of nodes in one sector, or None."""
    best = None
    for sector_index, sector in enumerate(hpd.sectors):
        min_x, max_x, _, _, min_z, max_z = sector.bounds
        reach = JOIN_XZ + 1
        if not (min_x - reach <= a[0] <= max_x + reach and min_z - reach <= a[2] <= max_z + reach):
            continue
        near_a, near_b = _nearest(sector, a), _nearest(sector, b)
        if near_a and near_b and near_a[0] != near_b[0]:
            score = near_a[1] + near_b[1]
            if best is None or score < best[0]:
                best = (score, sector_index, near_a[0], near_b[0])
    return None if best is None else best[1:]


def recompute_first_edges(sector: hpdtool.Sector) -> None:
    """Edges sorted by source node (stable: existing order kept within a source); each node's `a` set."""
    sector.edges.sort(key=lambda edge: edge.src)
    first: dict[int, int] = {}
    for k, edge in enumerate(sector.edges):
        first.setdefault(edge.src, k)
    for n, node in enumerate(sector.nodes):
        node.a = first[n] + 1 if n in first else 0


def apply(hpd: hpdtool.HpdFile, operations: Sequence[Operation],
          log: Callable[[str], object] = print) -> list[Result]:
    """Applies the graph operations of a side file to a parsed path-data file (hpdtool.HpdFile) in place.
    Returns one result per graph operation: (operation index, applied: bool, message)."""
    results: list[Result] = []
    touched: set[int] = set()
    for k, operation in enumerate(operations):
        name = operation.get('op')
        if name not in GRAPH_OPS:
            continue
        pair = find_pair(hpd, operation['a'], operation['b'])
        if pair is None:
            results.append((k, False, f'{name}: no sector has route nodes near both points (within {JOIN_XZ:.0f} m) - '
                                      f'split it at the sector border, or put the points nearer the route graph'))
            continue
        sector_index, node_a, node_b = pair
        sector = hpd.sectors[sector_index]
        two_way = operation.get('two-way', True)
        directions = [(node_a, node_b), (node_b, node_a)] if two_way else [(node_a, node_b)]
        length = math.dist(sector.nodes[node_a].pos, sector.nodes[node_b].pos)
        limit = next((node.value for node in sector.nodes if node.value), 15.0)
        warn = ''
        if length > limit:
            warn = f' - longer than the file\'s DistEdgeMax ({limit:.0f} m): the game may not use it'
        touched.add(sector_index)
        if name == 'link-areas' or operation.get('action') == 'add':
            added = 0
            have = {(edge.src, edge.dst) for edge in sector.edges}
            for src, dst in directions:
                if (src, dst) not in have:
                    sector.edges.append(hpdtool.Edge(cost_tag(sector.nodes[src].pos, sector.nodes[dst].pos), src, dst))
                    added += 1
            results.append((k, True, f'{name}: {added} edge(s) added in sector 0x{sector.id:04X}, {length:.1f} m'
                                     f'{" (already there)" if not added else ""}{warn}'))
        else:
            before = len(sector.edges)
            drop = set(directions)
            sector.edges[:] = [edge for edge in sector.edges if (edge.src, edge.dst) not in drop]
            removed = before - len(sector.edges)
            if removed:
                message = f'route-edge remove: {removed} edge(s) removed in sector 0x{sector.id:04X}'
            else:
                message = f'route-edge remove: no edge between those nodes in sector 0x{sector.id:04X}'
            results.append((k, removed > 0, message))
    for sector_index in touched:
        recompute_first_edges(hpd.sectors[sector_index])
    for _, applied, message in results:
        log(('  ' if applied else '  NOT APPLIED: ') + message)
    return results
