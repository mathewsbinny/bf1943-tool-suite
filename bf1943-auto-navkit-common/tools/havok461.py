"""Class layouts for Havok 4.6.1 packfiles, which carry no reflection of their own (BF1943's *_Havok.res: the
__types__ section is empty, and the game executable keeps only Havok's type registry, hkTypeInfo, not hkClass).

Reconstructed from the files (2026-10-23): object starts come from the packfile's virtual fixups, pointer
fields from its local fixups, and every hkArray is (data pointer, count, capacity | 0xc0000000) with its data
ending where the next array's begins. Each layout is checked by tests/test_havok461.py against real data:
every triangle's indices below the vertex count, and contiguous array data.

    pf = hkpack.Packfile(data, classes=havok461.CLASSES)
    pf.read_object(off, 'hkStorageExtendedMeshShapeMeshSubpartStorage')['vertices']

Only classes whose layout is established are listed; add others the same way.
"""
from typing import TYPE_CHECKING, Any

from hkpack import T_ARRAY, T_UINT8, T_UINT16, T_UINT32, T_VECTOR4, T_VOID

if TYPE_CHECKING:
    import hkpack

type Point = tuple[float, float, float]


def member(name: str, offset: int, mtype: int, subtype: int = 0) -> dict[str, Any]:
    """A class member in hkpack's form (no member class, not a C array)."""
    return {'name': name, 'class': None, 'type': mtype, 'subtype': subtype, 'carray': 0, 'offset': offset}


CLASSES: dict[str, dict[str, Any]] = {
    # the triangle mesh storage of an hkStorageExtendedMeshShape: +0 vtable, +4 memory size and reference count;
    # triangles are 4 x uint16 each (a, b, c, then padding: usually 0, but not always cleared by the exporter)
    'hkStorageExtendedMeshShapeMeshSubpartStorage': {'size': 0x50, 'parent': None, 'members': [
        member('vertices', 0x08, T_ARRAY, T_VECTOR4),
        member('indices16', 0x14, T_ARRAY, T_UINT16),
        member('indices32', 0x20, T_ARRAY, T_UINT32),
        member('materialIndices', 0x2C, T_ARRAY, T_UINT8),
        member('materials', 0x38, T_ARRAY, T_VOID),         # element layout not established
        member('materialIndices16', 0x44, T_ARRAY, T_UINT16),
    ]},
}


BOX_FACES = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]


def convex_vertices(pf: 'hkpack.Packfile', off: int) -> list[Point]:
    """Vertices [(x, y, z)] of an hkConvexVerticesShape: +0x10 convex radius, +0x20 / +0x30 its box half-extents and
    centre (hkVector4), +0x40 rotatedVertices (hkArray of 'four transposed points': x0..3, y0..3, z0..3, 48 bytes
    each), +0x4c the vertex count, +0x50 planeEquations (hkArray of hkVector4). Layout from the files (2026-10-23)."""
    import struct
    endian, data = pf.e, pf.d
    groups_at = pf.deref(off + 0x40)
    if off < 0 or off + 0x50 > len(data):          # 2026-10-31 fuzzing: struct.error before
        raise ValueError(f'damaged Havok packfile: a convex shape at {off:#x} runs past the end of the file')
    groups = struct.unpack_from(endian + 'i', data, off + 0x44)[0]
    count = struct.unpack_from(endian + 'i', data, off + 0x4C)[0]
    if not groups_at or groups <= 0 or not 0 < count <= 4 * groups:
        return []
    if groups_at < 0 or groups_at + 48 * groups > len(data):
        raise ValueError(f'damaged Havok packfile: {groups} vertex groups at {groups_at:#x} run past the end of the '
                         f'file')
    out: list[Point] = []
    for group in range(groups):
        x = struct.unpack_from(endian + '4f', data, groups_at + 48 * group)
        y = struct.unpack_from(endian + '4f', data, groups_at + 48 * group + 16)
        z = struct.unpack_from(endian + '4f', data, groups_at + 48 * group + 32)
        out += list(zip(x, y, z))
    return out[:count]


def convex_box(pf: 'hkpack.Packfile', off: int) -> tuple[list[list[Point]], Point, Point]:
    """(the 12 triangles of an hkConvexVerticesShape's vertex bounding box as [[(x, y, z)] * 3],
    stored half-extents, stored centre) - NavKit's BFBC2 reader uses the same box for convex shapes."""
    import struct
    vertices = convex_vertices(pf, off)                # checks that the shape's 0x50 bytes are in the file
    half = struct.unpack_from(pf.e + '3f', pf.d, off + 0x20)
    centre = struct.unpack_from(pf.e + '3f', pf.d, off + 0x30)
    if not vertices:
        return [], half, centre
    lo = [min(vertex[k] for vertex in vertices) for k in range(3)]
    hi = [max(vertex[k] for vertex in vertices) for k in range(3)]
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return [[corners[a], corners[b], corners[c]] for a, b, c in BOX_FACES], half, centre


def triangles(pf: 'hkpack.Packfile', off: int) -> tuple[list[tuple[float, ...]], list[tuple[int, ...]]]:
    """(vertices [(x, y, z)], triangles [(a, b, c)]) of an hkStorageExtendedMeshShapeMeshSubpartStorage. ValueError
    when a triangle names a vertex the storage does not have (2026-10-31 fuzzing: read, then an IndexError in
    levelobjects1943.collision; on BF1943's files every index is below the vertex count, tests/test_havok461.py)."""
    fields = pf.read_object(off, 'hkStorageExtendedMeshShapeMeshSubpartStorage')
    vertices = [vertex[:3] for vertex in fields['vertices']]
    if fields['indices16']:
        indices = fields['indices16']
        tris = [tuple(indices[k:k + 3]) for k in range(0, len(indices) - 2, 4)]     # a last one without padding kept
    else:
        indices = fields['indices32']
        tris = [tuple(indices[k:k + 3]) for k in range(0, len(indices) - 2, 3)]
    top = max((max(tri) for tri in tris), default=-1)
    if top >= len(vertices):
        raise ValueError(f'damaged Havok packfile: a triangle at {off:#x} names vertex {top} of {len(vertices)}')
    return vertices, tris
