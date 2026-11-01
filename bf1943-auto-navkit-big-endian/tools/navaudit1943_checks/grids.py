"""navaudit1943_checks: the level - one grid's samples and masks (Grid), why a surface is not kept, the build's
graph (Nav).

Grid wraps one sampling run (navsample.Samples) with the build's kept mask, the terrain and the water over every
column; Nav wraps the build as navdata reads it (areas with exact outlines, cells, route graph) for the checks'
lookups: the area near a point, the route node near a point, what a node reaches.
"""
import collections
import math
from typing import TYPE_CHECKING, Any, cast, overload

import numpy as np
import numpy.typing as npt

import navaudit1943  # noqa: E402
import soldier1943  # noqa: E402

from .geometry import seg_dist

if TYPE_CHECKING:
    from bfbc2core.materialmap1943 import MaterialMap
    from bfbc2core.navdata import NavData
    from bfbc2core.terrain import Heightfield
    from navsample import Samples

type BoolArray = npt.NDArray[np.bool]
type Index = int | np.integer[Any]        # a column or surface index, plain or numpy
type WaterMesh = tuple[npt.NDArray[np.float32], npt.NDArray[np.uint32]]   # (vertices, triangles)
type BuildCells = list[tuple[int, float, npt.NDArray[np.int32], npt.NDArray[np.int32], npt.NDArray[np.float32]]]
type AreaHit = tuple[float, int, float]     # (distance, area index, the area's ground height there)
type SurfaceRecord = dict[str, Any]         # one surface of Grid.describe's column


# ------------------------------------------------------------------------------------------------ the level

class Grid:
    """One sampling run with its kept mask: surfaces per column, the build's verdict, water over every column."""

    def __init__(self, s: 'Samples', keep: BoolArray, hf: 'Heightfield', water: WaterMesh | None,
                 label_: str) -> None:
        import navbuild
        self.s, self.keep, self.hf, self.label = s, keep, hf, label_
        if water is not None:
            self.top = navbuild.water_tops(s, water[0], water[1]).astype(np.float32)
        else:
            self.top = np.full(s.nx * s.nz, -np.inf, np.float32)
        self.variants: dict[str, BoolArray] = {}        # rule name -> the kept mask with that rule off

    def col(self, x: float, z: float) -> int | None:
        """The column nearest (x, z), None off the grid."""
        s = self.s
        i, j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            return None
        return j * s.nx + i

    def centre(self, c: Index) -> tuple[float, float]:
        """Column c's (x, z)."""
        s = self.s
        return s.min_x + (c % s.nx) * s.pitch, s.min_z + (c // s.nx) * s.pitch

    def surfaces(self, c: Index) -> range:
        """Column c's surface indices, top down."""
        return range(self.s.start[c], self.s.start[c + 1])

    def col_of(self) -> npt.NDArray[np.int_]:
        """Every surface's column (computed once)."""
        if not hasattr(self, '_col_of'):
            self._col_of = np.repeat(np.arange(self.s.nx * self.s.nz), np.diff(self.s.start))
        return self._col_of

    def describe(self, c: Index, lo: float = -1e9, hi: float = 1e9, mm: 'MaterialMap | None' = None) -> dict[str, Any]:
        """The column's surfaces between heights lo and hi, top down: the sample columns of the report."""
        s = self.s
        x, z = self.centre(c)
        terrain_y = float(self.hf.height(x, z))
        water_top = float(self.top[c])
        indices = list(self.surfaces(c))
        out = []
        for position, n in enumerate(indices):
            y = float(s.y[n])
            if not (lo <= y <= hi):
                continue
            above = float(s.y[indices[position - 1]]) if position > 0 else None     # surfaces are stored top down
            slope_deg = math.degrees(math.acos(max(-1.0, min(1.0, float(s.normal[n, 1])))))
            on_terrain = abs(y - terrain_y) < 0.06
            surface = {'y': round(y, 2), 'slope_deg': round(slope_deg, 1), 'blocked': bool(s.flags[n] & 1),
                       'kept': bool(self.keep[n]), 'clearance': None if above is None else round(above - y, 2),
                       'terrain': on_terrain,
                       'below_terrain': round(terrain_y - y, 2) if y < terrain_y - 0.06 else 0.0,
                       'water_depth': round(water_top - y, 2) if water_top > -1e30 and water_top > y else 0.0}
            if on_terrain and mm is not None:
                surface['material'] = mm.name_at(x, z)[0]
            changed_by = [rule for rule, rule_keep in self.variants.items() if rule_keep[n] != self.keep[n]]
            if changed_by:
                surface['rules'] = {rule: ('removed it' if self.variants[rule][n] else 'added it')
                                    for rule in changed_by}
            surface['why_not'] = None if surface['kept'] else why_not(surface)
            out.append(surface)
        return {'grid': self.label, 'column': [round(x, 2), round(z, 2)], 'terrain_y': round(terrain_y, 2),
                'water_top': None if water_top < -1e30 else round(water_top, 2), 'surfaces': out}


def why_not(surface: SurfaceRecord) -> list[str]:
    """Why a surface of Grid.describe was not kept: the surface tests it fails, else the flood's."""
    reasons = []
    if surface['blocked']:
        reasons.append('capsule blocked')
    if surface['slope_deg'] > soldier1943.SLOPE_MAX + 0.6:
        reasons.append(f'steeper than {soldier1943.SLOPE_MAX:g} deg')
    if surface['clearance'] is not None and 0.5 <= surface['clearance'] < soldier1943.HEIGHT:
        reasons.append('headroom under 1.8 m')
    if surface['water_depth'] > soldier1943.WATER_DEPTH:
        reasons.append('deeper than 1.4 m under water')
    if surface['below_terrain'] > 0.5:
        reasons.append('more than 0.5 m under the terrain')
    for rule, what in (surface.get('rules') or {}).items():
        if what == 'removed it':
            reasons.append(f'{rule} rule removed it')
    return reasons or ['passes the surface tests; not reached by the flood from the seeds (or an object rule: forbid, '
                       'exclude, dynamic)']


class Nav:
    """The build: areas with exact outlines (point in area, distance to area), cells, route graph."""

    def __init__(self, nav: 'NavData', exact: BuildCells | None = None) -> None:
        self.nav = nav
        # the build's own cells (navbuild's <level>.cells.npz) per area: x, z, ground height - exact, where the
        # reader's cells (navdata, 0.8 m, heights from the nearest outline point) can be metres off on rock and slopes
        # (Coral Sea's South Base flag, 2026-10-26)
        self.exact: list[tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]] | None
        self.exact = None
        if exact is not None and len(exact) == len(nav.areas):
            self.exact = [(np.asarray(cell_i, float) * pitch, np.asarray(cell_j, float) * pitch,
                           np.asarray(cell_y, float))
                          for _sector, pitch, cell_i, cell_j, cell_y in exact]
        # per area: its outline, portal and border segments ((n, 2, 3) array) and their box (x0, z0, x1, z1)
        self.segs: list[npt.NDArray[np.float64]] = []
        self.box: list[tuple[float, float, float, float] | None] = []
        for area in nav.areas:
            segments = []
            for line in area.outline:
                line = np.asarray(line, float)
                for k in range(len(line) - 1):
                    segments.append((line[k], line[k + 1]))
            for p0, p1, *_ in area.portals:
                segments.append((np.asarray(p0, float), np.asarray(p1, float)))
            for p0, p1 in area.borders:
                segments.append((np.asarray(p0, float), np.asarray(p1, float)))
            seg_array = np.array(segments, float).reshape(-1, 2, 3) if segments else np.zeros((0, 2, 3))
            self.segs.append(seg_array)
            if len(seg_array):
                self.box.append((seg_array[:, :, 0].min(), seg_array[:, :, 2].min(),
                                 seg_array[:, :, 0].max(), seg_array[:, :, 2].max()))
            else:
                self.box.append(None)
        # areas by 10 m bucket of their box
        self.bucket: collections.defaultdict[tuple[int, int], list[int]] = collections.defaultdict(list)
        for k, box in enumerate(self.box):
            if box is None:
                continue
            for i in range(int(box[0] // 10), int(box[2] // 10) + 1):
                for j in range(int(box[1] // 10), int(box[3] // 10) + 1):
                    self.bucket[(i, j)].append(k)
        # the reader's cells by 0.8 m grid position
        self.cell_key: collections.defaultdict[tuple[int, int], list[int]] = collections.defaultdict(list)
        cell = 0.8
        for n, (x, z) in enumerate(nav.cell_xz):
            self.cell_key[(int(round(x / cell)), int(round(z / cell)))].append(n)
        main = max(range(len(nav.islands)), key=lambda i: nav.islands[i].nodes) if nav.islands else -1
        self.main = main
        # the route graph as compressed rows: node u's successors are fwd_dst[fwd_start[u]:fwd_start[u + 1]]
        node_count = len(nav.node_pos)
        edges = nav.edges
        order = np.argsort(edges[:, 0], kind='stable')
        self.fwd_dst = edges[order, 1]
        self.fwd_start = np.searchsorted(edges[order, 0], np.arange(node_count + 1))
        order = np.argsort(edges[:, 1], kind='stable')
        self.bwd_dst = edges[order, 0]
        self.bwd_start = np.searchsorted(edges[order, 1], np.arange(node_count + 1))
        self.node_ground = nav.node_pos[:, 1] - soldier1943.HEIGHT / 2

    def area_height(self, k: int, x: float, z: float) -> float:
        """The area's ground height near (x, z): its nearest cell's (the build's own cells where the build has its
        cells file, else the reader's 0.8 m cells), else its nearest outline point's."""
        if self.exact is not None and len(self.exact[k][0]):
            cell_x, cell_z, cell_y = self.exact[k]
            return float(cell_y[int(np.argmin((cell_x - x) ** 2 + (cell_z - z) ** 2))])
        best = None                                         # (squared distance, height)
        for di in range(-3, 4):
            for dj in range(-3, 4):
                for n in self.cell_key.get((int(round(x / 0.8)) + di, int(round(z / 0.8)) + dj), ()):
                    if self.nav.cell_area[n] == k:
                        dist_sq = (self.nav.cell_xz[n, 0] - x) ** 2 + (self.nav.cell_xz[n, 1] - z) ** 2
                        if best is None or dist_sq < best[0]:
                            best = (dist_sq, float(self.nav.cell_y[n]))
        if best is not None:
            return best[1]
        points = self.segs[k].reshape(-1, 3)
        nearest = np.argmin((points[:, 0] - x) ** 2 + (points[:, 2] - z) ** 2)
        return float(points[nearest, 1]) - soldier1943.HEIGHT / 2

    def candidates(self, x: float, z: float, r: float) -> set[int]:
        """The areas whose 10 m buckets lie within r of (x, z)."""
        out = set()
        for i in range(int((x - r) // 10), int((x + r) // 10) + 1):
            for j in range(int((z - r) // 10), int((z + r) // 10) + 1):
                out.update(self.bucket.get((i, j), ()))
        return out

    def locate(self, x: float, z: float, floor_y: float, r: float = 30.0,
               dy: float | None = None) -> tuple[AreaHit | None, AreaHit | None]:
        """Areas near (x, z): the nearest on the floor (|area ground - floor_y| <= dy) and the nearest on any floor.
        Distance 0 inside the area's outline."""
        dy = navaudit1943.FLOOR_DY if dy is None else dy
        same = any_floor = None
        for k in self.candidates(x, z, r):
            segments = self.segs[k]
            # an area without segments has no box, and so no bucket: candidates() never gives it
            box = cast('tuple[float, float, float, float]', self.box[k])
            if x < box[0] - r or x > box[2] + r or z < box[1] - r or z > box[3] + r:
                continue
            seg_a, seg_b = segments[:, 0][:, [0, 2]], segments[:, 1][:, [0, 2]]   # each segment's ends on xz
            straddles = (seg_a[:, 1] > z) != (seg_b[:, 1] > z)
            with np.errstate(divide='ignore', invalid='ignore'):
                x_cross = seg_a[:, 0] + (z - seg_a[:, 1]) * (seg_b[:, 0] - seg_a[:, 0]) / (seg_b[:, 1] - seg_a[:, 1])
            inside = bool(np.count_nonzero(straddles & (x < x_cross)) % 2)
            distance = 0.0 if inside else float(seg_dist(x, z, seg_a, seg_b).min())
            if distance > r:
                continue
            ground = self.area_height(k, x, z)
            hit = (distance, k, ground)
            if any_floor is None or (distance, abs(ground - floor_y)) < (any_floor[0], abs(any_floor[2] - floor_y)):
                any_floor = hit
            if abs(ground - floor_y) <= dy and (same is None or distance < same[0]):
                same = hit
        return same, any_floor

    @overload
    def area_info(self, rec: AreaHit, floor_y: float | None = None) -> dict[str, Any]: ...
    @overload
    def area_info(self, rec: None, floor_y: float | None = None) -> None: ...
    @overload
    def area_info(self, rec: AreaHit | None, floor_y: float | None = None) -> dict[str, Any] | None: ...

    def area_info(self, rec: AreaHit | None, floor_y: float | None = None) -> dict[str, Any] | None:
        """The report's record of an area locate() found (None for none)."""
        if rec is None:
            return None
        distance, k, ground = rec
        area = self.nav.areas[k]
        island = area.island
        return {'distance': round(distance, 2), 'area': k, 'sector': f'{area.sector:04x}', 'layer': area.layer,
                'pitch': round(area.pitch, 2), 'ground_y': round(ground, 2),
                'dy': None if floor_y is None else round(ground - floor_y, 2), 'island': island,
                'island_nodes': self.nav.islands[island].nodes if island >= 0 else None, 'main': island == self.main}

    def join(self, x: float, z: float, ground_y: float, island: int | None = None, r: float = 15.0,
             dy: float = 2.0) -> int | None:
        """The nearest route node on the ground's floor (and on the island, when given)."""
        nodes = self.nav.node_pos
        distance = np.hypot(nodes[:, 0] - x, nodes[:, 2] - z)
        ok = (distance <= r) & (np.abs(self.node_ground - ground_y) <= dy)
        if island is not None:
            on_island = ok & (self.nav.node_island == island)
            if on_island.any():
                ok = on_island
        if not ok.any():
            return None
        return int(np.argmin(np.where(ok, distance, np.inf)))

    def reach(self, start: int, backward: bool = False) -> BoolArray:
        """The nodes reachable from start (along the edges, or against them with backward)."""
        node_count = len(self.nav.node_pos)
        seen = np.zeros(node_count, bool)
        seen[start] = True
        row_start, successors = (self.bwd_start, self.bwd_dst) if backward else (self.fwd_start, self.fwd_dst)
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in successors[row_start[node]:row_start[node + 1]]:
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append(int(nxt))
        return seen
