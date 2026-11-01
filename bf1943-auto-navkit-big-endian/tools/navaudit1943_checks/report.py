"""navaudit1943_checks: the report - audit.md from audit.json's content, and the JSON encoder."""
import collections
import json

import numpy as np


def _cols(col):
    """A sample column as one line per surface."""
    if not col:
        return []
    out = [f"    column {col['grid']} at ({col['column'][0]}, {col['column'][1]}): terrain {col['terrain_y']}, "
           f"water {col['water_top']}"]
    for s in col['surfaces']:
        extra = [f"slope {s['slope_deg']}", f"clearance {s['clearance']}"]
        if s.get('material'):
            extra.append(s['material'])
        if s['water_depth']:
            extra.append(f"{s['water_depth']} m under water")
        if s['below_terrain']:
            extra.append(f"{s['below_terrain']} m under the terrain")
        if s['blocked']:
            extra.append('capsule blocked')
        if s.get('rules'):
            extra.append('; '.join(f'{k} rule {v}' for k, v in s['rules'].items()))
        verdict = 'kept' if s['kept'] else 'not kept: ' + '; '.join(s['why_not'])
        out.append(f"      y {s['y']:8.2f}  {', '.join(extra)}  -> {verdict}")
    return out


def _fmt(p):
    return f"({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f})" if len(p) == 3 else f"({p[0]:.1f}, {p[1]:.1f})"


def markdown(R):
    L = [f"# Navigation audit: {R['level']}", '', f"{R['date']}. Settings: {json.dumps(R['settings'])}.",
         f"Masks: {json.dumps(R['masks'])}.", '', '## Inputs', '']
    for k, v in R['inputs'].items():
        L.append(f"- {k}: `{v.get('sha256', '')[:16]}...` {v.get('path') or v.get('name')}")
    P = R['1 positions']
    L += ['', '## 1. Required positions', '']
    cnt = collections.Counter((p['kind'], p.get('class') or '', p['status']) for p in P)
    L += ['| kind | class | status | count |', '|---|---|---|---|']
    L += [f'| {a} | {b} | {c} | {n} |' for (a, b, c), n in sorted(cnt.items())]
    ph = R.get('placeholders at the origin (not checked)') or []
    if ph:
        L += ['', f"Placeholders at the origin, not checked: {len(ph)} ("
              + ', '.join(f'{k}: {n}' for k, n in collections.Counter(p['kind'] for p in ph).items()) + ').']
    L += ['', '### Failures', '']
    for p in P:
        if p['status'] != 'fail':
            continue
        ns, na = p.get('nearest_same_floor'), p.get('nearest_any_floor')
        L.append(f"- **{p['kind']} {p.get('class') or ''} {p['name']}** at {_fmt(p['position'])}, floor {p.get('floor_y')} "
                 f"({p.get('grid')}): {p.get('reason')}")
        if ns:
            L.append(f"    nearest area on its floor: {ns['distance']} m, layer {ns['layer']}, sector {ns['sector']}, "
                     f"island {ns['island']} ({ns['island_nodes']} nodes{', main' if ns['main'] else ''})")
        if na:
            L.append(f"    nearest area on any floor: {na['distance']} m, {na['dy']:+} m, layer {na['layer']}, "
                     f"island {na['island']} ({na['island_nodes']} nodes)")
        if p.get('objects'):
            L.append(f"    objects: {', '.join(p['objects'])}")
        L += _cols(p.get('column'))
    L += ['', '### Capture areas and repair stations', '', '| position | source | area m2 | land m2 | main m2 | share | of | status |',
          '|---|---|---|---|---|---|---|---|']
    for p in P:
        c = p.get('capture_share')
        if c:
            L.append(f"| {p['kind']} {p['name']} | {c['source']} | {c['area_m2']} | {c['land_m2']} | {c['main_network_m2']} | "
                     f"{c['share']} | {c['share_of']} | {c['status']} |")
    rs = R['2 reachability']
    L += ['', '## 2. Reachability', '', f"Capture-area target nodes: {json.dumps(rs['targets'])}.", '']
    bad = [s for s in rs['spawns'] if not s.get('joined') or not all(s['to'].values()) or not all(s['back'].values())]
    L.append(f"{len(rs['spawns'])} infantry spawn points; {len(bad)} without a route to and from every capture area:")
    for s in bad:
        miss = [k for k, v in s.get('to', {}).items() if not v or not s['back'].get(k)]
        L.append(f"- {s['spawn']}: island {s.get('island')}, main {s.get('main')}; {s.get('reason') or 'no route: ' + ', '.join(miss)}")
    I = R['2 islands']
    L += ['', '### Islands', '']
    L.append(', '.join(f'{k}: {n}' for k, n in collections.Counter(i['kind'] for i in I).items()))
    for title, kinds in (('Gap candidates', ('gap candidate',)), ('Vehicle-only zones and separate landmasses',
                                                                  ('vehicle-only zone', 'separate landmass (reef or islet: boat, or swimming)')),
                         ('Raised surfaces without access', ('raised surface, no access',))):
        L += ['', f'#### {title}', '']
        for i in I:
            if i['kind'] in kinds:
                L.append(f"- island {i['island']}: {i['nodes']} nodes, {i['area_m2']} m2, centre {_fmt(i['centre'])}, "
                         f"{i['mean_height_above_terrain']} m above the terrain; objects {', '.join(i['objects'][:4]) or '-'}"
                         + (f"; required: {', '.join(i['required'])}" if i['required'] else '')
                         + (f"; linked by: {', '.join(i['linked_by'])}" if i.get('linked_by') else ''))
    c = R['3 consistency']
    L += ['', '## 3. File consistency', '']
    for k, v in c['hpdcheck'].items():
        L.append(f"- hpdcheck {k}: {len(v)}" + (f" (e.g. {v[0]})" if v else ''))
    for k in ('portals one-way', 'nodes sharing a position', 'self-loop edges'):
        L.append(f"- {k}: {len(c[k])}" + ''.join(f'\n    {x}' for x in c[k][:10]))
    L.append(f"- border nodes: {c['border nodes']}; owners per border id: {json.dumps(c['border node owners (copies without bit 16, per border id)'])}")
    cr = c.get('crossings') or []
    real = [d for d in cr if d['verdict'] == 'real break']
    L += ['', f"### Crossings: {len(cr)} places where touching ground of one floor lies on two islands; "
          f"{len(real)} real breaks (kept ground joins them under the build's step)", '']
    for d in cr:
        L.append(f"- {d['verdict']}: {_fmt(d['centre'])}, {d['cell_pairs']} cell pairs, "
                 f"{'across a sector seam' if d['across_a_seam'] else 'inside a sector'} ({d['grid']}), islands of "
                 f"{' and '.join(str(n) for n in d['island_nodes'])} nodes{' (one is the main network)' if d['main'] else ''}")
    r4 = R['4 resolution']
    L += ['', '## 4. Resolution (0.8 m against 0.2 m, detailed sectors)', '']
    if 'skipped' in r4:
        L.append(r4['skipped'])
    else:
        for k in ('walkable at 0.8 m, not at 0.2 m', 'walkable at 0.2 m, not at 0.8 m'):
            L += ['', f'### {k}: {len(r4[k])} places wider than a cell row', '']
            for d in r4[k][:40]:
                L.append(f"- sector {d['sector']} at ({d['centre'][0]}, {d['terrain_y']}, {d['centre'][1]}): {d['core_m2']} m2 core; "
                         f"objects {', '.join(d['objects'][:4]) or '-'}")
                L += _cols({'grid': '0.8 m', 'column': d['centre'], 'terrain_y': d['terrain_y'], 'water_top': None,
                            'surfaces': d['column_08'] or []})[1:]
        L += ['', f"### Connectivity: {len(r4['connectivity'])} places", '']
        L += [f"- sector {d['sector']} at {_fmt(d['centre'])}: {d['kind']} ({d['pieces']} pieces)" for d in r4['connectivity'][:40]]
    L += ['', '## 5. Layers at required positions', '']
    for p in P:
        col = p.get('column')
        if col and sum(1 for s in col['surfaces']) > 1:
            L.append(f"- {p['kind']} {p['name']} at {_fmt(p['position'])}: {p['status']}; area used: "
                     + (f"layer {p['nearest_same_floor']['layer']} at {p['nearest_same_floor']['ground_y']}" if p.get('nearest_same_floor') else 'none'))
            L += _cols(col)
    w = R['6 water']
    L += ['', '## 6. Water and shore', '', f"Sea level {w['sea_level']}. Walkable cells under more than 1.5 m of water: "
          f"{w['walkable cells under more than 1.5 m of water']}; in the wading band: {w['walkable cells in the wading band (0-1.5 m)']}."]
    for c_ in w['deep walkable clusters']:
        L.append(f"- deep: {c_['points']} cells around {_fmt(c_['centre'])}")
    for g, v in w['wading band'].items():
        L.append(f"- {g}: band surfaces {v['band surfaces (terrain, 0-1.4 m deep)']}, flat and clear {v['flat and clear']}, kept "
                 f"{v['kept']}, not kept {v['not kept']}; kept deeper than 1.4 m: {v['kept surfaces deeper than 1.4 m']}")
        for c_ in v['not kept clusters'][:10]:
            L.append(f"    not kept: {c_['points']} surfaces around {_fmt(c_['centre'])}")
    L.append('')
    for pz in w['piers, jetties, bridges']:
        L.append(f"- {pz['object']} at {_fmt(pz['centre'])}: {pz['cells']} cells, {pz['on_main']} on the main network, other islands {pz['other_islands']}")
    rl = R['7 rules']
    L += ['', '## 7. Rules', '', f"- objective floor: {rl['objective floor']}"]
    for k, v in rl.items():
        if k in ('objective floor', 'near-misses', 'ladders'):
            continue
        L.append(f"- {k}: removed {v['removed']}, added {v['added']}")
        for nm in ('removed', 'added'):
            for c_ in v.get(nm + ' clusters', [])[:6]:
                L.append(f"    {nm}: {c_['points']} surfaces around {_fmt(c_['centre'])} - {c_.get('nearest_required', '')}")
            for x in v.get(nm + ' near required positions', [])[:12]:
                L.append(f"    {nm} near {x}")
    L += ['', '### Near-misses (within 20 % of a threshold)', '']
    for g, rec in rl['near-misses'].items():
        for k, v in rec.items():
            L.append(f"- {g} {k}: {json.dumps({a: b for a, b in v.items() if 'clusters' not in a})}")
            for a, b in v.items():
                if 'clusters' in a:
                    for c_ in b[:5]:
                        L.append(f"    {c_['points']} around {_fmt(c_['centre'])}")
    L += ['', '### Ladders', '']
    for d in rl['ladders']:
        L.append(f"- {d['ladder']} at {_fmt(d['centre'])} to {d['top']}: foot area {d['foot_area'] and d['foot_area']['distance']}, "
                 f"top area {d['top_area'] and d['top_area']['distance']}, ladder edges near {d['ladder_edges_near']}"
                 + (', ends in two sectors' if d['ends_in_two_sectors'] else '') + (', near-miss' if d['near_miss'] else ''))
    pa = R['8 playable area']
    L += ['', '## 8. Playable area', '']
    L += [f"- {k}: {v}" for k, v in pa.items() if k != 'walkable outside, clusters']
    for c_ in pa.get('walkable outside, clusters', []):
        L.append(f"    outside: {c_['points']} cells around {_fmt(c_['centre'])}; islands {c_.get('islands')}; {', '.join(c_.get('objects', [])[:4])}")
    return '\n'.join(L) + '\n'


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(type(o))
