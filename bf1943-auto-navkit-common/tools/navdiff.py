"""NavKit: content diff of two pathdata.hpd files of one level (reference = DICE's, candidate = NavKit's).

Usage: navdiff.py <reference.hpd> <candidate.hpd> [--sectors] [--json out.json] [--png out_dir] [--top N]

Compares what the AI gets, not bytes (ordering, ids and rounding are free). Five layers, each only meaningful
where the one before agrees:
  sectors   which sector ids exist in each file
  cells     walkable ground: every area is filled on the sample grid (samples at whole multiples of the pitch,
            outlines halfway between them); a cell is (i, j, height) - stacked floors stay apart by height
  areas     DICE areas reproduced as exactly the same set of cells (else split / merged / different)
  bounds    for areas that match: outline, portal and border-portal geometry, compared as grid-sized pieces
            (so runs merged differently still compare equal)
  graph     route nodes matched by position (0.05 m horizontally, 0.3 m vertically), then edges and costs

Parity = the share of the reference that the candidate reproduces exactly, per layer. 100 % on every layer
is the goal (navkit-dice-parity: same content, bytes may differ).
"""
import json
import math
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

Y_OFFSET = 0.95      # outline heights are ground + 0.95 m
GRID = 0.8           # cells are compared on this grid whatever an area's pitch (detailed zones use 0.2; 0.8 is a
                     # multiple, so these points are sample points of every area)
STACK_TOL = 4.0      # where floors are stacked, cells pair by nearest height up to this difference (storeys are 2.5 m+)
NODE_XZ, NODE_Y = 0.05, 0.3

type IJ = tuple[int, int]                       # a cell's grid index (i, j)
type Cells = dict[IJ, float]                    # one area's cells: (i, j) -> ground height estimate
type CellKey = tuple[int, IJ]                   # (area index, (i, j))
type Segment = tuple[kymesh.Vec3, kymesh.Vec3]
type Piece = tuple[tuple[int, int], tuple[int, int]]   # a grid-sized piece in half-pitch units, ends sorted


# ------------------------------------------------------------------------------------------------ loading

class Sector:
    """One sector of a pathdata.hpd: its parsed mesh, route nodes and edges, and its cells (computed once)."""

    def __init__(self, sector: hpdtool.Sector) -> None:
        self.id = sector.id
        self.mesh = kymesh.parse_mesh(sector.mesh)
        self.nodes = sector.nodes
        self.edges = sector.edges
        self._cells: list[Cells] | None = None

    def cells(self) -> list[Cells]:
        """Per area: dict (i, j) -> ground height estimate."""
        if self._cells is None:
            self._cells = [area_cells(area) for area in self.mesh.areas]
        return self._cells


def load(path: str) -> dict[int, Sector]:
    """The sectors of a pathdata.hpd by id."""
    hpd = hpdtool.parse(open(path, 'rb').read())
    return {sector.id: Sector(sector) for sector in hpd.sectors}


def segments(area: kymesh.Area) -> list[Segment]:
    """All boundary segments of an area in (x, y, z): outline polylines, portals, border portals."""
    segs: list[Segment] = []
    for line in area.outline:
        segs.extend(zip(line[:-1], line[1:]))
    segs.extend((portal.p0, portal.p1) for portal in area.portals)
    segs.extend((border.p0, border.p1) for border in area.border_portals)
    return segs


def area_cells(area: kymesh.Area, y_offset: float | None = None, points: bool = False) -> dict[IJ, Any]:
    """Samples inside the area's boundary (even-odd rule on the xz plane), with a ground height estimate from the
    nearest boundary point: its height less y_offset (default Y_OFFSET, DICE's 0.95; BF1943 builds store 0.9).
    points: the values are (estimate, x, z of that boundary point)."""
    y_offset = Y_OFFSET if y_offset is None else y_offset
    segs = segments(area)
    if not segs:
        return {}
    pitch = GRID
    # the segments' two end points, one row each (x, y, z)
    starts = np.array([seg[0] for seg in segs], dtype=np.float64)
    ends = np.array([seg[1] for seg in segs], dtype=np.float64)
    xs = np.concatenate([starts[:, 0], ends[:, 0]])
    zs = np.concatenate([starts[:, 2], ends[:, 2]])
    i0 = int(math.floor(xs.min() / pitch)) - 1
    i1 = int(math.ceil(xs.max() / pitch)) + 1
    j0 = int(math.floor(zs.min() / pitch)) - 1
    j1 = int(math.ceil(zs.max() / pitch)) + 1
    # the grid index of every sample in the area's bounding box
    grid_i, grid_j = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    grid_i, grid_j = grid_i.ravel(), grid_j.ravel()
    # the sample points; a tiny offset keeps them off the 45-degree bridges that pass through cell corners
    px = grid_i * pitch + pitch * 1.37e-3
    pz = grid_j * pitch + pitch * 0.71e-3
    inside = np.zeros(len(grid_i), dtype=bool)
    start_x, start_z, end_x, end_z = starts[:, 0], starts[:, 2], ends[:, 0], ends[:, 2]
    # even-odd rule: a sample is inside when a ray towards +x crosses the boundary an odd number of times;
    # segments are taken 256 at a time to bound the memory (x0, z0 - x1, z1: this chunk's segments)
    for k in range(0, len(start_x), 256):
        x0, z0 = start_x[k:k + 256], start_z[k:k + 256]
        x1, z1 = end_x[k:k + 256], end_z[k:k + 256]
        spans_z = (z0[None, :] > pz[:, None]) != (z1[None, :] > pz[:, None])
        with np.errstate(divide='ignore', invalid='ignore'):
            # x where each segment crosses each sample's z line
            x_cross = x0[None, :] + (pz[:, None] - z0[None, :]) * (x1 - x0)[None, :] / (z1 - z0)[None, :]
        crosses = spans_z & (px[:, None] < x_cross)
        inside ^= (np.count_nonzero(crosses, axis=1) % 2 == 1)
    if not inside.any():
        return {}
    boundary_points = np.concatenate([starts, ends])
    sel_i, sel_j = grid_i[inside], grid_j[inside]
    cell_x, cell_z = sel_i * pitch, sel_j * pitch
    out: dict[IJ, Any] = {}
    for k in range(0, len(sel_i), 2048):
        dist2 = ((cell_x[k:k + 2048, None] - boundary_points[None, :, 0]) ** 2
                 + (cell_z[k:k + 2048, None] - boundary_points[None, :, 2]) ** 2)
        nearest = np.argmin(dist2, axis=1)
        ground = boundary_points[nearest, 1] - y_offset
        for n in range(len(ground)):
            key = (int(sel_i[k + n]), int(sel_j[k + n]))
            if points:
                point = boundary_points[nearest[n]]
                out[key] = (float(ground[n]), float(point[0]), float(point[2]))
            else:
                out[key] = float(ground[n])
    return out


# ------------------------------------------------------------------------------------------------ comparing

def match_cells(ref_cells: Sequence[Cells], cand_cells: Sequence[Cells]
                ) -> tuple[dict[CellKey, CellKey], set[CellKey]]:
    """Pairs cells of two sectors at the same (i, j). Heights are estimates (from the boundary, so rough on large
    sloped areas): they only tell stacked floors apart. One floor on each side: the same ground. Several: paired
    by nearest height, at most STACK_TOL apart. Returns ref (area, ij) -> cand (area, ij) and the used cand cells."""
    # (i, j) -> [(area index, height)]; ai indexes reference areas, bj candidate areas
    cand_at, ref_at = defaultdict(list), defaultdict(list)
    for bj, cells in enumerate(cand_cells):
        for ij, y in cells.items():
            cand_at[ij].append((bj, y))
    for ai, cells in enumerate(ref_cells):
        for ij, y in cells.items():
            ref_at[ij].append((ai, y))
    ref_to_cand, cand_used = {}, set()
    for ij, refs in ref_at.items():
        cands = cand_at.get(ij)
        if not cands:
            continue
        if len(refs) == 1 and len(cands) == 1:
            ref_to_cand[(refs[0][0], ij)] = (cands[0][0], ij)
            cand_used.add((cands[0][0], ij))
            continue
        pairs = sorted((abs(ref_y - cand_y), ai, bj) for ai, ref_y in refs for bj, cand_y in cands)
        paired_ref, paired_cand = set(), set()
        for height_gap, ai, bj in pairs:
            if height_gap > STACK_TOL or ai in paired_ref or bj in paired_cand:
                continue
            paired_ref.add(ai)
            paired_cand.add(bj)
            ref_to_cand[(ai, ij)] = (bj, ij)
            cand_used.add((bj, ij))
    return ref_to_cand, cand_used


def pieces(segs: Iterable[Segment], pitch: float) -> set[Piece]:
    """Segments as a set of grid-sized pieces in half-pitch units (xz only), direction-free."""
    half = pitch / 2.0
    out: set[Piece] = set()
    for p0, p1 in segs:
        x0, z0, x1, z1 = p0[0] / half, p0[2] / half, p1[0] / half, p1[2] / half
        steps = max(int(round(max(abs(x1 - x0), abs(z1 - z0)))), 1)
        for k in range(steps):
            start = (round(x0 + (x1 - x0) * k / steps), round(z0 + (z1 - z0) * k / steps))
            end = (round(x0 + (x1 - x0) * (k + 1) / steps), round(z0 + (z1 - z0) * (k + 1) / steps))
            out.add((start, end) if start <= end else (end, start))
    return out


def _outline_segments(area: kymesh.Area) -> list[Segment]:
    return [seg for line in area.outline for seg in zip(line[:-1], line[1:])]


def _border_pieces(area: kymesh.Area, pitch: float) -> set[tuple[Piece, tuple[float, ...]]]:
    """The area's border portals as (grid piece, rounded direction) pairs."""
    return {(piece, tuple(round(component, 3) for component in border.direction))
            for border in area.border_portals for piece in pieces([(border.p0, border.p1)], pitch)}


def compare_sector(ref: Sector, cand: Sector) -> dict[str, Any]:
    result: dict[str, Any] = {
        'cells_ref': 0, 'cells_cand': 0, 'cells_same': 0,
        'areas_ref': len(ref.mesh.areas), 'areas_cand': len(cand.mesh.areas),
        'areas_same': 0, 'areas_split': 0, 'areas_other': 0,
        'outline_same': 0, 'portals_same': 0, 'borders_same': 0,
        'nodes_ref': len(ref.nodes), 'nodes_cand': len(cand.nodes), 'nodes_same': 0,
        'edges_ref': len(ref.edges), 'edges_same': 0, 'costs_same': 0,
        'only_ref': [], 'only_cand': [],
        'detailed_ref': sum(1 for area in ref.mesh.areas if area.pitch < 0.5),
        'detailed_cand': sum(1 for area in cand.mesh.areas if area.pitch < 0.5)}
    ref_cells, cand_cells = ref.cells(), cand.cells()
    result['cells_ref'] = sum(len(cells) for cells in ref_cells)
    result['cells_cand'] = sum(len(cells) for cells in cand_cells)
    ref_to_cand, cand_used = match_cells(ref_cells, cand_cells)
    result['cells_same'] = len(ref_to_cand)
    for ai, cells in enumerate(ref_cells):
        for ij in cells:
            if (ai, ij) not in ref_to_cand:
                result['only_ref'].append(ij)
    for bj, cells in enumerate(cand_cells):
        for ij in cells:
            if (bj, ij) not in cand_used:
                result['only_cand'].append(ij)
    # areas: a reference area is reproduced by a candidate area with exactly the same boundary (outline, portals,
    # border portals as grid pieces at its own pitch) at the same height; unmatched ones are "split" when their
    # ground is spread over several candidate areas, else "other"
    def shape(area: kymesh.Area) -> tuple[float, frozenset[Piece], int, int]:
        segs = (_outline_segments(area) + [(portal.p0, portal.p1) for portal in area.portals]
                + [(border.p0, border.p1) for border in area.border_portals])
        return (round(area.pitch, 2), frozenset(pieces(segs, area.pitch)), round(area.min_y * 2),
                round(area.max_y * 2))
    by_shape = defaultdict(list)
    for bj, area in enumerate(cand.mesh.areas):
        by_shape[shape(area)].append(bj)
    area_map: dict[int, int] = {}           # reference area index -> candidate area index
    for ai, area in enumerate(ref.mesh.areas):
        hits = by_shape.get(shape(area))
        if hits:
            area_map[ai] = hits.pop(0)
            result['areas_same'] += 1
            continue
        targets = {ref_to_cand[(ai, ij)][0] for ij in ref_cells[ai] if (ai, ij) in ref_to_cand}
        if len(targets) > 1:
            result['areas_split'] += 1
        else:
            result['areas_other'] += 1
    # boundaries of matched areas
    for ai, bj in area_map.items():
        ref_area, cand_area = ref.mesh.areas[ai], cand.mesh.areas[bj]
        pitch = ref_area.pitch
        ref_outline = pieces(_outline_segments(ref_area), pitch)
        cand_outline = pieces(_outline_segments(cand_area), pitch)
        result['outline_same'] += ref_outline == cand_outline
        ref_portals = pieces([(portal.p0, portal.p1) for portal in ref_area.portals], pitch)
        cand_portals = pieces([(portal.p0, portal.p1) for portal in cand_area.portals], pitch)
        if ref_portals == cand_portals:
            targets_same = (sorted(area_map.get(portal.target, -1) for portal in ref_area.portals)
                            == sorted(portal.target for portal in cand_area.portals))
        else:
            targets_same = False
        result['portals_same'] += ref_portals == cand_portals and targets_same
        result['borders_same'] += _border_pieces(ref_area, pitch) == _border_pieces(cand_area, pitch)
    # graph: candidate nodes bucketed by 0.5 m squares
    cand_nodes = defaultdict(list)
    for cand_index, node in enumerate(cand.nodes):
        cand_nodes[(round(node.pos[0] / 0.5), round(node.pos[2] / 0.5))].append(cand_index)
    # one to one: each candidate node pairs with at most one reference node (the closest still free)
    node_map, used = {}, set()
    for ref_index, node in enumerate(ref.nodes):
        key = (round(node.pos[0] / 0.5), round(node.pos[2] / 0.5))
        best, best_dist2 = None, None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other_index in cand_nodes.get((key[0] + dx, key[1] + dz), ()):
                    other = cand.nodes[other_index]
                    if (other_index in used or abs(other.pos[0] - node.pos[0]) > NODE_XZ
                            or abs(other.pos[2] - node.pos[2]) > NODE_XZ or abs(other.pos[1] - node.pos[1]) > NODE_Y):
                        continue
                    dist2 = ((other.pos[0] - node.pos[0]) ** 2 + (other.pos[1] - node.pos[1]) ** 2
                             + (other.pos[2] - node.pos[2]) ** 2)
                    if best is None or dist2 < best_dist2 or (dist2 == best_dist2 and other_index == ref_index):
                        best, best_dist2 = other_index, dist2
        if best is not None:
            node_map[ref_index] = best
            used.add(best)
    result['nodes_same'] = len(node_map)
    cand_edges = defaultdict(list)          # (src, dst) -> cost tags
    for edge in cand.edges:
        cand_edges[(edge.src, edge.dst)].append(edge.tag)
    for edge in ref.edges:
        src, dst = node_map.get(edge.src), node_map.get(edge.dst)
        if src is not None and dst is not None and cand_edges.get((src, dst)):
            tags = cand_edges[(src, dst)]
            result['edges_same'] += 1
            result['costs_same'] += edge.tag in tags
            tags.remove(edge.tag if edge.tag in tags else tags[0])
    return result


# ------------------------------------------------------------------------------------------------ report

def pct(part: float, whole: float) -> float:
    """part as a percentage of whole (100 when whole is 0)."""
    return 100.0 * part / whole if whole else 100.0


def diff(reference: str, candidate: str, per_sector: bool = False, log: Callable[[str], object] = print,
         clip: tuple[float, float, float, float] | None = None
         ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[int, Sector], dict[int, Sector]]:
    """clip: (minX, minZ, maxX, maxZ) - reference cells outside it are left out (ground beyond the level's terrain)."""
    ref_sectors, cand_sectors = load(reference), load(candidate)
    if clip:
        min_x, min_z, max_x, max_z = clip
        for sector in ref_sectors.values():
            sector._cells = [{ij: height for ij, height in cells.items()
                              if min_x <= ij[0] * GRID <= max_x and min_z <= ij[1] * GRID <= max_z}
                             for cells in sector.cells()]
    only_ref = sorted(set(ref_sectors) - set(cand_sectors))
    only_cand = sorted(set(cand_sectors) - set(ref_sectors))
    total: defaultdict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []
    for sid in sorted(set(ref_sectors) & set(cand_sectors)):
        row = compare_sector(ref_sectors[sid], cand_sectors[sid])
        row['id'] = sid
        rows.append(row)
        for key, value in row.items():
            if isinstance(value, int) and key != 'id':
                total[key] += value
    # sectors only in the reference count fully as missing
    for sid in only_ref:
        sector = ref_sectors[sid]
        total['cells_ref'] += sum(len(cells) for cells in sector.cells())
        total['areas_ref'] += len(sector.mesh.areas)
        total['nodes_ref'] += len(sector.nodes)
        total['edges_ref'] += len(sector.edges)
    for sid in only_cand:
        total['cells_cand'] += sum(len(cells) for cells in cand_sectors[sid].cells())

    def share(part: str, whole: str) -> float:
        return round(pct(total[part], total[whole]), 2)

    worst = sorted(rows, key=lambda row: -(len(row['only_ref']) + len(row['only_cand'])))[:15]
    report: dict[str, Any] = {
        'reference': reference, 'candidate': candidate,
        'sectors': {'reference': len(ref_sectors), 'candidate': len(cand_sectors),
                    'only_reference': [hex(sid) for sid in only_ref],
                    'only_candidate': [hex(sid) for sid in only_cand]},
        'cells': {'reference': total['cells_ref'], 'candidate': total['cells_cand'], 'same': total['cells_same'],
                  'parity': share('cells_same', 'cells_ref'),
                  'only_reference': total['cells_ref'] - total['cells_same'],
                  'only_candidate': total['cells_cand'] - total['cells_same']},
        'areas': {'reference': total['areas_ref'], 'candidate': total['areas_cand'], 'same': total['areas_same'],
                  'split': total['areas_split'], 'other': total['areas_other'],
                  'parity': share('areas_same', 'areas_ref'),
                  'detailed_reference': total['detailed_ref'], 'detailed_candidate': total['detailed_cand']},
        'bounds_of_same_areas': {'outline': share('outline_same', 'areas_same'),
                                 'portals': share('portals_same', 'areas_same'),
                                 'borders': share('borders_same', 'areas_same')},
        'graph': {'nodes_reference': total['nodes_ref'], 'nodes_candidate': total['nodes_cand'],
                  'nodes_same': total['nodes_same'], 'node_parity': share('nodes_same', 'nodes_ref'),
                  'edges_reference': total['edges_ref'], 'edges_same': total['edges_same'],
                  'edge_parity': share('edges_same', 'edges_ref'),
                  'costs_same_of_same_edges': share('costs_same', 'edges_same')},
        'worst_sectors': [{'id': hex(row['id']), 'cells_only_reference': len(row['only_ref']),
                           'cells_only_candidate': len(row['only_cand']),
                           'areas_same': f"{row['areas_same']}/{row['areas_ref']}"}
                          for row in worst],
    }
    sectors, cells, areas = report['sectors'], report['cells'], report['areas']
    bounds, graph = report['bounds_of_same_areas'], report['graph']
    log(f"sectors   reference {sectors['reference']}, candidate {sectors['candidate']}; "
        f"only in reference {len(sectors['only_reference'])}, only in candidate {len(sectors['only_candidate'])}")
    log(f"cells     {cells['same']}/{cells['reference']} reproduced ({cells['parity']} %); "
        f"{cells['only_reference']} only in reference, {cells['only_candidate']} only in candidate")
    log(f"areas     {areas['same']}/{areas['reference']} exactly the same ({areas['parity']} %); "
        f"{areas['split']} split differently, {areas['other']} other; candidate has {areas['candidate']}"
        f" (detailed 0.2 m areas: reference {areas['detailed_reference']}, candidate {areas['detailed_candidate']})")
    log(f"bounds    of the same areas: outline {bounds['outline']} %, portals {bounds['portals']} %, "
        f"border portals {bounds['borders']} %")
    log(f"graph     nodes {graph['nodes_same']}/{graph['nodes_reference']} ({graph['node_parity']} %), "
        f"edges {graph['edges_same']}/{graph['edges_reference']} ({graph['edge_parity']} %), "
        f"costs of matched edges {graph['costs_same_of_same_edges']} %; candidate has {graph['nodes_candidate']} nodes")
    if per_sector:
        log('worst sectors (cells only in reference / only in candidate, areas the same):')
        for worst_row in report['worst_sectors']:
            log(f"  {worst_row['id']:>8}  {worst_row['cells_only_reference']:6d} / "
                f"{worst_row['cells_only_candidate']:6d}   areas {worst_row['areas_same']}")
    return report, rows, ref_sectors, cand_sectors


def pngs(rows: list[dict[str, Any]], ref_sectors: dict[int, Sector], cand_sectors: dict[int, Sector], out_dir: str,
         top: int) -> None:
    """One picture per sector for the `top` sectors with the most cells in only one file. ref_sectors, cand_sectors:
    the reference's and the candidate's sectors by id (diff's third and fourth results; only the reference's are
    drawn)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    os.makedirs(out_dir, exist_ok=True)

    def scatter(ax, cells, pitch, size, colour, label):
        ax.scatter([i * pitch for i, _ in cells], [j * pitch for _, j in cells], s=size, c=colour, label=label)

    for row in sorted(rows, key=lambda row: -(len(row['only_ref']) + len(row['only_cand'])))[:top]:
        sid = row['id']
        pitch = ref_sectors[sid].mesh.pitch
        fig, ax = plt.subplots(figsize=(8, 8))
        same = [ij for cells in ref_sectors[sid].cells() for ij in cells]
        if same:
            scatter(ax, same, pitch, 1, '#c8c8c8', 'walkable in reference')
        if row['only_ref']:
            scatter(ax, row['only_ref'], pitch, 3, '#d62728', 'only reference')
        if row['only_cand']:
            scatter(ax, row['only_cand'], pitch, 3, '#1f77b4', 'only candidate')
        ax.set_aspect('equal')
        ax.set_title(f'sector {sid:#06x}: {len(row["only_ref"])} only in reference, '
                     f'{len(row["only_cand"])} only in candidate')
        ax.legend(loc='upper right', markerscale=4, fontsize=8)
        fig.savefig(os.path.join(out_dir, f'sector_{sid:04x}.png'), dpi=110, bbox_inches='tight')
        plt.close(fig)


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith('--')]
    opts = dict(zip(argv[1:], argv[2:]))
    if len(args) < 2:
        print(__doc__)
        return 1
    report, rows, ref_sectors, cand_sectors = diff(args[0], args[1], per_sector='--sectors' in argv)
    if '--json' in opts:
        json.dump(report, open(opts['--json'], 'w', encoding='utf-8', newline='\n'), indent=1)
    if '--png' in opts:
        pngs(rows, ref_sectors, cand_sectors, opts['--png'], int(opts.get('--top', 6)))
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navdiff', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
