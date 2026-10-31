"""NavKit: are a level's AI order positions on the walkable areas / route network of a pathdata.hpd?

Collects AreaOrder / PatrolOrder / InterludeOrder / FireAtArea positions and the patrol waypoints
(AreaGeometryEntityData chains referenced by PatrolOrders) from the converted level partition, then for
each pathdata.hpd given reports per point: inside an area (horizontal, with height tolerance) and the
route-network group of the nearest node.

Usage: ordercheck.py <level.xml> <a.hpd> [<b.hpd> ...]

With two files (reference first), also the pairwise route check: of all point pairs the reference's route
network connects, the share the second file also connects. With one file, the self-check: the share of
point pairs on its areas that its route network connects (for maps without reference data).
"""
import itertools
import math
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
import hpdtool  # noqa: E402
from bfbc2core import xmlinst  # noqa: E402
import kymesh  # noqa: E402

Y_OFFSET = 0.95


def order_points(xml_path):
    text = open(xml_path, encoding='utf-8', errors='replace').read()
    inst = {}           # guid -> (type, body)
    for typ, guid, body in xmlinst.instances(text):
        inst[guid.lower()] = (typ, body)

    def pos(body):
        """The instance's translation, or None where it has none (or sits at the origin)."""
        transform = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        if not transform:
            return None
        values = [float(a) for a in transform.group(1).split('/')]
        if any(abs(c) > 1e-3 for c in values[12:15]):
            return (values[12], values[13], values[14])
        return None

    pts = []            # (kind, order name, position)
    for guid, (typ, body) in inst.items():
        short = typ.split('.')[-1]
        if short in ('AreaOrderEntityData', 'PatrolOrderEntityData', 'InterludeOrderEntityData',
                     'FireAtAreaOrderEntityData'):
            name_field = re.search(r'<field name="Name">([^<]*)</field>', body)
            name = name_field[1] if name_field else ''
            p = pos(body)
            if p:
                pts.append((short.replace('EntityData', ''), name, p))
            if short == 'PatrolOrderEntityData':
                # each referenced waypoint chain, followed through its Next fields
                for ref in re.findall(r'ref="([0-9a-f-]{36})"', body):
                    seen = set()
                    waypoint = ref.lower()
                    while (waypoint in inst and waypoint not in seen
                           and inst[waypoint][0].endswith('AreaGeometryEntityData')):
                        seen.add(waypoint)
                        point = pos(inst[waypoint][1])
                        if point:
                            pts.append(('PatrolWaypoint', name, point))
                        next_ref = xmlinst.field_ref(inst[waypoint][1], 'Next')
                        if next_ref and re.fullmatch(r'[0-9a-f-]{36}', next_ref):
                            waypoint = next_ref.lower()
                        else:
                            waypoint = ''
    # unique
    out, seen = [], set()
    for kind, name, p in pts:
        key = (kind, round(p[0], 1), round(p[2], 1))
        if key not in seen:
            seen.add(key)
            out.append((kind, name, p))
    return out


def drop_cracks(segs):
    """Coincident segment pairs (in plan) are a zero-width crack inside an area - two adjacent cells of it that
    are not connected to each other (a step) - not its boundary; both are removed for the inside test."""
    from collections import Counter

    def key(seg):
        """The segment in plan, direction-free."""
        start, end = seg
        return tuple(sorted(((round(start[0], 2), round(start[2], 2)), (round(end[0], 2), round(end[2], 2)))))

    count = Counter(key(seg) for seg in segs)
    return [seg for seg in segs if count[key(seg)] % 2 == 1]


class PathData:
    def __init__(self, path, clip=None, drop=None):
        """clip: (minX, minZ, maxX, maxZ) - route nodes outside it, and the edges touching them, are left out (a
        reference's ground beyond the level's terrain, which the game on PC does not have; navparity). drop: node ids
        left out the same way (a reference's nodes on ground PC does not have; navparity.stale_nodes)."""
        hpd = hpdtool.parse(open(path, 'rb').read())
        drop = drop or set()

        def in_box(p):
            return not clip or clip[0] <= p[0] <= clip[2] and clip[1] <= p[2] <= clip[3]

        self.areas = []      # (minx, maxx, minz, maxz, miny, maxy, segs)
        for sector in hpd.sectors:
            mesh = kymesh.parse_mesh(sector.mesh)
            for area in mesh.areas:
                segs = [(line[k], line[k + 1]) for line in area.outline for k in range(len(line) - 1)]
                segs += [(b.p0, b.p1) for b in area.portals] + [(c.p0, c.p1) for c in area.border_portals]
                segs = drop_cracks(segs)
                if not segs:
                    continue
                xs = [p[0] for sg in segs for p in sg]
                zs = [p[2] for sg in segs for p in sg]
                ys = [p[1] for sg in segs for p in sg]
                self.areas.append((min(xs), max(xs), min(zs), max(zs), min(ys) - Y_OFFSET, max(ys) - Y_OFFSET, segs))
        # graph groups (shared border node ids join sectors)
        parent = {}

        def find(a):
            while parent.setdefault(a, a) != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        self.nodes = []

        def inside(node):
            return node.id not in drop and in_box(node.pos)

        for sector in hpd.sectors:
            for e in sector.edges:
                src, dst = sector.nodes[e.src], sector.nodes[e.dst]
                if inside(src) and inside(dst):
                    parent[find(src.id)] = find(dst.id)
            for node in sector.nodes:
                if inside(node):
                    self.nodes.append((node.id, node.pos))
        self.group = {nid: find(nid) for nid, _ in self.nodes}
        sizes = Counter(self.group.values())
        self.group_size = sizes
        self.pos = np.array([p for _, p in self.nodes])
        self.ids = [nid for nid, _ in self.nodes]

    def on_area(self, p, ytol=2.5):
        x, y, z = p
        for x0, x1, z0, z1, y0, y1, segs in self.areas:
            if not (x0 <= x <= x1 and z0 <= z <= z1 and y0 - ytol <= y <= y1 + ytol):
                continue
            inside = False
            for (ax, _, az), (bx, _, bz) in segs:
                if az == bz:
                    continue
                if min(az, bz) <= z < max(az, bz) and x < ax + (z - az) * (bx - ax) / (bz - az):
                    inside = not inside
            if inside:
                return True
        return False

    def nearest_group(self, p, radius=15.0):
        if not len(self.ids):                    # a file with no route nodes: none near anything
            return None, None
        dist = np.hypot(self.pos[:, 0] - p[0], self.pos[:, 2] - p[2])
        dist = np.where(np.abs(self.pos[:, 1] - Y_OFFSET - p[1]) <= 4.0, dist, 1e9)    # within 4 m of height only
        k = int(np.argmin(dist))
        if dist[k] > radius:
            return None, None
        group = self.group[self.ids[k]]
        return group, self.group_size[group]


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    pts = order_points(argv[1])
    print(f'{len(pts)} order / waypoint positions: {dict(Counter(k for k, _, _ in pts))}')
    results = {}
    for path in argv[2:]:
        pdata = PathData(path)
        biggest = max(pdata.group_size.values(), default=0)
        res = []            # per point: (on an area, size of the nearest node's route group or None)
        for _kind, _name, p in pts:
            on = pdata.on_area(p)
            _group, size = pdata.nearest_group(p)
            res.append((on, size))
        results[path] = res
        on = sum(1 for o, _ in res if o)
        near = sum(1 for _, s in res if s)
        big = sum(1 for _, s in res if s and s >= 0.25 * biggest)
        label = os.path.basename(os.path.dirname(os.path.dirname(path))) or path
        print(f'{label}: {os.path.basename(path)}  on an area: {on}/{len(pts)}  '
              f'node within 15 m: {near}  in a large route group (>= 25% of largest): {big}')
    groups = {}
    for path in argv[2:]:
        pdata = PathData(path)
        groups[path] = [pdata.nearest_group(p)[0] if pdata.on_area(p) else None for _, _, p in pts]
    if len(argv) == 3:
        group = groups[argv[2]]
        idx = [i for i in range(len(pts)) if group[i] is not None]
        total = len(idx) * (len(idx) - 1) // 2
        connected = sum(1 for a, b in itertools.combinations(idx, 2) if group[a] == group[b])
        print(f'self-check: {connected}/{total} point pairs on areas are route-connected '
              f'({connected / max(total, 1) * 100:.1f}%)')
    if len(argv) == 4:
        ref_group, other_group = groups[argv[2]], groups[argv[3]]
        idx = [i for i in range(len(pts)) if ref_group[i] is not None]
        ref = both = extra = 0
        for a, b in itertools.combinations(idx, 2):
            ref_connected = ref_group[a] == ref_group[b]
            other_connected = other_group[a] is not None and other_group[a] == other_group[b]
            ref += ref_connected
            both += ref_connected and other_connected
            extra += other_connected and not ref_connected
        print(f'pairwise route check: {both}/{ref} reference-connected pairs also connected '
              f'({both / max(ref, 1) * 100:.1f}%); {extra} pairs connected only in the second file')
    if len(results) == 2:
        (path_a, res_a), (path_b, res_b) = list(results.items())
        diff = [(pts[i], res_a[i], res_b[i]) for i in range(len(pts))
                if res_a[i][0] != res_b[i][0] or bool(res_a[i][1]) != bool(res_b[i][1])]
        print(f'\npoints that differ ({len(diff)}): first = {os.path.basename(path_a)}, '
              f'second = {os.path.basename(path_b)}')
        for (kind, name, p), a, b in diff[:60]:
            print(f'  {kind:22s} {name[:34]:34s} ({p[0]:8.1f} {p[1]:6.1f} {p[2]:8.1f})  '
                  f'on-area {a[0]!s:5} / {b[0]!s:5}   group size {a[1]} / {b[1]}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('ordercheck', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
