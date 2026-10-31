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

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

Y_OFFSET = 0.95      # outline heights are ground + 0.95 m
GRID = 0.8           # cells are compared on this grid whatever an area's pitch (detailed zones use 0.2; 0.8 is a
                     # multiple, so these points are sample points of every area)
STACK_TOL = 4.0      # where floors are stacked, cells pair by nearest height up to this difference (storeys are 2.5 m+)
NODE_XZ, NODE_Y = 0.05, 0.3


# ------------------------------------------------------------------------------------------------ loading

class Sector:
    def __init__(self, s):
        self.id = s.id
        self.mesh = kymesh.parse_mesh(s.mesh)
        self.nodes = s.nodes
        self.edges = s.edges
        self._cells = None

    def cells(self):
        """Per area: dict (i, j) -> ground height estimate."""
        if self._cells is None:
            self._cells = [area_cells(a) for a in self.mesh.areas]
        return self._cells


def load(path):
    f = hpdtool.parse(open(path, 'rb').read())
    return {s.id: Sector(s) for s in f.sectors}


def segments(area):
    """All boundary segments of an area in (x, y, z): outline polylines, portals, border portals."""
    segs = []
    for line in area.outline:
        segs.extend(zip(line[:-1], line[1:]))
    segs.extend((p.p0, p.p1) for p in area.portals)
    segs.extend((b.p0, b.p1) for b in area.border_portals)
    return segs


def area_cells(area, y_offset=None, points=False):
    """Samples inside the area's boundary (even-odd rule on the xz plane), with a ground height estimate from the
    nearest boundary point: its height less y_offset (default Y_OFFSET, DICE's 0.95; BF1943 builds store 0.9).
    points: the values are (estimate, x, z of that boundary point)."""
    y_offset = Y_OFFSET if y_offset is None else y_offset
    segs = segments(area)
    if not segs:
        return {}
    pitch = GRID
    # a, b: the segments' two end points, one row each (x, y, z)
    a = np.array([s[0] for s in segs], dtype=np.float64)
    b = np.array([s[1] for s in segs], dtype=np.float64)
    xs = np.concatenate([a[:, 0], b[:, 0]])
    zs = np.concatenate([a[:, 2], b[:, 2]])
    i0 = int(math.floor(xs.min() / pitch)) - 1
    i1 = int(math.ceil(xs.max() / pitch)) + 1
    j0 = int(math.floor(zs.min() / pitch)) - 1
    j1 = int(math.ceil(zs.max() / pitch)) + 1
    # ii, jj: the grid index of every sample in the area's bounding box
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    ii, jj = ii.ravel(), jj.ravel()
    # a tiny offset keeps sample points off the 45-degree bridges that pass through cell corners
    px = ii * pitch + pitch * 1.37e-3
    pz = jj * pitch + pitch * 0.71e-3
    inside = np.zeros(len(ii), dtype=bool)
    ax, az, bx, bz = a[:, 0], a[:, 2], b[:, 0], b[:, 2]
    # even-odd rule: a sample is inside when a ray towards +x crosses the boundary an odd number of times;
    # segments are taken 256 at a time to bound the memory
    for k in range(0, len(ax), 256):
        sax, saz = ax[k:k + 256], az[k:k + 256]
        sbx, sbz = bx[k:k + 256], bz[k:k + 256]
        spans_z = (saz[None, :] > pz[:, None]) != (sbz[None, :] > pz[:, None])
        with np.errstate(divide='ignore', invalid='ignore'):
            # x where each segment crosses each sample's z line
            x_cross = sax[None, :] + (pz[:, None] - saz[None, :]) * (sbx - sax)[None, :] / (sbz - saz)[None, :]
        crosses = spans_z & (px[:, None] < x_cross)
        inside ^= (np.count_nonzero(crosses, axis=1) % 2 == 1)
    if not inside.any():
        return {}
    boundary_points = np.concatenate([a, b])
    sel_i, sel_j = ii[inside], jj[inside]
    sx, sz = sel_i * pitch, sel_j * pitch
    out = {}
    for k in range(0, len(sel_i), 2048):
        dist2 = ((sx[k:k + 2048, None] - boundary_points[None, :, 0]) ** 2
                 + (sz[k:k + 2048, None] - boundary_points[None, :, 2]) ** 2)
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

def match_cells(ref_cells, cand_cells):
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


def pieces(segs, pitch):
    """Segments as a set of grid-sized pieces in half-pitch units (xz only), direction-free."""
    half = pitch / 2.0
    out = set()
    for p0, p1 in segs:
        x0, z0, x1, z1 = p0[0] / half, p0[2] / half, p1[0] / half, p1[2] / half
        steps = max(int(round(max(abs(x1 - x0), abs(z1 - z0)))), 1)
        for k in range(steps):
            start = (round(x0 + (x1 - x0) * k / steps), round(z0 + (z1 - z0) * k / steps))
            end = (round(x0 + (x1 - x0) * (k + 1) / steps), round(z0 + (z1 - z0) * (k + 1) / steps))
            out.add((start, end) if start <= end else (end, start))
    return out


def _outline_segments(area):
    return [s for line in area.outline for s in zip(line[:-1], line[1:])]


def _border_pieces(area, pitch):
    """The area's border portals as (grid piece, rounded direction) pairs."""
    return {(piece, tuple(round(v, 3) for v in b.direction))
            for b in area.border_portals for piece in pieces([(b.p0, b.p1)], pitch)}


def compare_sector(ref, cand):
    r = {'cells_ref': 0, 'cells_cand': 0, 'cells_same': 0,
         'areas_ref': len(ref.mesh.areas), 'areas_cand': len(cand.mesh.areas),
         'areas_same': 0, 'areas_split': 0, 'areas_other': 0,
         'outline_same': 0, 'portals_same': 0, 'borders_same': 0,
         'nodes_ref': len(ref.nodes), 'nodes_cand': len(cand.nodes), 'nodes_same': 0,
         'edges_ref': len(ref.edges), 'edges_same': 0, 'costs_same': 0,
         'only_ref': [], 'only_cand': [],
         'detailed_ref': sum(1 for a in ref.mesh.areas if a.pitch < 0.5),
         'detailed_cand': sum(1 for a in cand.mesh.areas if a.pitch < 0.5)}
    ref_cells, cand_cells = ref.cells(), cand.cells()
    r['cells_ref'] = sum(len(c) for c in ref_cells)
    r['cells_cand'] = sum(len(c) for c in cand_cells)
    ref_to_cand, cand_used = match_cells(ref_cells, cand_cells)
    r['cells_same'] = len(ref_to_cand)
    for ai, cells in enumerate(ref_cells):
        for ij in cells:
            if (ai, ij) not in ref_to_cand:
                r['only_ref'].append(ij)
    for bi, cells in enumerate(cand_cells):
        for ij in cells:
            if (bi, ij) not in cand_used:
                r['only_cand'].append(ij)
    # areas: a reference area is reproduced by a candidate area with exactly the same boundary (outline, portals,
    # border portals as grid pieces at its own pitch) at the same height; unmatched ones are "split" when their
    # ground is spread over several candidate areas, else "other"
    def shape(area):
        segs = (_outline_segments(area) + [(p.p0, p.p1) for p in area.portals]
                + [(b.p0, b.p1) for b in area.border_portals])
        return (round(area.pitch, 2), frozenset(pieces(segs, area.pitch)), round(area.min_y * 2), round(area.max_y * 2))
    by_shape = defaultdict(list)
    for bj, area in enumerate(cand.mesh.areas):
        by_shape[shape(area)].append(bj)
    area_map = {}           # reference area index -> candidate area index
    for ai, area in enumerate(ref.mesh.areas):
        hits = by_shape.get(shape(area))
        if hits:
            area_map[ai] = hits.pop(0)
            r['areas_same'] += 1
            continue
        targets = {ref_to_cand[(ai, ij)][0] for ij in ref_cells[ai] if (ai, ij) in ref_to_cand}
        if len(targets) > 1:
            r['areas_split'] += 1
        else:
            r['areas_other'] += 1
    # boundaries of matched areas
    for ai, bj in area_map.items():
        ref_area, cand_area = ref.mesh.areas[ai], cand.mesh.areas[bj]
        pitch = ref_area.pitch
        ref_outline = pieces(_outline_segments(ref_area), pitch)
        cand_outline = pieces(_outline_segments(cand_area), pitch)
        r['outline_same'] += ref_outline == cand_outline
        ref_portals = pieces([(p.p0, p.p1) for p in ref_area.portals], pitch)
        cand_portals = pieces([(p.p0, p.p1) for p in cand_area.portals], pitch)
        if ref_portals == cand_portals:
            targets_same = (sorted(area_map.get(p.target, -1) for p in ref_area.portals)
                            == sorted(p.target for p in cand_area.portals))
        else:
            targets_same = False
        r['portals_same'] += ref_portals == cand_portals and targets_same
        r['borders_same'] += _border_pieces(ref_area, pitch) == _border_pieces(cand_area, pitch)
    # graph: candidate nodes bucketed by 0.5 m squares
    cand_nodes = defaultdict(list)
    for idx, node in enumerate(cand.nodes):
        cand_nodes[(round(node.pos[0] / 0.5), round(node.pos[2] / 0.5))].append(idx)
    # one to one: each candidate node pairs with at most one reference node (the closest still free)
    node_map, used = {}, set()
    for idx, node in enumerate(ref.nodes):
        key = (round(node.pos[0] / 0.5), round(node.pos[2] / 0.5))
        best, best_dist2 = None, None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for c in cand_nodes.get((key[0] + dx, key[1] + dz), ()):
                    other = cand.nodes[c]
                    if (c in used or abs(other.pos[0] - node.pos[0]) > NODE_XZ
                            or abs(other.pos[2] - node.pos[2]) > NODE_XZ or abs(other.pos[1] - node.pos[1]) > NODE_Y):
                        continue
                    dist2 = ((other.pos[0] - node.pos[0]) ** 2 + (other.pos[1] - node.pos[1]) ** 2
                             + (other.pos[2] - node.pos[2]) ** 2)
                    if best is None or dist2 < best_dist2 or (dist2 == best_dist2 and c == idx):
                        best, best_dist2 = c, dist2
        if best is not None:
            node_map[idx] = best
            used.add(best)
    r['nodes_same'] = len(node_map)
    cand_edges = defaultdict(list)          # (src, dst) -> cost tags
    for e in cand.edges:
        cand_edges[(e.src, e.dst)].append(e.tag)
    for e in ref.edges:
        src, dst = node_map.get(e.src), node_map.get(e.dst)
        if src is not None and dst is not None and cand_edges.get((src, dst)):
            tags = cand_edges[(src, dst)]
            r['edges_same'] += 1
            r['costs_same'] += e.tag in tags
            tags.remove(e.tag if e.tag in tags else tags[0])
    return r


# ------------------------------------------------------------------------------------------------ report

def pct(a, b):
    return 100.0 * a / b if b else 100.0


def diff(reference, candidate, per_sector=False, log=print, clip=None):
    """clip: (minX, minZ, maxX, maxZ) - reference cells outside it are left out (ground beyond the level's terrain)."""
    ref_sectors, cand_sectors = load(reference), load(candidate)
    if clip:
        min_x, min_z, max_x, max_z = clip
        for sector in ref_sectors.values():
            sector._cells = [{ij: h for ij, h in cells.items()
                              if min_x <= ij[0] * GRID <= max_x and min_z <= ij[1] * GRID <= max_z}
                             for cells in sector.cells()]
    only_ref = sorted(set(ref_sectors) - set(cand_sectors))
    only_cand = sorted(set(cand_sectors) - set(ref_sectors))
    total = defaultdict(int)
    rows = []
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
        total['cells_ref'] += sum(len(c) for c in sector.cells())
        total['areas_ref'] += len(sector.mesh.areas)
        total['nodes_ref'] += len(sector.nodes)
        total['edges_ref'] += len(sector.edges)
    for sid in only_cand:
        total['cells_cand'] += sum(len(c) for c in cand_sectors[sid].cells())

    def share(part, whole):
        return round(pct(total[part], total[whole]), 2)

    worst = sorted(rows, key=lambda row: -(len(row['only_ref']) + len(row['only_cand'])))[:15]
    res = {
        'reference': reference, 'candidate': candidate,
        'sectors': {'reference': len(ref_sectors), 'candidate': len(cand_sectors),
                    'only_reference': [hex(s) for s in only_ref], 'only_candidate': [hex(s) for s in only_cand]},
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
    sectors, cells, areas = res['sectors'], res['cells'], res['areas']
    bounds, graph = res['bounds_of_same_areas'], res['graph']
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
        for w in res['worst_sectors']:
            log(f"  {w['id']:>8}  {w['cells_only_reference']:6d} / {w['cells_only_candidate']:6d}   "
                f"areas {w['areas_same']}")
    return res, rows, ref_sectors, cand_sectors


def pngs(rows, R, C, out_dir, top):
    """R, C: the reference's and the candidate's sectors by id (diff's third and fourth results)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    os.makedirs(out_dir, exist_ok=True)

    def scatter(ax, cells, pitch, size, colour, label):
        ax.scatter([i * pitch for i, _ in cells], [j * pitch for _, j in cells], s=size, c=colour, label=label)

    for r in sorted(rows, key=lambda r: -(len(r['only_ref']) + len(r['only_cand'])))[:top]:
        sid = r['id']
        pitch = R[sid].mesh.pitch
        fig, ax = plt.subplots(figsize=(8, 8))
        same = [ij for cells in R[sid].cells() for ij in cells]
        if same:
            scatter(ax, same, pitch, 1, '#c8c8c8', 'walkable in reference')
        if r['only_ref']:
            scatter(ax, r['only_ref'], pitch, 3, '#d62728', 'only reference')
        if r['only_cand']:
            scatter(ax, r['only_cand'], pitch, 3, '#1f77b4', 'only candidate')
        ax.set_aspect('equal')
        ax.set_title(f'sector {sid:#06x}: {len(r["only_ref"])} only in reference, '
                     f'{len(r["only_cand"])} only in candidate')
        ax.legend(loc='upper right', markerscale=4, fontsize=8)
        fig.savefig(os.path.join(out_dir, f'sector_{sid:04x}.png'), dpi=110, bbox_inches='tight')
        plt.close(fig)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    opts = dict(zip(argv[1:], argv[2:]))
    if len(args) < 2:
        print(__doc__)
        return 1
    res, rows, R, C = diff(args[0], args[1], per_sector='--sectors' in argv)
    if '--json' in opts:
        json.dump(res, open(opts['--json'], 'w', encoding='utf-8', newline='\n'), indent=1)
    if '--png' in opts:
        pngs(rows, R, C, opts['--png'], int(opts.get('--top', 6)))
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navdiff', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
