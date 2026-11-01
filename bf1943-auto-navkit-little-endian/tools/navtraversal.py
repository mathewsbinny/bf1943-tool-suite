"""NavKit: the traversal annotations of a navigation side file (ladders, jumps, vaults, drops) as
`<level>.traversal.json`, for Project OPFOR's bot layer (DESIGN.md 5.1-5.2). Input: the written path data (parsed,
and its file for the hash) and the side file's operations; output: the JSON file below.

They are not written into the path data: a route edge there would make the game's AI plan through a ladder
or a jump its movement cannot perform, and get stuck. Whether Kynapse's format can carry such links is
open question 3 in DESIGN.md. The file gives each annotation its two ends, the route node at each end (the
nearest within 15 m horizontally and 4 m in height, in the written path data) and that node's island, so the
bot layer knows which parts of the route graph it joins.

{
  "format": "bfbc2-traversal", "version": 1, "level": "Levels/SP_01", "pathdata": "<file>", "pathdata_sha1": "...",
  "links": [{"kind": "ladder", "two-way": true, "bottom": [x, y, z], "top": [x, y, z], "rise": 6.0, "length": 6.1,
             "bottom_node": {"sector": 515, "id": 1234, "pos": [x, y, z], "island": 0} | null,
             "top_node": {...} | null, "joins_islands": true, "by": "MATHEWS", "when": "..."}]
}
Islands are numbered by size (0: the largest, the main route network), as the Navigation Studio shows them.
"""
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    import hpdtool

FORMAT, VERSION = 'bfbc2-traversal', 1
JOIN_XZ, JOIN_Y, Y_OFFSET = 15.0, 4.0, 0.95

# (node ids, their sectors, positions (n, 3), islands) - unique nodes, as graph() returns them
type Graph = tuple[list[int], list[int], npt.NDArray[np.float64], list[int]]
type NodeRef = dict[str, Any]       # {'sector', 'id', 'pos', 'island', 'distance'}


def graph(hpd: 'hpdtool.HpdFile') -> Graph:
    """Unique nodes (by id) with their sector, position and island (0 = the largest)."""
    index: dict[int, int] = {}
    ids: list[int] = []
    sectors: list[int] = []
    positions: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    for sector in hpd.sectors:
        local = []
        for node in sector.nodes:
            if node.id not in index:
                index[node.id] = len(ids)
                ids.append(node.id)
                sectors.append(sector.id)
                positions.append(node.pos)
            local.append(index[node.id])
        edges += [(local[edge.src], local[edge.dst]) for edge in sector.edges]
    parent = list(range(len(ids)))

    def find(node: int) -> int:
        """The node's island root (union-find with path halving)."""
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node
    for src, dst in edges:
        root_src, root_dst = find(src), find(dst)
        if root_src != root_dst:
            parent[root_src] = root_dst
    roots = [find(k) for k in range(len(ids))]
    rank = {root: k for k, (root, _) in enumerate(Counter(roots).most_common())}
    return ids, sectors, np.array(positions, dtype=np.float64).reshape(-1, 3), [rank[root] for root in roots]


def nearest(nodes: Graph, point: Sequence[float]) -> NodeRef | None:
    """The node nearest the point within 15 m in plan and 4 m in height (node heights are ground + 0.95), or None."""
    ids, sectors, pos, island = nodes
    if not len(pos):
        return None
    dist = np.hypot(pos[:, 0] - point[0], pos[:, 2] - point[2])
    dist = np.where(np.abs(pos[:, 1] - Y_OFFSET - point[1]) <= JOIN_Y, dist, np.inf)
    k = int(np.argmin(dist))
    if dist[k] > JOIN_XZ:
        return None
    return {'sector': int(sectors[k]), 'id': int(ids[k]), 'pos': [round(float(coord), 3) for coord in pos[k]],
            'island': int(island[k]), 'distance': round(float(dist[k]), 2)}


def write(hpd: 'hpdtool.HpdFile', pathdata_path: str, operations: Sequence[dict[str, Any]], level: str | None,
          out_path: str, log: Callable[[str], object] = print) -> list[dict[str, Any]]:
    """Writes the traversal annotations among operations to out_path (an empty list when there are none).
    Returns the links written."""
    nodes = graph(hpd)
    links = []
    for operation in operations:
        if operation.get('op') != 'traversal':
            continue
        bottom, top = operation['bottom'], operation['top']
        bottom_node, top_node = nearest(nodes, bottom), nearest(nodes, top)
        link = {'kind': operation['kind'], 'two-way': operation.get('two-way', True), 'bottom': bottom, 'top': top,
                'rise': round(top[1] - bottom[1], 2), 'length': round(math.dist(bottom, top), 2),
                'bottom_node': bottom_node, 'top_node': top_node,
                'joins_islands': bool(bottom_node and top_node and bottom_node['island'] != top_node['island']),
                'by': operation.get('by'), 'when': operation.get('when')}
        links.append(link)
        ends = ' and '.join(f'{name} {"island " + str(node["island"]) if node else "no route node within 15 m"}'
                            for name, node in (('bottom', bottom_node), ('top', top_node)))
        log(f'  traversal {operation["kind"]}{" (two-way)" if link["two-way"] else ""}: {link["length"]:.1f} m, {ends}'
            + (' - joins two islands' if link['joins_islands'] else ''))
    sha = hashlib.sha1(open(pathdata_path, 'rb').read()).hexdigest()
    doc = {'format': FORMAT, 'version': VERSION, 'level': level, 'pathdata': os.path.basename(pathdata_path),
           'pathdata_sha1': sha, 'links': links}
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(doc, stream, indent=1)
        stream.write('\n')
    os.replace(tmp, out_path)
    return links
