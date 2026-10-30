"""water.read_mesh on made-up WaterMesh files (no game data): a sound one reads, in both byte orders, bodies joined; a
triangle naming a vertex its body does not have, or an index count that is not whole triangles, is refused with a
ValueError (2026-10-30: read, then an IndexError far away, in NavKit's water rule or a viewer).

  python -B test_water_mesh.py
"""
import os
import struct
import sys
import tempfile

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import water  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def mesh(bodies, e='<'):
    """A WaterMesh of [(vertex count, [indices])], vertices at y 4.5."""
    out = struct.pack(e + 'I', len(bodies))
    for n, idx in bodies:
        v = np.zeros((n, 3), np.float32)
        v[:, 1] = 4.5
        out += struct.pack(e + 'I', n) + v.astype(e + 'f4').tobytes()
        out += struct.pack(e + 'I', len(idx)) + np.asarray(idx, e + 'u4').tobytes()
    return out


def read(data):
    fd, path = tempfile.mkstemp(suffix='.water.mesh.res')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        return water.read_mesh(path)
    finally:
        os.remove(path)


def refused(data):
    try:
        read(data)
        return None
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f'CRASH {type(e).__name__}: {e}'


if __name__ == '__main__':
    for e in ('<', '>'):
        v, t = read(mesh([(3, [0, 1, 2]), (4, [0, 1, 2, 1, 2, 3])], e))
        check(len(v) == 7 and t.tolist() == [[0, 1, 2], [3, 4, 5], [4, 5, 6]] and float(v[:, 1].max()) == 4.5,
              f'a sound mesh of two bodies reads, joined ({"little" if e == "<" else "big"}-endian)')
    why = refused(mesh([(3, [0, 1, 7])]))
    check(why is not None and 'vertex 7' in why, f'a triangle past its body\'s vertices is refused: {why}')
    why = refused(mesh([(3, [0, 1, 2]), (3, [0, 1, 3])]))
    check(why is not None and 'vertex 3' in why, f'a second body\'s triangle past its own vertices is refused: {why}')
    why = refused(mesh([(3, [0, 1])]))
    check(why is not None and not why.startswith('CRASH'), f'an index count that is not whole triangles is refused: {why}')
    check(refused(mesh([(0, [])])) is None, 'a body without water (no vertices, no triangles) still reads')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
