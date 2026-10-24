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
  EDGE 8   u32 target (bits 0-23) | kind (bits 24-31, EDGE_*), u16 cost (1/16 m), u16 flags
  AREA 64  f32 x0, z0 (first bitmap cell centre), x1, z1 (last), minY, maxY; u32 BMAP byte offset; u16 bitmap width,
           height; u32 first PORT, u16 portal count, u16 sector; u16 island, u16 flags; u32 first node (in AREA order,
           ANOD), u16 node count, u16 pitch in mm; u32 HGHT offset (entries), u16 height grid width, height; u32 0
  PORT 24  f32 x0, z0, x1, z1, y, u16 from area, u16 to area (both directions stored)
  BMAP 1   per area, rows of ceil(width / 8) bytes, bit (i & 7) of byte i >> 3; 1 = walkable cell of the area
  HGHT 2   per area, u16 cm of ground height above the area's minY on a 4 m grid from (x0, z0); 0xFFFF no cell
  GBKT 8   u32 first GLST entry, u32 count
  GLST 2   u16 area ids, each bucket's sorted by minY
  ANOD 4   u32 node ids, grouped by area
  LADR 40  f32 foot x, y, z, top x, y, z, yaw, u32 foot node, u32 top node, u32 aux = kind (bits 24-31: 0 ladder,
           1 jump) | source index
  OFFM 32  u8 kind (OFFM_*), u8 team mask, u16 vehicle class, u32 node, f32 x, y, z, yaw, radius, u32 aux
  ISLD 16  u32 nodes, u32 representative node, u16 flags (ISLD_*), u16 transfer count, u32 first transfer
  META 1   UTF-8 JSON: exporter version, sources and their hashes, NavKit settings
Deviations from the design's table: AREA is 64 B (its 48 B does not hold the listed fields), GRID is GBKT + GLST,
ANOD lists nodes by area, LADR carries jump links too (kind), COND / CEDG / OBJT come with destruction (N6).
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
OFFM_BOARD_VEHICLE, OFFM_MOUNT_STATIC = 1, 5
ISLD_MAIN, ISLD_CARRIER, ISLD_LADDER_GROUND = 1, 2, 4
PITCHES = {0: 0.2, 1: 0.8}
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
}
for _t, _f in SCHEMA.items():
    assert struct.calcsize('<' + _f) == {'SECT': 32, 'NODE': 16, 'NAUX': 8, 'EDGE': 8, 'AREA': 64, 'PORT': 24, 'HGHT': 2,
                                         'GBKT': 8, 'GLST': 2, 'ANOD': 4, 'LADR': 40, 'OFFM': 32, 'ISLD': 16}[_t], _t


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
                         'OFFM', 'ISLD', 'META') if t in sections]
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
        """The area's ground height near (x, z) from its 4 m height grid (the nearest filled entry)."""
        gi = min(max(int((x - a.x0) // BUCKET), 0), a.hw - 1)
        gj = min(max(int((z - a.z0) // BUCKET), 0), a.hh - 1)
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
        """Area ids of the 4 m bucket holding (x, z), lowest first."""
        for si, s in enumerate(self.sectors):
            _id, _layer, _pc, x0, z0, x1, z1, first_bucket, _fa, _na, _r = s
            if x0 <= x < x1 and z0 <= z < z1:
                bi = min(int((x - x0) // BUCKET), BUCKETS - 1)
                bj = min(int((z - z0) // BUCKET), BUCKETS - 1)
                first, count = self.gbkt[first_bucket + bj * BUCKETS + bi]
                return [int(a) for a in self.glst[first:first + count]]
        return []

    def locate(self, x, y, z, max_dist=3.0, dy=None):
        """(area index, (i, j), horizontal distance) of the walkable cell nearest (x, z) whose area's ground is within
        dy of y (default: the step plus half a metre), searching outward up to max_dist; None when there is none."""
        dy = self.step + 0.5 if dy is None else dy
        best = None
        cand = set()
        r = int(math.ceil(max_dist / BUCKET))
        for bj in range(-r, r + 1):
            for bi in range(-r, r + 1):
                cand.update(self.areas_near(x + bi * BUCKET, z + bj * BUCKET))
        for k in cand:
            a = self.areas[k]
            if not (a.min_y - dy <= y <= a.max_y + dy):
                continue
            if abs(self.ground(a, x, z) - y) > dy:
                continue
            n = int(math.ceil(max_dist / a.pitch))
            ci, cj = int(round((x - a.x0) / a.pitch)), int(round((z - a.z0) / a.pitch))
            for j in range(max(cj - n, 0), min(cj + n, a.height - 1) + 1):
                for i in range(max(ci - n, 0), min(ci + n, a.width - 1) + 1):
                    if self.walkable(a, i, j):
                        d = math.hypot(a.x0 + i * a.pitch - x, a.z0 + j * a.pitch - z)
                        if d <= max_dist and (best is None or d < best[2]):
                            best = (k, (i, j), d)
        return best

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
                            pmm / 1000.0, hg, hw, hh))
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
    if 'META' in w.sections:
        o, c, _ = sec('META')
        w.meta = json.loads(raw[o:o + c].decode('utf-8'))
    return w


def astar(w, start, goal, max_expansions=1 << 30, kind_mask=None):
    """The Python reference search (design section 6.3): integer costs in 1/16 m, heuristic floor(16 x Euclidean) x
    0.999, ties broken by node index. Returns (cost, [nodes], expansions) or (None, [], expansions)."""
    import heapq
    pos = w.node_pos
    fe, tgt, cost, kind = w.node_first_edge, w.edge_target, w.edge_cost, w.edge_kind
    gx = pos[goal]

    def h(n):
        d = pos[n] - gx
        return int(math.floor(16.0 * math.sqrt(float(d @ d)) * 0.999))
    g = {start: 0}
    parent = {start: -1}
    heap = [(h(start), start)]
    closed = set()
    exp = 0
    while heap:
        f, n = heapq.heappop(heap)
        if n in closed:
            continue
        if n == goal:
            path = [n]
            while parent[path[-1]] != -1:
                path.append(parent[path[-1]])
            return g[n], path[::-1], exp
        closed.add(n)
        exp += 1
        if exp > max_expansions:
            break
        for k in range(fe[n], fe[n + 1]):
            if kind_mask is not None and not (kind_mask >> int(kind[k]) & 1):
                continue
            m = int(tgt[k])
            ng = g[n] + int(cost[k])
            if m not in g or ng < g[m] or (ng == g[m] and n < parent[m]):
                g[m] = ng
                parent[m] = n
                heapq.heappush(heap, (ng + h(m), m))
    return None, [], exp
