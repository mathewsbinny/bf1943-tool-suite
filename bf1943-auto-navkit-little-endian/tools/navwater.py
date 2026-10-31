r"""NavKit: does DICE's path data leave out ground under water? Water depth against path-data coverage.

  navwater.py [level ...] [--candidate <pattern with {lv}>] [--json out.json] [--opfor <folder>]

Per level, every walkable cell (0.8 m grid, navdiff's cells) of NavKit's file and of DICE's gets a water
depth: the level's water surface (core/bfbc2core/water.py) above the cell minus the ground height (the
terrain height field where the cell lies on terrain, else navdiff's height estimate). Cells are grouped in
depth bands; for each band: NavKit's cells, the share DICE also has, and DICE's own cells there. If DICE
leaves out ground under water, its share drops in the deeper bands. Also: how much of NavKit's extra ground
(cells DICE does not have) is under water.

Default candidate: OPFOR's work\navbuild\{lv}_W.hpd (baseline A's files); default levels: the campaign
levels with water and DICE path data.
"""
import json
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
import navdiff  # noqa: E402
import navparity  # noqa: E402

BANDS = [(-1e9, -0.5, 'dry (water more than 0.5 m below)'), (-0.5, 0.0, 'at the shore (0-0.5 m below)'),
         (0.0, 0.5, 'under 0-0.5 m'), (0.5, 1.0, 'under 0.5-1 m'), (1.0, 1.5, 'under 1-1.5 m'),
         (1.5, 2.0, 'under 1.5-2 m'), (2.0, 3.0, 'under 2-3 m'), (3.0, 1e9, 'under more than 3 m')]
TILE = 16.0
NO_WATER = -1e9


def cells(path):
    """(i, j) -> list of ground height estimates, every area of every sector."""
    out = defaultdict(list)
    for sector in navdiff.load(path).values():
        for area in sector.cells():
            for ij, y in area.items():
                out[ij].append(y)
    return out


def water_heights(points, water):
    """Water surface height over each (x, z) point (the highest where pieces overlap), NO_WATER where none."""
    top = np.full(len(points), NO_WATER)
    if water is None or not len(water):
        return top
    tiles = defaultdict(list)           # TILE square -> indices of the points in it
    keys = np.floor(points / TILE).astype(np.int64)
    for n, (a, b) in enumerate(keys):
        tiles[(a, b)].append(n)
    tiles = {k: np.array(v) for k, v in tiles.items()}
    tri = water.vertices[water.triangles].astype(np.float64)
    # each triangle's first and last TILE square
    tile_lo = np.floor(tri[:, :, [0, 2]].min(axis=1) / TILE).astype(np.int64)
    tile_hi = np.floor(tri[:, :, [0, 2]].max(axis=1) / TILE).astype(np.int64)
    for t in range(len(tri)):
        near = [tiles[(a, b)]
                for a in range(tile_lo[t, 0], tile_hi[t, 0] + 1)
                for b in range(tile_lo[t, 1], tile_hi[t, 1] + 1) if (a, b) in tiles]
        if not near:
            continue
        idx = np.concatenate(near)
        p = points[idx]
        # barycentric coordinates l1, l2, l3 of each point in the triangle (a, b, c) on the xz plane
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri[t]
        det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(det) < 1e-12:
            continue
        l1 = ((bz - cz) * (p[:, 0] - cx) + (cx - bx) * (p[:, 1] - cz)) / det
        l2 = ((cz - az) * (p[:, 0] - cx) + (ax - cx) * (p[:, 1] - cz)) / det
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            y = l1[inside] * ay + l2[inside] * by + l3[inside] * cy
            k = idx[inside]
            top[k] = np.maximum(top[k], y)
    return top


def floors(cellmap):
    """Every floor of every cell: ((i, j), height estimate), stacked floors (a bridge over a river) apart."""
    out = []
    for ij, ys in cellmap.items():
        for y in sorted(set(round(v, 1) for v in ys)):
            if not out or out[-1][0] != ij or abs(out[-1][1] - y) > 1.0:
                out.append((ij, y))
    return out


def depths(entries, heightfield, water):
    """Per floor: water depth above its ground (NaN where there is no water above). The ground is the terrain
    height field's where the floor lies on terrain, else navdiff's estimate."""
    pts = np.array([(i * navdiff.GRID, j * navdiff.GRID) for (i, j), _ in entries], dtype=np.float64)
    estimate = np.array([y for _, y in entries])
    ground = estimate
    if heightfield is not None and len(pts):
        terrain_y = heightfield.height(pts[:, 0], pts[:, 1])
        ground = np.where(np.abs(terrain_y - estimate) < 1.5, terrain_y, estimate)
    surface = water_heights(pts, water)
    return np.where(surface > NO_WATER / 2, surface - ground, np.nan)


def band_of(depth):
    if np.isnan(depth):
        return 0
    for n, (low, high, _) in enumerate(BANDS):
        if low <= depth < high:
            return n
    return len(BANDS) - 1


def level_report(root, lv, candidate, log=print):
    from bfbc2core import level as levelmod, terrain as terrainmod, water as watermod
    ref = navparity.reference_of(root, lv)
    name = None
    for folder in ('pkg', 'dist'):
        import glob
        for path in glob.glob(os.path.join(root, 'work', 'levels', lv, folder, 'levels', '*.dbx')):
            if os.path.splitext(os.path.basename(path))[0].lower() == lv:
                from bfbc2core import dbx
                name = levelmod.terrain_name(dbx.read(path).instances)
                break
        if name:
            break
    hf = levelmod.heightfield_of(root, lv, log=lambda *a: None, name=name)
    water = watermod.read(os.path.join(root, 'work', 'levels', lv, 'terrain'), name or lv)
    if water is None or not len(water):
        log(f'{lv}: no water')
        return None
    dice_cells, navkit_cells = cells(ref), cells(candidate)
    navkit_floors, dice_floors = floors(navkit_cells), floors(dice_cells)
    navkit_depth, dice_depth = depths(navkit_floors, hf, water), depths(dice_floors, hf, water)
    # the same ground, as navdiff pairs cells: one floor on each side is the same; stacked floors pair by
    # height, up to navdiff's STACK_TOL apart (the height estimates are rough on sloped areas)
    dice_heights = defaultdict(list)            # (i, j) -> DICE's floor heights there
    for ij, y in dice_floors:
        dice_heights[ij].append(y)
    navkit_count = defaultdict(int)             # (i, j) -> NavKit's floors there
    for ij, _ in navkit_floors:
        navkit_count[ij] += 1

    def dice_has(ij, y):
        if not dice_heights.get(ij):
            return False
        if navkit_count[ij] == 1 and len(dice_heights[ij]) == 1:
            return True
        return any(abs(y - dice_y) <= navdiff.STACK_TOL for dice_y in dice_heights[ij])

    has = np.array([dice_has(ij, y) for ij, y in navkit_floors])
    navkit_band = np.array([band_of(x) for x in navkit_depth])
    dice_band = np.array([band_of(x) for x in dice_depth])
    rows = []
    for n, (_, _, label) in enumerate(BANDS):
        sel = navkit_band == n
        rows.append({'band': label, 'navkit': int(sel.sum()), 'dice': int((dice_band == n).sum()),
                     'navkit_in_dice': int(has[sel].sum()),
                     'share': round(100.0 * has[sel].sum() / sel.sum(), 1) if sel.any() else None})
    extra = ~has
    wet = extra & ~np.isnan(navkit_depth) & (navkit_depth >= 0)
    dice_wet = dice_depth[~np.isnan(dice_depth) & (dice_depth >= 0)]
    limit = float(np.percentile(dice_wet, 99.9)) if len(dice_wet) else None      # the deepest DICE goes
    deep = ~np.isnan(navkit_depth) & (navkit_depth > (limit if limit is not None else 0.0))
    return {'level': lv, 'bands': rows, 'navkit_cells': len(navkit_floors), 'dice_cells': len(dice_floors),
            'extra_cells': int(extra.sum()), 'extra_under_water': int(wet.sum()),
            'extra_under_water_pct': round(100.0 * wet.sum() / max(extra.sum(), 1), 1),
            'dice_under_water': int(len(dice_wet)), 'dice_depth_limit': None if limit is None else round(limit, 2),
            'navkit_deeper_than_dice': int(deep.sum()),
            'navkit_deeper_pct_of_extra': round(100.0 * deep.sum() / max(extra.sum(), 1), 1),
            'water_triangles': len(water)}


def main(argv):
    args = argv[1:]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    root = navparity.opfor_root(opt('--opfor'))
    pattern = opt('--candidate', os.path.join(root, 'work', 'navbuild', '{lv}_W.hpd'))
    option_values = {opt('--candidate'), opt('--json'), opt('--opfor')}
    levels = [a for a in args if not a.startswith('--') and a not in option_values]
    if not levels:
        levels = [lv for lv in navparity.LEVELS if lv not in ('sp_006', 'sp_009')]
    reports = []
    totals = defaultdict(lambda: [0, 0, 0])     # band -> [NavKit floors, of them in DICE's, DICE floors]
    for lv in levels:
        report = level_report(root, lv, pattern.replace('{lv}', lv))
        if report is None:
            continue
        reports.append(report)
        if report['dice_depth_limit'] is not None:
            limit = f'{report["dice_depth_limit"]:.2f} m'
        else:
            limit = 'n/a (no DICE ground under water)'
        print(f'\n{lv}: NavKit {report["navkit_cells"]:,} floors, DICE {report["dice_cells"]:,}; '
              f'DICE under water {report["dice_under_water"]:,}, '
              f'99.9 % of it within {limit}; NavKit deeper than that: {report["navkit_deeper_than_dice"]:,} '
              f'({report["navkit_deeper_pct_of_extra"]} % of NavKit\'s extra ground {report["extra_cells"]:,})')
        for band in report['bands']:
            total = totals[band['band']]
            total[0] += band['navkit']
            total[1] += band['navkit_in_dice']
            total[2] += band['dice']
            if band['navkit'] or band['dice']:
                share = f'{band["share"]:5.1f} %' if band['share'] is not None else '   -   '
                print(f'  {band["band"]:36s} NavKit {band["navkit"]:9,}  DICE has {share}   '
                      f'DICE cells {band["dice"]:9,}')
    print(f'\nall {len(reports)} levels:')
    for _, _, label in BANDS:
        navkit, both, dice = totals[label]
        if navkit or dice:
            print(f'  {label:36s} NavKit {navkit:9,}  DICE has {100.0 * both / navkit if navkit else 0:5.1f} %   '
                  f'DICE cells {dice:9,}')
    extra = sum(r['extra_cells'] for r in reports)
    wet = sum(r['extra_under_water'] for r in reports)
    deep = sum(r['navkit_deeper_than_dice'] for r in reports)
    print(f'  NavKit extra ground under water: {wet:,} of {extra:,} floors ({100.0 * wet / max(extra, 1):.1f} %); '
          f'deeper than DICE goes on the level: {deep:,} ({100.0 * deep / max(extra, 1):.1f} %)')
    limits = [r['dice_depth_limit'] for r in reports if r['dice_depth_limit'] is not None]
    if limits:
        print(f'  DICE\'s depth limit per level (99.9 % of its ground under water is within): '
              f'{min(limits):.2f} - {max(limits):.2f} m, median {float(np.median(limits)):.2f} m')
    if opt('--json'):
        json.dump(reports, open(opt('--json'), 'w', encoding='utf-8', newline='\n'), indent=1)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navwater', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
