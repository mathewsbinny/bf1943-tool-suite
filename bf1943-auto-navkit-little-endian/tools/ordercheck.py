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
import os
import re
import sys
from collections import Counter
from collections.abc import Collection, Sequence

import numpy as np
import numpy.typing as npt

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
ORDER_TYPES = ('AreaOrderEntityData', 'PatrolOrderEntityData', 'InterludeOrderEntityData', 'FireAtAreaOrderEntityData')
GUID = r'[0-9a-f-]{36}'

type Vec3 = tuple[float, float, float]
type OrderPoint = tuple[str, str, Vec3]          # (kind, order name, position)
type Segment = tuple[Sequence[float], Sequence[float]]     # two (x, y, z) ends
# an area's plan box, its height range (ground: outline heights less Y_OFFSET) and its boundary segments
type AreaBox = tuple[float, float, float, float, float, float, list[Segment]]


def _translation(body: str) -> Vec3 | None:
    """The instance's translation, or None where it has none (or sits at the origin)."""
    transform = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
    if not transform:
        return None
    values = [float(value) for value in transform.group(1).split('/')]
    if any(abs(coord) > 1e-3 for coord in values[12:15]):
        return (values[12], values[13], values[14])
    return None


def order_points(xml_path: str) -> list[OrderPoint]:
    """The level's AI order positions and patrol waypoints, unique by (kind, x, z) to 0.1 m."""
    text = open(xml_path, encoding='utf-8', errors='replace').read()
    instances: dict[str, tuple[str, str]] = {}           # guid -> (type, body)
    for type_name, guid, body in xmlinst.instances(text):
        instances[guid.lower()] = (type_name, body)

    found: list[OrderPoint] = []
    for _guid, (type_name, body) in instances.items():
        short = type_name.split('.')[-1]
        if short not in ORDER_TYPES:
            continue
        name_field = re.search(r'<field name="Name">([^<]*)</field>', body)
        name = name_field[1] if name_field else ''
        position = _translation(body)
        if position:
            found.append((short.replace('EntityData', ''), name, position))
        if short == 'PatrolOrderEntityData':
            # each referenced waypoint chain, followed through its Next fields
            for ref in re.findall(rf'ref="({GUID})"', body):
                seen = set()
                waypoint = ref.lower()
                while (waypoint in instances and waypoint not in seen
                       and instances[waypoint][0].endswith('AreaGeometryEntityData')):
                    seen.add(waypoint)
                    point = _translation(instances[waypoint][1])
                    if point:
                        found.append(('PatrolWaypoint', name, point))
                    next_ref = xmlinst.field_ref(instances[waypoint][1], 'Next')
                    if next_ref and re.fullmatch(GUID, next_ref):
                        waypoint = next_ref.lower()
                    else:
                        waypoint = ''
    # unique
    out: list[OrderPoint] = []
    seen_keys = set()
    for kind, name, position in found:
        key = (kind, round(position[0], 1), round(position[2], 1))
        if key not in seen_keys:
            seen_keys.add(key)
            out.append((kind, name, position))
    return out


def drop_cracks(segs: Sequence[Segment]) -> list[Segment]:
    """Coincident segment pairs (in plan) are a zero-width crack inside an area - two adjacent cells of it that
    are not connected to each other (a step) - not its boundary; both are removed for the inside test."""

    def key(seg: Segment) -> tuple[tuple[float, float], ...]:
        """The segment in plan, direction-free."""
        start, end = seg
        return tuple(sorted(((round(start[0], 2), round(start[2], 2)), (round(end[0], 2), round(end[2], 2)))))

    count = Counter(key(seg) for seg in segs)
    return [seg for seg in segs if count[key(seg)] % 2 == 1]


class PathData:
    """A path-data file's walkable areas (for the inside test) and its route groups (connected node sets)."""

    def __init__(self, path: str, clip: Sequence[float] | None = None, drop: Collection[int] | None = None) -> None:
        """clip: (minX, minZ, maxX, maxZ) - route nodes outside it, and the edges touching them, are left out (a
        reference's ground beyond the level's terrain, which the game on PC does not have; navparity). drop: node ids
        left out the same way (a reference's nodes on ground PC does not have; navparity.stale_nodes)."""
        hpd = hpdtool.parse(open(path, 'rb').read())
        drop = drop or set()

        def in_box(position: Sequence[float]) -> bool:
            return not clip or clip[0] <= position[0] <= clip[2] and clip[1] <= position[2] <= clip[3]

        self.areas: list[AreaBox] = []      # (minx, maxx, minz, maxz, miny, maxy, segs)
        for sector in hpd.sectors:
            mesh = kymesh.parse_mesh(sector.mesh)
            for area in mesh.areas:
                segs: list[Segment] = [(line[k], line[k + 1]) for line in area.outline for k in range(len(line) - 1)]
                segs += [(portal.p0, portal.p1) for portal in area.portals]
                segs += [(border.p0, border.p1) for border in area.border_portals]
                segs = drop_cracks(segs)
                if not segs:
                    continue
                x_values = [end[0] for seg in segs for end in seg]
                z_values = [end[2] for seg in segs for end in seg]
                y_values = [end[1] for seg in segs for end in seg]
                self.areas.append((min(x_values), max(x_values), min(z_values), max(z_values),
                                   min(y_values) - Y_OFFSET, max(y_values) - Y_OFFSET, segs))
        # graph groups (shared border node ids join sectors)
        parent: dict[int, int] = {}

        def find(node_id: int) -> int:
            """The node's group root (union-find with path halving)."""
            while parent.setdefault(node_id, node_id) != node_id:
                parent[node_id] = parent[parent[node_id]]
                node_id = parent[node_id]
            return node_id
        self.nodes: list[tuple[int, Vec3]] = []      # (id, position) of the nodes kept

        def inside(node: hpdtool.Node) -> bool:
            return node.id not in drop and in_box(node.pos)

        for sector in hpd.sectors:
            for edge in sector.edges:
                src, dst = sector.nodes[edge.src], sector.nodes[edge.dst]
                if inside(src) and inside(dst):
                    parent[find(src.id)] = find(dst.id)
            for node in sector.nodes:
                if inside(node):
                    self.nodes.append((node.id, node.pos))
        self.group: dict[int, int] = {node_id: find(node_id) for node_id, _ in self.nodes}
        self.group_size: Counter[int] = Counter(self.group.values())
        self.pos: npt.NDArray[np.float64] = np.array([position for _, position in self.nodes])
        self.ids: list[int] = [node_id for node_id, _ in self.nodes]

    def on_area(self, point: Sequence[float], ytol: float = 2.5) -> bool:
        """Whether the point lies inside an area in plan, its height within ytol of the area's ground range."""
        x, y, z = point
        for min_x, max_x, min_z, max_z, min_y, max_y, segs in self.areas:
            if not (min_x <= x <= max_x and min_z <= z <= max_z and min_y - ytol <= y <= max_y + ytol):
                continue
            inside = False
            # even-odd ray test along +x; (ax, az) and (bx, bz) are the segment's ends in plan
            for (ax, _, az), (bx, _, bz) in segs:
                if az == bz:
                    continue
                if min(az, bz) <= z < max(az, bz) and x < ax + (z - az) * (bx - ax) / (bz - az):
                    inside = not inside
            if inside:
                return True
        return False

    def nearest_group(self, point: Sequence[float], radius: float = 15.0) -> tuple[int, int] | tuple[None, None]:
        """(group, its size) of the node nearest the point within radius in plan and 4 m in height, else (None,
        None)."""
        if not len(self.ids):                    # a file with no route nodes: none near anything
            return None, None
        dist = np.hypot(self.pos[:, 0] - point[0], self.pos[:, 2] - point[2])
        dist = np.where(np.abs(self.pos[:, 1] - Y_OFFSET - point[1]) <= 4.0, dist, 1e9)    # within 4 m of height only
        k = int(np.argmin(dist))
        if dist[k] > radius:
            return None, None
        group = self.group[self.ids[k]]
        return group, self.group_size[group]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    points = order_points(argv[1])
    print(f'{len(points)} order / waypoint positions: {dict(Counter(kind for kind, _, _ in points))}')
    results = {}
    for path in argv[2:]:
        path_data = PathData(path)
        biggest = max(path_data.group_size.values(), default=0)
        per_point = []          # per point: (on an area, size of the nearest node's route group or None)
        for _kind, _name, position in points:
            on_area = path_data.on_area(position)
            _group, size = path_data.nearest_group(position)
            per_point.append((on_area, size))
        results[path] = per_point
        on_count = sum(1 for on_area, _ in per_point if on_area)
        near = sum(1 for _, size in per_point if size)
        big = sum(1 for _, size in per_point if size and size >= 0.25 * biggest)
        label = os.path.basename(os.path.dirname(os.path.dirname(path))) or path
        print(f'{label}: {os.path.basename(path)}  on an area: {on_count}/{len(points)}  '
              f'node within 15 m: {near}  in a large route group (>= 25% of largest): {big}')
    groups = {}
    for path in argv[2:]:
        path_data = PathData(path)
        groups[path] = [path_data.nearest_group(position)[0] if path_data.on_area(position) else None
                        for _, _, position in points]
    if len(argv) == 3:
        group = groups[argv[2]]
        on_areas = [i for i in range(len(points)) if group[i] is not None]
        total = len(on_areas) * (len(on_areas) - 1) // 2
        connected = sum(1 for first, second in itertools.combinations(on_areas, 2) if group[first] == group[second])
        print(f'self-check: {connected}/{total} point pairs on areas are route-connected '
              f'({connected / max(total, 1) * 100:.1f}%)')
    if len(argv) == 4:
        ref_group, other_group = groups[argv[2]], groups[argv[3]]
        on_areas = [i for i in range(len(points)) if ref_group[i] is not None]
        ref = both = extra = 0
        for first, second in itertools.combinations(on_areas, 2):
            ref_connected = ref_group[first] == ref_group[second]
            other_connected = other_group[first] is not None and other_group[first] == other_group[second]
            ref += ref_connected
            both += ref_connected and other_connected
            extra += other_connected and not ref_connected
        print(f'pairwise route check: {both}/{ref} reference-connected pairs also connected '
              f'({both / max(ref, 1) * 100:.1f}%); {extra} pairs connected only in the second file')
    if len(results) == 2:
        (path_a, results_a), (path_b, results_b) = list(results.items())
        diff = [(points[i], results_a[i], results_b[i]) for i in range(len(points))
                if results_a[i][0] != results_b[i][0] or bool(results_a[i][1]) != bool(results_b[i][1])]
        print(f'\npoints that differ ({len(diff)}): first = {os.path.basename(path_a)}, '
              f'second = {os.path.basename(path_b)}')
        for (kind, name, position), first_result, second_result in diff[:60]:
            print(f'  {kind:22s} {name[:34]:34s} ({position[0]:8.1f} {position[1]:6.1f} {position[2]:8.1f})  '
                  f'on-area {first_result[0]!s:5} / {second_result[0]!s:5}   '
                  f'group size {first_result[1]} / {second_result[1]}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('ordercheck', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
