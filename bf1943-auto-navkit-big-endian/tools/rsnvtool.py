r"""RSNV tool: look inside a navigation export (core/bfbc2core/rsnv.py, written by navexport1943.py and
navexport_vehicles1943.py), ask it questions, draw it, and compare two of them.

  rsnvtool.py info <file|level> [--class C]                     header, sections, counts, edge kinds, tight edges, islands
  rsnvtool.py validate <file|level> [--class C]                 consistency (rsnv.World.validate); exit 1 on a problem
  rsnvtool.py locate <file|level> X Y Z [--max-dist M]          the walkable cell and nearest node under a point
  rsnvtool.py los <file|level> X,Y,Z X,Y,Z                      line of sight between two points (the runtime's rule)
  rsnvtool.py route <file|level> FROM TO [--soldier] [--png F]  a search between nodes or points (X,Y,Z), as rsnav finds it
  rsnvtool.py pockets <file|level> [--top N]                    the main network's pieces when tight edges are left out
  rsnvtool.py tight <file|level> [--samples] [--json F]         every tight walk edge: its ends' areas, the first
                                                                failing line-of-sight sample each way, the rule that
                                                                refuses it and by how much, a cause; --samples adds
                                                                the build's own samples there (a level's work folder)
  rsnvtool.py cells <file|level> --at X,Z --y Y [--radius R] [--dy D] [--samples] [--line A B]
                                                                text maps around a point: each area's cells, the
                                                                build's samples on both grids, a line over them
  rsnvtool.py walk <level> A B [--reach R]                      the build's kept samples along a line (A, B: nodes or
                                                                X,Y,Z), both grids: the surface followed column by
                                                                column, the largest step, the gaps, the end's offset
  rsnvtool.py picture <file|level> --at X,Z [--radius R] [--y Y [--dy D]] [--route FROM TO] [--png F]
                                                                top-down: cells, edges (tight ones red), nodes, a route;
                                                                --y draws only the floors near that height
  rsnvtool.py diff <a> <b>                                       what changed between two exports

<file|level> is an .rsnv path or a level name (work\levels\<level>\navkit\<level>.rsnv; --class jeep|tank|lcvp|plane
for a vehicle world). FROM / TO are node numbers or points X,Y,Z (the nearest walkable cell's area node). --soldier
searches with the runtime's soldier filter (water x1.5 / x3, a tight edge +50 m); otherwise the file's own costs.
Pictures default to work\levels\<level>\navkit\rsnvtool-*.png. Reads only; never writes an export.
"""
import argparse
import collections
import math
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
from bfbc2core import game1943, rsnv  # noqa: E402

SOLDIER = {'water_scale': (256, 384, 768), 'tight_extra': 800, 'kind_mask': (1 << 7) - 1 & ~(1 << rsnv.EDGE_BOARD)
           & ~(1 << rsnv.EDGE_ALIGHT)}          # rsnav's rsn_filter_soldier, without the dynamic state


def resolve(spec, klass=None):
    """An .rsnv path, or a level name's export (with --class, its vehicle world)."""
    if os.path.isfile(spec):
        return spec
    p = os.path.join(game1943.level_work(spec), 'navkit', f'{spec}.{klass}.rsnv' if klass else f'{spec}.rsnv')
    if not os.path.isfile(p):
        raise FileNotFoundError(f'{p}: no such export (navexport1943.py {spec}'
                                f'{"; navexport_vehicles1943.py " + spec if klass else ""})')
    return p


def out_dir(path):
    return os.path.dirname(os.path.abspath(path))


def point(text):
    v = [float(x) for x in text.split(',')]
    if len(v) != 3:
        raise ValueError(f'{text!r}: a point is X,Y,Z')
    return tuple(v)


def node_of(w, text):
    """A node number, or the node of the walkable cell nearest a point X,Y,Z."""
    if ',' not in text:
        n = int(text)
        if not 0 <= n < len(w.node_pos):
            raise ValueError(f'node {n}: the file has nodes 0..{len(w.node_pos) - 1}')
        return n
    x, y, z = point(text)
    loc = w.locate(x, y, z, max_dist=6.0)
    if loc is None:
        raise ValueError(f'{text}: no walkable cell within 6 m')
    n = w.nearest_node(loc[0], x, z)
    if n is None:
        raise ValueError(f'{text}: area {loc[0]} has no node')
    return n


def edge_source(w):
    src = [0] * len(w.edge_target)
    for n in range(len(w.node_pos)):
        for k in range(int(w.node_first_edge[n]), int(w.node_first_edge[n + 1])):
            src[k] = n
    return src


def pieces(w, nodes):
    """Pieces of `nodes` when tight edges are left out, largest first."""
    left, out = set(nodes), []
    while left:
        s = left.pop()
        seen, q = {s}, [s]
        while q:
            m = q.pop()
            for k in range(int(w.node_first_edge[m]), int(w.node_first_edge[m + 1])):
                t = int(w.edge_target[k])
                if t not in seen and not int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT:
                    seen.add(t)
                    q.append(t)
        seen &= set(nodes)
        left -= seen
        out.append(sorted(seen))
    return sorted(out, key=len, reverse=True)


def cmd_info(w, path):
    kinds = collections.Counter(int(k) for k in w.edge_kind)
    walk = kinds.get(rsnv.EDGE_WALK, 0)
    tight = sum(1 for k in range(len(w.edge_target)) if int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT)
    cond = sum(1 for k in range(len(w.edge_target)) if int(w.edge_flags[k]) & rsnv.EDGEF_COND)
    pitch = collections.Counter(round(a.pitch, 1) for a in w.areas)
    print(f'{path}')
    print(f'  {os.path.getsize(path) / 1e6:.2f} MB, {"big" if w.big_endian else "little"}-endian, level id '
          f'{w.level_id:#010x}, sea level {w.sea_level:.2f} m; agent radius {w.radius} m, height {w.agent_height} m, '
          f'step {w.step} m, water {w.water_depth} m')
    print('  sections: ' + ', '.join(f'{t} {c}' for t, (o, c, s) in w.sections.items()))
    print(f'  {len(w.node_pos)} nodes, {len(w.edge_target)} edges ('
          + ', '.join(f'{rsnv.EDGE_NAMES[k] if k < len(rsnv.EDGE_NAMES) else k} {v}' for k, v in sorted(kinds.items()))
          + f'); tight {tight} of {walk} walk edges ({100 * tight / max(walk, 1):.2f} %); conditional {cond}')
    print(f'  {len(w.areas)} areas (' + ', '.join(f'{p} m: {c}' for p, c in sorted(pitch.items())) + f'), '
          f'{len(getattr(w, "ports", []))} portals, {len(w.ladders)} ladder / jump records, {len(w.offmesh)} '
          f'off-mesh records, {len(w.objects)} objects')
    isl = collections.Counter(int(i) for i in w.node_island)
    main = isl.most_common(1)[0] if isl else (None, 0)
    print(f'  {len(isl)} islands; the largest (island {main[0]}) has {main[1]} nodes')
    meta = w.meta or {}
    if meta:
        print(f"  exporter {meta.get('exporter', '?')}; sources: " + ', '.join(
            f"{k} {v.get('sha256', '')[:12]}" for k, v in (meta.get('sources') or {}).items()))
    return 0


def cmd_validate(w, path):
    probs = w.validate()
    for p in probs:
        print(f'  {p}')
    print(f'{path}: {len(probs)} problem(s)')
    return 1 if probs else 0


def cmd_locate(w, a):
    loc = w.locate(a.x, a.y, a.z, max_dist=a.max_dist)
    if loc is None:
        print(f'no walkable cell within {a.max_dist} m of ({a.x}, {a.y}, {a.z})')
        return 1
    k, (i, j), d = loc
    ar = w.areas[k]
    n = w.nearest_node(k, a.x, a.z)
    print(f'area {k} (pitch {ar.pitch} m, sector {ar.sector}), cell ({i}, {j}) at {d:.2f} m, ground '
          f'{w.ground(ar, a.x, a.z):.2f} m; nearest node {n} at {tuple(round(float(v), 2) for v in w.node_pos[n])}'
          if n is not None else f'area {k}, cell ({i}, {j}) at {d:.2f} m; the area has no node')
    return 0


def search(w, s, g, soldier):
    return rsnv.search(w, s, g, **(SOLDIER if soldier else {}))


def cmd_route(w, a, path):
    s, g = node_of(w, a.start), node_of(w, a.goal)
    st, cost, nodes, exp = search(w, s, g, a.soldier)
    length = sum(math.dist(w.node_pos[p], w.node_pos[q]) for p, q in zip(nodes, nodes[1:]))
    kinds = collections.Counter()
    tight = 0
    for p, q in zip(nodes, nodes[1:]):
        for k in range(int(w.node_first_edge[p]), int(w.node_first_edge[p + 1])):
            if int(w.edge_target[k]) == q:
                kinds[rsnv.EDGE_NAMES[int(w.edge_kind[k])]] += 1
                tight += bool(int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT)
                break
    print(f'{s} -> {g}: {st}, cost {cost / 16:.1f} m over {length:.1f} m, {len(nodes)} nodes, {exp} expansions; '
          f'edges {dict(kinds)}, tight {tight}' + (' (soldier filter)' if a.soldier else ''))
    if a.png:
        pts = [tuple(float(v) for v in w.node_pos[n]) for n in nodes]
        xs, zs = [p[0] for p in pts], [p[2] for p in pts]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        r = max(max(xs) - min(xs), max(zs) - min(zs)) / 2 + 8
        picture(w, cx, cz, r, nodes, a.png if a.png != '-' else os.path.join(out_dir(path), f'rsnvtool-route-{s}-{g}.png'))
    return 0 if st == rsnv.DONE else 1


def cmd_pockets(w, a):
    isl = collections.Counter(int(i) for i in w.node_island)
    main_id = isl.most_common(1)[0][0]
    main = [n for n in range(len(w.node_pos)) if int(w.node_island[n]) == main_id]
    ps = pieces(w, main)
    out = sum(len(p) for p in ps[1:])
    print(f'main network (island {main_id}): {len(main)} nodes; without tight edges {len(ps)} pieces, the largest '
          f'{len(ps[0])}; {out} nodes ({100 * out / len(main):.2f} %) outside it')
    for p in ps[1:1 + a.top]:
        c = [sum(float(w.node_pos[n][i]) for n in p) / len(p) for i in range(3)]
        print(f'  {len(p):5d} nodes around ({c[0]:.1f}, {c[1]:.1f}, {c[2]:.1f})')
    return 0


def end_info(w, p):
    """The area under an edge's end (a node position): index, sector id, pitch, layer, cell and ground."""
    loc = w.locate(p[0], p[1], p[2], max_dist=1.0)
    if loc is None:
        return {'area': None}
    k, (i, j), _d = loc
    a = w.areas[k]
    sec = w.sectors[a.sector]
    return {'area': k, 'sector': f'{int(sec[0]):04x}', 'layer': int(sec[1]), 'pitch': round(a.pitch, 1),
            'cell': [i, j], 'ground': round(w.ground(a, p[0], p[2]), 3)}


def _hood(w, a, x, z):
    """The 3 x 3 cells of area a around (x, z), rows from +z down: '#' walkable, '.' not, ' ' outside the bitmap."""
    i, j, _in = w._cell_of(a, x, z)
    rows = []
    for dj in (1, 0, -1):
        rows.append(''.join(('#' if w.walkable(a, i + di, j + dj) else '.') if 0 <= i + di < a.width
                            and 0 <= j + dj < a.height else ' ' for di in (-1, 0, 1)))
    return '/'.join(rows)


def los_failure(w, pa, pb):
    """None when pa sees pb; otherwise the first failing sample (rsnv.World.los's trace) with what lies there: the
    current area's cells, every nearby area with ground on that spot (its height against the current area's, and the
    allowance), and a cause: 'hole' (no other area has a cell there: a gap in the cells, its length along the line),
    'step' (cells there, all beyond the height allowance), 'lean' (the sample on its 0.8 m area's ground, a neighbour
    cell it leans to blocked), 'cell' (ground within the allowance whose cell test fails), 'climb' (the ground
    changing too fast between samples), 'start' / 'end' (an end off its ground)."""
    tr = {}
    if w.los(pa, pb, tr):
        return None
    out = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in tr.items()}
    rule = tr['rule']
    if rule not in ('cell', 'climb'):
        out['cause'] = rule
        return out
    x, z, cur = tr['x'], tr['z'], tr['area']
    a = w.areas[cur]
    i, j, _in = w._cell_of(a, x, z)
    here = w.ground(a, x, z)
    out.update(pitch=round(a.pitch, 1), sector=f'{int(w.sectors[a.sector][0]):04x}', cell=[i, j],
               own=w.walkable(a, i, j), hood=_hood(w, a, x, z), cur_ground=round(here, 3))
    cands = []
    for k in w._near_los(x, z):
        if k == cur:
            continue
        b = w.areas[k]
        if not w.on_cell(b, x, z):
            continue
        d = w.ground(b, x, z) - here
        cands.append({'area': k, 'pitch': round(b.pitch, 1), 'sector': f'{int(w.sectors[b.sector][0]):04x}',
                      'dy': round(d, 3), 'allowed': round(w._allowed(cur, k), 3), 'cell_ok': w._cell_ok(k, x, z),
                      'hood': _hood(w, b, x, z)})
    cands.sort(key=lambda c: abs(c['dy']))
    out['others'] = cands[:4]
    if rule == 'climb':
        out['cause'] = 'climb'
        return out
    if w.on_cell(a, x, z) and a.pitch_mm_ >= rsnv.COARSE_MM:
        out['cause'] = 'lean'
    elif not cands:
        out['cause'] = 'hole'
    elif all(abs(c['dy']) > c['allowed'] for c in cands):
        out['cause'] = 'step'
        out['step_excess'] = round(min(abs(c['dy']) - c['allowed'] for c in cands), 3)
    else:
        out['cause'] = 'cell'
    if out['cause'] == 'hole':
        # the gap's length along the line: to the next sample on any area's cells within the coarse allowance
        dx, dz = pb[0] - pa[0], pb[2] - pa[2]
        ln = math.hypot(dx, dz)
        k0, n = tr['k'], tr['n']
        gap = None
        for k2 in range(k0 + 1, n + 1):
            t2 = k2 / n
            x2, z2 = pa[0] + dx * t2, pa[2] + dz * t2
            if any(w.on_cell(w.areas[c], x2, z2) and abs(w.ground(w.areas[c], x2, z2) - tr['gy']) <= rsnv.LOS_COARSE_DY
                   for c in w._near_los(x2, z2)):
                gap = ln * (k2 - k0) / n
                break
        out['gap'] = None if gap is None else round(gap, 3)
    return out


class BuildSamples:
    """The build's own collision samples and kept surfaces (navaudit1943's base masks) of a level, both grids."""

    def __init__(self, level):
        import numpy as np
        import navaudit1943 as A
        p = A.level_paths(level)
        self.grids = []
        for which, src, mask in (('0.8 m', 'samples.bin', 'normal'), ('0.2 m', 'samples_02.bin', 'detailed')):
            mp = os.path.join(p['audit'], 'masks', f'{mask}_base.npz')
            sp = os.path.join(p['samples'], src)
            if not (os.path.isfile(mp) and os.path.isfile(sp)):
                continue
            s = A.load_samples(sp)
            m = np.load(mp)
            if int(m['surfaces']) != len(s.y):
                raise SystemExit(f'{mp}: made from another sampling run')
            self.grids.append((which, s, m['keep'], str(m['hpd_sha256'])))

    def at(self, x, z, y, dy=2.5):
        """Per grid, the nearest column's surfaces within dy of y: (height, kept, slope in degrees)."""
        out = {}
        for which, s, keep, _h in self.grids:
            i, j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
            if not (0 <= i < s.nx and 0 <= j < s.nz):
                continue
            col = []
            for q in s.column(i, j):
                if abs(float(s.y[q]) - y) <= dy:
                    ny = max(-1.0, min(1.0, float(s.normal[q][1])))
                    col.append((round(float(s.y[q]), 2), bool(keep[q]), round(math.degrees(math.acos(ny)), 1)))
            out[which] = {'column': [round(s.min_x + i * s.pitch, 2), round(s.min_z + j * s.pitch, 2)],
                          'surfaces': col}
        return out


def tight_pairs(w):
    """Every tight walk edge once: (node a, node b) with a < b."""
    src = edge_source(w)
    return sorted({(min(src[k], int(w.edge_target[k])), max(src[k], int(w.edge_target[k])))
                   for k in range(len(w.edge_target)) if int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT
                   and int(w.edge_kind[k]) == rsnv.EDGE_WALK})


def cmd_tight(w, a, path):
    import json
    bs = BuildSamples(os.path.basename(path).split('.')[0]) if a.samples else None
    rows = []
    causes = collections.Counter()
    for n0, n1 in tight_pairs(w):
        pa, pb = (tuple(float(v) for v in w.node_pos[n]) for n in (n0, n1))
        row = {'nodes': [n0, n1], 'a': [round(v, 2) for v in pa], 'b': [round(v, 2) for v in pb],
               'length': round(math.dist(pa, pb), 3), 'ends': [end_info(w, pa), end_info(w, pb)],
               'ab': los_failure(w, pa, pb), 'ba': los_failure(w, pb, pa)}
        if bs is not None:
            for d in ('ab', 'ba'):
                f = row[d]
                if f and 'x' in f:
                    f['samples'] = bs.at(f['x'], f['z'], f.get('gy', pa[1]))
        causes[(row['ab'] or {}).get('cause'), (row['ba'] or {}).get('cause')] += 1
        rows.append(row)
        e0, e1 = row['ends']
        print(f"{n0}-{n1}: ({pa[0]:.1f}, {pa[1]:.1f}, {pa[2]:.1f}) -> ({pb[0]:.1f}, {pb[1]:.1f}, {pb[2]:.1f}), "
              f"{row['length']:.2f} m; ends area {e0.get('area')} ({e0.get('sector')}, {e0.get('pitch')} m) / "
              f"area {e1.get('area')} ({e1.get('sector')}, {e1.get('pitch')} m)")
        for d in ('ab', 'ba'):
            f = row[d]
            if f is None:
                print(f'    {d}: in sight')
                continue
            extra = ''.join(f', {k} {f[k]}' for k in ('excess', 'step_excess', 'gap') if k in f)
            print(f"    {d}: {f['cause']} at {f.get('along', 0):.2f} m ({f.get('x', 0):.2f}, {f.get('z', 0):.2f}), "
                  f"area {f.get('area')} {f.get('pitch', '')} m, cells {f.get('hood', '')}{extra}")
            for c in f.get('others', [])[:2]:
                print(f"        other area {c['area']} ({c['sector']}, {c['pitch']} m): dy {c['dy']:+.2f} of "
                      f"{c['allowed']:.2f}, cell test {'ok' if c['cell_ok'] else 'fails'}, cells {c['hood']}")
            for g, col in (f.get('samples') or {}).items():
                print(f"        samples {g} at {tuple(col['column'])}: " + ', '.join(
                    f"{y}{' kept' if kp else ''} {sl} deg" for y, kp, sl in col['surfaces']))
    print(f'{path}: {len(rows)} tight pairs; causes (a->b, b->a): '
          + ', '.join(f'{k[0]}/{k[1]} {v}' for k, v in causes.most_common()))
    if a.json:
        meta = {'file': os.path.abspath(path), 'pairs': len(rows)}
        if bs is not None:
            meta['masks_hpd_sha256'] = [h for _w, _s, _k, h in bs.grids]
        with open(a.json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump({'meta': meta, 'pairs': rows}, f, indent=1)
        print(f'wrote {a.json}')
    return 0


def _on_line(line, x, z, tol):
    """Whether (x, z) lies within tol of the segment line ((x, y, z), (x, y, z)) in plan."""
    (ax, _ay, az), (bx, _by, bz) = line
    dx, dz = bx - ax, bz - az
    ll = dx * dx + dz * dz
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / ll))
    return math.hypot(ax + dx * t - x, az + dz * t - z) <= tol


def cmd_cells(w, a, path):
    """Text maps around a point, 0.2 m to a character, +z up: each area with ground within --dy of --y (its walkable
    cells '#', its other cells '.'); with --samples, the build's own samples on both grids ('K' a kept surface within
    0.6 m of --y, 'k' kept further off, 's' a surface within 0.6 m not kept, '.' none); with --line, '*' marks where the
    line runs over a character that is not ground."""
    cx, cz = (float(v) for v in a.at.split(','))
    r, y = a.radius, a.y
    line = (point(a.line[0]), point(a.line[1])) if a.line else None
    n = int(round(r / 0.2))
    xs = [cx + i * 0.2 for i in range(-n, n + 1)]
    zs = [cz + j * 0.2 for j in range(n, -n - 1, -1)]
    cand = set()
    for bx in (-1, 0, 1):
        for bz in (-1, 0, 1):
            cand.update(w._near_los(cx + bx * r, cz + bz * r))
    print(f'around ({cx:.2f}, {cz:.2f}), {r} m, ground within {a.dy} m of {y:.2f}; 0.2 m a character, +z up, x right')

    def show(title, ch):
        print(title)
        for z in zs:
            row = ''
            for x in xs:
                c = ch(x, z)
                if line and c in '. ' and _on_line(line, x, z, 0.1):
                    c = '*'
                if abs(x - cx) < 0.1 and abs(z - cz) < 0.1:
                    c = 'X' if c in '#K' else 'x'
                row += c
            print('    ' + row)
    for k in sorted(cand):
        ar = w.areas[k]
        if not (ar.x0 - ar.pitch <= cx + r and cx - r <= ar.x1 + ar.pitch and ar.z0 - ar.pitch <= cz + r
                and cz - r <= ar.z1 + ar.pitch):
            continue
        g = w.ground(ar, cx, cz)
        if abs(g - y) > a.dy:
            continue

        def ch(x, z, ar=ar):
            i, j, inside = w._cell_of(ar, x, z)
            return ('#' if w.walkable(ar, i, j) else '.') if inside else ' '
        sec = w.sectors[ar.sector]
        show(f'  area {k} (sector {int(sec[0]):04x}, {ar.pitch:.1f} m, ground {g:.2f} here, {ar.min_y:.2f}..'
             f'{ar.max_y - 1.52:.2f})', ch)
    if a.samples:
        bs = BuildSamples(os.path.basename(path).split('.')[0])
        for which, s, keep, _h in bs.grids:
            def ch(x, z, s=s, keep=keep):
                i, j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
                if not (0 <= i < s.nx and 0 <= j < s.nz):
                    return ' '
                best = '.'
                for q in s.column(i, j):
                    d = abs(float(s.y[q]) - y)
                    if keep[q] and d <= 0.6:
                        return 'K'
                    if keep[q] and d <= 2.0:
                        best = 'k'
                    elif d <= 0.6 and best == '.':
                        best = 's'
                return best
            show(f'  samples {which} (kept surfaces from the base mask of build {_h[:12]})', ch)
    return 0


def sample_walk(bs, a, b, grid='0.2 m', reach=1.0):
    """The build's own kept samples along the line a -> b on one grid: column by column (every 0.05 m along the line,
    the nearest column), the kept surface nearest the height followed so far (within reach), or a gap. Returns
    {'grid', 'columns': [(x, z, y or None, slope or None)], 'largest_step', 'step_at', 'gaps', 'end_dy'} or None when
    the grid does not cover the line. Read-only; what a straight walk over the kept surfaces meets."""
    g = [t for t in bs.grids if t[0] == grid]
    if not g:
        return None
    _w, s, keep, _h = g[0]
    dx, dz = b[0] - a[0], b[2] - a[2]
    ln = math.hypot(dx, dz)
    n = max(1, int(math.ceil(ln / 0.05)))
    y, cols, last = a[1], [], None
    largest, at, gaps, gap = 0.0, None, [], 0.0
    for k in range(n + 1):
        t = k / n
        x, z = a[0] + dx * t, a[2] + dz * t
        i, j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            return None
        if (i, j) == last:
            continue
        last = (i, j)
        best = None
        for q in s.column(i, j):
            if keep[q] and abs(float(s.y[q]) - y) <= reach and (best is None or abs(float(s.y[q]) - y) < abs(float(s.y[best]) - y)):
                best = q
        cx, cz = s.min_x + i * s.pitch, s.min_z + j * s.pitch
        if best is None:
            cols.append((round(cx, 2), round(cz, 2), None, None))
            gap += s.pitch
            continue
        if gap:
            gaps.append(round(gap, 2))
            gap = 0.0
        ny = float(s.y[best])
        if cols and abs(ny - y) > largest:
            largest, at = abs(ny - y), (round(cx, 2), round(cz, 2))
        sl = math.degrees(math.acos(max(-1.0, min(1.0, float(s.normal[best][1])))))
        cols.append((round(cx, 2), round(cz, 2), round(ny, 3), round(sl, 1)))
        y = ny
    if gap:
        gaps.append(round(gap, 2))
    return {'grid': grid, 'columns': cols, 'largest_step': round(largest, 3), 'step_at': at, 'gaps': gaps,
            'end_dy': round(y - b[1], 3)}


def cmd_walk(w, a, path):
    """The build's kept samples along a line, on both grids (sample_walk)."""
    bs = BuildSamples(os.path.basename(path).split('.')[0])
    pa, pb = node_of_point(w, a.a), node_of_point(w, a.b)
    print(f'{pa} -> {pb}: line of sight {"in sight" if w.los(pa, pb) else "not in sight"} / '
          f'{"in sight" if w.los(pb, pa) else "not in sight"} (back)')
    for grid in ('0.2 m', '0.8 m'):
        r = sample_walk(bs, pa, pb, grid, a.reach)
        if r is None:
            print(f'  {grid}: no samples there')
            continue
        print(f"  {grid}: largest step {r['largest_step']} m at {r['step_at']}, gaps {r['gaps'] or 'none'}, the end "
              f"{r['end_dy']:+.2f} m off b")
        print('    ' + ' '.join(f'{c[2]:.2f}' if c[2] is not None else '-' for c in r['columns']))
    return 0


def node_of_point(w, text):
    """A point X,Y,Z, or a node number's position."""
    if ',' in text:
        return point(text)
    return tuple(float(v) for v in w.node_pos[int(text)])


def picture(w, cx, cz, radius, route, png, y=None, dy=3.0):
    from PIL import Image, ImageDraw
    x0, x1, z0, z1 = cx - radius, cx + radius, cz - radius, cz + radius
    scale = min(40.0, 1600.0 / (2 * radius))
    W = H = int(2 * radius * scale) + 1
    img = Image.new('RGB', (W, H), (24, 24, 28))
    dr = ImageDraw.Draw(img)

    def px(x, z):
        return ((x - x0) * scale, (z1 - z) * scale)
    for a in w.areas:
        if a.x1 < x0 or a.x0 > x1 or a.z1 < z0 or a.z0 > z1:
            continue
        if y is not None and not (a.min_y - dy <= y <= a.max_y + dy):
            continue                                       # another floor
        h = a.pitch * scale / 2
        for j in range(a.height):
            z = a.z0 + j * a.pitch
            if z < z0 - 1 or z > z1 + 1:
                continue
            for i in range(a.width):
                x = a.x0 + i * a.pitch
                if x0 - 1 <= x <= x1 + 1 and w.walkable(a, i, j):
                    u, v = px(x, z)
                    dr.rectangle([u - h, v - h, u + h, v + h], fill=(46, 78, 52))
    inside = [n for n in range(len(w.node_pos))
              if x0 <= w.node_pos[n][0] <= x1 and z0 <= w.node_pos[n][2] <= z1
              and (y is None or abs(w.node_pos[n][1] - y) <= dy)]
    inside_set = set(inside)
    for n in inside:
        for k in range(int(w.node_first_edge[n]), int(w.node_first_edge[n + 1])):
            m = int(w.edge_target[k])
            if m < n and m in inside_set:
                continue
            tight = int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT
            kind = int(w.edge_kind[k])
            col = (230, 25, 75) if tight else (255, 140, 0) if kind in (1, 2) else (0, 200, 255) if kind == 4 \
                else (110, 110, 110)
            dr.line([px(w.node_pos[n][0], w.node_pos[n][2]), px(w.node_pos[m][0], w.node_pos[m][2])], fill=col,
                    width=2 if tight else 1)
    for n in inside:
        u, v = px(w.node_pos[n][0], w.node_pos[n][2])
        dr.ellipse([u - 2, v - 2, u + 2, v + 2], fill=(200, 200, 200))
    if route:
        dr.line([px(w.node_pos[n][0], w.node_pos[n][2]) for n in route], fill=(255, 215, 0), width=3)
    img.save(png)
    print(f'picture: {png} (cells green; edges grey, tight red, ladders orange, jumps cyan; route yellow)')


def cmd_picture(w, a, path):
    cx, cz = (float(v) for v in a.at.split(','))
    route = None
    if a.route:
        s, g = node_of(w, a.route[0]), node_of(w, a.route[1])
        route = search(w, s, g, a.soldier)[2]
    png = a.png or os.path.join(out_dir(path), f'rsnvtool-{cx:.0f}_{cz:.0f}.png')
    picture(w, cx, cz, a.radius, route, png, a.y, a.dy)
    return 0


def cmd_diff(pa, pb):
    a, b = rsnv.load(pa), rsnv.load(pb)

    def tight(w):
        return sum(1 for k in range(len(w.edge_target)) if int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT)
    rows = [('file size, MB', os.path.getsize(pa) / 1e6, os.path.getsize(pb) / 1e6),
            ('nodes', len(a.node_pos), len(b.node_pos)), ('edges', len(a.edge_target), len(b.edge_target)),
            ('areas', len(a.areas), len(b.areas)), ('tight edges', tight(a), tight(b)),
            ('islands', len(set(a.node_island.tolist())), len(set(b.node_island.tolist()))),
            ('ladder / jump records', len(a.ladders), len(b.ladders)), ('off-mesh records', len(a.offmesh), len(b.offmesh))]
    print(f'{"":24} {"a":>12} {"b":>12}')
    for name, x, y in rows:
        print(f'{name:24} {x:12.2f} {y:12.2f}' if isinstance(x, float) else f'{name:24} {x:12d} {y:12d}'
              + ('' if x == y else f'   {y - x:+d}'))
    print(f"exporter: a {a.meta.get('exporter', '?')}; b {b.meta.get('exporter', '?')}")
    if len(a.node_pos) == len(b.node_pos):
        import numpy as np
        d = np.linalg.norm(a.node_pos - b.node_pos, axis=1)
        ea = set(zip(edge_source(a), a.edge_target.tolist()))
        eb = set(zip(edge_source(b), b.edge_target.tolist()))
        print(f'same node count: {int((d > 1e-4).sum())} nodes moved (largest {d.max():.3f} m); edges only in a '
              f'{len(ea - eb)}, only in b {len(eb - ea)}')
    else:
        print('node counts differ: the graphs are different builds (compare counts, not nodes)')
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog='rsnvtool.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def add(name):
        p = sub.add_parser(name)
        p._negative_number_matcher = re.compile(r'^-\d')       # a point like -240.4,218.4,23.4 is a value
        p.add_argument('file')
        p.add_argument('--class', dest='klass', choices=('jeep', 'tank', 'lcvp', 'plane'))
        return p
    add('info')
    add('validate')
    p = add('locate')
    p.add_argument('x', type=float)
    p.add_argument('y', type=float)
    p.add_argument('z', type=float)
    p.add_argument('--max-dist', type=float, default=3.0)
    p = add('los')
    p.add_argument('a')
    p.add_argument('b')
    p = add('route')
    p.add_argument('start')
    p.add_argument('goal')
    p.add_argument('--soldier', action='store_true')
    p.add_argument('--png', nargs='?', const='-')
    p = add('pockets')
    p.add_argument('--top', type=int, default=10)
    p = add('tight')
    p.add_argument('--samples', action='store_true', help="the build's own samples at each failing sample")
    p.add_argument('--json')
    p = add('cells')
    p.add_argument('--at', required=True)
    p.add_argument('--y', type=float, required=True)
    p.add_argument('--radius', type=float, default=1.6)
    p.add_argument('--dy', type=float, default=1.5, help='areas whose ground at the point is within this of --y')
    p.add_argument('--samples', action='store_true')
    p.add_argument('--line', nargs=2, help='two points X,Y,Z: the line drawn over the maps')
    p = add('walk')
    p.add_argument('a')
    p.add_argument('b')
    p.add_argument('--reach', type=float, default=1.0, help='the kept surface followed lies within this of the last')
    p = add('picture')
    p.add_argument('--at', required=True)
    p.add_argument('--radius', type=float, default=25.0)
    p.add_argument('--route', nargs=2)
    p.add_argument('--soldier', action='store_true')
    p.add_argument('--y', type=float, help='draw only the floors within --dy of this height')
    p.add_argument('--dy', type=float, default=3.0)
    p.add_argument('--png')
    p = sub.add_parser('diff')
    p.add_argument('a')
    p.add_argument('b')
    a = ap.parse_args(argv[1:])
    if a.cmd == 'diff':
        return cmd_diff(resolve(a.a), resolve(a.b))
    path = resolve(a.file, a.klass)
    w = rsnv.load(path)
    if a.cmd == 'info':
        return cmd_info(w, path)
    if a.cmd == 'validate':
        return cmd_validate(w, path)
    if a.cmd == 'locate':
        return cmd_locate(w, a)
    if a.cmd == 'los':
        pa, pb = point(a.a), point(a.b)
        ok = w.los(pa, pb)
        print(f'{"in sight" if ok else "not in sight"}: {pa} -> {pb}')
        return 0 if ok else 1
    if a.cmd == 'route':
        return cmd_route(w, a, path)
    if a.cmd == 'pockets':
        return cmd_pockets(w, a)
    if a.cmd == 'picture':
        return cmd_picture(w, a, path)
    if a.cmd == 'tight':
        return cmd_tight(w, a, path)
    if a.cmd == 'cells':
        return cmd_cells(w, a, path)
    if a.cmd == 'walk':
        return cmd_walk(w, a, path)
    return 2


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('rsnvtool', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
