r"""water mesh reader: triangles past their vertices refused

water.read_mesh on made-up WaterMesh files (no game data): a sound one reads, in both byte orders, bodies joined; a
triangle naming a vertex its body does not have, or an index count that is not whole triangles, is refused with a
ValueError (2026-10-30: read, then an IndexError far away, in NavKit's water rule or a viewer).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import struct

import numpy as np

from bfbc2core import water


def mesh(bodies, e='<'):
    """A WaterMesh of [(vertex count, [indices])], vertices at y 4.5."""
    out = struct.pack(e + 'I', len(bodies))
    for n, idx in bodies:
        v = np.zeros((n, 3), np.float32)
        v[:, 1] = 4.5
        out += struct.pack(e + 'I', n) + v.astype(e + 'f4').tobytes()
        out += struct.pack(e + 'I', len(idx)) + np.asarray(idx, e + 'u4').tobytes()
    return out


def read(folder, data):
    path = folder / 'made-up.water.mesh.res'
    path.write_bytes(data)
    return water.read_mesh(str(path))


def refused(folder, data):
    """The ValueError's text, None when the file reads, 'CRASH ...' on any other error."""
    try:
        read(folder, data)
        return None
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f'CRASH {type(e).__name__}: {e}'


def test_sound_mesh_reads_in_both_byte_orders(tmp_path):
    for e in ('<', '>'):
        v, t = read(tmp_path, mesh([(3, [0, 1, 2]), (4, [0, 1, 2, 1, 2, 3])], e))
        assert len(v) == 7 and t.tolist() == [[0, 1, 2], [3, 4, 5], [4, 5, 6]] and float(v[:, 1].max()) == 4.5, \
            f'a sound mesh of two bodies reads, joined ({"little" if e == "<" else "big"}-endian)'


def test_triangle_past_its_vertices_refused(tmp_path):
    why = refused(tmp_path, mesh([(3, [0, 1, 7])]))
    assert why is not None and 'vertex 7' in why, f'a triangle past its body\'s vertices is refused: {why}'
    why = refused(tmp_path, mesh([(3, [0, 1, 2]), (3, [0, 1, 3])]))
    assert why is not None and 'vertex 3' in why, f'a second body\'s triangle past its own vertices is refused: {why}'


def test_partial_triangle_refused(tmp_path):
    why = refused(tmp_path, mesh([(3, [0, 1])]))
    assert why is not None and not why.startswith('CRASH'), \
        f'an index count that is not whole triangles is refused: {why}'


def test_empty_body_reads(tmp_path):
    assert refused(tmp_path, mesh([(0, [])])) is None, 'a body without water (no vertices, no triangles) still reads'
