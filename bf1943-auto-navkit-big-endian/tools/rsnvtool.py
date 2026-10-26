r"""RSNV tool: look inside a navigation export (core/bfbc2core/rsnv.py, written by navexport1943.py and
navexport_vehicles1943.py), ask it questions, draw it, and compare two of them.

  rsnvtool.py info <file|level> [--class C]                     header, sections, counts, edge kinds, tight edges, islands
  rsnvtool.py validate <file|level> [--class C]                 consistency (rsnv.World.validate); exit 1 on a problem
  rsnvtool.py locate <file|level> X Y Z [--max-dist M]          the walkable cell and nearest node under a point
  rsnvtool.py los <file|level> X,Y,Z X,Y,Z                      line of sight between two points (the runtime's rule)
  rsnvtool.py route <file|level> FROM TO [--soldier] [--png F]  a search between nodes or points (X,Y,Z), as rsnav finds it
  rsnvtool.py pockets <file|level> [--top N]                    the main network's pieces when tight edges are left out
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
    return 2


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('rsnvtool', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
