"""RSNV: our own per-level navigation file (operation-rising-sun research/NAVIGATION_RUNTIME_DESIGN.md, section 1.3),
the input of the navigation runtime (project-opfor-bf1943 `rsnav`). Written by NavKit's exporter (navexport1943.py)
from a checked build; read here for tests, tools and the Python reference search.

    w = rsnv.load(path_or_bytes)       # either byte order
    w.node_pos, w.edge_target, w.edge_kind, w.edge_cost, w.areas, ...
    w.locate(x, y, z)                  # (area, (i, j), distance) or None
    w.validate()                       # [] when consistent

Layout v1. All sections 16-byte aligned, fixed strides, indices and offsets only (no pointers), so a loader can swap
in place by the stride schema (SCHEMA) and then read native types.

  header (64 B): 'RSNV', u32 0x01020304 in the writer's order, u16 version, u16 flags (bit 0 big-endian), u32 file
    size, u32 section count, u32 level id (FNV-1a of the level name), f32 sea level, f32 agent radius, f32 height,
    f32 step, f32 max water depth, u32 first 4 bytes of the source .hpd's SHA-256, 16 reserved bytes
  directory: n x {u32 tag, u32 offset, u32 count, u32 stride} from 0x40
  SECT 32  u16 id 0xXXYY, u8 layer, u8 pitch code (0: 0.2 m, 1: 0.8 m), f32 minX, minZ, maxX, maxZ, u32 first GBKT
           bucket (25 x 25 buckets of 4 m from (minX, minZ), row-major in z), u16 first area, u16 area count, u32 0
  NODE 16  f32 x, y (ground), z, u32 first edge; nodes + 1 (the sentinel's first edge is the edge count)
  NAUX 8   u16 area (0xFFFF none), u16 sector, u16 island, u16 flags (NAUX_*)
  EDGE 8   u32 target (bits 0-23) | kind (bits 24-31, EDGE_*), u16 cost (1/16 m), u16 flags (EDGEF_*)
  AREA 64  f32 x0, z0 (first bitmap cell centre), x1, z1 (last), minY, maxY; u32 BMAP byte offset; u16 bitmap width,
           height; u32 first PORT, u16 portal count, u16 sector; u16 island, u16 flags; u32 first node (in AREA order,
           ANOD), u16 node count, u16 pitch in mm; u32 HGHT offset (entries), u16 height grid width, height; u32
           height grid step in mm (0: 4 m, files before 2026-10-25)
  PORT 24  f32 x0, z0, x1, z1, y, u16 from area, u16 to area (both directions stored)
  BMAP 1   per area, rows of ceil(width / 8) bytes, bit (i & 7) of byte i >> 3; 1 = walkable cell of the area
  HGHT 2   per area, u16 cm of ground height above the area's minY: the mean of the cells in each block of the
           height grid (blocks of a whole number of cells from the first cell); 0xFFFF a block with no cell
  GBKT 8   u32 first GLST entry, u32 count
  GLST 2   u16 area ids, each bucket's sorted by minY
  ANOD 4   u32 node ids, grouped by area
  LADR 40  f32 foot x, y, z, top x, y, z, yaw, u32 foot node, u32 top node, u32 aux = kind (bits 24-31: 0 ladder,
           1 jump) | source index
  OFFM 32  u8 kind (OFFM_*), u8 team mask, u16 vehicle class, u32 node, f32 x, y, z, yaw, radius, u32 aux
  ISLD 16  u32 nodes, u32 representative node, u16 flags (ISLD_*), u16 transfer count, u32 first transfer
  META 1   UTF-8 JSON: exporter version, sources and their hashes, NavKit settings
  OBJT 24  destructible objects: u32 key (the level DBX Enumeration), u32 blueprint hash, f32 x, y, z, u32 flags
  CEDG 16  conditional edges (EDGE flag EDGEF_COND), sorted by edge: u32 edge, u32 first OREF, u32 count, u32 0;
           open only when every object listed is destroyed
  OREF 4   u32 OBJT indices
Deviations from the design's table: AREA is 64 B (its 48 B does not hold the listed fields), GRID is GBKT + GLST,
ANOD lists nodes by area, LADR carries jump links too (kind), COND is CEDG + OREF. The exporter writes no OBJT /
CEDG / OREF yet: they need NavKit's blocker attribution (design section 3.1).
"""
import json
import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

MAGIC = b'RSNV'
VERSION = 1
HEADER = 64
EDGE_WALK, EDGE_LADDER_UP, EDGE_LADDER_DOWN, EDGE_DROP, EDGE_JUMP, EDGE_BOARD, EDGE_ALIGHT = range(7)
EDGE_NAMES = ['walk', 'ladder up', 'ladder down', 'drop', 'jump', 'board', 'alight']
NAUX_SHALLOW, NAUX_WADE, NAUX_BORDER, NAUX_LADDER_END, NAUX_JUMP_END = 1, 2, 4, 8, 16
EDGEF_COND = 1
EDGEF_TIGHT = 2        # a walk edge with no line of sight either way (exporter 1.2): the cells leave no room
OFFM_BOARD_VEHICLE, OFFM_MOUNT_STATIC = 1, 5
ISLD_MAIN, ISLD_CARRIER, ISLD_LADDER_GROUND = 1, 2, 4
PITCHES = {0: 0.2, 1: 0.8}
# line of sight (rsnav's rsnav_los.c): the soldier's step, the ground change allowed, coarse zones' allowances
LOS_STEP = 0.37
LOS_DY = 0.6
LOS_COARSE_DY = LOS_STEP + 0.8
COARSE_GAP = 1.0
LOS_GRACE = 0.25
LOS_GRAZE = 0.25        # of a cell (2026-10-26): a sample this close to a cell's side leans on that side. In a 0.2 m
                        # area a sample on a blocked cell passes when it is within this of a walkable neighbour's square
                        # (the build's own lines step diagonally between cells: navbuild.line_walkable); in a 0.8 m
                        # area a sample needs the neighbour only on the sides it leans to
COARSE_MM = 400
BUCKET = 4.0
BUCKETS = 25

# per section: struct format of one record (without byte order); BMAP / META are bytes
SCHEMA = {
    'SECT': 'HBB4fIHHI',
    'NODE': '3fI',
    'NAUX': '4H',
    'EDGE': 'IHH',
    'AREA': '6fIHHIHHHHIHHIHHI',
    'PORT': '5fHH',
    'HGHT': 'H',
    'GBKT': 'II',
    'GLST': 'H',
    'ANOD': 'I',
    'LADR': '7f3I',
    'OFFM': 'BBHI5fI',
    'ISLD': 'IIHHI',
    'OBJT': 'II3fI',
    'CEDG': 'IIII',
    'OREF': 'I',
}
for _t, _f in SCHEMA.items():
    assert struct.calcsize('<' + _f) == {'SECT': 32, 'NODE': 16, 'NAUX': 8, 'EDGE': 8, 'AREA': 64, 'PORT': 24, 'HGHT': 2,
                                         'GBKT': 8, 'GLST': 2, 'ANOD': 4, 'LADR': 40, 'OFFM': 32, 'ISLD': 16, 'OBJT': 24,
                                         'CEDG': 16, 'OREF': 4}[_t], _t


def fnv1a(text):
    h = 0x811C9DC5
    for b in text.encode('utf-8'):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def write(path, sections: Dict[str, object], header: dict, big_endian=False):
    """Writes the file. sections: tag -> list of tuples (by SCHEMA) or bytes (BMAP, META). header: level, sea_level,
    radius, height, step, water_depth, hpd_sha256 (hex)."""
    e = '>' if big_endian else '<'
    order = [t for t in ('SECT', 'NODE', 'NAUX', 'EDGE', 'AREA', 'PORT', 'BMAP', 'HGHT', 'GBKT', 'GLST', 'ANOD', 'LADR',
                         'OFFM', 'ISLD', 'OBJT', 'CEDG', 'OREF', 'META') if t in sections]
    blobs = []
    for t in order:
        v = sections[t]
        if isinstance(v, (bytes, bytearray)):
            blobs.append((t, bytes(v), len(v), 1))
        else:
            st = struct.Struct(e + SCHEMA[t])
            blobs.append((t, b''.join(st.pack(*r) for r in v), len(v), st.size))
    off = HEADER + 16 * len(order)
    off = (off + 15) & ~15
    directory, body = [], bytearray()
    for t, b, count, stride in blobs:
        directory.append((t, off + len(body), count, stride))
        body += b
        body += bytes((-len(body)) % 16)
    size = off + len(body)
    out = bytearray()
    out += MAGIC + struct.pack(e + 'IHHIII', 0x01020304, VERSION, 1 if big_endian else 0, size, len(order),
                               fnv1a(header['level']))
    out += struct.pack(e + '5f', header['sea_level'], header['radius'], header['height'], header['step'],
                       header['water_depth'])
    out += bytes.fromhex(header['hpd_sha256'][:8]) + bytes(16)
    assert len(out) == HEADER
    for t, o, count, stride in directory:
        out += t.encode('ascii') + struct.pack(e + 'III', o, count, stride)
    out += bytes(off - len(out))
    out += body
    open(path, 'wb').write(bytes(out))
    return size


@dataclass
class Area:
    index: int
    x0: float
    z0: float
    x1: float
    z1: float
    min_y: float
    max_y: float
    bmap: int
    width: int
    height: int
    first_portal: int
    portals: int
    sector: int
    island: int
    flags: int
    first_node: int
    nodes: int
    pitch: float
    hght: int
    hw: int
    hh: int
    hstep_mm: int = 0

    @property
    def pitch_mm_(self):
        return int(round(self.pitch * 1000))

    def cell(self, x, z):
        """(i, j) of the bitmap cell at (x, z), or None outside the bitmap."""
        i = int(round((x - self.x0) / self.pitch))
        j = int(round((z - self.z0) / self.pitch))
        return (i, j) if 0 <= i < self.width and 0 <= j < self.height else None


@dataclass
class World:
    level_id: int
    big_endian: bool
    sea_level: float
    radius: float
    agent_height: float
    step: float
    water_depth: float
    sections: Dict[str, tuple] = field(default_factory=dict)       # tag -> (offset, count, stride)
    raw: bytes = b''
    sectors: list = field(default_factory=list)
    areas: List[Area] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # -- bitmaps and heights
    def walkable(self, a: Area, i, j):
        if not (0 <= i < a.width and 0 <= j < a.height):
            return False
        row = (a.width + 7) >> 3
        return bool(self.bmap[a.bmap + j * row + (i >> 3)] >> (i & 7) & 1)

    def ground(self, a: Area, x, z):
        """The area's ground height at (x, z) from its height grid: bilinear between the four block centres around
        the point; up to half a block past the outermost centres the slope continues (linear extrapolation, so an
        area's edge on a slope is not flattened). Where one of the four has no cells, the others' weights (within
        the grid) are renormalised; the nearest filled block of the 3 x 3 around it only when none carries weight.
        rsnav's rsn_area_ground computes the same, in the same order of operations."""
        if a.hw == 0 or a.hh == 0:
            return a.min_y
        pmm = int(round(a.pitch * 1000))
        hmm = a.hstep_mm or 4000
        cpb = (hmm + pmm // 2) // pmm                   # cells per block
        u = (x - a.x0) / a.pitch
        v = (z - a.z0) / a.pitch
        gu = min(max((u - (cpb - 1) / 2.0) / cpb, -0.5), a.hw - 0.5)
        gv = min(max((v - (cpb - 1) / 2.0) / cpb, -0.5), a.hh - 0.5)
        i0 = min(max(math.floor(gu), 0), max(a.hw - 2, 0))
        j0 = min(max(math.floor(gv), 0), max(a.hh - 2, 0))
        i1, j1 = min(i0 + 1, a.hw - 1), min(j0 + 1, a.hh - 1)
        fu = gu - i0 if i1 > i0 else 0.0
        fv = gv - j0 if j1 > j0 else 0.0
        h = [int(self.hght[a.hght + jj * a.hw + ii]) for jj, ii in ((j0, i0), (j0, i1), (j1, i0), (j1, i1))]
        if 0xFFFF not in h:
            top = (1.0 - fu) * h[0] + fu * h[1]
            bottom = (1.0 - fu) * h[2] + fu * h[3]
            return a.min_y + ((1.0 - fv) * top + fv * bottom) / 100.0
        fu = min(max(fu, 0.0), 1.0)
        fv = min(max(fv, 0.0), 1.0)
        wts = ((1.0 - fu) * (1.0 - fv), fu * (1.0 - fv), (1.0 - fu) * fv, fu * fv)
        s = ws = 0.0
        for wt, hv in zip(wts, h):
            if hv != 0xFFFF:
                s += wt * hv
                ws += wt
        if ws > 1e-6:
            return a.min_y + (s / ws) / 100.0
        gi = min(max(math.floor(u / cpb), 0), a.hw - 1)
        gj = min(max(math.floor(v / cpb), 0), a.hh - 1)
        best = None
        for dj in range(-1, 2):
            for di in range(-1, 2):
                ii, jj = gi + di, gj + dj
                if 0 <= ii < a.hw and 0 <= jj < a.hh:
                    v = int(self.hght[a.hght + jj * a.hw + ii])
                    if v != 0xFFFF and (best is None or abs(di) + abs(dj) < best[0]):
                        best = (abs(di) + abs(dj), a.min_y + v / 100.0)
        return best[1] if best else a.min_y

    def areas_near(self, x, z):
        """Area ids of the 4 m bucket holding (x, z) in every sector that contains it, each bucket's lowest first."""
        out = []
        for s in self.sectors:
            _id, _layer, _pc, x0, z0, x1, z1, first_bucket, _fa, _na, _r = s
            if x0 <= x < x1 and z0 <= z < z1:
                bi = min(math.floor((x - x0) / BUCKET), BUCKETS - 1)
                bj = min(math.floor((z - z0) / BUCKET), BUCKETS - 1)
                first, count = self.gbkt[first_bucket + bj * BUCKETS + bi]
                out += [int(a) for a in self.glst[first:first + count]]
        return out

    def locate(self, x, y, z, max_dist=3.0, dy=None):
        """(area index, (i, j), horizontal distance) of the walkable cell nearest (x, z) whose area's ground is within
        dy of y (default: the step plus half a metre), searching outward up to max_dist; None when there is none.
        Areas in index order, cells by rows then columns, the first nearest kept; all in double precision with a fixed
        order of operations, so the C runtime (rsnav) gives the same answer."""
        dy = self.step + 0.5 if dy is None else dy
        best = None
        cand = set()
        r = math.ceil(max_dist / BUCKET)
        for bj in range(-r, r + 1):
            for bi in range(-r, r + 1):
                cand.update(self.areas_near(x + bi * BUCKET, z + bj * BUCKET))
        for k in sorted(cand):
            a = self.areas[k]
            if not (a.min_y - dy <= y <= a.max_y + dy):
                continue
            if abs(self.ground(a, x, z) - y) > dy:
                continue
            n = math.ceil(max_dist / a.pitch)
            ci, cj = math.floor((x - a.x0) / a.pitch + 0.5), math.floor((z - a.z0) / a.pitch + 0.5)
            for j in range(max(cj - n, 0), min(cj + n, a.height - 1) + 1):
                for i in range(max(ci - n, 0), min(ci + n, a.width - 1) + 1):
                    if self.walkable(a, i, j):
                        dx, dz = a.x0 + i * a.pitch - x, a.z0 + j * a.pitch - z
                        d = math.sqrt(dx * dx + dz * dz)
                        if d <= max_dist and (best is None or d < best[2]):
                            best = (k, (i, j), d)
        return best

    def nearest_node(self, k, x, z):
        """The node of area k nearest (x, z) horizontally (the first in ANOD order on a tie), or None."""
        a, best = self.areas[k], None
        for t in range(a.first_node, a.first_node + a.nodes):
            nd = int(self.anod[t])
            dx, dz = self.node_pos[nd][0] - x, self.node_pos[nd][2] - z
            d = dx * dx + dz * dz
            if best is None or d < best[1]:
                best = (nd, d)
        return best[0] if best else None

    # -- line of sight (rsnav's rsn_los, the same rule and order of operations)
    def _cell_of(self, a, x, z):
        i, j = math.floor((x - a.x0) / a.pitch + 0.5), math.floor((z - a.z0) / a.pitch + 0.5)
        return i, j, 0 <= i < a.width and 0 <= j < a.height

    @staticmethod
    def _lean(f):
        """-1, 0 or 1: the side a sample at fraction f (-0.5 .. 0.5) from its cell's centre leans to (LOS_GRAZE)."""
        return 0 if abs(f) < 0.5 - LOS_GRAZE else (1 if f > 0 else -1)

    def on_cell(self, a, x, z):
        """Whether (x, z) lies on area a's walkable ground: its nearest cell is walkable or, in a fine (0.2 m) area,
        it lies within LOS_GRAZE of the square of a walkable cell beside it (rsnav's raw_ok)."""
        u, v = (x - a.x0) / a.pitch, (z - a.z0) / a.pitch
        i, j = math.floor(u + 0.5), math.floor(v + 0.5)
        if self.walkable(a, i, j):
            return True
        if a.pitch_mm_ >= COARSE_MM:
            return False
        si, sj = self._lean(u - i), self._lean(v - j)
        return bool((si and self.walkable(a, i + si, j)) or (sj and self.walkable(a, i, j + sj))
                    or (si and sj and self.walkable(a, i + si, j + sj)))

    def _raw_ok(self, a, x, z):
        return self.on_cell(a, x, z)

    def _allowed(self, k1, k2):
        return LOS_COARSE_DY if self.areas[k1].pitch_mm_ >= COARSE_MM or self.areas[k2].pitch_mm_ >= COARSE_MM \
            else LOS_DY

    def _other_ok(self, skip, x, z, y):
        for k in self.areas_near(x, z):
            b = self.areas[k]
            if k == skip or not self._raw_ok(b, x, z):
                continue
            if abs(self.ground(b, x, z) - y) <= self._allowed(skip, k):
                return True
        return False

    def _cell_ok(self, k, x, z):
        a = self.areas[k]
        if a.pitch_mm_ < COARSE_MM:
            return self.on_cell(a, x, z)
        u, v = (x - a.x0) / a.pitch, (z - a.z0) / a.pitch
        i, j = math.floor(u + 0.5), math.floor(v + 0.5)
        if not self.walkable(a, i, j):
            return False
        y = self.ground(a, x, z)
        si, sj = self._lean(u - i), self._lean(v - j)
        for di, dj in ((si, 0), (0, sj)):
            if not (di or dj):
                continue
            ii, jj = i + di, j + dj
            if ii < 0 or jj < 0 or ii >= a.width or jj >= a.height or self.walkable(a, ii, jj):
                continue
            if not self._other_ok(k, a.x0 + ii * a.pitch, a.z0 + jj * a.pitch, y):
                return False
        return True

    def _compatible(self, f, t, x, z):
        d = self.ground(self.areas[t], x, z) - self.ground(self.areas[f], x, z)
        return abs(d) <= self._allowed(f, t) and self._cell_ok(t, x, z)

    def _neighbour(self, cur, x, z):
        a = self.areas[cur]
        for p in self.ports[a.first_portal:a.first_portal + a.portals]:
            if p[6] != cur and self._compatible(cur, p[6], x, z):
                return p[6]
        for k in self.areas_near(x, z):
            if k != cur and self._compatible(cur, k, x, z):
                return k
        return None

    def los(self, a, b):
        """Whether a soldier walks straight from a to b (x, y, z; pass float32 values for parity with rsnav): every
        sample, a quarter of the finest pitch apart, on a walkable cell (on_cell: in fine areas within LOS_GRAZE of
        one); between areas through portals or the buckets, to ground within LOS_DY (LOS_COARSE_DY where either area
        is coarse); the ground changing by at most that between samples; coarse cells needing their neighbours on the
        sides a sample leans to (LOS_GRAZE); gaps up to COARSE_GAP crossed in coarse zones; the first LOS_GRACE not
        tested. See rsnav's rsnav_los.c for the reasons."""
        loc = self.locate(a[0], a[1], a[2], max_dist=1.0)
        if loc is None:
            return False
        cur = loc[0]
        gy = self.ground(self.areas[cur], a[0], a[2])
        dx, dz = b[0] - a[0], b[2] - a[2]
        ln = math.sqrt(dx * dx + dz * dz)
        step = self.min_pitch / 4.0
        n = math.ceil(ln / step)
        if n > 200000:
            return False
        k = 0
        while k < n:
            k += 1
            t = k / n
            if ln * t < LOS_GRACE:
                continue
            x, z = a[0] + dx * t, a[2] + dz * t
            prev = cur
            if not self._cell_ok(cur, x, z):
                nb = self._neighbour(cur, x, z)
                if nb is None and self.areas[cur].pitch_mm_ >= COARSE_MM:
                    k2 = k + 1
                    while k2 <= n and ln * ((k2 - k) / n) <= COARSE_GAP:
                        t2 = k2 / n
                        x2, z2 = a[0] + dx * t2, a[2] + dz * t2
                        gap = ln * (t2 - t) + step
                        for c in self.areas_near(x2, z2):
                            d2 = self.ground(self.areas[c], x2, z2) - gy
                            if self._raw_ok(self.areas[c], x2, z2) and abs(d2) <= LOS_STEP + gap:
                                nb = c
                                break
                        if nb is not None:
                            k, t, x, z = k2, t2, x2, z2
                            break
                        k2 += 1
                    if nb is not None:
                        cur = nb
                        gy = self.ground(self.areas[cur], x, z)
                        continue
                if nb is None:
                    return False
                cur = nb
            ny = self.ground(self.areas[cur], x, z)
            lim = LOS_DY if cur == prev else self._allowed(prev, cur)
            if ln * t >= LOS_GRACE + step and abs(ny - gy) > lim:
                return False
            gy = ny
        d = self.ground(self.areas[cur], b[0], b[2]) - b[1]
        return abs(d) <= (LOS_COARSE_DY if self.areas[cur].pitch_mm_ >= COARSE_MM else LOS_DY)

    def validate(self):
        """Problems with the file's internal consistency ([] when none)."""
        out = []
        n = len(self.node_pos)
        if self.node_first_edge[-1] != len(self.edge_target):
            out.append('NODE sentinel does not end at the edge count')
        if np.any(np.diff(self.node_first_edge) < 0):
            out.append('NODE first edges not ascending')
        if len(self.edge_target) and (self.edge_target.max() >= n):
            out.append('EDGE target out of range')
        for a in self.areas:
            row = (a.width + 7) >> 3
            if a.bmap + row * a.height > len(self.bmap):
                out.append(f'AREA {a.index}: bitmap past BMAP')
            if a.hght + a.hw * a.hh > len(self.hght):
                out.append(f'AREA {a.index}: height grid past HGHT')
            if a.first_node + a.nodes > len(self.anod):
                out.append(f'AREA {a.index}: nodes past ANOD')
        if len(self.glst) and int(self.glst.max()) >= len(self.areas):
            out.append('GLST area id out of range')
        # node indices of the other sections, as rsnav's loader checks them (rsn_load refuses the file otherwise)
        bad = sum(1 for L in self.ladders if L[7] >= n or L[8] >= n)
        if bad:
            out.append(f'LADR node out of range ({bad})')
        bad = sum(1 for o in self.offmesh if o[3] != 0xFFFFFFFF and o[3] >= n)
        if bad:
            out.append(f'OFFM node out of range ({bad})')
        bad = sum(1 for i in self.islands if i[1] >= n)
        if bad:
            out.append(f'ISLD representative out of range ({bad})')
        for t, f in ((self.node_area, 'NAUX area'),):
            bad = (t != 0xFFFF) & (t >= len(self.areas))
            if bad.any():
                out.append(f'{f} out of range ({int(bad.sum())})')
        return out


def _array(raw, e, off, count, fmt):
    st = struct.Struct(e + fmt)
    return [st.unpack_from(raw, off + k * st.size) for k in range(count)]


def load(src):
    raw = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    if raw[:4] != MAGIC:
        raise ValueError('not an RSNV file')
    e = '<' if struct.unpack_from('<I', raw, 4)[0] == 0x01020304 else '>'
    if struct.unpack_from(e + 'I', raw, 4)[0] != 0x01020304:
        raise ValueError('RSNV: bad byte-order marker')
    version, flags, size, nsec, level_id = struct.unpack_from(e + 'HHIII', raw, 8)
    if version != VERSION:
        raise ValueError(f'RSNV version {version}, this reader reads {VERSION}')
    if size != len(raw):
        raise ValueError(f'RSNV: header says {size} bytes, the file has {len(raw)}')
    sea, radius, height, step, water = struct.unpack_from(e + '5f', raw, 0x18)
    w = World(level_id, e == '>', sea, radius, height, step, water, raw=bytes(raw))
    for k in range(nsec):
        tag = raw[HEADER + 16 * k:HEADER + 16 * k + 4].decode('ascii')
        off, count, stride = struct.unpack_from(e + 'III', raw, HEADER + 16 * k + 4)
        if off + count * stride > len(raw) or off % 16:
            raise ValueError(f'RSNV: section {tag} outside the file or misaligned')
        if tag in SCHEMA and stride != struct.calcsize('<' + SCHEMA[tag]):
            raise ValueError(f'RSNV: section {tag} stride {stride}')
        w.sections[tag] = (off, count, stride)
    for need in ('SECT', 'NODE', 'NAUX', 'EDGE', 'AREA', 'BMAP', 'HGHT', 'GBKT', 'GLST', 'ANOD'):
        if need not in w.sections:
            raise ValueError(f'RSNV: no {need} section')
    sec = lambda t: w.sections.get(t, (0, 0, 0))
    o, c, _ = sec('NODE')
    nodes = np.frombuffer(raw, np.dtype([('p', e + 'f4', 3), ('e', e + 'u4')]), c, o)
    w.node_pos = nodes['p'][:-1].astype(np.float64)
    w.node_first_edge = nodes['e'].astype(np.int64)
    o, c, _ = sec('NAUX')
    naux = np.frombuffer(raw, np.dtype(e + 'u2'), 4 * c, o).reshape(c, 4).astype(np.int64)
    w.node_area, w.node_sector, w.node_island, w.node_flags = naux.T
    o, c, _ = sec('EDGE')
    edges = np.frombuffer(raw, np.dtype([('t', e + 'u4'), ('c', e + 'u2'), ('f', e + 'u2')]), c, o)
    w.edge_target = (edges['t'] & 0xFFFFFF).astype(np.int64)
    w.edge_kind = (edges['t'] >> 24).astype(np.int64)
    w.edge_cost = edges['c'].astype(np.int64)
    w.edge_flags = edges['f'].astype(np.int64)
    w.sectors = _array(raw, e, *sec('SECT')[:2], SCHEMA['SECT'])
    for k, r in enumerate(_array(raw, e, *sec('AREA')[:2], SCHEMA['AREA'])):
        (x0, z0, x1, z1, mny, mxy, bmap, bw, bh, fp, npo, sector, island, flags, fnode, nnode, pmm, hg, hw, hh, _z) = r
        w.areas.append(Area(k, x0, z0, x1, z1, mny, mxy, bmap, bw, bh, fp, npo, sector, island, flags, fnode, nnode,
                            pmm / 1000.0, hg, hw, hh, _z))
    o, c, _ = sec('BMAP')
    w.bmap = raw[o:o + c]
    o, c, _ = sec('HGHT')
    w.hght = np.frombuffer(raw, np.dtype(e + 'u2'), c, o)
    o, c, _ = sec('GBKT')
    w.gbkt = np.frombuffer(raw, np.dtype(e + 'u4'), 2 * c, o).reshape(c, 2).astype(np.int64)
    o, c, _ = sec('GLST')
    w.glst = np.frombuffer(raw, np.dtype(e + 'u2'), c, o)
    o, c, _ = sec('ANOD')
    w.anod = np.frombuffer(raw, np.dtype(e + 'u4'), c, o)
    w.ladders = _array(raw, e, *sec('LADR')[:2], SCHEMA['LADR']) if 'LADR' in w.sections else []
    w.offmesh = _array(raw, e, *sec('OFFM')[:2], SCHEMA['OFFM']) if 'OFFM' in w.sections else []
    w.islands = _array(raw, e, *sec('ISLD')[:2], SCHEMA['ISLD']) if 'ISLD' in w.sections else []
    w.ports = _array(raw, e, *sec('PORT')[:2], SCHEMA['PORT']) if 'PORT' in w.sections else []
    pm = min((a.pitch_mm_ for a in w.areas), default=200)
    w.min_pitch = float(np.float32(np.float32(pm) / np.float32(1000.0)))      # as rsnav computes it, in float
    w.objects = _array(raw, e, *sec('OBJT')[:2], SCHEMA['OBJT']) if 'OBJT' in w.sections else []
    w.cond = {}                                     # edge -> [object indices]
    if 'CEDG' in w.sections:
        o, c, _ = sec('OREF')
        oref = list(np.frombuffer(raw, np.dtype(e + 'u4'), c, o)) if c else []
        for edge, first, count, _r in _array(raw, e, *sec('CEDG')[:2], SCHEMA['CEDG']):
            w.cond[int(edge)] = [int(x) for x in oref[first:first + count]]
    if 'META' in w.sections:
        o, c, _ = sec('META')
        w.meta = json.loads(raw[o:o + c].decode('utf-8'))
    return w


DONE, PARTIAL = 'done', 'partial'


def edge_cost(w, n, k, water_scale=(256, 256, 256), kind_extra=None, penalty=None, destroyed=None, tight_extra=0):
    """The filter's cost of edge k from node n (rsnav's rsn_edge_cost), or None when it is closed: floor(cost x
    water_scale[class] / 256) + kind_extra[kind] + tight_extra (a tight edge) + penalty, class the deeper end's (0 dry,
    1 shallow, 2 wading); a
    conditional edge is closed unless all its objects are in `destroyed`."""
    if int(w.edge_flags[k]) & EDGEF_COND and not (destroyed is not None and all(o in destroyed for o in w.cond.get(k, [None]))):
        return None

    def cls(m):
        f = int(w.node_flags[m])
        return 2 if f & NAUX_WADE else 1 if f & NAUX_SHALLOW else 0
    c = int(w.edge_cost[k]) * water_scale[max(cls(n), cls(int(w.edge_target[k])))] >> 8
    if kind_extra:
        c += kind_extra[int(w.edge_kind[k])]
    if tight_extra and int(w.edge_flags[k]) & EDGEF_TIGHT:
        c += tight_extra
    if penalty:
        c += penalty.get(k, 0)
    return c


def search(w, start, goal, max_expansions=1 << 30, kind_mask=None, water_scale=(256, 256, 256), kind_extra=None,
           penalty=None, destroyed=None, tight_extra=0):
    """The reference search (design sections 2.6 and 6.3), which the C runtime (rsnav) must match exactly: integer
    costs in 1/16 m; heuristic floor(16 x Euclidean x 0.999) in double precision, summed x, y, then z; the open list
    ordered by (f, node); a closed node is never reopened; an equal-cost parent is replaced by a lower-indexed one.
    The goal reached when popped is DONE; otherwise, once max_expansions nodes are expanded or the open list runs out,
    PARTIAL: the path to the expanded node with the least heuristic (then cost, then index), or [start] if none.
    Edge costs are edge_cost()'s (water_scale, kind_extra, penalty: {edge: extra}, destroyed: object indices).
    Returns (status, cost, [nodes], expansions)."""
    import heapq
    pos = w.node_pos
    fe, tgt, cost, kind = w.node_first_edge, w.edge_target, w.edge_cost, w.edge_kind
    gx, gy, gz = (float(v) for v in pos[goal])

    def h(n):
        dx, dy, dz = float(pos[n][0]) - gx, float(pos[n][1]) - gy, float(pos[n][2]) - gz
        return math.floor(16.0 * math.sqrt((dx * dx + dy * dy) + dz * dz) * 0.999)
    g = {start: 0}
    hv = {start: h(start)}
    parent = {start: -1}
    heap = [(hv[start], start)]
    closed = set()
    exp = 0
    best = None

    def path_to(n):
        p = [n]
        while parent[p[-1]] != -1:
            p.append(parent[p[-1]])
        return p[::-1]
    while heap:
        f, n = heap[0]
        if n in closed:
            heapq.heappop(heap)
            continue
        if n == goal:
            return DONE, g[n], path_to(n), exp
        if exp >= max_expansions:
            break
        heapq.heappop(heap)
        closed.add(n)
        exp += 1
        if best is None or (hv[n], g[n], n) < best:
            best = (hv[n], g[n], n)
        for k in range(fe[n], fe[n + 1]):
            if kind_mask is not None and not (kind_mask >> int(kind[k]) & 1):
                continue
            m = int(tgt[k])
            if m in closed:
                continue
            c = edge_cost(w, n, k, water_scale, kind_extra, penalty, destroyed, tight_extra)
            if c is None:
                continue
            ng = g[n] + c
            if m not in g or ng < g[m] or (ng == g[m] and n < parent[m]):
                if m not in hv:
                    hv[m] = h(m)
                if m not in g or ng < g[m]:
                    heapq.heappush(heap, (ng + hv[m], m))
                g[m] = ng
                parent[m] = n
    end = best[2] if best else start
    return PARTIAL, g[end], path_to(end), exp


def astar(w, start, goal, max_expansions=1 << 30, kind_mask=None):
    """search() for a complete path: (cost, [nodes], expansions), or (None, [], expansions) when the goal is not
    reached."""
    status, c, path, exp = search(w, start, goal, max_expansions, kind_mask)
    return (c, path, exp) if status == DONE else (None, [], exp)
