"""navaudit1943_checks: the report - audit.md from audit.json's content, and the JSON encoder.

markdown() turns the report run() builds (as audit.json holds it: plain JSON values) into audit.md, one section per
check; _json encodes the numpy values and sets the report holds for json.dump.
"""
import collections
import json
from typing import Any

import numpy as np

type Record = dict[str, Any]


def _cols(col: Record | None) -> list[str]:
    """A sample column as one line per surface."""
    if not col:
        return []
    out = [f"    column {col['grid']} at ({col['column'][0]}, {col['column'][1]}): terrain {col['terrain_y']}, "
           f"water {col['water_top']}"]
    for surface in col['surfaces']:
        extra = [f"slope {surface['slope_deg']}", f"clearance {surface['clearance']}"]
        if surface.get('material'):
            extra.append(surface['material'])
        if surface['water_depth']:
            extra.append(f"{surface['water_depth']} m under water")
        if surface['below_terrain']:
            extra.append(f"{surface['below_terrain']} m under the terrain")
        if surface['blocked']:
            extra.append('capsule blocked')
        if surface.get('rules'):
            extra.append('; '.join(f'{k} rule {v}' for k, v in surface['rules'].items()))
        verdict = 'kept' if surface['kept'] else 'not kept: ' + '; '.join(surface['why_not'])
        out.append(f"      y {surface['y']:8.2f}  {', '.join(extra)}  -> {verdict}")
    return out


def _fmt(p: list[float]) -> str:
    """A point (x, y, z) or (x, z) to 0.1 m."""
    return f"({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f})" if len(p) == 3 else f"({p[0]:.1f}, {p[1]:.1f})"


def markdown(report: Record) -> str:
    """audit.md: the report's sections as markdown."""
    lines = [f"# Navigation audit: {report['level']}", '',
             f"{report['date']}. Settings: {json.dumps(report['settings'])}.",
             f"Masks: {json.dumps(report['masks'])}.", '', '## Inputs', '']
    for k, v in report['inputs'].items():
        lines.append(f"- {k}: `{v.get('sha256', '')[:16]}...` {v.get('path') or v.get('name')}")
    positions = report['1 positions']
    lines += ['', '## 1. Required positions', '']
    counts = collections.Counter((position['kind'], position.get('class') or '', position['status'])
                                 for position in positions)
    lines += ['| kind | class | status | count |', '|---|---|---|---|']
    lines += [f'| {kind} | {class_} | {status} | {n} |' for (kind, class_, status), n in sorted(counts.items())]
    placeholders = report.get('placeholders at the origin (not checked)') or []
    if placeholders:
        kinds = collections.Counter(position['kind'] for position in placeholders)
        lines += ['', f"Placeholders at the origin, not checked: {len(placeholders)} ("
                  + ', '.join(f'{k}: {n}' for k, n in kinds.items()) + ').']
    lines += ['', '### Failures', '']
    for position in positions:
        if position['status'] != 'fail':
            continue
        same, any_floor = position.get('nearest_same_floor'), position.get('nearest_any_floor')
        lines.append(f"- **{position['kind']} {position.get('class') or ''} {position['name']}** "
                     f"at {_fmt(position['position'])}, "
                     f"floor {position.get('floor_y')} ({position.get('grid')}): {position.get('reason')}")
        if same:
            lines.append(f"    nearest area on its floor: {same['distance']} m, layer {same['layer']}, "
                         f"sector {same['sector']}, island {same['island']} "
                         f"({same['island_nodes']} nodes{', main' if same['main'] else ''})")
        if any_floor:
            lines.append(f"    nearest area on any floor: {any_floor['distance']} m, {any_floor['dy']:+} m, "
                         f"layer {any_floor['layer']}, island {any_floor['island']} "
                         f"({any_floor['island_nodes']} nodes)")
        if position.get('objects'):
            lines.append(f"    objects: {', '.join(position['objects'])}")
        lines += _cols(position.get('column'))
    lines += ['', '### Capture areas and repair stations', '',
              '| position | source | area m2 | land m2 | main m2 | share | of | status |',
              '|---|---|---|---|---|---|---|---|']
    for position in positions:
        share = position.get('capture_share')
        if share:
            lines.append(f"| {position['kind']} {position['name']} | {share['source']} | {share['area_m2']} | "
                         f"{share['land_m2']} | "
                         f"{share['main_network_m2']} | {share['share']} | {share['share_of']} | {share['status']} |")
    reach = report['2 reachability']
    lines += ['', '## 2. Reachability', '', f"Capture-area target nodes: {json.dumps(reach['targets'])}.", '']
    bad = [spawn for spawn in reach['spawns']
           if not spawn.get('joined') or not all(spawn['to'].values()) or not all(spawn['back'].values())]
    lines.append(f"{len(reach['spawns'])} infantry spawn points; {len(bad)} without a route to and from every "
                 f"capture area:")
    for spawn in bad:
        missing = [k for k, v in spawn.get('to', {}).items() if not v or not spawn['back'].get(k)]
        lines.append(f"- {spawn['spawn']}: island {spawn.get('island')}, main {spawn.get('main')}; "
                     f"{spawn.get('reason') or 'no route: ' + ', '.join(missing)}")
    islands = report['2 islands']
    lines += ['', '### Islands', '']
    lines.append(', '.join(f'{k}: {n}' for k, n in collections.Counter(isl['kind'] for isl in islands).items()))
    island_groups = (('Gap candidates', ('gap candidate',)),
                     ('Vehicle-only zones and separate landmasses',
                      ('vehicle-only zone', 'separate landmass (reef or islet: boat, or swimming)')),
                     ('Raised surfaces without access', ('raised surface, no access',)))
    for title, kinds in island_groups:
        lines += ['', f'#### {title}', '']
        for isl in islands:
            if isl['kind'] in kinds:
                lines.append(f"- island {isl['island']}: {isl['nodes']} nodes, {isl['area_m2']} m2, "
                             f"centre {_fmt(isl['centre'])}, {isl['mean_height_above_terrain']} m above the terrain; "
                             f"objects {', '.join(isl['objects'][:4]) or '-'}"
                             + (f"; required: {', '.join(isl['required'])}" if isl['required'] else '')
                             + (f"; linked by: {', '.join(isl['linked_by'])}" if isl.get('linked_by') else ''))
    consistency = report['3 consistency']
    lines += ['', '## 3. File consistency', '']
    for k, v in consistency['hpdcheck'].items():
        lines.append(f"- hpdcheck {k}: {len(v)}" + (f" (e.g. {v[0]})" if v else ''))
    for k in ('portals one-way', 'nodes sharing a position', 'self-loop edges'):
        lines.append(f"- {k}: {len(consistency[k])}" + ''.join(f'\n    {x}' for x in consistency[k][:10]))
    owners = consistency['border node owners (copies without bit 16, per border id)']
    lines.append(f"- border nodes: {consistency['border nodes']}; owners per border id: {json.dumps(owners)}")
    crossings = consistency.get('crossings') or []
    real = [place for place in crossings if place['verdict'] == 'real break']
    lines += ['', f"### Crossings: {len(crossings)} places where touching ground of one floor lies on two islands; "
              f"{len(real)} real breaks (kept ground joins them under the build's step)", '']
    for place in crossings:
        lines.append(f"- {place['verdict']}: {_fmt(place['centre'])}, {place['cell_pairs']} cell pairs, "
                     f"{'across a sector seam' if place['across_a_seam'] else 'inside a sector'} ({place['grid']}), "
                     f"islands of {' and '.join(str(n) for n in place['island_nodes'])} nodes"
                     f"{' (one is the main network)' if place['main'] else ''}")
    resolution = report['4 resolution']
    lines += ['', '## 4. Resolution (0.8 m against 0.2 m, detailed sectors)', '']
    if 'skipped' in resolution:
        lines.append(resolution['skipped'])
    else:
        for k in ('walkable at 0.8 m, not at 0.2 m', 'walkable at 0.2 m, not at 0.8 m'):
            lines += ['', f'### {k}: {len(resolution[k])} places wider than a cell row', '']
            for place in resolution[k][:40]:
                lines.append(f"- sector {place['sector']} at ({place['centre'][0]}, {place['terrain_y']}, "
                             f"{place['centre'][1]}): {place['core_m2']} m2 core; "
                             f"objects {', '.join(place['objects'][:4]) or '-'}")
                lines += _cols({'grid': '0.8 m', 'column': place['centre'], 'terrain_y': place['terrain_y'],
                                'water_top': None, 'surfaces': place['column_08'] or []})[1:]
        lines += ['', f"### Connectivity: {len(resolution['connectivity'])} places", '']
        lines += [f"- sector {place['sector']} at {_fmt(place['centre'])}: {place['kind']} ({place['pieces']} pieces)"
                  for place in resolution['connectivity'][:40]]
    lines += ['', '## 5. Layers at required positions', '']
    for position in positions:
        col = position.get('column')
        if col and sum(1 for _surface in col['surfaces']) > 1:
            same = position.get('nearest_same_floor')
            used = f"layer {same['layer']} at {same['ground_y']}" if same else 'none'
            lines.append(f"- {position['kind']} {position['name']} at {_fmt(position['position'])}: "
                         f"{position['status']}; area used: " + used)
            lines += _cols(col)
    water = report['6 water']
    lines += ['', '## 6. Water and shore', '',
              f"Sea level {water['sea_level']}. Walkable cells under more than 1.5 m of water: "
              f"{water['walkable cells under more than 1.5 m of water']}; in the wading band: "
              f"{water['walkable cells in the wading band (0-1.5 m)']}."]
    for cluster in water['deep walkable clusters']:
        lines.append(f"- deep: {cluster['points']} cells around {_fmt(cluster['centre'])}")
    for grid, band in water['wading band'].items():
        lines.append(f"- {grid}: band surfaces {band['band surfaces (terrain, 0-1.4 m deep)']}, "
                     f"flat and clear {band['flat and clear']}, kept {band['kept']}, not kept {band['not kept']}; "
                     f"kept deeper than 1.4 m: {band['kept surfaces deeper than 1.4 m']}")
        for cluster in band['not kept clusters'][:10]:
            lines.append(f"    not kept: {cluster['points']} surfaces around {_fmt(cluster['centre'])}")
    lines.append('')
    for pier in water['piers, jetties, bridges']:
        lines.append(f"- {pier['object']} at {_fmt(pier['centre'])}: {pier['cells']} cells, {pier['on_main']} on the "
                     f"main network, other islands {pier['other_islands']}")
    rules = report['7 rules']
    lines += ['', '## 7. Rules', '', f"- objective floor: {rules['objective floor']}"]
    for k, v in rules.items():
        if k in ('objective floor', 'near-misses', 'ladders'):
            continue
        lines.append(f"- {k}: removed {v['removed']}, added {v['added']}")
        for change in ('removed', 'added'):
            for cluster in v.get(change + ' clusters', [])[:6]:
                lines.append(f"    {change}: {cluster['points']} surfaces around {_fmt(cluster['centre'])} - "
                             f"{cluster.get('nearest_required', '')}")
            for x in v.get(change + ' near required positions', [])[:12]:
                lines.append(f"    {change} near {x}")
    lines += ['', '### Near-misses (within 20 % of a threshold)', '']
    for grid, record in rules['near-misses'].items():
        for k, v in record.items():
            counts_only = {name: value for name, value in v.items() if 'clusters' not in name}
            lines.append(f"- {grid} {k}: {json.dumps(counts_only)}")
            for name, value in v.items():
                if 'clusters' in name:
                    for cluster in value[:5]:
                        lines.append(f"    {cluster['points']} around {_fmt(cluster['centre'])}")
    lines += ['', '### Ladders', '']
    for ladder in rules['ladders']:
        foot_area, top_area = ladder['foot_area'], ladder['top_area']
        lines.append(f"- {ladder['ladder']} at {_fmt(ladder['centre'])} to {ladder['top']}: "
                     f"foot area {foot_area and foot_area['distance']}, top area {top_area and top_area['distance']}, "
                     f"ladder edges near {ladder['ladder_edges_near']}"
                     + (', ends in two sectors' if ladder['ends_in_two_sectors'] else '')
                     + (', near-miss' if ladder['near_miss'] else ''))
    playable = report['8 playable area']
    lines += ['', '## 8. Playable area', '']
    lines += [f"- {k}: {v}" for k, v in playable.items() if k != 'walkable outside, clusters']
    for cluster in playable.get('walkable outside, clusters', []):
        lines.append(f"    outside: {cluster['points']} cells around {_fmt(cluster['centre'])}; "
                     f"islands {cluster.get('islands')}; {', '.join(cluster.get('objects', [])[:4])}")
    return '\n'.join(lines) + '\n'


def _json(o: object) -> object:
    """json.dump's default: numpy numbers and arrays, sets and tuples as plain JSON values."""
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(type(o))
