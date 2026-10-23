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
from hkpack import T_ARRAY, T_UINT8, T_UINT16, T_UINT32, T_VECTOR4, T_VOID


def member(name, offset, mtype, subtype=0):
    return {'name': name, 'class': None, 'type': mtype, 'subtype': subtype, 'carray': 0, 'offset': offset}


CLASSES = {
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


def convex_vertices(pf, off):
    """Vertices [(x, y, z)] of an hkConvexVerticesShape: +0x10 convex radius, +0x20 / +0x30 its box half-extents and
    centre (hkVector4), +0x40 rotatedVertices (hkArray of 'four transposed points': x0..3, y0..3, z0..3, 48 bytes
    each), +0x4c the vertex count, +0x50 planeEquations (hkArray of hkVector4). Layout from the files (2026-10-23)."""
    import struct
    e, d = pf.e, pf.d
    p = pf.deref(off + 0x40)
    groups = struct.unpack_from(e + 'i', d, off + 0x44)[0]
    n = struct.unpack_from(e + 'i', d, off + 0x4C)[0]
    if not p or groups <= 0 or not 0 < n <= 4 * groups:
        return []
    out = []
    for g in range(groups):
        x = struct.unpack_from(e + '4f', d, p + 48 * g)
        y = struct.unpack_from(e + '4f', d, p + 48 * g + 16)
        z = struct.unpack_from(e + '4f', d, p + 48 * g + 32)
        out += list(zip(x, y, z))
    return out[:n]


def convex_box(pf, off):
    """(the 12 triangles of an hkConvexVerticesShape's vertex bounding box as [[(x, y, z)] * 3],
    stored half-extents, stored centre) - NavKit's BFBC2 reader uses the same box for convex shapes."""
    import struct
    v = convex_vertices(pf, off)
    half = struct.unpack_from(pf.e + '3f', pf.d, off + 0x20)
    centre = struct.unpack_from(pf.e + '3f', pf.d, off + 0x30)
    if not v:
        return [], half, centre
    lo = [min(c[k] for c in v) for k in range(3)]
    hi = [max(c[k] for c in v) for k in range(3)]
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return [[corners[a], corners[b], corners[c]] for a, b, c in BOX_FACES], half, centre


def triangles(pf, off):
    """(vertices [(x, y, z)], triangles [(a, b, c)]) of an hkStorageExtendedMeshShapeMeshSubpartStorage."""
    o = pf.read_object(off, 'hkStorageExtendedMeshShapeMeshSubpartStorage')
    vertices = [v[:3] for v in o['vertices']]
    if o['indices16']:
        i = o['indices16']
        tris = [tuple(i[k:k + 3]) for k in range(0, len(i) - 3, 4)]
    else:
        i = o['indices32']
        tris = [tuple(i[k:k + 3]) for k in range(0, len(i) - 2, 3)]
    return vertices, tris
