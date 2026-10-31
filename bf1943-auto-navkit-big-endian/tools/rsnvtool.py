r"""RSNV tool: look inside a navigation export (core/bfbc2core/rsnv.py, written by navexport1943.py and
navexport_vehicles1943.py), ask it questions, draw it, and compare two of them.

  rsnvtool.py info <file|level> [--class C]                     header, sections, counts, edge kinds, tight edges, islands
  rsnvtool.py validate <file|level> [--class C]                 consistency (rsnv.World.validate); exit 1 on a problem
  rsnvtool.py locate <file|level> X Y Z [--max-dist M]          the walkable cell and nearest node under a point
  rsnvtool.py los <file|level> X,Y,Z X,Y,Z                      line of sight between two points (the runtime's rule)
  rsnvtool.py route <file|level> FROM TO [--soldier] [--png F]  a search between nodes or points (X,Y,Z), as rsnav finds it
  rsnvtool.py pockets <file|level> [--top N]                    the main network's pieces when tight edges are left out
                                                                (weakly connected, disjoint), and the one-way share
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
if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
from bfbc2core import game1943, rsnv  # noqa: E402

SOLDIER = {'water_scale': (256, 384, 768), 'tight_extra': 800, 'kind_mask': (1 << 7) - 1 & ~(1 << rsnv.EDGE_BOARD)
           & ~(1 << rsnv.EDGE_ALIGHT)}          # rsnav's rsn_filter_soldier, without the dynamic state


def resolve(spec, klass=None):
    """An .rsnv path, or a level name's export (with --class, its vehicle world)."""
    if os.path.isfile(spec):
        return spec
    path = os.path.join(game1943.level_work(spec), 'navkit', f'{spec}.{klass}.rsnv' if klass else f'{spec}.rsnv')
    if not os.path.isfile(path):
        raise FileNotFoundError(f'{path}: no such export (navexport1943.py {spec}'
                                f'{"; navexport_vehicles1943.py " + spec if klass else ""})')
    return path


def out_dir(path):
    return os.path.dirname(os.path.abspath(path))


def point(text):
    values = [float(x) for x in text.split(',')]
    if len(values) != 3:
        raise ValueError(f'{text!r}: a point is X,Y,Z')
    return tuple(values)


def node_of(w, text):
    """A node number, or the node of the walkable cell nearest a point X,Y,Z."""
    if ',' not in text:
        node = int(text)
        if not 0 <= node < len(w.node_pos):
            raise ValueError(f'node {node}: the file has nodes 0..{len(w.node_pos) - 1}')
        return node
    x, y, z = point(text)
    loc = w.locate(x, y, z, max_dist=6.0)
    if loc is None:
        raise ValueError(f'{text}: no walkable cell within 6 m')
    node = w.nearest_node(loc[0], x, z)
    if node is None:
        raise ValueError(f'{text}: area {loc[0]} has no node')
    return node


def edge_source(w):
    """The source node of every edge (the edges are stored grouped by source)."""
    sources = [0] * len(w.edge_target)
    for n in range(len(w.node_pos)):
        for k in range(int(w.node_first_edge[n]), int(w.node_first_edge[n + 1])):
            sources[k] = n
    return sources


def _kept_edges(w, nodes):
    """(a, b) of every edge between two of `nodes` that is not tight."""
    inside = set(nodes)
    for a in inside:
        for k in range(int(w.node_first_edge[a]), int(w.node_first_edge[a + 1])):
            b = int(w.edge_target[k])
            if b in inside and not int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT:
                yield a, b


def pieces(w, nodes):
    """The pieces of `nodes` when tight edges are left out: weakly connected components (an edge joins its ends
    whichever way it runs), disjoint, largest first, each sorted. 2026-10-30 second review: the pieces followed
    outgoing edges only and could take nodes of an earlier piece again (1 -> 0 alone gave [[0, 1], [0]]), so pieces
    overlapped and the share outside the largest could be counted twice."""
    parent = {n: n for n in nodes}                   # union-find

    def root(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for a, b in _kept_edges(w, nodes):
        root_a, root_b = root(a), root(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)
    groups = collections.defaultdict(list)
    for n in parent:
        groups[root(n)].append(n)
    return sorted((sorted(group) for group in groups.values()), key=lambda group: (-len(group), group[0]))


def strong_core(w, nodes):
    """The largest strongly connected component of `nodes` without tight edges (every node of it reaches every other
    one and back), sorted: the nodes of a piece outside it can be left but not reached again, or reached but not left
    (one-way edges: a drop, a one-way link)."""
    out_edges, in_edges = collections.defaultdict(list), collections.defaultdict(list)
    for a, b in _kept_edges(w, nodes):
        out_edges[a].append(b)
        in_edges[b].append(a)
    order, seen = [], set()
    for seed in nodes:                               # Kosaraju, iteratively: finishing order on the edges ...
        if seed in seen:
            continue
        seen.add(seed)
        stack = [(seed, iter(out_edges[seed]))]
        while stack:
            node, targets = stack[-1]
            for target in targets:
                if target not in seen:
                    seen.add(target)
                    stack.append((target, iter(out_edges[target])))
                    break
            else:
                stack.pop()
                order.append(node)
    best, done = [], set()
    for seed in reversed(order):                     # ... then the components on the reversed edges
        if seed in done:
            continue
        component, todo = [seed], [seed]
        done.add(seed)
        while todo:
            node = todo.pop()
            for source in in_edges[node]:
                if source not in done:
                    done.add(source)
                    component.append(source)
                    todo.append(source)
        if len(component) > len(best) or (len(component) == len(best) and min(component) < min(best)):
            best = component
    return sorted(best)


def _tight_count(w):
    return sum(1 for k in range(len(w.edge_target)) if int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT)


def cmd_info(w, path):
    kinds = collections.Counter(int(k) for k in w.edge_kind)
    walk = kinds.get(rsnv.EDGE_WALK, 0)
    tight = _tight_count(w)
    conditional = sum(1 for k in range(len(w.edge_target)) if int(w.edge_flags[k]) & rsnv.EDGEF_COND)
    pitches = collections.Counter(round(a.pitch, 1) for a in w.areas)
    kind_counts = ', '.join(f'{rsnv.EDGE_NAMES[k] if k < len(rsnv.EDGE_NAMES) else k} {count}'
                            for k, count in sorted(kinds.items()))
    print(f'{path}')
    print(f'  {os.path.getsize(path) / 1e6:.2f} MB, {"big" if w.big_endian else "little"}-endian, level id '
          f'{w.level_id:#010x}, sea level {w.sea_level:.2f} m; agent radius {w.radius} m, height {w.agent_height} m, '
          f'step {w.step} m, water {w.water_depth} m')
    print('  sections: ' + ', '.join(f'{tag} {count}' for tag, (_offset, count, _stride) in w.sections.items()))
    print(f'  {len(w.node_pos)} nodes, {len(w.edge_target)} edges ({kind_counts}); tight {tight} of {walk} walk edges '
          f'({100 * tight / max(walk, 1):.2f} %); conditional {conditional}')
    print(f'  {len(w.areas)} areas (' + ', '.join(f'{p} m: {count}' for p, count in sorted(pitches.items())) + '), '
          f'{len(getattr(w, "ports", []))} portals, {len(w.ladders)} ladder / jump records, {len(w.offmesh)} '
          f'off-mesh records, {len(w.objects)} objects')
    island_sizes = collections.Counter(int(i) for i in w.node_island)
    largest = island_sizes.most_common(1)[0] if island_sizes else (None, 0)
    print(f'  {len(island_sizes)} islands; the largest (island {largest[0]}) has {largest[1]} nodes')
    meta = w.meta or {}
    if meta:
        sources = ', '.join(f"{name} {source.get('sha256', '')[:12]}"
                            for name, source in (meta.get('sources') or {}).items())
        print(f"  exporter {meta.get('exporter', '?')}; sources: " + sources)
    return 0


def cmd_validate(w, path):
    problems = w.validate()
    for problem in problems:
        print(f'  {problem}')
    print(f'{path}: {len(problems)} problem(s)')
    return 1 if problems else 0


def cmd_locate(w, args):
    loc = w.locate(args.x, args.y, args.z, max_dist=args.max_dist)
    if loc is None:
        print(f'no walkable cell within {args.max_dist} m of ({args.x}, {args.y}, {args.z})')
        return 1
    area_index, (i, j), dist = loc
    area = w.areas[area_index]
    node = w.nearest_node(area_index, args.x, args.z)
    if node is not None:
        node_at = tuple(round(float(v), 2) for v in w.node_pos[node])
        print(f'area {area_index} (pitch {area.pitch} m, sector {area.sector}), cell ({i}, {j}) at {dist:.2f} m, '
              f'ground {w.ground(area, args.x, args.z):.2f} m; nearest node {node} at {node_at}')
    else:
        print(f'area {area_index}, cell ({i}, {j}) at {dist:.2f} m; the area has no node')
    return 0


def search(w, start, goal, soldier):
    return rsnv.search(w, start, goal, **(SOLDIER if soldier else {}))


def cmd_route(w, args, path):
    start, goal = node_of(w, args.start), node_of(w, args.goal)
    status, cost, nodes, expansions = search(w, start, goal, args.soldier)
    length = sum(math.dist(w.node_pos[p], w.node_pos[q]) for p, q in zip(nodes, nodes[1:]))
    kinds = collections.Counter()
    tight = 0
    for p, q in zip(nodes, nodes[1:]):                 # the edge p -> q taken: its kind, whether it is tight
        for k in range(int(w.node_first_edge[p]), int(w.node_first_edge[p + 1])):
            if int(w.edge_target[k]) == q:
                kinds[rsnv.EDGE_NAMES[int(w.edge_kind[k])]] += 1
                tight += bool(int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT)
                break
    print(f'{start} -> {goal}: {status}, cost {cost / 16:.1f} m over {length:.1f} m, {len(nodes)} nodes, {expansions} '
          f'expansions; edges {dict(kinds)}, tight {tight}' + (' (soldier filter)' if args.soldier else ''))
    if args.png:
        points = [tuple(float(v) for v in w.node_pos[n]) for n in nodes]
        xs, zs = [p[0] for p in points], [p[2] for p in points]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        radius = max(max(xs) - min(xs), max(zs) - min(zs)) / 2 + 8
        if args.png != '-':
            png = args.png
        else:
            png = os.path.join(out_dir(path), f'rsnvtool-route-{start}-{goal}.png')
        picture(w, cx, cz, radius, nodes, png)
    return 0 if status == rsnv.DONE else 1


def cmd_pockets(w, args):
    island_sizes = collections.Counter(int(i) for i in w.node_island)
    main_id = island_sizes.most_common(1)[0][0]
    main = [n for n in range(len(w.node_pos)) if int(w.node_island[n]) == main_id]
    parts = pieces(w, main)                          # disjoint: the shares below add up to the main network
    outside = sum(len(part) for part in parts[1:])
    print(f'main network (island {main_id}): {len(main)} nodes; without tight edges {len(parts)} pieces, the largest '
          f'{len(parts[0])}; {outside} nodes ({100 * outside / len(main):.2f} %) outside it')
    core = strong_core(w, parts[0])
    one_way = len(parts[0]) - len(core)
    print(f'  of the largest piece, {one_way} nodes ({100 * one_way / len(main):.2f} %) are outside its strongly '
          f'connected core (reached but not left, or left but not reached again, by one-way edges)')
    for part in parts[1:1 + args.top]:
        centre = [sum(float(w.node_pos[n][i]) for n in part) / len(part) for i in range(3)]
        print(f'  {len(part):5d} nodes around ({centre[0]:.1f}, {centre[1]:.1f}, {centre[2]:.1f})')
    return 0


def end_info(w, p):
    """The area under an edge's end (a node position): index, sector id, pitch, layer, cell and ground."""
    loc = w.locate(p[0], p[1], p[2], max_dist=1.0)
    if loc is None:
        return {'area': None}
    area_index, (i, j), _dist = loc
    area = w.areas[area_index]
    sector = w.sectors[area.sector]
    return {'area': area_index, 'sector': f'{int(sector[0]):04x}', 'layer': int(sector[1]),
            'pitch': round(area.pitch, 1), 'cell': [i, j], 'ground': round(w.ground(area, p[0], p[2]), 3)}


def _hood(w, area, x, z):
    """The 3 x 3 cells of the area around (x, z), rows from +z down: '#' walkable, '.' not, ' ' outside the bitmap."""
    i, j, _in = w._cell_of(area, x, z)

    def mark(a, b):
        if not (0 <= a < area.width and 0 <= b < area.height):
            return ' '
        return '#' if w.walkable(area, a, b) else '.'
    rows = []
    for dj in (1, 0, -1):
        rows.append(''.join(mark(i + di, j + dj) for di in (-1, 0, 1)))
    return '/'.join(rows)


def los_failure(w, pa, pb):
    """None when pa sees pb; otherwise the first failing sample (rsnv.World.los's trace) with what lies there: the
    current area's cells, every nearby area with ground on that spot (its height against the current area's, and the
    allowance), and a cause: 'hole' (no other area has a cell there: a gap in the cells, its length along the line),
    'step' (cells there, all beyond the height allowance), 'lean' (the sample on its 0.8 m area's ground, a neighbour
    cell it leans to blocked), 'cell' (ground within the allowance whose cell test fails), 'climb' (the ground
    changing too fast between samples), 'start' / 'end' (an end off its ground)."""
    trace = {}
    if w.los(pa, pb, trace):
        return None
    out = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in trace.items()}
    rule = trace['rule']
    if rule not in ('cell', 'climb'):
        out['cause'] = rule
        return out
    x, z, current = trace['x'], trace['z'], trace['area']
    area = w.areas[current]
    i, j, _in = w._cell_of(area, x, z)
    here = w.ground(area, x, z)
    out.update(pitch=round(area.pitch, 1), sector=f'{int(w.sectors[area.sector][0]):04x}', cell=[i, j],
               own=w.walkable(area, i, j), hood=_hood(w, area, x, z), cur_ground=round(here, 3))
    others = []
    for k in w._near_los(x, z):
        if k == current:
            continue
        other = w.areas[k]
        if not w.on_cell(other, x, z):
            continue
        dy = w.ground(other, x, z) - here
        others.append({'area': k, 'pitch': round(other.pitch, 1), 'sector': f'{int(w.sectors[other.sector][0]):04x}',
                       'dy': round(dy, 3), 'allowed': round(w._allowed(current, k), 3), 'cell_ok': w._cell_ok(k, x, z),
                       'hood': _hood(w, other, x, z)})
    others.sort(key=lambda c: abs(c['dy']))
    out['others'] = others[:4]
    if rule == 'climb':
        out['cause'] = 'climb'
        return out
    if w.on_cell(area, x, z) and area.pitch_mm_ >= rsnv.COARSE_MM:
        out['cause'] = 'lean'
    elif not others:
        out['cause'] = 'hole'
    elif all(abs(c['dy']) > c['allowed'] for c in others):
        out['cause'] = 'step'
        out['step_excess'] = round(min(abs(c['dy']) - c['allowed'] for c in others), 3)
    else:
        out['cause'] = 'cell'
    if out['cause'] == 'hole':
        # the gap's length along the line: to the next sample on any area's cells within the coarse allowance
        dx, dz = pb[0] - pa[0], pb[2] - pa[2]
        length = math.hypot(dx, dz)
        failed_k, n_samples = trace['k'], trace['n']

        def on_ground(x2, z2):
            for c in w._near_los(x2, z2):
                near = w.areas[c]
                if w.on_cell(near, x2, z2) and abs(w.ground(near, x2, z2) - trace['gy']) <= rsnv.LOS_COARSE_DY:
                    return True
            return False
        gap = None
        for k in range(failed_k + 1, n_samples + 1):
            t = k / n_samples
            if on_ground(pa[0] + dx * t, pa[2] + dz * t):
                gap = length * (k - failed_k) / n_samples
                break
        out['gap'] = None if gap is None else round(gap, 3)
    return out


class BuildSamples:
    """The build's own collision samples and kept surfaces (navaudit1943's base masks) of a level, both grids."""

    def __init__(self, level):
        import numpy as np
        import navaudit1943
        paths = navaudit1943.level_paths(level)
        self.grids = []                              # (grid name, samples, kept mask, the mask's build hpd_sha256)
        for which, src, mask_name in (('0.8 m', 'samples.bin', 'normal'), ('0.2 m', 'samples_02.bin', 'detailed')):
            mask_path = os.path.join(paths['audit'], 'masks', f'{mask_name}_base.npz')
            samples_path = os.path.join(paths['samples'], src)
            if not (os.path.isfile(mask_path) and os.path.isfile(samples_path)):
                continue
            samples = navaudit1943.load_samples(samples_path)
            mask = np.load(mask_path)
            if int(mask['surfaces']) != len(samples.y):
                raise SystemExit(f'{mask_path}: made from another sampling run')
            self.grids.append((which, samples, mask['keep'], str(mask['hpd_sha256'])))

    def at(self, x, z, y, dy=2.5):
        """Per grid, the nearest column's surfaces within dy of y: (height, kept, slope in degrees)."""
        out = {}
        for which, samples, keep, _sha in self.grids:
            i = int(round((x - samples.min_x) / samples.pitch))
            j = int(round((z - samples.min_z) / samples.pitch))
            if not (0 <= i < samples.nx and 0 <= j < samples.nz):
                continue
            column = []
            for q in samples.column(i, j):
                if abs(float(samples.y[q]) - y) <= dy:
                    normal_y = max(-1.0, min(1.0, float(samples.normal[q][1])))
                    slope = round(math.degrees(math.acos(normal_y)), 1)
                    column.append((round(float(samples.y[q]), 2), bool(keep[q]), slope))
            out[which] = {'column': [round(samples.min_x + i * samples.pitch, 2),
                                     round(samples.min_z + j * samples.pitch, 2)],
                          'surfaces': column}
        return out


def tight_pairs(w):
    """Every tight walk edge once: (node a, node b) with a < b."""
    sources = edge_source(w)
    pairs = set()
    for k in range(len(w.edge_target)):
        if int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT and int(w.edge_kind[k]) == rsnv.EDGE_WALK:
            a, b = sources[k], int(w.edge_target[k])
            pairs.add((min(a, b), max(a, b)))
    return sorted(pairs)


def cmd_tight(w, args, path):
    import json
    build_samples = BuildSamples(os.path.basename(path).split('.')[0]) if args.samples else None
    rows = []
    causes = collections.Counter()
    for n0, n1 in tight_pairs(w):
        pa, pb = (tuple(float(v) for v in w.node_pos[n]) for n in (n0, n1))
        row = {'nodes': [n0, n1], 'a': [round(v, 2) for v in pa], 'b': [round(v, 2) for v in pb],
               'length': round(math.dist(pa, pb), 3), 'ends': [end_info(w, pa), end_info(w, pb)],
               'ab': los_failure(w, pa, pb), 'ba': los_failure(w, pb, pa)}
        if build_samples is not None:
            for way in ('ab', 'ba'):
                failure = row[way]
                if failure and 'x' in failure:
                    failure['samples'] = build_samples.at(failure['x'], failure['z'], failure.get('gy', pa[1]))
        causes[(row['ab'] or {}).get('cause'), (row['ba'] or {}).get('cause')] += 1
        rows.append(row)
        end_a, end_b = row['ends']
        print(f"{n0}-{n1}: ({pa[0]:.1f}, {pa[1]:.1f}, {pa[2]:.1f}) -> ({pb[0]:.1f}, {pb[1]:.1f}, {pb[2]:.1f}), "
              f"{row['length']:.2f} m; ends area {end_a.get('area')} ({end_a.get('sector')}, {end_a.get('pitch')} m) / "
              f"area {end_b.get('area')} ({end_b.get('sector')}, {end_b.get('pitch')} m)")
        for way in ('ab', 'ba'):
            failure = row[way]
            if failure is None:
                print(f'    {way}: in sight')
                continue
            extra = ''.join(f', {k} {failure[k]}' for k in ('excess', 'step_excess', 'gap') if k in failure)
            print(f"    {way}: {failure['cause']} at {failure.get('along', 0):.2f} m ({failure.get('x', 0):.2f}, "
                  f"{failure.get('z', 0):.2f}), area {failure.get('area')} {failure.get('pitch', '')} m, cells "
                  f"{failure.get('hood', '')}{extra}")
            for other in failure.get('others', [])[:2]:
                print(f"        other area {other['area']} ({other['sector']}, {other['pitch']} m): dy "
                      f"{other['dy']:+.2f} of {other['allowed']:.2f}, cell test "
                      f"{'ok' if other['cell_ok'] else 'fails'}, cells {other['hood']}")
            for grid, column in (failure.get('samples') or {}).items():
                surfaces = ', '.join(f"{y}{' kept' if kept else ''} {slope} deg"
                                     for y, kept, slope in column['surfaces'])
                print(f"        samples {grid} at {tuple(column['column'])}: " + surfaces)
    print(f'{path}: {len(rows)} tight pairs; causes (a->b, b->a): '
          + ', '.join(f'{k[0]}/{k[1]} {v}' for k, v in causes.most_common()))
    if args.json:
        meta = {'file': os.path.abspath(path), 'pairs': len(rows)}
        if build_samples is not None:
            meta['masks_hpd_sha256'] = [sha for _which, _samples, _keep, sha in build_samples.grids]
        with open(args.json, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump({'meta': meta, 'pairs': rows}, fh, indent=1)
        print(f'wrote {args.json}')
    return 0


def _on_line(line, x, z, tol):
    """Whether (x, z) lies within tol of the segment line ((x, y, z), (x, y, z)) in plan."""
    (ax, _ay, az), (bx, _by, bz) = line
    dx, dz = bx - ax, bz - az
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / length2))
    return math.hypot(ax + dx * t - x, az + dz * t - z) <= tol


def cmd_cells(w, args, path):
    """Text maps around a point, 0.2 m to a character, +z up: each area with ground within --dy of --y (its walkable
    cells '#', its other cells '.'); with --samples, the build's own samples on both grids ('K' a kept surface within
    0.6 m of --y, 'k' kept further off, 's' a surface within 0.6 m not kept, '.' none); with --line, '*' marks where the
    line runs over a character that is not ground."""
    cx, cz = (float(v) for v in args.at.split(','))
    radius, y = args.radius, args.y
    line = (point(args.line[0]), point(args.line[1])) if args.line else None
    n = int(round(radius / 0.2))
    xs = [cx + i * 0.2 for i in range(-n, n + 1)]
    zs = [cz + j * 0.2 for j in range(n, -n - 1, -1)]
    near_areas = set()
    for bx in (-1, 0, 1):
        for bz in (-1, 0, 1):
            near_areas.update(w._near_los(cx + bx * radius, cz + bz * radius))
    print(f'around ({cx:.2f}, {cz:.2f}), {radius} m, ground within {args.dy} m of {y:.2f}; 0.2 m a character, +z up, '
          f'x right')

    def show(title, char_at):
        print(title)
        for z in zs:
            row = ''
            for x in xs:
                char = char_at(x, z)
                if line and char in '. ' and _on_line(line, x, z, 0.1):
                    char = '*'
                if abs(x - cx) < 0.1 and abs(z - cz) < 0.1:
                    char = 'X' if char in '#K' else 'x'
                row += char
            print('    ' + row)
    for k in sorted(near_areas):
        area = w.areas[k]
        overlaps = (area.x0 - area.pitch <= cx + radius and cx - radius <= area.x1 + area.pitch
                    and area.z0 - area.pitch <= cz + radius and cz - radius <= area.z1 + area.pitch)
        if not overlaps:
            continue
        ground = w.ground(area, cx, cz)
        if abs(ground - y) > args.dy:
            continue

        def area_char(x, z, area=area):
            i, j, inside = w._cell_of(area, x, z)
            if not inside:
                return ' '
            return '#' if w.walkable(area, i, j) else '.'
        sector = w.sectors[area.sector]
        show(f'  area {k} (sector {int(sector[0]):04x}, {area.pitch:.1f} m, ground {ground:.2f} here, '
             f'{area.min_y:.2f}..{area.max_y - 1.52:.2f})', area_char)
    if args.samples:
        build_samples = BuildSamples(os.path.basename(path).split('.')[0])
        for which, samples, keep, sha in build_samples.grids:
            def sample_char(x, z, samples=samples, keep=keep):
                i = int(round((x - samples.min_x) / samples.pitch))
                j = int(round((z - samples.min_z) / samples.pitch))
                if not (0 <= i < samples.nx and 0 <= j < samples.nz):
                    return ' '
                best = '.'
                for q in samples.column(i, j):
                    dist = abs(float(samples.y[q]) - y)
                    if keep[q] and dist <= 0.6:
                        return 'K'
                    if keep[q] and dist <= 2.0:
                        best = 'k'
                    elif dist <= 0.6 and best == '.':
                        best = 's'
                return best
            show(f'  samples {which} (kept surfaces from the base mask of build {sha[:12]})', sample_char)
    return 0


def sample_walk(bs, a, b, grid='0.2 m', reach=1.0):
    """The build's own kept samples along the line a -> b on one grid: column by column (every 0.05 m along the line,
    the nearest column), the kept surface nearest the height followed so far (within reach), or a gap. Returns
    {'grid', 'columns': [(x, z, y or None, slope or None)], 'largest_step', 'step_at', 'gaps', 'end_dy'} or None when
    the grid does not cover the line. Read-only; what a straight walk over the kept surfaces meets."""
    found = [entry for entry in bs.grids if entry[0] == grid]
    if not found:
        return None
    _which, samples, keep, _sha = found[0]
    dx, dz = b[0] - a[0], b[2] - a[2]
    length = math.hypot(dx, dz)
    n = max(1, int(math.ceil(length / 0.05)))
    y, columns, last_column = a[1], [], None
    largest, step_at, gaps, gap = 0.0, None, [], 0.0
    for k in range(n + 1):
        t = k / n
        x, z = a[0] + dx * t, a[2] + dz * t
        i = int(round((x - samples.min_x) / samples.pitch))
        j = int(round((z - samples.min_z) / samples.pitch))
        if not (0 <= i < samples.nx and 0 <= j < samples.nz):
            return None
        if (i, j) == last_column:
            continue
        last_column = (i, j)
        nearest = None                               # the kept surface nearest y within reach
        for q in samples.column(i, j):
            off = abs(float(samples.y[q]) - y)
            if keep[q] and off <= reach and (nearest is None or off < abs(float(samples.y[nearest]) - y)):
                nearest = q
        col_x, col_z = samples.min_x + i * samples.pitch, samples.min_z + j * samples.pitch
        if nearest is None:
            columns.append((round(col_x, 2), round(col_z, 2), None, None))
            gap += samples.pitch
            continue
        if gap:
            gaps.append(round(gap, 2))
            gap = 0.0
        surface_y = float(samples.y[nearest])
        if columns and abs(surface_y - y) > largest:
            largest, step_at = abs(surface_y - y), (round(col_x, 2), round(col_z, 2))
        slope = math.degrees(math.acos(max(-1.0, min(1.0, float(samples.normal[nearest][1])))))
        columns.append((round(col_x, 2), round(col_z, 2), round(surface_y, 3), round(slope, 1)))
        y = surface_y
    if gap:
        gaps.append(round(gap, 2))
    return {'grid': grid, 'columns': columns, 'largest_step': round(largest, 3), 'step_at': step_at, 'gaps': gaps,
            'end_dy': round(y - b[1], 3)}


def cmd_walk(w, args, path):
    """The build's kept samples along a line, on both grids (sample_walk)."""
    build_samples = BuildSamples(os.path.basename(path).split('.')[0])
    pa, pb = node_of_point(w, args.a), node_of_point(w, args.b)
    print(f'{pa} -> {pb}: line of sight {"in sight" if w.los(pa, pb) else "not in sight"} / '
          f'{"in sight" if w.los(pb, pa) else "not in sight"} (back)')
    for grid in ('0.2 m', '0.8 m'):
        walk = sample_walk(build_samples, pa, pb, grid, args.reach)
        if walk is None:
            print(f'  {grid}: no samples there')
            continue
        print(f"  {grid}: largest step {walk['largest_step']} m at {walk['step_at']}, gaps {walk['gaps'] or 'none'}, "
              f"the end {walk['end_dy']:+.2f} m off b")
        print('    ' + ' '.join(f'{c[2]:.2f}' if c[2] is not None else '-' for c in walk['columns']))
    return 0


def node_of_point(w, text):
    """A point X,Y,Z, or a node number's position."""
    if ',' in text:
        return point(text)
    return tuple(float(v) for v in w.node_pos[int(text)])


def _edge_colour(tight, kind):
    if tight:
        return (230, 25, 75)
    if kind in (1, 2):                               # ladders
        return (255, 140, 0)
    if kind == 4:                                    # jumps
        return (0, 200, 255)
    return (110, 110, 110)


def picture(w, cx, cz, radius, route, png, y=None, dy=3.0):
    from PIL import Image, ImageDraw
    x0, x1, z0, z1 = cx - radius, cx + radius, cz - radius, cz + radius
    scale = min(40.0, 1600.0 / (2 * radius))         # pixels per metre
    size = int(2 * radius * scale) + 1
    img = Image.new('RGB', (size, size), (24, 24, 28))
    draw = ImageDraw.Draw(img)

    def to_pixel(x, z):
        return ((x - x0) * scale, (z1 - z) * scale)

    def node_pixel(n):
        return to_pixel(w.node_pos[n][0], w.node_pos[n][2])
    for area in w.areas:
        if area.x1 < x0 or area.x0 > x1 or area.z1 < z0 or area.z0 > z1:
            continue
        if y is not None and not (area.min_y - dy <= y <= area.max_y + dy):
            continue                                       # another floor
        half = area.pitch * scale / 2
        for j in range(area.height):
            z = area.z0 + j * area.pitch
            if z < z0 - 1 or z > z1 + 1:
                continue
            for i in range(area.width):
                x = area.x0 + i * area.pitch
                if x0 - 1 <= x <= x1 + 1 and w.walkable(area, i, j):
                    u, v = to_pixel(x, z)
                    draw.rectangle([u - half, v - half, u + half, v + half], fill=(46, 78, 52))
    inside = [n for n in range(len(w.node_pos))
              if x0 <= w.node_pos[n][0] <= x1 and z0 <= w.node_pos[n][2] <= z1
              and (y is None or abs(w.node_pos[n][1] - y) <= dy)]
    inside_set = set(inside)
    for n in inside:
        for k in range(int(w.node_first_edge[n]), int(w.node_first_edge[n + 1])):
            target = int(w.edge_target[k])
            if target < n and target in inside_set:
                continue                                   # drawn from the other end
            tight = int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT
            draw.line([node_pixel(n), node_pixel(target)], fill=_edge_colour(tight, int(w.edge_kind[k])),
                      width=2 if tight else 1)
    for n in inside:
        u, v = node_pixel(n)
        draw.ellipse([u - 2, v - 2, u + 2, v + 2], fill=(200, 200, 200))
    if route:
        draw.line([node_pixel(n) for n in route], fill=(255, 215, 0), width=3)
    img.save(png)
    print(f'picture: {png} (cells green; edges grey, tight red, ladders orange, jumps cyan; route yellow)')


def cmd_picture(w, args, path):
    cx, cz = (float(v) for v in args.at.split(','))
    route = None
    if args.route:
        start, goal = node_of(w, args.route[0]), node_of(w, args.route[1])
        route = search(w, start, goal, args.soldier)[2]
    png = args.png or os.path.join(out_dir(path), f'rsnvtool-{cx:.0f}_{cz:.0f}.png')
    picture(w, cx, cz, args.radius, route, png, args.y, args.dy)
    return 0


def cmd_diff(path_a, path_b):
    a, b = rsnv.load(path_a), rsnv.load(path_b)
    rows = [('file size, MB', os.path.getsize(path_a) / 1e6, os.path.getsize(path_b) / 1e6),
            ('nodes', len(a.node_pos), len(b.node_pos)),
            ('edges', len(a.edge_target), len(b.edge_target)),
            ('areas', len(a.areas), len(b.areas)),
            ('tight edges', _tight_count(a), _tight_count(b)),
            ('islands', len(set(a.node_island.tolist())), len(set(b.node_island.tolist()))),
            ('ladder / jump records', len(a.ladders), len(b.ladders)),
            ('off-mesh records', len(a.offmesh), len(b.offmesh))]
    print(f'{"":24} {"a":>12} {"b":>12}')
    for name, value_a, value_b in rows:
        if isinstance(value_a, float):
            print(f'{name:24} {value_a:12.2f} {value_b:12.2f}')
        else:
            change = '' if value_a == value_b else f'   {value_b - value_a:+d}'
            print(f'{name:24} {value_a:12d} {value_b:12d}' + change)
    print(f"exporter: a {a.meta.get('exporter', '?')}; b {b.meta.get('exporter', '?')}")
    if len(a.node_pos) == len(b.node_pos):
        import numpy as np
        moved = np.linalg.norm(a.node_pos - b.node_pos, axis=1)
        edges_a = set(zip(edge_source(a), a.edge_target.tolist()))
        edges_b = set(zip(edge_source(b), b.edge_target.tolist()))
        print(f'same node count: {int((moved > 1e-4).sum())} nodes moved (largest {moved.max():.3f} m); '
              f'edges only in a {len(edges_a - edges_b)}, only in b {len(edges_b - edges_a)}')
    else:
        print('node counts differ: the graphs are different builds (compare counts, not nodes)')
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog='rsnvtool.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)

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
    args = parser.parse_args(argv[1:])
    if args.cmd == 'diff':
        return cmd_diff(resolve(args.a), resolve(args.b))
    path = resolve(args.file, args.klass)
    w = rsnv.load(path)
    if args.cmd == 'info':
        return cmd_info(w, path)
    if args.cmd == 'validate':
        return cmd_validate(w, path)
    if args.cmd == 'locate':
        return cmd_locate(w, args)
    if args.cmd == 'los':
        pa, pb = point(args.a), point(args.b)
        in_sight = w.los(pa, pb)
        print(f'{"in sight" if in_sight else "not in sight"}: {pa} -> {pb}')
        return 0 if in_sight else 1
    if args.cmd == 'route':
        return cmd_route(w, args, path)
    if args.cmd == 'pockets':
        return cmd_pockets(w, args)
    if args.cmd == 'picture':
        return cmd_picture(w, args, path)
    if args.cmd == 'tight':
        return cmd_tight(w, args, path)
    if args.cmd == 'cells':
        return cmd_cells(w, args, path)
    if args.cmd == 'walk':
        return cmd_walk(w, args, path)
    return 2


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('rsnvtool', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
