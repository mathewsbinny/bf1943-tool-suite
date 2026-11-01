"""kymesh.py - structured model of the "KynogonRuntimeMesh" record inside pathdata.hpd sector blocks.

Part of BC2 NavKit; layout as documented in ..\\research\\KYNAPSE_SPEC.md section 4.4/4.5.
parse_mesh(bytes) -> Mesh, write_mesh(Mesh) -> bytes. All offsets in the record are relative to its
start; parse_mesh replaces them with references (object indices etc.) and write_mesh lays the record
out again and recomputes every offset.
"""
import bisect
import struct
from dataclasses import dataclass, field
from typing import Any, Tuple, cast

NULL = 0xFFFFFFFF
MARKER = b'KynogonRuntimeMesh\0\0'
Vec3 = Tuple[float, float, float]


@dataclass
class Portal:                 # B: portal to another area of the same sector
    p0: Vec3
    p1: Vec3
    target: int               # index of the target area (Mesh.areas)


@dataclass
class BorderPortal:           # C: portal on the sector border
    p0: Vec3
    p1: Vec3
    direction: Vec3           # unit vector out of the sector (or "up")
    runtime: tuple[int, int] = (NULL, NULL)   # filled by the game at runtime; NULL in files


@dataclass
class Cell:                   # F: special pitch-sized cell (ledge / height break)
    direction: Vec3
    points: list[Vec3]


@dataclass
class Area:                   # one walkable area inside one tile
    sector_id: int
    tile_x: int
    tile_z: int
    layer: int
    radius: float
    pitch: float
    max_y: float
    min_y: float
    portals: list[Portal] = field(default_factory=list)
    border_portals: list[BorderPortal] = field(default_factory=list)
    d_lines: list[list[Vec3]] = field(default_factory=list)
    outline: list[list[Vec3]] = field(default_factory=list)      # E
    cells: list[Cell] = field(default_factory=list)
    end_row: Vec3 = (0.0, 0.0, 0.0)          # floats of the F table's terminating row
    null_offsets: dict[int, int] = field(default_factory=dict)  # raw offset values stored with count 0


@dataclass
class TileItem:
    area: int                 # index into Mesh.areas
    a: float                  # stored per tile item (area min / max height in the samples)
    b: float


@dataclass
class LinkObject:
    header: tuple[int, int, int]
    refs: list[tuple[int, int]]   # (area index, byte offset inside that area object)


@dataclass
class NeighbourLink:
    sector_id: int
    objects: list[LinkObject]


@dataclass
class Mesh:
    version: int
    flags: int
    loaded: int
    sector_id: int
    resolution: float
    pitch: float
    rect: tuple[float, float, float, float]   # minX, maxX, minZ, maxZ
    tiles_x: int
    tiles_z: int
    radius: float
    tiles: list[list[TileItem]]               # tiles_x * tiles_z
    areas: list[Area]
    links: list[NeighbourLink]
    end_slot: bytes                           # 4 bytes the loader never reads (uninitialised in DICE files)
    links_null_offset: int = NULL


def _at(fmt: str, rec: bytes, at: int) -> tuple[Any, ...]:
    """struct.unpack_from(fmt, rec, at); ValueError when it runs past the record (2026-10-31 fuzzing: struct.error,
    and a negative offset read from the end)."""
    size = struct.calcsize(fmt)
    if at < 0 or at + size > len(rec):
        raise ValueError(f'damaged runtime mesh: {size} bytes at {at:#x} lie outside the {len(rec)}-byte record')
    return struct.unpack_from(fmt, rec, at)


def _u32(rec: bytes, at: int) -> int:
    return _at('<I', rec, at)[0]


def _f32(rec: bytes, at: int) -> float:
    return _at('<f', rec, at)[0]


def _v3(rec: bytes, at: int) -> Vec3:
    return _at('<3f', rec, at)


def _points(rec: bytes, start: int, end: int) -> list[Vec3]:
    """The 12-byte points from start to end."""
    return [_v3(rec, at) for at in range(start, end, 12)]


class _Budget:
    """The bytes a record's tables may take together: every table lies in the record, and none overlaps another in a
    file write_mesh makes, so a damaged record whose counts or offsets point many tables at the same bytes is refused
    before it is read many times over (2026-10-31 review, while fuzzing: reading time grew with the square of the
    record's size)."""

    def __init__(self, rec: bytes) -> None:
        self.left = len(rec)

    def take(self, size: int, what: str) -> None:
        self.left -= max(0, size)
        if self.left < 0:
            raise ValueError(f'damaged runtime mesh: {what} would take more bytes than the record has (tables '
                             f'overlap)')


def parse_mesh(rec: bytes) -> Mesh:
    """The record as a Mesh. ValueError for a record that is not one write_mesh could make: cut short, a count or
    offset past its end, tables that overlap, a reference to no area."""
    if not rec.startswith(MARKER):
        raise ValueError('not a KynogonRuntimeMesh record')
    budget = _Budget(rec)
    version, flags = _at('<HH', rec, 0x14)
    loaded, sector_id = _u32(rec, 0x18), _u32(rec, 0x1C)
    resolution, pitch = _f32(rec, 0x20), _f32(rec, 0x24)
    rect = _at('<4f', rec, 0x28)
    tiles_x, tiles_z = _u32(rec, 0x38), _u32(rec, 0x3C)
    radius = _f32(rec, 0x40)
    tile_table, link_count, links_at = _u32(rec, 0x44), _u32(rec, 0x48), _u32(rec, 0x4C)
    tile_count = tiles_x * tiles_z
    budget.take(4 * (tile_count + 1), f'a tile table of {tiles_x} x {tiles_z} tiles')
    tile_offs = [_u32(rec, tile_table + i * 4) for i in range(tile_count + 1)]

    area_index: dict[int, int] = {}           # object offset -> index
    area_offsets: list[int] = []
    tiles_raw: list[list[tuple[Any, ...]]] = []
    for tile in range(tile_count):
        tile_at = tile_offs[tile]
        count = _u32(rec, tile_at)
        budget.take(4 + 12 * count, f'the {count} items of tile {tile}')
        items = [_at('<Iff', rec, tile_at + 4 + k * 12) for k in range(count)]
        for ptr, _, _ in items:
            if ptr not in area_index:
                area_index[ptr] = len(area_offsets)
                area_offsets.append(ptr)
        tiles_raw.append(items)
    tiles = [[TileItem(area_index[ptr], low, high) for ptr, low, high in items] for items in tiles_raw]

    areas: list[Area] = []
    for area_at in area_offsets:
        budget.take(0x50, f'the area at {area_at:#x}')
        header = _at('<4I4f', rec, area_at)
        # the area's tables: count at area_at + key, offset (from the area's start) at area_at + key + 4
        counts = {key: _u32(rec, area_at + key) for key in (0x20, 0x28, 0x30, 0x38, 0x40, 0x48)}
        offsets = {key: _u32(rec, area_at + key + 4) for key in counts}
        null_offsets = {key: offsets[key] for key in counts if counts[key] == 0}
        area = Area(header[0], header[1], header[2], header[3], header[4], header[5], header[6], header[7],
                    null_offsets=null_offsets)
        if counts[0x20]:
            raise ValueError('A records present (never seen before)')
        budget.take(0x20 * counts[0x28] + 0x2C * counts[0x30], f'the portals of the area at {area_at:#x}')
        table_at = area_at + offsets[0x28] if counts[0x28] else None
        for k in range(counts[0x28]):
            record_at = cast(int, table_at) + k * 0x20      # not None: counts[0x28] > 0
            area.portals.append(Portal(_v3(rec, record_at), _v3(rec, record_at + 12),
                                       _u32(rec, record_at + 0x18)))   # target resolved below
        table_at = area_at + offsets[0x30] if counts[0x30] else None
        for k in range(counts[0x30]):
            record_at = cast(int, table_at) + k * 0x2C      # not None: counts[0x30] > 0
            area.border_portals.append(BorderPortal(_v3(rec, record_at), _v3(rec, record_at + 12),
                                                    _v3(rec, record_at + 0x20),
                                                    (_u32(rec, record_at + 0x18), _u32(rec, record_at + 0x1C))))
        for key, target in ((0x38, area.d_lines), (0x40, area.outline)):
            if counts[key]:
                list_table = area_at + offsets[key]
                budget.take(4 * (counts[key] + 1), f'the point lists of the area at {area_at:#x}')
                list_starts = [_u32(rec, list_table + j * 4) for j in range(counts[key] + 1)]
                for j in range(counts[key]):
                    budget.take(list_starts[j + 1] - list_starts[j], f'the points of the area at {area_at:#x}')
                    target.append(_points(rec, area_at + list_starts[j], area_at + list_starts[j + 1]))
        if counts[0x48]:
            cell_table = area_at + offsets[0x48]
            budget.take(16 * (counts[0x48] + 1), f'the cells of the area at {area_at:#x}')
            rows = [_at('<I3f', rec, cell_table + j * 16) for j in range(counts[0x48] + 1)]
            for j in range(counts[0x48]):
                budget.take(rows[j + 1][0] - rows[j][0], f'the cell points of the area at {area_at:#x}')
                area.cells.append(Cell(rows[j][1:], _points(rec, area_at + rows[j][0], area_at + rows[j + 1][0])))
            area.end_row = rows[-1][1:]
        areas.append(area)
    for k, area in enumerate(areas):
        for portal in area.portals:
            if portal.target not in area_index:
                raise ValueError(f'area {k}: portal target {portal.target:#x} is not the start of an area')
            portal.target = area_index[portal.target]

    starts = sorted(area_offsets)
    links: list[NeighbourLink] = []
    budget.take(12 * link_count, f'{link_count} neighbour links')
    for k in range(link_count):
        sid, object_count, list_at = _at('<3I', rec, links_at + k * 12)
        budget.take(4 * object_count, f'the list of link {sid:#x}')
        objs: list[LinkObject] = []
        for j in range(object_count):
            object_at = _u32(rec, list_at + j * 4)
            link_header = _at('<3I', rec, object_at)
            ref_count = _u32(rec, object_at + 0xC)
            budget.take(0x10 + 4 * ref_count, f'link {sid:#x}/{j}')
            refs: list[tuple[int, int]] = []
            for ref in range(ref_count):
                ptr = _u32(rec, object_at + 0x10 + ref * 4)
                after = bisect.bisect_right(starts, ptr)       # the area that starts last at or before ptr
                if not after:
                    raise ValueError(f'link {sid:#x}/{j}: reference {ptr:#x} lies before the first area')
                owner = starts[after - 1]
                refs.append((area_index[owner], ptr - owner))
            objs.append(LinkObject(link_header, refs))
        links.append(NeighbourLink(sid, objs))
    end_slot = rec[tile_offs[tile_count]:tile_offs[tile_count] + 4]
    return Mesh(version, flags, loaded, sector_id, resolution, pitch, rect, tiles_x, tiles_z, radius, tiles, areas,
                links, end_slot, links_at if link_count == 0 else NULL)


def write_mesh(mesh: Mesh) -> bytes:
    """The record's bytes, every offset computed again."""
    # Pass 1: serialise areas without B target fields to learn their sizes; pass 2: lay out and patch.
    bodies = [_area_body(area) for area in mesh.areas]
    out = bytearray(MARKER)
    out += struct.pack('<HHII', mesh.version, mesh.flags, mesh.loaded, mesh.sector_id)
    out += struct.pack('<2f4f2If', mesh.resolution, mesh.pitch, *mesh.rect, mesh.tiles_x, mesh.tiles_z, mesh.radius)
    out += b'\0' * 12                      # tile table offset, link count, link offset (patched below)
    tile_count = mesh.tiles_x * mesh.tiles_z
    tile_table = len(out)
    out += b'\0' * ((tile_count + 1) * 4)
    tile_offs: list[int] = []
    area_at: dict[int, int] = {}           # area index -> its offset in the record
    for items in mesh.tiles:
        tile_at = len(out)
        tile_offs.append(tile_at)
        out += struct.pack('<I', len(items))
        item_pos = len(out)
        out += b'\0' * (len(items) * 12)
        if items:
            out += b'\0' * 4               # end offset, patched below
        for item in items:
            if item.area not in area_at:
                area_at[item.area] = len(out)
                out += bodies[item.area][0]
        for k, item in enumerate(items):
            struct.pack_into('<Iff', out, item_pos + k * 12, area_at[item.area], item.a, item.b)
        if items:
            struct.pack_into('<I', out, item_pos + len(items) * 12, len(out))
    unplaced = [i for i in range(len(mesh.areas)) if i not in area_at]
    if unplaced:
        raise ValueError(f'areas {unplaced[:5]} are in no tile: every area must be listed by a tile to be written')
    end_slot_off = len(out)
    out += mesh.end_slot
    tile_offs.append(end_slot_off)
    for i, tile_at in enumerate(tile_offs):
        struct.pack_into('<I', out, tile_table + i * 4, tile_at)
    # Patch B portal targets (offset and size of the target area).
    sizes = [len(body[0]) for body in bodies]
    for i, (_body, portal_pos) in enumerate(bodies):
        base = area_at[i]
        for k, portal in enumerate(mesh.areas[i].portals):
            struct.pack_into('<II', out, base + portal_pos + k * 0x20 + 0x18, area_at[portal.target],
                             sizes[portal.target])
    # Neighbour links.
    links_off = len(out) if mesh.links else mesh.links_null_offset
    if mesh.links:
        table = len(out)
        out += b'\0' * (len(mesh.links) * 12)
        for k, link in enumerate(mesh.links):
            list_at = len(out)
            out += b'\0' * (len(link.objects) * 4)
            struct.pack_into('<3I', out, table + k * 12, link.sector_id, len(link.objects), list_at)
            for j, obj in enumerate(link.objects):
                struct.pack_into('<I', out, list_at + j * 4, len(out))
                out += struct.pack('<4I', *obj.header, len(obj.refs))
                for ai, rel in obj.refs:
                    out += struct.pack('<I', area_at[ai] + rel)
    struct.pack_into('<3I', out, 0x44, tile_table, len(mesh.links), links_off)
    out += struct.pack('<II', len(out), 0)   # trailer
    return bytes(out)


def _area_body(area: Area) -> tuple[bytes, int]:
    """Bytes of one area object with B target fields zeroed; returns (bytes, offset of the B records)."""
    hdr_size = 0x50
    blob = bytearray()
    offsets: dict[int, int] = {}            # header key -> the table's offset from the area's start
    pos = hdr_size
    portal_pos = pos
    offsets[0x28] = pos if area.portals else area.null_offsets.get(0x28, NULL)
    for portal in area.portals:
        blob += struct.pack('<6fII', *portal.p0, *portal.p1, 0, 0)
    pos = hdr_size + len(blob)
    offsets[0x30] = pos if area.border_portals else area.null_offsets.get(0x30, NULL)
    for border in area.border_portals:
        blob += struct.pack('<6f2I3f', *border.p0, *border.p1, *border.runtime, *border.direction)
    for key, lists in ((0x38, area.d_lines), (0x40, area.outline)):
        if not lists:
            offsets[key] = area.null_offsets.get(key, NULL)
            continue
        table = hdr_size + len(blob)
        offsets[key] = table
        data_start = table + (len(lists) + 1) * 4
        list_starts, next_at = [], data_start
        for points in lists:
            list_starts.append(next_at)
            next_at += len(points) * 12
        list_starts.append(next_at)
        blob += struct.pack(f'<{len(list_starts)}I', *list_starts)
        for points in lists:
            for point in points:
                blob += struct.pack('<3f', *point)
    if area.cells:
        table = hdr_size + len(blob)
        offsets[0x48] = table
        next_at = table + (len(area.cells) + 1) * 16
        for cell in area.cells:
            blob += struct.pack('<I3f', next_at, *cell.direction)
            next_at += len(cell.points) * 12
        blob += struct.pack('<I3f', next_at, *area.end_row)
        for cell in area.cells:
            for point in cell.points:
                blob += struct.pack('<3f', *point)
    else:
        offsets[0x48] = area.null_offsets.get(0x48, NULL)
    offsets[0x20] = area.null_offsets.get(0x20, NULL)
    header = struct.pack('<4I4f', area.sector_id, area.tile_x, area.tile_z, area.layer, area.radius, area.pitch,
                         area.max_y, area.min_y)
    counts = {0x20: 0, 0x28: len(area.portals), 0x30: len(area.border_portals), 0x38: len(area.d_lines),
              0x40: len(area.outline), 0x48: len(area.cells)}
    for key in (0x20, 0x28, 0x30, 0x38, 0x40, 0x48):
        header += struct.pack('<II', counts[key], offsets[key])
    return bytes(header + blob), portal_pos
