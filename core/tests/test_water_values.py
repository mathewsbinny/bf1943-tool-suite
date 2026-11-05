r"""Water (water): the mesh and VisualWater readers' layouts, limits and shader ranges on made-up files

water on made-up .Water.Mesh.res and .Water.res files (no game data): the mesh's byte order from its body count
(little-endian below 0x10000), each body's vertices and indices read up to exactly the file's end and refused one byte
short with their own message, a file with bytes after its bodies refused, an empty mesh read as empty (n x 3) arrays;
VisualWater's index bytes, slot names (each followed by an extra zero byte; the fragment shader's name is not) and
ranges read exactly; and read() giving each triangle its range's slot (a slot past the list named by number), no
shaders for a mesh without triangles, and none when the indices do not match.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import struct

import numpy as np

from bfbc2core import water


def mesh_bytes(bodies, e='<'):
    """A WaterMesh: the body count, then per body its vertex count, vertices (x, y, z f32), index count, indices."""
    out = struct.pack(e + 'I', len(bodies))
    for verts, idx in bodies:
        out += struct.pack(e + 'I', len(verts)) + b''.join(struct.pack(e + '3f', *v) for v in verts)
        out += struct.pack(e + 'I', len(idx)) + struct.pack(f'{e}{len(idx)}I', *idx)
    return out


SQUARE = ([(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)], [0, 1, 2, 0, 2, 3])


def visual_bytes(indices, slots, ranges, fragment='Systems/Water/WaterOceanFragment', extra_zero=True):
    out = bytes(8) + struct.pack('<I', 8) + bytes(8)                  # 8 bytes of vertices, unread
    out += struct.pack('<I', 4 * len(indices)) + struct.pack(f'<{len(indices)}I', *indices)
    out += struct.pack('<I', len(slots))
    for s in slots:
        out += s.encode('ascii') + b'\0' + (b'\0' if extra_zero else b'')
    out += fragment.encode('ascii') + b'\0'
    out += struct.pack('<I', len(ranges)) + b''.join(struct.pack('<4I', *r) for r in ranges)
    return out


def refusal(fn, path):
    try:
        fn(path)
    except ValueError as e:
        return str(e).split(': ', 1)[1]
    return 'read'


def put(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_mesh_byte_orders_and_bodies(tmp_path):
    data = mesh_bytes([SQUARE, ([(5, 1, 5), (6, 1, 5), (6, 1, 6)], [0, 1, 2])])
    v, t = water.read_mesh(put(tmp_path, 'le.res', data))
    assert v.shape == (7, 3) and t.tolist() == [[0, 1, 2], [0, 2, 3], [4, 5, 6]], 'the second body after the first'
    v2, t2 = water.read_mesh(put(tmp_path, 'be.res', mesh_bytes([SQUARE, ([(5, 1, 5), (6, 1, 5), (6, 1, 6)], [0, 1, 2])], '>')))
    assert np.array_equal(v, v2) and np.array_equal(t, t2)
    v, t = water.read_mesh(put(tmp_path, 'empty.res', mesh_bytes([])))
    assert v.shape == (0, 3) and t.shape == (0, 3) and v.dtype == np.float32 and t.dtype == np.uint32
    # a little-endian count of 0x10000 is taken as big-endian (256 bodies): the file ends at the first one's count
    assert refusal(water.read_mesh, put(tmp_path, 'many.res', struct.pack('<I', 0x10000))) == \
        "the file ends at 4 bytes, before body 0's vertex count at 4 - a damaged or cut file"
    assert refusal(water.read_mesh, put(tmp_path, 'fffff.res', struct.pack('<I', 0xFFFF))) == \
        "the file ends at 4 bytes, before body 0's vertex count at 4 - a damaged or cut file"


def test_mesh_limits(tmp_path):
    whole = mesh_bytes([SQUARE])
    vertex_end = 4 + 4 + 12 * 4                     # the body count, the vertex count and the vertices
    cut = whole[:vertex_end + 4]                    # the vertices and the index count, no indices
    assert refusal(water.read_mesh, put(tmp_path, 'a.res', cut)) == \
        "body 0's 6 indices run past the end of the file - a damaged or cut file"
    assert refusal(water.read_mesh, put(tmp_path, 'b.res', whole[:vertex_end + 3])) == \
        "body 0's 4 vertices run past the end of the file - a damaged or cut file"
    assert refusal(water.read_mesh, put(tmp_path, 'c.res', whole[:-1])) == \
        "body 0's 6 indices run past the end of the file - a damaged or cut file"
    assert refusal(water.read_mesh, put(tmp_path, 'd.res', whole + b'\0')) == '1 unread bytes - not the WaterMesh layout'
    assert refusal(water.read_mesh, put(tmp_path, 'e.res', mesh_bytes([(SQUARE[0], [0, 1, 2, 0])]))) == \
        'a body of 4 indices, not whole triangles - not the WaterMesh layout'
    assert refusal(water.read_mesh, put(tmp_path, 'f.res', mesh_bytes([(SQUARE[0], [0, 1, 4])]))) == \
        'a triangle names vertex 4 of a body of 4 - a damaged WaterMesh'
    assert water.read_mesh(put(tmp_path, 'g.res', mesh_bytes([(SQUARE[0], [0, 1, 3])])))[1].tolist() == [[0, 1, 3]]
    no_tris = mesh_bytes([(SQUARE[0], [])])
    assert water.read_mesh(put(tmp_path, 'h.res', no_tris))[1].shape == (0, 3)


def test_visual(tmp_path):
    data = visual_bytes([0, 1, 2, 0, 2, 3], ['Shaders/A', 'Shaders/B'], [(1, 1, 3, 3), (0, 1, 0, 3)])
    idx, slots, ranges = water.read_visual(put(tmp_path, 'v.res', data))
    assert idx.tolist() == [0, 1, 2, 0, 2, 3] and slots == ['Shaders/A', 'Shaders/B']
    assert ranges == [(1, 1, 3, 3), (0, 1, 0, 3)]
    idx, slots, ranges = water.read_visual(put(tmp_path, 'n.res', visual_bytes([], ['A', 'B'], [], extra_zero=False)))
    assert slots == ['A', 'B'] and ranges == [], 'names without the extra zero byte'
    idx, slots, ranges = water.read_visual(put(tmp_path, 'z.res', visual_bytes([], [], [])))
    assert len(idx) == 0 and slots == [] and ranges == [], 'no slots, no ranges: the zero count read in place'
    end = visual_bytes([0, 1, 2], [], [])
    indices_end = 12 + 8 + 4 + 12
    assert refusal(water.read_visual, put(tmp_path, 'c.res', end[:indices_end])) == \
        f'the file ends at {indices_end} bytes, before the slot count at {indices_end} - a damaged or cut file'
    assert refusal(water.read_visual, put(tmp_path, 'd.res', end[:indices_end - 1])) == \
        '12 index bytes run past the end of the file - a damaged or cut VisualWater'
    assert refusal(water.read_visual, put(tmp_path, 'e.res', end[:-4] + struct.pack('<I', 1))) == \
        '1 ranges run past the end of the file - a damaged or cut VisualWater'


def test_shaders_per_triangle(tmp_path):
    (tmp_path / 't').mkdir()
    put(tmp_path / 't', 'Lv.Water.Mesh.res', mesh_bytes([SQUARE, ([(5, 1, 5), (6, 1, 5), (6, 1, 6)], [0, 1, 2])]))
    put(tmp_path / 't', 'LV.WATER.res', visual_bytes([0, 1, 2, 0, 2, 3, 4, 5, 6], ['Shaders/A', 'Shaders/B'],
                                                    [(1, 1, 3, 3), (0, 1, 0, 3), (2, 1, 6, 3)]))
    w = water.read(str(tmp_path / 't'), 'lv')
    assert w is not None and len(w) == 3 and w.shaders == ['Shaders/A', 'Shaders/B']
    assert w.shader == ['Shaders/A', 'Shaders/B', 'slot 2'], 'from each range\'s first index, a slot past the list by number'
    put(tmp_path / 't', 'LV.WATER.res', visual_bytes([0, 1, 2], ['Shaders/A'], [(0, 1, 0, 3)]))
    w = water.read(str(tmp_path / 't'), 'lv')
    assert w is not None and w.shader is None and w.shaders is None, 'indices not the mesh\'s: no shaders'
    (tmp_path / 'e').mkdir()
    put(tmp_path / 'e', 'lv.water.mesh.res', mesh_bytes([(SQUARE[0], [])]))
    put(tmp_path / 'e', 'lv.water.res', visual_bytes([], ['Shaders/A'], []))
    w = water.read(str(tmp_path / 'e'), 'lv')
    assert w is not None and len(w) == 0 and w.shader is None, 'no triangles: the visual is not read'
    assert water.read(str(tmp_path / 'e'), 'other') is None


def test_byte_order_threshold(tmp_path):
    # a vertex count of 1 little-endian, 2^24 big-endian: the order chosen shows in the refusal
    one = struct.pack('<I', 1)
    assert refusal(water.read_mesh, put(tmp_path, 'le.res', struct.pack('<I', 0xFFFF) + one)) == \
        "body 0's 1 vertices run past the end of the file - a damaged or cut file", 'below 0x10000: little-endian'
    assert refusal(water.read_mesh, put(tmp_path, 'be.res', struct.pack('<I', 0x10000) + one)) == \
        f"body 0's {1 << 24} vertices run past the end of the file - a damaged or cut file", '0x10000: big-endian'
