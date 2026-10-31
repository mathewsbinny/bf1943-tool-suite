"""NavKit: DICE-parity report over many levels, saved per named run so runs can be compared.

  navparity.py run <name> <level>=<candidate.hpd> ... [--opfor <folder>]
  navparity.py run <name> --pattern "<path with {lv}>" [--levels sp_01,sp_02,...]
  navparity.py show <name> [<other name>]        a table; with two names, the second against the first

Each level is compared with its original (DICE) path data in OPFOR's game copy, on everything:
  sectors        sector ids in each file
  walkable       DICE ground reproduced, extra ground, areas exactly the same, detailed (0.2 m) areas
  boundaries     outline, portals and border portals of the areas that match
  routing        route nodes and edges in the same place (0.05 m), edge costs
  islands        separately connected regions (the seeded islands): count, large ones (>= 25 nodes), and over
                 DICE's route nodes (each paired with a NavKit node within 2 m): of the pairs DICE connects,
                 the share NavKit connects (connectivity), and of the pairs NavKit connects, the share DICE does
                 not (over-connection)
  ai positions   the earlier figures on the level's AI order positions: coverage, pairwise route check,
                 main route network

Runs are saved in OPFOR's work\\navkit\\parity\\<name>.json.
"""
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
import navcompare  # noqa: E402
import navdiff  # noqa: E402
import ordercheck  # noqa: E402

LEVELS = ['sp_01', 'sp_02', 'sp_03', 'sp_04', 'sp_003_b', 'sp_004_b', 'sp_005', 'sp_005_b', 'sp_006', 'sp_007',
          'sp_008', 'sp_008_b', 'sp_009']
ONSLAUGHT = ['ons_mp_002', 'ons_mp_004']       # Napisal's ports with DICE's console path data (OPFOR's Onslaught import)
NEAR = 2.0
NEAR_DY = 1.0                   # height tolerance of a node match (both files' nodes sit 0.95 m over their ground). Was
                                # 2.5 m: a street node then matched a deck node 2.5 m above it, and ONS_MP_002's detailed
                                # build showed 119 'missing' DICE links - 12 with 1 m (2026-10-21)


def opfor_root(given=None):
    import navkit_le as navkit
    return navkit.find_opfor(given)


def reference_of(root, lv):
    """DICE's path data for the level: the kept original while NavKit's file is installed. A level that had none
    (navkit_le.py install left its .opfor-none marker) gives the backup's path, which does not exist: the installed
    file is NavKit's own, not a reference."""
    path = os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')
    if os.path.isfile(path + '.opfor-orig') or os.path.isfile(path + '.opfor-none'):
        return path + '.opfor-orig'
    return path


def level_xml(root, lv):
    folder = os.path.join(root, 'work', 'levels', lv, 'dist', 'levels')
    for name in os.listdir(folder):
        if name.lower() == lv + '.xml':
            return os.path.join(folder, name)
    raise FileNotFoundError(f'{lv}: converted level partition not found in {folder} (navkit_le.py prepare {lv})')


def terrain_bounds(root, lv):
    r"""(minX, minZ, maxX, maxZ) of the level's terrain heightfield (work\levels\<lv>\terrain), or None. DICE's console
    path data can reach past it (ONS_MP_002: ground to z -527, the PC terrain ends at -512 - 3,600 m2 and a route
    that do not exist on PC), so the reference is clipped to it: ground and connectivity are measured on PC ground."""
    from bfbc2core import terrain
    folder = os.path.join(root, 'work', 'levels', lv, 'terrain')
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith('.heightfield-000.res'):
                hf = terrain.read(os.path.join(dirpath, name))
                return (float(hf.min[0]), float(hf.min[2]), float(hf.max[0]), float(hf.max[2]))
    return None


STALE_DY = 1.0     # a reference route node with no sampled PC surface within this of its ground height is stale


def stale_nodes(root, lv, reference, clip=None):
    """Ids of the reference's route nodes on ground the PC level does not have: no surface of NavKit's 0.8 m samples
    (the column and its 8 neighbours) within STALE_DY of the node's ground (node height - 0.95). DICE's data was built
    before some late geometry - sp_03: its only valley-to-plateau links run 1.5-5 m under the cliff mesh sp03_cliff_2
    and raised terrain (85 nodes; 99.8 % of its nodes are on PC ground) - and a route there is no route on PC, so
    connectivity leaves those nodes out, as it leaves out ground past the terrain (terrain_bounds). Empty without
    0.8 m samples."""
    import numpy as np
    import hpdtool
    import navsample
    candidates = (os.path.join(root, 'work', 'navsample', f'{lv}_navkit.bin'),
                  os.path.join(root, 'work', 'navsample', f'{lv}_full.bin'))
    samples_path = next((p for p in candidates if os.path.isfile(p)), None)
    if samples_path is None:
        return set()
    s, _ = navsample.load(samples_path)
    out = set()
    for sector in hpdtool.parse(open(reference, 'rb').read()).sectors:
        for node in sector.nodes:
            x, y, z = node.pos
            if clip and not (clip[0] <= x <= clip[2] and clip[1] <= z <= clip[3]):
                continue
            i = int(round((x - s.min_x) / s.pitch))
            j = int(round((z - s.min_z) / s.pitch))
            if not (1 <= i < s.nx - 1 and 1 <= j < s.nz - 1):
                continue
            ground = y - 0.95
            on_pc_ground = False
            for dj in (-1, 0, 1):
                # columns i-1..i+1 of row j+dj are consecutive, so their surfaces are one run of the sample arrays
                column = (j + dj) * s.nx + i
                first, end = s.start[column - 1], s.start[column + 2]
                if end > first and np.min(np.abs(s.y[first:end] - ground)) <= STALE_DY:
                    on_pc_ground = True
                    break
            if not on_pc_ground:
                out.add(node.id)
    return out


def ladder_ground(reference, candidate, reference_cells):
    """Ground reached only through ladder links (navdata: ladder edges, ladder areas): its share of the reference's
    ground, and how many of its cells the reference lacks (kept out of 'extra ground')."""
    from bfbc2core import navdata
    import numpy as np
    cand_nav, ref_nav = navdata.load(candidate), navdata.load(reference)
    if not len(cand_nav.cell_area) or not any(a.ladder for a in cand_nav.areas):
        return {'percent': 0.0, 'extra_cells': 0, 'cells': 0}
    cand_in_ref, _ = navdata.compare_cells(cand_nav, ref_nav)
    on_ladder_area = np.array([a.ladder for a in cand_nav.areas])[cand_nav.cell_area]     # per candidate cell
    return {'percent': round(100.0 * int(on_ladder_area.sum()) / max(reference_cells, 1), 2),
            'extra_cells': int((on_ladder_area & ~cand_in_ref).sum()),
            'cells': int(on_ladder_area.sum())}


def pairs(n):
    return n * (n - 1) // 2


def islands(reference, candidate, clip=None, drop=None):
    ref_data, cand_data = ordercheck.PathData(reference, clip, drop), ordercheck.PathData(candidate)
    res = {'reference': len(ref_data.group_size), 'candidate': len(cand_data.group_size),
           'large_reference': sum(1 for v in ref_data.group_size.values() if v >= 25),
           'large_candidate': sum(1 for v in cand_data.group_size.values() if v >= 25)}
    grid = defaultdict(list)            # NEAR-sized square -> candidate node indices
    for k, (_nid, pos) in enumerate(cand_data.nodes):
        grid[(math.floor(pos[0] / NEAR), math.floor(pos[2] / NEAR))].append(k)
    group_pairs = Counter()             # (reference group, candidate group) -> reference nodes matched across them
    matched = 0
    for nid, pos in ref_data.nodes:
        gx, gz = math.floor(pos[0] / NEAR), math.floor(pos[2] / NEAR)
        best, best_dist2 = None, None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for k in grid.get((gx + dx, gz + dz), ()):
                    other = cand_data.nodes[k][1]
                    if abs(other[1] - pos[1]) > NEAR_DY:
                        continue
                    dist2 = (other[0] - pos[0]) ** 2 + (other[2] - pos[2]) ** 2
                    if dist2 <= NEAR * NEAR and (best is None or dist2 < best_dist2):
                        best, best_dist2 = k, dist2
        if best is not None:
            matched += 1
            group_pairs[(ref_data.group[nid], cand_data.group[cand_data.nodes[best][0]])] += 1
    by_ref, by_cand = Counter(), Counter()
    for (ref_group, cand_group), n in group_pairs.items():
        by_ref[ref_group] += n
        by_cand[cand_group] += n
    both = sum(pairs(n) for n in group_pairs.values())
    ref_pairs = sum(pairs(n) for n in by_ref.values())
    cand_pairs = sum(pairs(n) for n in by_cand.values())
    res['nodes_near'] = round(100.0 * matched / max(len(ref_data.nodes), 1), 2)
    res['connectivity'] = round(100.0 * both / max(ref_pairs, 1), 2)
    res['over_connection'] = round(100.0 * (cand_pairs - both) / max(cand_pairs, 1), 2)
    return res


def run_level(root, lv, candidate, log):
    ref = reference_of(root, lv)
    xml = level_xml(root, lv)
    started = time.time()
    clip = terrain_bounds(root, lv)
    content, _, _, _ = navdiff.diff(ref, candidate, log=lambda *a: None, clip=clip)
    ai = navcompare.compare(xml, ref, candidate)
    stale = stale_nodes(root, lv, ref, clip)
    isl = islands(ref, candidate, clip, stale)
    ladder = ladder_ground(ref, candidate, content['cells']['reference'])
    sectors, cells, areas = content['sectors'], content['cells'], content['areas']
    bounds, graph = content['bounds_of_same_areas'], content['graph']
    # extra ground without the ground reached by ladder links (its own column: DICE has no ladder links)
    extra_cells = max(cells['only_candidate'] - ladder['extra_cells'], 0)
    row = {
        'level': lv, 'candidate': candidate, 'reference_clip': clip, 'stale_reference_nodes': len(stale),
        'sectors_reference': sectors['reference'], 'sectors_candidate': sectors['candidate'],
        'sectors_missing': len(sectors['only_reference']), 'sectors_extra': len(sectors['only_candidate']),
        'ground': cells['parity'],
        'extra_ground': round(100.0 * extra_cells / max(cells['reference'], 1), 2),
        'ladder_ground': ladder['percent'],
        'areas_same': areas['parity'], 'areas_reference': areas['reference'], 'areas_candidate': areas['candidate'],
        'detailed_reference': areas['detailed_reference'], 'detailed_candidate': areas['detailed_candidate'],
        'outline': bounds['outline'], 'portals': bounds['portals'], 'borders': bounds['borders'],
        'nodes_same': graph['node_parity'], 'edges_same': graph['edge_parity'],
        'costs': graph['costs_same_of_same_edges'],
        'islands_reference': isl['reference'], 'islands_candidate': isl['candidate'],
        'large_islands_reference': isl['large_reference'], 'large_islands_candidate': isl['large_candidate'],
        'nodes_near': isl['nodes_near'], 'connectivity': isl['connectivity'], 'over_connection': isl['over_connection'],
        'ai_coverage': ai['coverage_percent'], 'ai_pairwise': ai['pairwise']['percent'],
        'ai_main_reference': ai['main_network']['reference'], 'ai_main_candidate': ai['main_network']['candidate'],
    }
    log(f"  {lv:9s} ground {row['ground']:6.2f} % (+{row['extra_ground']:.1f} % extra)  "
        f"areas {row['areas_same']:6.2f} %  nodes {row['nodes_same']:6.2f} %  "
        f"connectivity {row['connectivity']:6.2f} %  AI positions {row['ai_coverage']:5.1f} %  "
        f"({time.time() - started:.0f} s)")
    return row


COLUMNS = [
    ('ground', 'ground %', '{:.1f}'), ('extra_ground', 'extra %', '{:.1f}'), ('areas_same', 'areas %', '{:.1f}'),
    ('detailed_reference', 'det.ref', '{}'), ('detailed_candidate', 'det.ours', '{}'),
    ('portals', 'portals %', '{:.1f}'), ('nodes_same', 'nodes %', '{:.1f}'), ('edges_same', 'edges %', '{:.1f}'),
    ('islands_reference', 'isl.ref', '{}'), ('islands_candidate', 'isl.ours', '{}'),
    ('connectivity', 'connect %', '{:.1f}'), ('over_connection', 'over %', '{:.1f}'),
    ('ai_coverage', 'AI pos %', '{:.1f}'), ('ai_pairwise', 'pairwise %', '{:.1f}'),
    ('ladder_ground', 'ladder %', '{:.1f}'),
]
TOTALS = ['ground', 'extra_ground', 'areas_same', 'portals', 'nodes_same', 'edges_same', 'connectivity',
          'over_connection', 'ai_coverage', 'ai_pairwise', 'ladder_ground']


def table(run, other=None, log=print):
    rows = {r['level']: r for r in run['levels']}
    base = {r['level']: r for r in other['levels']} if other else {}
    head = f"{'level':9s} " + ' '.join(f'{h:>10s}' for _, h, _ in COLUMNS)
    against = f" against '{other['name']}' ({other['date']}): value (change)" if other else ''
    log(f"run '{run['name']}' ({run['date']})" + against)
    log(head)
    for lv in [name for name in LEVELS if name in rows] + sorted(set(rows) - set(LEVELS)):
        row = rows[lv]
        cells = []
        for key, _, fmt in COLUMNS:
            if key in ('portals',) and row['areas_same'] == 0:
                cells.append(f"{'n/a':>10s}")   # a share of matching areas, and none match
                continue
            value = row.get(key, 0)
            text = fmt.format(value)
            if lv in base and isinstance(value, (int, float)) and value != base[lv].get(key, 0):
                delta = value - base[lv].get(key, 0)
                text += f"({'+' if delta > 0 else ''}{fmt.format(delta) if isinstance(delta, float) else delta})"
            cells.append(f'{text:>10s}')
        log(f'{lv:9s} ' + ' '.join(cells))
    mean = {k: sum(r.get(k, 0) for r in rows.values()) / len(rows) for k in TOTALS}
    if all(r['areas_same'] == 0 for r in rows.values()):
        mean.pop('portals')
    line = f"{'mean':9s} " + ' '.join(f"{(fmt.format(mean[k]) if k in mean else ''):>10s}" for k, _, fmt in COLUMNS)
    log(line)
    if other:
        base_mean = {k: sum(r.get(k, 0) for r in other['levels']) / len(other['levels']) for k in TOTALS}

        def change(key, fmt):
            if key not in mean or key not in base_mean:
                return ''
            delta = mean[key] - base_mean[key]
            return ('+' if delta > 0 else '') + fmt.format(delta)

        log(f"{'change':9s} " + ' '.join(f'{change(k, fmt):>10s}' for k, _, fmt in COLUMNS))


def save_path(root, name):
    folder = os.path.join(root, 'work', 'navkit', 'parity')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name.replace(' ', '_') + '.json')


def main(argv):
    args = argv[1:]
    opfor = None
    if '--opfor' in args:
        i = args.index('--opfor')
        opfor = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2 or args[0] not in ('run', 'show'):
        print(__doc__)
        return 1
    root = opfor_root(opfor)
    if args[0] == 'show':
        runs = [json.load(open(save_path(root, n))) for n in args[1:3]]
        if len(runs) == 1:
            table(runs[0])
        else:
            table(runs[1], runs[0])         # the second against the first
        return 0
    name = args[1]
    targets = []
    if '--pattern' in args:
        pattern = args[args.index('--pattern') + 1]
        levels = args[args.index('--levels') + 1].split(',') if '--levels' in args else LEVELS
        targets = [(lv, pattern.replace('{lv}', lv)) for lv in levels]
    else:
        targets = [tuple(a.split('=', 1)) for a in args[2:] if '=' in a]
    print(f"parity run '{name}': {len(targets)} level(s)")
    rows = [run_level(root, lv, path, print) for lv, path in targets]
    run = {'name': name, 'date': time.strftime('%Y-%m-%d %H:%M'), 'levels': rows}
    json.dump(run, open(save_path(root, name), 'w', encoding='utf-8', newline='\n'), indent=1)
    print()
    table(run)
    print(f"\nsaved: {save_path(root, name)}")
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navparity', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
