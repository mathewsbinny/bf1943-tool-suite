"""hpdtool.py - reader/writer for BFBC2 Kynapse navigation data (pathdata.hpd).

Part of BC2 NavKit (see ..\\README.md; written from ..\\research\\KYNAPSE_SPEC.md).

Usage:
    python hpdtool.py info <pathdata.hpd>                 file summary
    python hpdtool.py sector <pathdata.hpd> <index>       one sector: nodes, edges, mesh header
    python hpdtool.py roundtrip <file-or-folder>...       parse + write back, compare byte for byte
    python hpdtool.py coverage <file-or-folder>...        check every byte of every runtime mesh is explained
    python hpdtool.py meshtrip <file-or-folder>...        structured mesh model (kymesh.py) round trip

Sector.mesh holds the raw runtime-mesh record; kymesh.parse_mesh / write_mesh convert it to and
from the structured model (areas, portals, outlines, cells, neighbour links).
"""
import os
import struct
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()

MAGIC = b'Kynogon HPD\0'
HEADER_WORDS = 36           # u32 words after the magic (0x0C..0x9B)
HEADER_SIZE = 12 + HEADER_WORDS * 4
TABLE_ENTRY = struct.Struct('<I6f5I')  # 48 bytes
NODE = struct.Struct('<I3fIf3I')       # 36 bytes
EDGE = struct.Struct('<3I')            # 12 bytes
MESH_MARKER = b'KynogonRuntimeMesh'


@dataclass
class Node:
    id: int                  # node id; high bit = border node shared with a neighbouring sector
                             # (same id and position appear in that sector too)
    pos: tuple[float, float, float]  # world position, Y up
    flags: int               # always 0x80000000
    value: float             # DistEdgeMax on the first node of a sector (infantry 15, vehicle 50), else 0
    a: int                   # index of this node's first outgoing edge + 1 (edges are sorted by source)
    b: int                   # low u16: entry of the node list sorted by x; high u16: sorted by y
    c: int                   # low u16: entry of the node list sorted by z; bit 16: border node


@dataclass
class Edge:
    tag: int                 # cost: 0x80000000 | floor(3-D distance between the nodes * 16)
    src: int
    dst: int


@dataclass
class Sector:
    id: int                  # grid coordinates, 0xXXYY
    bounds: tuple[float, ...]  # minX, maxX, minY, maxY, minZ, maxZ
    reserved: int            # always 0 so far
    date: int                # build date YYMMDD, zero-based month (100007 = 2010-01-07)
    time: int                # build time HHMMSScc
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    mesh: bytes = b''        # runtime mesh record (starts with "KynogonRuntimeMesh"), raw for now


@dataclass
class HpdFile:
    header: tuple[int, ...]  # the 36 header words (word 2 = sector count, rewritten on save)
    sectors: list[Sector]


def parse(data: bytes) -> HpdFile:
    """The file's header and sectors. Raises ValueError on a file that is not exactly what write() makes of them:
    a truncated file, blocks that overlap, leave gaps or run past the end, or a sector mesh without its trailer."""
    if data[:12] != MAGIC or len(data) < HEADER_SIZE:
        raise ValueError('not a Kynogon HPD file')
    header = struct.unpack_from(f'<{HEADER_WORDS}I', data, 12)
    count = header[2]
    blocks = HEADER_SIZE + count * TABLE_ENTRY.size
    if blocks > len(data):
        raise ValueError(f'truncated: {count} sector entries need {blocks} bytes, the file has {len(data)}')
    sectors: list[Sector] = []
    running = 0
    for i in range(count):
        entry = TABLE_ENTRY.unpack_from(data, HEADER_SIZE + i * TABLE_ENTRY.size)
        sid, bounds, reserved = entry[0], entry[1:7], entry[7]
        size, offset, date, time = entry[8], entry[9], entry[10], entry[11]
        if offset != running:
            raise ValueError(f'sector {i} ({sid:#x}): block at offset {offset}, expected {running} '
                             f'(blocks in table order)')
        start = blocks + offset
        if size < 8 or start + size > len(data):
            raise ValueError(f'sector {i} ({sid:#x}): block of {size} bytes at {start} runs past the end '
                             f'({len(data)} bytes)')
        running += size
        block = data[start:start + size]
        n_nodes, n_edges = struct.unpack_from('<2I', block, 0)
        if 8 + n_nodes * NODE.size + n_edges * EDGE.size > size:
            raise ValueError(f'sector {i} ({sid:#x}): {n_nodes} nodes and {n_edges} edges do not fit its '
                             f'{size}-byte block')
        pos = 8
        nodes: list[Node] = []
        for _ in range(n_nodes):
            values = NODE.unpack_from(block, pos)
            nodes.append(Node(values[0], (values[1], values[2], values[3]), values[4], values[5], values[6],
                              values[7], values[8]))
            pos += NODE.size
        edges: list[Edge] = []
        for _ in range(n_edges):
            edges.append(Edge(*EDGE.unpack_from(block, pos)))
            pos += EDGE.size
        mesh = block[pos:]
        if not mesh.startswith(MESH_MARKER):
            raise ValueError(f'sector {i} ({sid:#x}): runtime mesh not where expected')
        # the mesh ends with its trailer: its own offset, then 0 (mesh_layout)
        if len(mesh) < len(MESH_MARKER) + 8 or \
                struct.unpack_from('<2I', mesh, len(mesh) - 8) != (len(mesh) - 8, 0):
            raise ValueError(f'sector {i} ({sid:#x}): runtime mesh has no trailer (truncated?)')
        sectors.append(Sector(sid, bounds, reserved, date, time, nodes, edges, mesh))
    if blocks + sum(len(_block(sector)) for sector in sectors) != len(data):
        raise ValueError('sector blocks do not cover the file')
    return HpdFile(header, sectors)


MESH_OBJECT_HEADER = 0x50


def mesh_layout(rec: bytes) -> tuple[list[tuple[int, int, str]], list[tuple[int, list[tuple[Any, ...]], int | None]],
                                   dict[int, int], list[tuple[int, int, int]], list[int]]:
    """Walk a runtime mesh record (offsets relative to its start) and return a list of
    (start, end, label) regions plus the parsed structure. Raises ValueError on inconsistencies."""
    u32 = lambda at: _at('<I', rec, at)[0]
    regions: list[tuple[int, int, str]] = [(0, 0x50, 'header')]
    nx, nz = u32(0x38), u32(0x3C)
    tiles_off, links_n, links_off = u32(0x44), u32(0x48), u32(0x4C)
    if tiles_off != 0x50:
        raise ValueError(f'tile table at {tiles_off:#x}')
    tile_count = nx * nz
    if 0x50 + 4 * (tile_count + 1) > len(rec):
        raise ValueError(f'a tile table of {nx} x {nz} tiles runs past the end of the mesh')
    regions.append((0x50, 0x50 + (tile_count + 1) * 4, 'tile table'))
    tile_offs = [u32(0x50 + i * 4) for i in range(tile_count + 1)]
    objects: dict[int, int] = {}
    tiles: list[tuple[int, list[tuple[Any, ...]], int | None]] = []
    for tile in range(tile_count):
        tile_at = tile_offs[tile]
        count = u32(tile_at)
        if tile_at + 4 + 12 * count > len(rec):
            raise ValueError(f'tile {tile}: {count} items run past the end of the mesh')
        items = [_at('<Iff', rec, tile_at + 4 + k * 12) for k in range(count)]
        # An empty tile is just its count; otherwise items are followed by an end offset.
        end_ptr = u32(tile_at + 4 + count * 12) if count else None
        regions.append((tile_at, tile_at + (8 + count * 12 if count else 4), f'tile {tile}'))
        for ptr, _, _ in items:
            if ptr not in objects:
                objects[ptr] = _object_end(rec, ptr)
                regions.append((ptr, objects[ptr], f'object @{ptr:#x}'))
        tiles.append((tile_at, items, end_ptr))
    # The extra (last) tile-table entry points at a 4-byte slot the loader never reads; DICE's tool
    # left uninitialised memory there (preserved for exact round trips, zero when generating).
    regions.append((tile_offs[tile_count], tile_offs[tile_count] + 4, 'tile table end slot'))
    # Trailer: the last 8 bytes hold their own offset and 0.
    if u32(len(rec) - 8) != len(rec) - 8 or u32(len(rec) - 4) != 0:
        raise ValueError('mesh trailer not found')
    regions.append((len(rec) - 8, len(rec), 'trailer'))
    links: list[tuple[int, int, int]] = []
    if links_n:
        if links_off + 12 * links_n > len(rec):
            raise ValueError(f'{links_n} neighbour links run past the end of the mesh')
        regions.append((links_off, links_off + links_n * 12, 'link table'))
        for k in range(links_n):
            sid, object_count, list_at = _at('<3I', rec, links_off + k * 12)
            if list_at + 4 * object_count > len(rec):
                raise ValueError(f'link {sid:#x}: a list of {object_count} runs past the end of the mesh')
            regions.append((list_at, list_at + object_count * 4, f'link list {sid:#x}'))
            for j in range(object_count):
                object_at = u32(list_at + j * 4)
                ref_count = u32(object_at + 0xC)
                regions.append((object_at, object_at + 0x10 + ref_count * 4, f'link {sid:#x}/{j}'))
            links.append((sid, object_count, list_at))
    return regions, tiles, objects, links, tile_offs


def _at(fmt: str, rec: bytes, at: int) -> tuple[Any, ...]:
    """struct.unpack_from(fmt, rec, at); ValueError when it runs past the mesh (2026-10-31 fuzzing: struct.error, and
    a negative offset read from the end)."""
    size = struct.calcsize(fmt)
    if at < 0 or at + size > len(rec):
        raise ValueError(f'damaged runtime mesh: {size} bytes at {at:#x} lie outside the {len(rec)}-byte record')
    return struct.unpack_from(fmt, rec, at)


def _object_end(rec: bytes, start: int) -> int:
    """Where the area object at `start` ends: its header, A / B / C records, then its point-list and cell tables."""
    u32 = lambda at: _at('<I', rec, at)[0]
    a_count, b_count, c_count = u32(start + 0x20), u32(start + 0x28), u32(start + 0x30)
    end = start + MESH_OBJECT_HEADER + a_count * 0x20 + b_count * 0x20 + c_count * 0x2C
    for count_off, table_off, stride in ((0x38, 0x3C, 4), (0x40, 0x44, 4), (0x48, 0x4C, 16)):
        count = u32(start + count_off)
        if not count:
            continue
        table = start + u32(start + table_off)
        if table != end:
            raise ValueError(f'object {start:#x}: table {count_off:#x} at {table:#x}, expected {end:#x}')
        first = u32(table)
        last = u32(table + count * stride)
        end = table + (count + 1) * stride + ((last - first) & ~3)
    return end


def mesh_coverage(rec: bytes) -> tuple[list[tuple[int, int]], list[tuple[int, int, str]]]:
    """(gaps, overlaps) of the regions mesh_layout explains in the record: (start, end) and (start, end, label)."""
    regions, *_ = mesh_layout(rec)
    regions.sort()
    gaps, overlaps, pos = [], [], 0
    for start, end, label in regions:
        if start > pos:
            gaps.append((pos, start))
        elif start < pos:
            overlaps.append((start, pos, label))
        pos = max(pos, end)
    if pos < len(rec):
        gaps.append((pos, len(rec)))
    return gaps, overlaps


def _block(sector: Sector) -> bytes:
    out = bytearray(struct.pack('<2I', len(sector.nodes), len(sector.edges)))
    for node in sector.nodes:
        out += NODE.pack(node.id, *node.pos, node.flags, node.value, node.a, node.b, node.c)
    for edge in sector.edges:
        out += EDGE.pack(edge.tag, edge.src, edge.dst)
    out += sector.mesh
    return bytes(out)


def write(hpd: HpdFile) -> bytes:
    """The file's bytes (the header's sector count rewritten)."""
    header = list(hpd.header)
    header[2] = len(hpd.sectors)
    out = bytearray(MAGIC + struct.pack(f'<{HEADER_WORDS}I', *header))
    blocks = [_block(sector) for sector in hpd.sectors]
    offset = 0
    for sector, block in zip(hpd.sectors, blocks):
        out += TABLE_ENTRY.pack(sector.id, *sector.bounds, sector.reserved, len(block), offset, sector.date,
                                sector.time)
        offset += len(block)
    for block in blocks:
        out += block
    return bytes(out)


def _files(args: Iterable[str]) -> Iterator[str]:
    """The files named, and every pathdata.hpd under the folders named."""
    for arg in args:
        if os.path.isdir(arg):
            for dirpath, _, filenames in os.walk(arg):
                for filename in filenames:
                    if filename.lower() == 'pathdata.hpd':
                        yield os.path.join(dirpath, filename)
        else:
            yield arg


def _last_tile_entry(mesh: bytes) -> int:
    """The runtime mesh's last tile-table entry (the end slot's offset)."""
    tiles_x = struct.unpack_from("<I", mesh, 0x38)[0]
    tiles_z = struct.unpack_from("<I", mesh, 0x3C)[0]
    return struct.unpack_from("<I", mesh, 0x50 + tiles_x * tiles_z * 4)[0]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == 'info':
        hpd = parse(open(argv[2], 'rb').read())
        nodes = sum(len(sector.nodes) for sector in hpd.sectors)
        edges = sum(len(sector.edges) for sector in hpd.sectors)
        mesh = sum(len(sector.mesh) for sector in hpd.sectors)
        dates = sorted({sector.date for sector in hpd.sectors})
        print(f'{len(hpd.sectors)} sectors, {nodes} graph nodes, {edges} edges, {mesh} bytes of runtime mesh; '
              + (f'build dates {dates[0]}..{dates[-1]}' if dates else 'no build dates (no sectors)'))
        return 0
    if cmd == 'sector':
        hpd = parse(open(argv[2], 'rb').read())
        sector = hpd.sectors[int(argv[3])]
        print(f'sector {sector.id:#06x} bounds {[round(b, 2) for b in sector.bounds]} built {sector.date} '
              f'{sector.time:08d}')
        for node in sector.nodes:
            print(f'  node {node.id:#010x} pos ({node.pos[0]:.2f}, {node.pos[1]:.2f}, {node.pos[2]:.2f}) '
                  f'flags {node.flags:#x} value {node.value:g} a {node.a} b {node.b & 0xFFFF},{node.b >> 16} '
                  f'c {node.c}')
        for edge in sector.edges:
            print(f'  edge {edge.tag:#010x} {edge.src} -> {edge.dst}')
        print(f'  mesh {len(sector.mesh)} bytes: {sector.mesh[:48].hex(" ")}')
        return 0
    if cmd == 'coverage':
        total = clean = 0
        gap_kinds = {}
        for path in _files(argv[2:]):
            hpd = parse(open(path, 'rb').read())
            for sector in hpd.sectors:
                total += 1
                try:
                    gaps, overlaps = mesh_coverage(sector.mesh)
                except (ValueError, struct.error) as ex:
                    print(f'ERROR {path} sector {sector.id:#x}: {ex}')
                    continue
                if not gaps and not overlaps:
                    clean += 1
                else:
                    key = (tuple((end - start) for start, end in gaps[:3]), len(overlaps))
                    gap_kinds[key] = gap_kinds.get(key, 0) + 1
                    if gap_kinds[key] <= 2:
                        desc = [f'{start:#x}:{sector.mesh[start:end].hex(" ")}' for start, end in gaps[:4]]
                        level_folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
                        print(f'{level_folder}/{os.path.basename(os.path.dirname(path))} '
                              f'sector {sector.id:#x} len {len(sector.mesh):#x} last-tile-entry '
                              f'{_last_tile_entry(sector.mesh):#x}: '
                              f'gaps {desc} overlaps {overlaps[:2]}')
        print(f'coverage: {clean}/{total} meshes fully explained; gap patterns {len(gap_kinds)}')
        return 0
    if cmd == 'meshtrip':
        import kymesh
        total = same = 0
        reasons = {}
        for path in _files(argv[2:]):
            for sector in parse(open(path, 'rb').read()).sectors:
                total += 1
                try:
                    out = kymesh.write_mesh(kymesh.parse_mesh(sector.mesh))
                except Exception as ex:
                    reason = type(ex).__name__ + ': ' + str(ex)[:60]
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
                if out == sector.mesh:
                    same += 1
                else:
                    common = min(len(out), len(sector.mesh))
                    first = next((i for i in range(common) if out[i] != sector.mesh[i]), common)
                    word = first // 4 * 4
                    key = f'len {len(out) - len(sector.mesh):+d}, first diff at word {word:#x}'
                    if not reasons:
                        print(f'first mismatch: {path} sector {sector.id:#x}: {key}; '
                              f'orig {sector.mesh[word:word + 16].hex(" ")} new {out[word:word + 16].hex(" ")}')
                    reasons[key] = reasons.get(key, 0) + 1
        print(f'mesh round trip: {same}/{total} identical')
        for reason, count in sorted(reasons.items(), key=lambda item: -item[1])[:8]:
            print(f'  {count:6} x {reason}')
        return 0 if same == total else 2
    if cmd == 'roundtrip':
        ok = bad = 0
        for path in _files(argv[2:]):
            data = open(path, 'rb').read()
            try:
                same = write(parse(data)) == data
            except (ValueError, struct.error, IndexError) as ex:      # one damaged file must not end the batch
                same = False
                print(f'ERROR {path}: {ex}')
            ok += same
            bad += not same
            if not same:
                print(f'MISMATCH {path}')
        print(f'round trip: {ok} identical, {bad} different')
        return 0 if bad == 0 else 2
    print(__doc__)
    return 1


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('hpdtool', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
