r"""NavKit (big-endian): an RSNV world (core rsnv.py) from cells on a grid - the vehicle worlds' writer
(groundnav1943.py: jeeps and tanks; seanav1943.py: the landing craft; airnav1943.py: planes).

  sets = [CellSet(pitch, x0, z0, i, j, y, links)]      cells: grid indices, ground heights, 4-neighbour links
  write_world(lv, cls, sets, nodes, edges, offm, out, sector_offsets, header, meta)

A world holds what the soldier's export holds, built from cells instead of a NavKit .hpd:
  - SECT: the 100 m sectors (the soldier build's sector grid: XOffset / ZOffset from navkit.json), one record per
    sector and cell set; `layer` is the class id (vehicleclasses1943.CLASS_IDS);
  - AREA: per sector and cell set, flood-filled pieces of linked cells with at most one cell per column (so the
    bitmap and height grid are unique); bitmaps at the set's pitch, one height block per cell (hstep = pitch);
  - PORT: one record per pair of neighbouring areas (both directions), the shared cell edges' extent;
  - GBKT / GLST: 25 x 25 buckets of 4 m per sector record, areas by their bounding box, lowest first;
  - NODE / NAUX / EDGE / ANOD: route nodes at their cells' centres and ground, edges both ways unless one-way,
    integer costs in 1/16 m (the 3-D length unless given), kinds as given (walk = drive; drop for a launch);
  - ISLD: the graph's islands (MAIN on the largest, flags as given);
  - OFFM: transfers as given (kind, team mask, vehicle class, node, the soldier-side point, yaw, radius, aux);
  - META: exporter version, sources and their hashes, the class's parameters.
"""
import collections
import json
import math
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

import navkitjson1943 as nj  # noqa: E402
from bfbc2core import rsnv  # noqa: E402

if TYPE_CHECKING:
    from groundnav1943 import Grid

type IntArray = npt.NDArray[np.int64]
type Links = dict[tuple[int, int, int], int]            # {(cell, di, dj): neighbour cell}
type Edge = tuple[int, int, int, float | None, bool]    # (node a, node b, kind, cost in m or None, both ways)
type Point = Sequence[float] | npt.NDArray[np.float64]  # x, y, z
type Log = Callable[[str], object]

EXPORTER_VERSION = '1.2 (2026-10-29: costs rounded up to 16 x length, the heuristic scale in the header)'
OFFM_ALIGHT_ZONE = 2         # design 1.3's OFFM kinds beside rsnv.py's BOARD_VEHICLE (1) and MOUNT_STATIC (5)
ALIGHT_TARGET_REACH = 60.0   # m: a ground vehicle's alight node lies within this of its base or flag
ALIGHT_REACH = 6.0           # m from the alight node to the soldier node


def soldier_node(soldier: rsnv.World, point: Point, reach: float, dy: float = 3.5, main_only: bool = False) -> int | None:
    """The soldier node nearest p horizontally within reach whose ground is within dy of p's height (lowest index on
    a tie; main_only: on the main island), or None - the rule the runtime resolves a transfer with again
    (core rsnvtrip.resolve)."""
    dist = np.hypot(soldier.node_pos[:, 0] - point[0], soldier.node_pos[:, 2] - point[2])
    within = (dist <= reach) & (np.abs(soldier.node_pos[:, 1] - point[1]) <= dy)
    if main_only:
        within &= soldier.node_island == 0
    if not within.any():
        return None
    candidates = np.nonzero(within)[0]
    return int(candidates[np.argmin(dist[candidates])])


STRAIGHT = ((1, 0), (-1, 0), (0, 1), (0, -1))
SECTOR_NUDGE = 1e-3          # m


class CellSet:
    """Cells of one grid: indices i, j (centre x0 + i * pitch, z0 + j * pitch), ground y, and links
    {(k, di, dj): n} between straight neighbours (only those are used for areas)."""

    def __init__(self, pitch: float, x0: float, z0: float, i: npt.ArrayLike, j: npt.ArrayLike, y: npt.ArrayLike,
                 links: Links, name: str = '') -> None:
        self.pitch, self.x0, self.z0 = float(pitch), float(x0), float(z0)
        self.i, self.j = np.asarray(i, np.int64), np.asarray(j, np.int64)
        self.y = np.asarray(y, np.float64)
        self.links = links
        self.name = name
        self.n = len(self.i)

    def xz(self, k: int) -> tuple[float, float]:
        """Cell k's centre (x, z)."""
        return self.x0 + int(self.i[k]) * self.pitch, self.z0 + int(self.j[k]) * self.pitch


def grid_links(mask_index: npt.NDArray[np.integer], di_dj: Iterable[tuple[int, int]] = STRAIGHT) -> Links:
    """Links between straight neighbours of a 2-D index grid (-1: no cell): {(k, di, dj): n}."""
    nz, nx = mask_index.shape
    out = {}
    for di, dj in di_dj:
        # the grid shifted by (di, dj): dst[r, c] is the neighbour of src[r, c]
        dst = mask_index[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)]
        src = mask_index[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
        both = (src >= 0) & (dst >= 0)
        for src_cell, dst_cell in zip(src[both].tolist(), dst[both].tolist()):
            out[(src_cell, di, dj)] = dst_cell
    return out


sha256 = nj.sha256                 # (navkitjson1943: one chunked SHA-256 for every tool)


def areas_of(cell_set: CellSet, sector_offsets: Sequence[int]) -> tuple[list[tuple[int, list[int]]], IntArray]:
    """[(sector id, [cell indices])] - flood fills of linked cells within each sector, one cell per column."""
    xoff, zoff = sector_offsets
    x = cell_set.x0 + cell_set.i * cell_set.pitch
    z = cell_set.z0 + cell_set.j * cell_set.pitch
    # a cell on a sector edge (x a multiple of 100 m, computed as i x 0.8 with rounding either side) goes to the sector
    # it starts: queries nudge the same way (rsnvhybrid.BUCKET_NUDGE), so the bucket holding it is found
    sector_x = np.floor((x + SECTOR_NUDGE) / 100.0).astype(np.int64) - xoff
    sector_z = np.floor((z + SECTOR_NUDGE) / 100.0).astype(np.int64) - zoff
    if cell_set.n and (sector_x.min() < 0 or sector_z.min() < 0 or sector_x.max() > 255 or sector_z.max() > 255):
        raise ValueError(f'{cell_set.name}: cells outside the sector grid (X {sector_x.min()}..{sector_x.max()}, '
                         f'Z {sector_z.min()}..{sector_z.max()})')
    sector = sector_x * 256 + sector_z
    order = np.lexsort((cell_set.i, cell_set.j, sector))
    area_of = np.full(cell_set.n, -1, np.int64)
    out = []
    links = cell_set.links
    for seed in order.tolist():
        if area_of[seed] >= 0:
            continue
        sector_id = int(sector[seed])
        area_id = len(out)
        columns = {(int(cell_set.i[seed]), int(cell_set.j[seed]))}
        members = [seed]
        area_of[seed] = area_id
        queue = collections.deque([seed])
        while queue:
            cell = queue.popleft()
            for di, dj in STRAIGHT:
                neighbour = links.get((cell, di, dj))
                if neighbour is None or area_of[neighbour] >= 0 or sector[neighbour] != sector_id:
                    continue
                column = (int(cell_set.i[neighbour]), int(cell_set.j[neighbour]))
                if column in columns:
                    continue
                columns.add(column)
                area_of[neighbour] = area_id
                members.append(neighbour)
                queue.append(neighbour)
        out.append((sector_id, members))
    return out, area_of


def write_world(lv: str, cls: str, sets: Sequence[CellSet], nodes: Sequence[tuple[int, int]], edges: Iterable[Edge],
                offm: Sequence[Sequence[Any]], out: str, sector_offsets: Sequence[int], header: Mapping[str, Any],
                meta: Mapping[str, Any], island_flags: Mapping[int, int] | None = None,
                node_flags: Sequence[int] | None = None, big_endian: bool = False, flag_tight: bool = True,
                log: Log = print) -> dict[str, Any]:
    """nodes: [(set index, cell index)]; edges: [(a, b, kind, cost_m or None, both_ways)]; offm: rows as rsnv.SCHEMA
    ['OFFM'] with the node as a node index; header: sea_level, radius, height, step, water_depth, source sha256 (hex);
    island_flags: {island: flags}; node_flags: [u16 per node]. Returns stats."""
    import vehicleclasses1943 as vc
    layer = vc.CLASS_IDS[cls]
    xoff, zoff = sector_offsets
    # areas per set, grouped by sector
    per_set = [areas_of(cell_set, sector_offsets) for cell_set in sets]
    sect_keys = sorted({(sector_id, set_index) for set_index, (set_areas, _) in enumerate(per_set)
                        for sector_id, _members in set_areas})
    area_rows, bmap, hght, area_cells = [], bytearray(), [], []
    area_index = {}                 # (set, local area) -> global area
    sect_rows, gbkt, glst = [], [], []
    by_key = collections.defaultdict(list)          # (sector id, set) -> [(local area, cells)]
    for set_index, (set_areas, _) in enumerate(per_set):
        for local_area, (sector_id, members) in enumerate(set_areas):
            by_key[(sector_id, set_index)].append((local_area, members))
    for sect_row, (sector_id, set_index) in enumerate(sect_keys):
        cell_set = sets[set_index]
        first_area = len(area_rows)
        for local_area, members in by_key[(sector_id, set_index)]:
            cells = np.asarray(members)
            ii, jj, yy = cell_set.i[cells], cell_set.j[cells], cell_set.y[cells]    # the area's cells: indices, ground
            i0, j0 = int(ii.min()), int(jj.min())
            width, height = int(ii.max()) - i0 + 1, int(jj.max()) - j0 + 1
            row_bytes = (width + 7) >> 3
            bits = np.zeros((height, row_bytes * 8), np.uint8)
            bits[jj - j0, ii - i0] = 1
            packed = np.packbits(bits.reshape(height, row_bytes, 8)[:, :, ::-1], axis=-1).reshape(-1)
            min_y, max_y = float(yy.min()), float(yy.max())
            grid = np.full((height, width), 0xFFFF, np.int64)
            grid[jj - j0, ii - i0] = np.minimum(np.round((yy - min_y) * 100).astype(np.int64), 0xFFFE)
            pitch_mm = int(round(cell_set.pitch * 1000))
            x0, z0 = cell_set.x0 + i0 * cell_set.pitch, cell_set.z0 + j0 * cell_set.pitch
            area_index[(set_index, local_area)] = len(area_rows)
            area_rows.append([x0, z0, x0 + (width - 1) * cell_set.pitch, z0 + (height - 1) * cell_set.pitch, min_y, max_y,
                              len(bmap), width, height, 0, 0, sect_row, 0, 0, 0, 0, pitch_mm, len(hght), width, height,
                              pitch_mm])
            bmap += packed.tobytes()
            hght += grid.ravel().tolist()
            area_cells.append((set_index, cells))
        sector_x, sector_z = sector_id // 256, sector_id % 256
        sector_x0, sector_z0 = (sector_x + xoff) * 100.0, (sector_z + zoff) * 100.0
        first_bucket = len(gbkt)
        buckets = [[] for _ in range(rsnv.BUCKETS * rsnv.BUCKETS)]
        for k in range(first_area, len(area_rows)):
            box = area_rows[k]                          # min x, min z, max x, max z of the cell centres
            half = sets[set_index].pitch / 2
            bucket_i0 = max(int((box[0] - half - sector_x0) // rsnv.BUCKET), 0)
            bucket_i1 = min(int((box[2] + half - sector_x0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            bucket_j0 = max(int((box[1] - half - sector_z0) // rsnv.BUCKET), 0)
            bucket_j1 = min(int((box[3] + half - sector_z0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            for bj in range(bucket_j0, bucket_j1 + 1):
                for bi in range(bucket_i0, bucket_i1 + 1):
                    buckets[bj * rsnv.BUCKETS + bi].append(k)
        for bucket in buckets:
            bucket.sort(key=lambda k: (area_rows[k][4], k))
            gbkt.append((len(glst), len(bucket)))
            glst += bucket
        normal_pitch = 1 if sets[set_index].pitch >= 0.5 else 0
        sect_rows.append(((sector_x << 8) | sector_z, layer, normal_pitch, sector_x0, sector_z0, sector_x0 + 100.0,
                          sector_z0 + 100.0, first_bucket, first_area, len(area_rows) - first_area, 0))
    if len(area_rows) >= 0xFFFF:
        raise ValueError(f'{len(area_rows)} areas: more than an RSNV area id holds')
    # global area of every cell
    cell_area = [np.full(cell_set.n, -1, np.int64) for cell_set in sets]
    for set_index, (set_areas, local) in enumerate(per_set):
        if set_areas:
            global_of_local = np.array([area_index[(set_index, local_area)] for local_area in range(len(set_areas))],
                                       np.int64)
        else:
            global_of_local = np.zeros(0, np.int64)
        cell_area[set_index] = global_of_local[local] if len(local) else local
    # portals: neighbouring cells in different areas (same set)
    port_pairs = collections.defaultdict(list)
    for set_index, cell_set in enumerate(sets):
        areas = cell_area[set_index]
        for (k, di, dj), n in cell_set.links.items():
            if (di, dj) not in STRAIGHT:
                continue
            area_a, area_b = int(areas[k]), int(areas[n])
            if area_a != area_b and area_a >= 0 and area_b >= 0:
                x, z = cell_set.xz(k)
                port_pairs[(area_a, area_b)].append((x + di * cell_set.pitch / 2, z + dj * cell_set.pitch / 2,
                                                     (cell_set.y[k] + cell_set.y[n]) / 2))
    per_area = collections.defaultdict(list)
    for (area_a, area_b), edge_points in port_pairs.items():
        points = np.array(edge_points)                       # the shared edges' midpoints: x, z, y
        per_area[area_a].append((float(points[:, 0].min()), float(points[:, 1].min()), float(points[:, 0].max()),
                                 float(points[:, 1].max()), float(points[:, 2].mean()), area_a, area_b))
    portals = []
    for k in range(len(area_rows)):
        area_rows[k][9] = len(portals)
        area_rows[k][10] = len(per_area[k])
        portals += sorted(per_area[k], key=lambda portal: portal[6])
    # nodes and edges
    node_xyz = []
    for set_index, k in nodes:
        x, z = sets[set_index].xz(k)
        node_xyz.append([x, sets[set_index].y[k], z])
    pos = np.array(node_xyz, np.float64).reshape(-1, 3)
    node_area = np.array([cell_area[set_index][k] for set_index, k in nodes], np.int64)
    adjacency = collections.defaultdict(dict)
    pos32 = pos.astype(np.float32).astype(np.float64)       # the node positions as the file holds them (float32)
    for a, b, kind, cost, both in edges:
        # drive edges cost 16 x their length between the float32 positions as the search measures it (x, y summed, then
        # z), rounded up (exporter 1.4's rule: no chain of edges then costs less than the heuristic); a given cost (a
        # launch's drop: its horizontal run) no less than that either, so the header's scale stays near 0.999
        delta = pos32[b] - pos32[a]
        length = math.sqrt((delta[0] * delta[0] + delta[1] * delta[1]) + delta[2] * delta[2])
        cost_m = cost if cost is not None else length
        cost_q = max(1, min(int(math.ceil(cost_m * 16.0 - 1e-9)), 0xFFFF), min(int(math.ceil(16.0 * length)), 0xFFFF))
        adjacency[a][b] = (kind, cost_q)
        if both:
            adjacency[b][a] = (kind, cost_q)
    edge_rows, first_edge = [], []
    for node in range(len(nodes)):
        first_edge.append(len(edge_rows))
        for b in sorted(adjacency[node]):
            kind, cost_q = adjacency[node][b]
            edge_rows.append((int(b) | (int(kind) << 24), cost_q, 0))
    first_edge.append(len(edge_rows))
    # islands (undirected)
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a in adjacency:
        for b in adjacency[a]:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[max(root_a, root_b)] = min(root_a, root_b)
    root = np.array([find(x) for x in range(len(nodes))], np.int64)
    roots, root_of_node, counts = np.unique(root, return_inverse=True, return_counts=True)
    order = np.argsort(-counts, kind='stable')
    rank = np.empty(len(roots), np.int64)               # islands numbered largest first
    rank[order] = np.arange(len(roots))
    island = rank[root_of_node] if len(nodes) else np.zeros(0, np.int64)
    island_rows = []
    for island_k in range(len(roots)):
        members = np.nonzero(island == island_k)[0]
        flags = (rsnv.ISLD_MAIN if island_k == 0 else 0) | ((island_flags or {}).get(island_k, 0))
        island_rows.append([len(members), int(members[0]), flags, 0, 0])
    # transfers per island (OFFM sorted by island, then as given); row[3]: the transfer's vehicle node
    offm = sorted(offm, key=lambda row: (int(island[row[3]]) if row[3] != 0xFFFFFFFF else 1 << 30))
    for k, row in enumerate(offm):
        if row[3] == 0xFFFFFFFF:
            continue
        island_row = island_rows[int(island[row[3]])]
        if island_row[3] == 0:
            island_row[4] = k
        island_row[3] += 1
    # areas: island and nodes
    anod = []
    by_area = collections.defaultdict(list)
    for node, area in enumerate(node_area.tolist()):
        if area >= 0:
            by_area[area].append(node)
    for k, row in enumerate(area_rows):
        area_nodes = by_area.get(k, [])
        row[12] = collections.Counter(island[area_nodes].tolist()).most_common(1)[0][0] if area_nodes else 0xFFFF
        row[14], row[15] = len(anod), len(area_nodes)
        anod += area_nodes
    flags_of_node = node_flags if node_flags is not None else [0] * len(nodes)
    node_rows = [(float(x), float(y), float(z), int(first_edge[n])) for n, (x, y, z) in enumerate(pos)]
    node_rows.append((0.0, 0.0, 0.0, int(first_edge[-1])))
    naux = []
    for node in range(len(nodes)):
        area = int(node_area[node])
        naux.append((area if area >= 0 else 0xFFFF, area_rows[area][11] if area >= 0 else 0, int(island[node]),
                     int(flags_of_node[node])))
    meta = dict(meta)
    meta.setdefault('exporter', 'rsnvgrid1943 ' + EXPORTER_VERSION)
    meta['level'] = lv
    meta['class'] = cls
    meta['layer'] = layer
    meta['params'] = vc.params_id(cls)          # the class's parameters (navexport_vehicles1943.check compares them)
    meta['counts'] = {'nodes': len(nodes), 'edges': len(edge_rows), 'areas': len(area_rows), 'portals': len(portals),
                      'sectors': len(sect_rows), 'islands': len(island_rows), 'transfers': len(offm),
                      'cells': int(sum(cell_set.n for cell_set in sets))}
    sections = {'SECT': sect_rows, 'NODE': node_rows, 'NAUX': naux, 'EDGE': edge_rows,
                'AREA': [tuple(row) for row in area_rows], 'PORT': portals, 'BMAP': bytes(bmap),
                'HGHT': [(value,) for value in hght], 'GBKT': gbkt, 'GLST': [(value,) for value in glst],
                'ANOD': [(value,) for value in anod], 'OFFM': [tuple(row) for row in offm],
                'ISLD': [tuple(row) for row in island_rows],
                'META': json.dumps(meta, indent=1).encode('utf-8')}
    if not offm:
        del sections['OFFM']
    # the search heuristic's scale (rsnv.heuristic_scale): the least edge cost per 1/16 m of length, so the heuristic
    # floor(16 x distance x scale) never overestimates a path (admissible, as the soldier worlds from exporter 1.4)
    srcs = np.repeat(np.arange(len(nodes)), np.diff(np.asarray(first_edge, np.int64)))
    dsts = np.array([int(row[0]) & 0xFFFFFF for row in edge_rows], np.int64)
    h_scale_q = rsnv.heuristic_scale(pos, srcs, dsts, [row[1] for row in edge_rows]) if edge_rows else 0
    meta['heuristic scale'] = h_scale_q / rsnv.H_SCALE_ONE if h_scale_q else None
    sections['META'] = json.dumps(meta, indent=1).encode('utf-8')
    file_header = {'level': lv, 'sea_level': header['sea_level'], 'radius': header['radius'],
                   'height': header['height'], 'step': header['step'], 'water_depth': header['water_depth'],
                   'hpd_sha256': header.get('sha256', '0' * 8), 'h_scale_q': h_scale_q}
    size = rsnv.write(out, sections, file_header, big_endian)
    if flag_tight:
        # tight edges: drive edges with no line of sight either way under the runtime's rule (rsnv.World.los), as
        # navexport1943 flags the soldier's; the file is written again with the flags
        world = rsnv.load(out)
        edge_source = np.repeat(np.arange(len(nodes)), np.diff(world.node_first_edge))
        edge_pairs = list(zip(edge_source.tolist(), world.edge_target.tolist()))
        tight = set()
        index = {(int(a), int(b)): k for k, (a, b) in enumerate(edge_pairs)}

        def f32(coords: Point) -> tuple[float, ...]:
            """The point as the file holds it (float32)."""
            return tuple(float(np.float32(x)) for x in coords)
        for k, (a, b) in enumerate(edge_pairs):
            # each pair once: an edge whose reverse is in the file is looked at from its lower node
            if int(world.edge_kind[k]) != rsnv.EDGE_WALK or k in tight or a > b and (b, a) in index:
                continue
            pa, pb = f32(world.node_pos[a]), f32(world.node_pos[b])
            if not world.los(pa, pb) and not world.los(pb, pa):
                tight.add(k)
                if (b, a) in index:
                    tight.add(index[(b, a)])
        sections['EDGE'] = [(target, cost, rsnv.EDGEF_TIGHT if k in tight else 0)
                            for k, (target, cost, _flags) in enumerate(edge_rows)]
        meta['counts']['tight edges'] = len(tight)
        sections['META'] = json.dumps(meta, indent=1).encode('utf-8')
        size = rsnv.write(out, sections, file_header, big_endian)
    stats: dict[str, Any] = dict(meta['counts'])
    stats['bytes'] = size
    stats['island sizes'] = [row[0] for row in island_rows[:8]]
    log(f'  RSNV {out}: {size / 1e6:.2f} MB, {len(nodes)} nodes, {len(edge_rows)} edges, {len(area_rows)} areas, '
        f'{len(island_rows)} islands (largest {island_rows[0][0] if island_rows else 0}), {len(offm)} transfers')
    return stats


def export_surfaces(lv: str, cls: str, grid: 'Grid', kept: npt.NDArray[np.bool_], table: Links, nodes: Sequence[int],
                    edges: Iterable[tuple[int, int]], seeds: Sequence[Mapping[str, Any]], out: str,
                    st: Mapping[str, Any], sources: Mapping[str, str | None], sea: float, log: Log = print,
                    soldier: str | None = None,
                    objectives: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """groundnav1943's world: the kept surfaces as one cell set, boarding transfers at the class's spawns."""
    import vehicleclasses1943 as vc
    kept_cells = np.nonzero(kept)[0]
    local = np.full(grid.n, -1, np.int64)                  # surface index -> index in the cell set
    local[kept_cells] = np.arange(len(kept_cells))
    links = {}
    for (k, di, dj), n in table.items():
        if (di, dj) in STRAIGHT and kept[k] and kept[n]:
            links[(int(local[k]), di, dj)] = int(local[n])
    cell_set = CellSet(grid.pitch, grid.x0, grid.z0, grid.i[kept_cells], grid.j[kept_cells], grid.y[kept_cells], links,
                       name=f'{lv} {cls}')
    node_list = [(0, int(local[n])) for n in nodes]
    node_index = {n: k for k, n in enumerate(nodes)}
    edge_list = [(node_index[a], node_index[b], rsnv.EDGE_WALK, None, True) for a, b in edges]
    # node flags: shallow / wading by the water over them
    depth = sea - grid.y[np.asarray(nodes, np.int64)] if nodes else np.zeros(0)
    flags_of_node = np.zeros(len(nodes), np.int64)
    flags_of_node[(depth > 0) & (depth <= 0.5)] |= rsnv.NAUX_SHALLOW
    flags_of_node[depth > 0.5] |= rsnv.NAUX_WADE
    # boarding: each spawn of the class, its entry points on the soldier side, the vehicle's own node
    node_xyz = []
    for n in nodes:
        x, z = cell_set.xz(local[n])
        node_xyz.append([x, grid.y[n], z])
    node_pos = np.array(node_xyz).reshape(-1, 3)
    offm, board, alight = [], [], []
    soldier_world = rsnv.load(soldier) if soldier and os.path.isfile(soldier) else None
    for seed in seeds:
        if seed['class'] != cls or not len(node_pos):
            continue
        spawn = seed['pos']
        dist = (np.hypot(node_pos[:, 0] - spawn[0], node_pos[:, 2] - spawn[2])
                + 2 * np.abs(node_pos[:, 1] - spawn[1]))
        node = int(np.argmin(dist))
        team = {1: 1, 2: 2}.get(seed['team'], 3)
        entries = seed['entries'] or [{'point': spawn, 'radius': 4.0, 'order': 0}]
        for entry in entries:
            if entry.get('order', 0) != 0:
                continue                                     # the driver's seat
            # the soldier's node at the entry (on foot)
            foot_node = (soldier_node(soldier_world, entry['point'], float(entry['radius']) + 2.0)
                         if soldier_world is not None else None)
            offm.append((rsnv.OFFM_BOARD_VEHICLE, team, vc.CLASS_IDS[cls], node if dist[node] <= 12.0 else 0xFFFFFFFF,
                         *[float(v) for v in entry['point']], float(seed['yaw']), float(entry['radius']),
                         foot_node if foot_node is not None else 0xFFFFFFFF))
            board.append({'vehicle': seed['vehicle'], 'spawn': seed['index'], 'node': node,
                          'distance': round(float(dist[node]), 2), 'soldier node': foot_node,
                          # (foot_node is None without a soldier world)
                          'soldier island': (int(soldier_world.node_island[foot_node])  # pyright: ignore[reportOptionalMemberAccess]
                                             if foot_node is not None else None)})
    # leaving the vehicle: at the drivable node nearest each base and flag (and each boarding spawn), onto the soldier
    # node nearest it on the main island (the exits are 1.8-2.5 m beside the body [O])
    if soldier_world is not None and len(node_pos):
        targets = [(objective.get('name'), objective['position']) for objective in (objectives or []) if objective.get('position')]
        targets += [(f'spawn {b["spawn"]}', node_pos[b['node']].tolist()) for b in board]
        for name, target in targets:
            dist = np.hypot(node_pos[:, 0] - target[0], node_pos[:, 2] - target[2])
            for node in np.argsort(dist, kind='stable')[:40].tolist():
                if dist[node] > ALIGHT_TARGET_REACH:
                    break
                foot_node = soldier_node(soldier_world, node_pos[node], ALIGHT_REACH, 2.5, main_only=True)
                if foot_node is not None:
                    soldier_pos = soldier_world.node_pos[foot_node]
                    reach = float(np.hypot(soldier_pos[0] - node_pos[node, 0], soldier_pos[2] - node_pos[node, 2]))
                    offm.append((OFFM_ALIGHT_ZONE, 3, vc.CLASS_IDS[cls], node, float(soldier_pos[0]),
                                 float(soldier_pos[1]), float(soldier_pos[2]), 0.0, reach, foot_node))
                    alight.append({'target': name, 'node': node, 'soldier node': foot_node,
                                   'from target': round(float(dist[node]), 1)})
                    break
    meta = {'sources': {k: {'path': v, 'sha256': sha256(v)} for k, v in sources.items() if v and os.path.isfile(v)},
            'class parameters': {k: list(v) if isinstance(v, tuple) else v for k, v in vc.CLASSES[cls].items()},
            'boarding': board, 'alighting': alight}
    header = {'sea_level': sea, 'radius': vc.value(cls, 'radius'), 'height': vc.value(cls, 'height'),
              'step': vc.value(cls, 'step'), 'water_depth': vc.value(cls, 'water_depth'),
              'sha256': meta['sources'].get('samples', {}).get('sha256', '0' * 8)}
    return write_world(lv, cls, [cell_set], node_list, edge_list, offm, out, st['sector_offsets'], header, meta,
                       node_flags=flags_of_node.tolist(), log=log)
