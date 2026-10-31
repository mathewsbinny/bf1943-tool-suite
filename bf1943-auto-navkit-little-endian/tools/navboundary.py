r"""NavKit: cell-by-cell area boundary diagnostic against DICE's path data (normal pitch, single-layer columns).

  navboundary.py <level> [<candidate.hpd>] [--out <folder>] [--region x0,z0,x1,z1] [--opfor <folder>]

The candidate defaults to OPFOR's work\navkit\regress\<level>.hpd (navregress.py's build). Every straight
neighbour edge between two cells that both datasets have is classified:
  match      a boundary in both (the two cells are in different areas in DICE's data and in NavKit's)
  missing    a DICE boundary NavKit does not have (NavKit merges the two DICE areas there)
  extra      a NavKit boundary DICE does not have
  (interior) no boundary in either
Per cell: DICE and NavKit area, terrain height and slope, whether the DICE area is a single cell (DICE cuts steep
cells off on their own) and whether NavKit merges the DICE area with others. Columns with more than one floor in
either dataset are left out (counted in the summary).

Measures (summary.json): boundary precision / recall at 0, 1 and 2 cells' tolerance, exact area matches, DICE areas
merged / split, portal count and displacement (each portal's midpoint to the nearest portal midpoint of the other
dataset, both ways), ground in one dataset only.

Writes to --out (default OPFOR's work\navkit\boundary\<level>): cells.csv, edges.csv (non-interior edges),
portals.csv, summary.json, and maps: overview.png (whole level) and worst_sector.png (the 100 m square with the
most missing + extra edges), or region.png for --region.
"""
import csv
import json
import os
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))

TOLERANCES = (0, 1, 2)
OFF = 2 ** 20


def _keys(ij):
    """One sortable integer per (i, j) cell."""
    return (ij[:, 0] + OFF) * 2 ** 21 + (ij[:, 1] + OFF)


def cells_of(nav):
    """Single-layer normal cells of a NavData: (ij (n, 2), area (n,), key (n,) sorted), and the multi-layer keys."""
    from bfbc2core import navdata
    normal = np.array([not a.detailed for a in nav.areas], bool)
    if not len(nav.cell_area):
        return np.zeros((0, 2), np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
    in_normal = normal[nav.cell_area]
    ij = np.round(nav.cell_xz[in_normal] / navdata.GRID).astype(np.int64)
    area = nav.cell_area[in_normal].astype(np.int64)
    key = _keys(ij)
    distinct, count = np.unique(key, return_counts=True)
    single = np.isin(key, distinct[count == 1])
    order = np.argsort(key[single])
    return ij[single][order], area[single][order], key[single][order], distinct[count > 1]


def _index(keys, wanted):
    """Where each wanted key is in the sorted keys, and whether it is there at all."""
    pos = np.minimum(np.searchsorted(keys, wanted), max(len(keys) - 1, 0))
    found = (keys[pos] == wanted) if len(keys) else np.zeros(len(wanted), bool)
    return pos, found


def _near(points, targets, r):
    """For each point (half-cell integer coordinates), whether a target lies within r half cells (Chebyshev)."""
    target_set = {tuple(t) for t in targets.tolist()}
    offsets = [(a, b) for a in range(-r, r + 1) for b in range(-r, r + 1)]
    return np.array([any((p[0] + a, p[1] + b) in target_set for a, b in offsets) for p in points.tolist()], bool)


def _portal_mids(nav):
    """Each normal-pitch portal's midpoint (x, z) and length."""
    out = []
    for area in nav.areas:
        if area.detailed:
            continue
        for p0, p1, _ in area.portals:
            length = float(np.hypot(p1[0] - p0[0], p1[2] - p0[2]))
            out.append(((p0[0] + p1[0]) / 2, (p0[2] + p1[2]) / 2, length))
    return np.array(out, float).reshape(-1, 3)


def _nearest(a, b):
    """Distance from each 2-D point of a to the nearest of b (grid buckets of 10 m, then exact)."""
    if not len(a):
        return np.zeros(0)
    if not len(b):
        return np.full(len(a), np.inf)
    cell = 10.0
    buckets = {}
    for k, (x, z) in enumerate(b.tolist()):
        buckets.setdefault((int(x // cell), int(z // cell)), []).append(k)
    out = np.full(len(a), np.inf)
    for n, (x, z) in enumerate(a.tolist()):
        bx, bz = int(x // cell), int(z // cell)
        # rings of buckets outwards until the nearest found is closer than the next ring can be
        for ring in range(0, 30):
            in_ring = [k for i in range(bx - ring, bx + ring + 1) for j in range(bz - ring, bz + ring + 1)
                       if max(abs(i - bx), abs(j - bz)) == ring for k in buckets.get((i, j), [])]
            if in_ring:
                dist = np.hypot(b[in_ring, 0] - x, b[in_ring, 1] - z).min()
                out[n] = min(out[n], dist)
                if out[n] <= ring * cell:
                    break
    return out


def diagnose(ref, cand, heightfield=None):
    """The cell and edge classification and the measures. ref / cand: NavData (DICE / NavKit)."""
    from bfbc2core import navdata
    G = navdata.GRID
    ref_ij, ref_area, ref_key, ref_multi = cells_of(ref)
    cand_ij, cand_area, cand_key, cand_multi = cells_of(cand)
    multi = np.union1d(ref_multi, cand_multi)
    keep_ref = ~np.isin(ref_key, multi)
    keep_cand = ~np.isin(cand_key, multi)
    ref_ij, ref_area, ref_key = ref_ij[keep_ref], ref_area[keep_ref], ref_key[keep_ref]
    cand_ij, cand_area, cand_key = cand_ij[keep_cand], cand_area[keep_cand], cand_key[keep_cand]
    # the shared cells (in both datasets), in the reference's order
    pos, both = _index(cand_key, ref_key)
    ij = ref_ij[both]
    dice_area = ref_area[both]
    navkit_area = cand_area[pos[both]]
    key = ref_key[both]
    n = len(key)
    dice_size = Counter(ref_area.tolist())
    # DICE areas NavKit merges: the NavKit area holding most of a DICE area's shared cells also holds other DICE areas
    pairs = Counter(zip(dice_area.tolist(), navkit_area.tolist()))     # (DICE area, NavKit area) -> shared cells
    best = {}                       # DICE area -> (the NavKit area with most of its cells, how many)
    for (dice, navkit), count in pairs.items():
        if count > best.get(dice, (None, -1))[1]:
            best[dice] = (navkit, count)
    navkit_to_dice = {}
    for (dice, navkit) in pairs:
        navkit_to_dice.setdefault(navkit, set()).add(dice)
    merged = {dice for dice, (navkit, _) in best.items() if len(navkit_to_dice[navkit]) > 1}
    dice_to_navkit = {}
    for (dice, navkit) in pairs:
        dice_to_navkit.setdefault(dice, set()).add(navkit)

    def exactly_same(dice, navkits):
        """One NavKit area holding this DICE area's every cell and nothing else."""
        if len(navkits) != 1 or navkit_to_dice[next(iter(navkits))] != {dice}:
            return False
        return dice_size[dice] == sum(count for (other, _), count in pairs.items() if other == dice)

    exact = sum(1 for dice, navkits in dice_to_navkit.items() if exactly_same(dice, navkits))
    split = sum(1 for dice, navkits in dice_to_navkit.items() if len(navkits) > 1)

    height = slope = None
    if heightfield is not None:
        x, z = ij[:, 0] * G, ij[:, 1] * G
        height = heightfield.height(x, z)
        e = 0.05
        grad_x = (heightfield.height(x + e, z) - heightfield.height(x - e, z)) / (2 * e)
        grad_z = (heightfield.height(x, z + e) - heightfield.height(x, z - e)) / (2 * e)
        slope = np.degrees(np.arctan(np.hypot(grad_x, grad_z)))

    # straight neighbour edges between shared cells: (cell a, cell b, midpoint, boundary in DICE, boundary in NavKit)
    edges = []
    for di, dj in ((1, 0), (0, 1)):
        neighbour, found = _index(key, _keys(ij + np.array([di, dj])))
        cell_a = np.nonzero(found)[0]
        cell_b = neighbour[found]
        in_dice = dice_area[cell_a] != dice_area[cell_b]
        in_navkit = navkit_area[cell_a] != navkit_area[cell_b]
        mid = 2 * ij[cell_a] + np.array([di, dj])            # half-cell coordinates of the edge midpoint
        edges.append((cell_a, cell_b, mid, in_dice, in_navkit))
    cell_a = np.concatenate([e[0] for e in edges])
    cell_b = np.concatenate([e[1] for e in edges])
    mid = np.concatenate([e[2] for e in edges]).reshape(-1, 2)
    in_dice = np.concatenate([e[3] for e in edges])
    in_navkit = np.concatenate([e[4] for e in edges])
    kind = np.full(len(cell_a), '', dtype=object)
    kind[in_dice & in_navkit] = 'match'
    kind[in_dice & ~in_navkit] = 'missing'
    kind[~in_dice & in_navkit] = 'extra'
    summary = {'shared_cells': int(n), 'dice_only_cells': int((~both).sum()),
               'navkit_only_cells': int(len(cand_key) - both.sum()),
               'multi_layer_columns_left_out': int(len(multi)),
               'edges': int(len(cell_a)), 'match': int((in_dice & in_navkit).sum()),
               'missing': int((in_dice & ~in_navkit).sum()), 'extra': int((~in_dice & in_navkit).sum()),
               'dice_areas': len(dice_size), 'dice_single_cell_areas': sum(1 for v in dice_size.values() if v == 1),
               'dice_areas_merged_by_navkit': len(merged), 'dice_areas_split_by_navkit': split,
               'exact_areas': exact, 'exact_areas_pct': round(100.0 * exact / max(len(dice_size), 1), 2)}
    for t in TOLERANCES:
        navkit_mids, dice_mids = mid[in_navkit], mid[in_dice]
        if len(navkit_mids):
            summary[f'boundary_precision_{t}'] = round(100.0 * _near(navkit_mids, dice_mids, 2 * t).mean(), 2)
        else:
            summary[f'boundary_precision_{t}'] = None
        if len(dice_mids):
            summary[f'boundary_recall_{t}'] = round(100.0 * _near(dice_mids, navkit_mids, 2 * t).mean(), 2)
        else:
            summary[f'boundary_recall_{t}'] = None
    dice_portals, navkit_portals = _portal_mids(ref), _portal_mids(cand)
    dice_to_navkit_dist = _nearest(dice_portals[:, :2], navkit_portals[:, :2])
    navkit_to_dice_dist = _nearest(navkit_portals[:, :2], dice_portals[:, :2])

    def median(dist):
        return round(float(np.median(dist)), 2) if len(dist) else None

    def within_1m(dist):
        return round(100.0 * float(np.mean(dist <= 1.0)), 2) if len(dist) else None

    summary.update({'portals_dice': int(len(dice_portals)), 'portals_navkit': int(len(navkit_portals)),
                    'portal_displacement_dice_to_navkit_median': median(dice_to_navkit_dist),
                    'portal_displacement_navkit_to_dice_median': median(navkit_to_dice_dist),
                    'dice_portals_within_1m': within_1m(dice_to_navkit_dist),
                    'navkit_portals_within_1m': within_1m(navkit_to_dice_dist)})
    cells = {'ij': ij, 'dice_area': dice_area, 'navkit_area': navkit_area, 'height': height, 'slope': slope,
             'dice_single': np.array([dice_size[r] == 1 for r in dice_area.tolist()], bool),
             'dice_merged': np.array([r in merged for r in dice_area.tolist()], bool)}
    only = {'dice': ref_ij[~both], 'navkit': cand_ij[~np.isin(cand_key, ref_key)]}
    edge_table = {'p': cell_a, 'q': cell_b, 'mid': mid, 'kind': kind}
    return summary, cells, edge_table, {'dice': dice_portals, 'navkit': navkit_portals}, only


def render(path, cells, edges, portals, only, region, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from bfbc2core import navdata
    G = navdata.GRID
    x0, z0, x1, z1 = region
    span = max(x1 - x0, z1 - z0)
    fig, ax = plt.subplots(figsize=(12, 12), dpi=110 if span > 400 else 150)
    def in_region(x, z):
        return (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)

    ij = cells['ij']
    X, Z = ij[:, 0] * G, ij[:, 1] * G                       # the shared cells in metres
    shown = in_region(X, Z)
    marker_size = max(0.2, (72 * 12 / (span / G)) ** 2 * 0.9)
    if cells['slope'] is not None:
        slope_points = ax.scatter(X[shown], Z[shown], c=cells['slope'][shown], cmap='Greys', vmin=0, vmax=50,
                                  s=marker_size, marker='s', linewidths=0)
        fig.colorbar(slope_points, ax=ax, fraction=0.03, label='terrain slope (deg)')
    else:
        ax.scatter(X[shown], Z[shown], c='0.85', s=marker_size, marker='s', linewidths=0)
    for name, colour in (('dice', '#d6a400'), ('navkit', '#7a3db8')):
        only_cells = only[name]
        if len(only_cells):
            only_x, only_z = only_cells[:, 0] * G, only_cells[:, 1] * G
            mask = in_region(only_x, only_z)
            ax.scatter(only_x[mask], only_z[mask], c=colour, s=marker_size, marker='s', linewidths=0,
                       label=f'ground in {"DICE" if name == "dice" else "NavKit"} only')
    single = shown & cells['dice_single']
    ax.scatter(X[single], Z[single], facecolors='none', edgecolors='#e0007a', s=marker_size * 2.5, marker='o',
               linewidths=0.6, label='DICE single-cell area')
    mid = edges['mid']
    for kind, colour, label in (('match', '#1a9850', 'boundary in both'),
                                ('missing', '#d73027', 'DICE boundary NavKit lacks'),
                                ('extra', '#4575b4', 'NavKit boundary DICE lacks')):
        sel = edges['kind'] == kind
        if not sel.any():
            continue
        m = mid[sel].astype(float) / 2 * G                  # edge midpoint in metres
        vertical = (mid[sel, 0] % 2) == 1                   # x odd: an edge between (i, j) and (i+1, j) - a line along z
        along_z = np.stack([np.stack([m[:, 0], m[:, 1] - G / 2], 1), np.stack([m[:, 0], m[:, 1] + G / 2], 1)], 1)
        along_x = np.stack([np.stack([m[:, 0] - G / 2, m[:, 1]], 1), np.stack([m[:, 0] + G / 2, m[:, 1]], 1)], 1)
        segs = np.where(vertical[:, None, None], along_z, along_x)
        keep = in_region(m[:, 0], m[:, 1])
        ax.add_collection(LineCollection(segs[keep], colors=colour, linewidths=1.6 if span < 400 else 0.8, label=label))
    for name, colour, marker in (('dice', 'black', 'x'), ('navkit', '#ff7f00', '+')):
        mids = portals[name]
        if len(mids):
            mask = in_region(mids[:, 0], mids[:, 1])
            ax.scatter(mids[mask, 0], mids[mask, 1], c=colour, marker=marker, s=18, linewidths=0.8,
                       label=f'{"DICE" if name == "dice" else "NavKit"} portal (midpoint)')
    ax.set_xlim(x0, x1)
    ax.set_ylim(z0, z1)
    ax.set_aspect('equal')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('z (m)')
    ax.set_title(title)
    ax.legend(loc='upper right', fontsize=7, markerscale=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def export(out, cells, edges, portals):
    from bfbc2core import navdata
    G = navdata.GRID
    with open(os.path.join(out, 'cells.csv'), 'w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh, lineterminator='\n')
        writer.writerow(['x', 'z', 'dice_area', 'navkit_area', 'height', 'slope_deg', 'dice_single_cell',
                         'dice_area_merged'])
        height, slope = cells['height'], cells['slope']
        for k, (i, j) in enumerate(cells['ij'].tolist()):
            writer.writerow([round(i * G, 2), round(j * G, 2), int(cells['dice_area'][k]), int(cells['navkit_area'][k]),
                             '' if height is None else round(float(height[k]), 3),
                             '' if slope is None else round(float(slope[k]), 1),
                             int(cells['dice_single'][k]), int(cells['dice_merged'][k])])
    with open(os.path.join(out, 'edges.csv'), 'w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh, lineterminator='\n')
        writer.writerow(['x', 'z', 'along', 'kind', 'dice_area_a', 'dice_area_b', 'navkit_area_a', 'navkit_area_b',
                         'height_step'])
        height = cells['height']
        for k in np.nonzero(edges['kind'] != '')[0].tolist():
            p, q, mid = edges['p'][k], edges['q'][k], edges['mid'][k]      # the two cells, the half-cell midpoint
            writer.writerow([round(mid[0] / 2 * G, 2), round(mid[1] / 2 * G, 2), 'z' if mid[0] % 2 else 'x',
                             edges['kind'][k],
                             int(cells['dice_area'][p]), int(cells['dice_area'][q]),
                             int(cells['navkit_area'][p]), int(cells['navkit_area'][q]),
                             '' if height is None else round(float(abs(height[q] - height[p])), 3)])
    with open(os.path.join(out, 'portals.csv'), 'w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh, lineterminator='\n')
        writer.writerow(['source', 'x', 'z', 'length'])
        for name in ('dice', 'navkit'):
            for x, z, length in portals[name].tolist():
                writer.writerow([name, round(x, 2), round(z, 2), round(length, 2)])


def worst_square(edges, size=100.0):
    from bfbc2core import navdata
    G = navdata.GRID
    bad = edges['mid'][(edges['kind'] == 'missing') | (edges['kind'] == 'extra')].astype(float) / 2 * G
    if not len(bad):
        return None
    squares = Counter(map(tuple, np.floor(bad / size).astype(int).tolist()))
    (i, j), _ = squares.most_common(1)[0]
    return (i * size, j * size, (i + 1) * size, (j + 1) * size)


def main(argv):
    args = argv[1:]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    # positional arguments: neither an option nor an option's value
    positional = [a for k, a in enumerate(args)
                  if not a.startswith('--') and (k == 0 or not args[k - 1].startswith('--'))]
    if not positional:
        print(__doc__)
        return 2
    import navparity
    from bfbc2core import navdata, level as levelmod
    lv = positional[0].lower()
    root = navparity.opfor_root(opt('--opfor'))
    if len(positional) > 1:
        cand_path = positional[1]
    else:
        cand_path = os.path.join(root, 'work', 'navkit', 'regress', f'{lv}.hpd')
    if not os.path.isfile(cand_path):
        raise FileNotFoundError(f'no NavKit file {cand_path} (build it with navregress.py --levels {lv})')
    out = opt('--out') or os.path.join(root, 'work', 'navkit', 'boundary', lv)
    os.makedirs(out, exist_ok=True)
    started = time.time()
    ref = navdata.load(navparity.reference_of(root, lv))
    cand = navdata.load(cand_path)
    try:
        hf = levelmod.heightfield_of(root, lv, log=lambda *a: None)
    except Exception as e:
        print(f'no terrain heights ({e}); maps without slope')
        hf = None
    summary, cells, edges, portals, only = diagnose(ref, cand, hf)
    summary.update({'level': lv, 'candidate': cand_path, 'date': time.strftime('%Y-%m-%d %H:%M')})
    export(out, cells, edges, portals)
    G = navdata.GRID
    X, Z = cells['ij'][:, 0] * G, cells['ij'][:, 1] * G
    if opt('--region'):
        region = tuple(float(v) for v in opt('--region').split(','))
        render(os.path.join(out, 'region.png'), cells, edges, portals, only, region, f'{lv}: area boundaries, region')
    else:
        whole = (X.min() - 5, Z.min() - 5, X.max() + 5, Z.max() + 5) if len(X) else (0, 0, 100, 100)
        render(os.path.join(out, 'overview.png'), cells, edges, portals, only, whole, f'{lv}: area boundaries')
        worst = worst_square(edges)
        if worst:
            summary['worst_square'] = worst
            render(os.path.join(out, 'worst_sector.png'), cells, edges, portals, only, worst,
                   f'{lv}: the 100 m square with the most mismatched boundary edges')
    json.dump(summary, open(os.path.join(out, 'summary.json'), 'w', encoding='utf-8', newline='\n'), indent=1)
    for name in ('shared_cells', 'match', 'missing', 'extra', 'boundary_precision_0', 'boundary_recall_0',
              'boundary_precision_1', 'boundary_recall_1', 'boundary_precision_2', 'boundary_recall_2',
              'exact_areas_pct', 'dice_areas_merged_by_navkit', 'dice_areas_split_by_navkit',
              'portals_dice', 'portals_navkit', 'portal_displacement_dice_to_navkit_median', 'dice_portals_within_1m'):
        print(f'  {name:42s} {summary.get(name)}')
    print(f'wrote {out} ({time.time() - started:.0f} s)')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navboundary', gui=False)      # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
