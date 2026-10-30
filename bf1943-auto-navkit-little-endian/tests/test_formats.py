r"""NavKit's file readers on damaged and edge-case files built here (no game data; files only in a temp folder):
  - hpdtool.parse refuses a truncated pathdata.hpd (a cut trailer, a cut block, a cut table) and blocks out of order,
    so hpdcheck cannot pass one; `hpdtool roundtrip` reports a damaged file and goes on; `hpdtool info` works on a
    file with no sectors;
  - kymesh: an area no tile lists cannot be written (a clear ValueError, not KeyError);
  - navsample: an interrupted version 1 / 2 run keeps its finished columns;
  - 16-bit Havok triangles: a last triangle stored without its padding word is kept (levelobjects, havok461).

  python -B test_formats.py
"""
import contextlib
import io
import os
import struct
import sys
import tempfile
import types

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import havok461  # noqa: E402
import hpdcheck  # noqa: E402
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import levelobjects  # noqa: E402
import navsample  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def mesh(sid=0x0101):
    sq = [[(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]]
    a0 = kymesh.Area(sid, 0, 0, 0, 0.4, 0.8, 1.0, 0.0, outline=sq,
                     border_portals=[kymesh.BorderPortal((0, 0, 1), (0, 0, 0), (-1, 0, 0))])
    a1 = kymesh.Area(sid, 1, 0, 0, 0.4, 0.8, 1.0, 0.0, outline=sq, portals=[kymesh.Portal((0, 0, 1), (0, 0, 0), 0)])
    tiles = [[kymesh.TileItem(0, 0.0, 1.0)], [kymesh.TileItem(1, 0.0, 1.0)], [], []]
    links = [kymesh.NeighbourLink(0x0201, [kymesh.LinkObject((0, 0, 0), [(0, 0x50)])])]
    return kymesh.Mesh(2, 0, 0, sid, 0.025, 0.8, (0, 100, 0, 100), 2, 2, 0.4, tiles, [a0, a1], links, b'\0' * 4)


def hpd(n_sectors=2):
    secs = []
    for k in range(n_sectors):
        sid = 0x0101 + k
        nodes = [hpdtool.Node(1 + 2 * k, (1, 1, 1), 0x80000000, 15.0, 1, 0, 0),
                 hpdtool.Node(2 + 2 * k, (5, 1, 1), 0x80000000, 0.0, 2, 1, 1)]
        edges = [hpdtool.Edge(0x80000040, 0, 1), hpdtool.Edge(0x80000040, 1, 0)]
        secs.append(hpdtool.Sector(sid, (0, 100, 0, 5, 0, 100), 0, 100007, 13305600, nodes, edges,
                                   kymesh.write_mesh(mesh(sid))))
    return hpdtool.write(hpdtool.HpdFile((2, 2, n_sectors, 1) + (0,) * 32, secs))


def refused(data, what):
    try:
        hpdtool.parse(data)
        check(False, f'hpdtool.parse refuses {what}')
    except ValueError as e:
        check(True, f'hpdtool.parse refuses {what} ({e})')
    except Exception as e:                            # noqa: BLE001
        check(False, f'hpdtool.parse refuses {what} with ValueError, not {type(e).__name__}: {e}')


def hpd_files(tmp):
    data = hpd()
    check(hpdtool.write(hpdtool.parse(data)) == data, 'a made-up pathdata.hpd round-trips')
    refused(data[:-8], 'a file without its last mesh trailer')
    refused(data[:-30], 'a file cut inside its last block')
    refused(data[:0x9C + 48 + 4], 'a file cut inside its sector table')
    swapped = bytearray(data)                         # the second entry's block offset pointing at the first block
    struct.pack_into('<I', swapped, 0x9C + 48 + 36, 0)
    refused(bytes(swapped), 'blocks out of table order (overlapping)')
    p = os.path.join(tmp, 'pathdata.hpd')
    with open(p, 'wb') as f:
        f.write(data[:-8])
    try:
        res = hpdcheck.check(p)
        check(False, f'hpdcheck does not pass a truncated file ({res})')
    except ValueError:
        check(True, 'hpdcheck does not pass a truncated file')
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = hpdtool.main(['hpdtool.py', 'roundtrip', p])
        check(rc == 2 and 'ERROR' in out.getvalue(), f'`hpdtool roundtrip` reports a damaged file (exit {rc})')
    except Exception as e:                            # noqa: BLE001
        check(False, f'`hpdtool roundtrip` reports a damaged file, not {type(e).__name__}: {e}')
    p0 = os.path.join(tmp, 'empty.hpd')
    with open(p0, 'wb') as f:
        f.write(hpdtool.write(hpdtool.HpdFile((2, 2, 0, 1) + (0,) * 32, [])))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = hpdtool.main(['hpdtool.py', 'info', p0])
        check(rc == 0, '`hpdtool info` on a file with no sectors')
    except Exception as e:                            # noqa: BLE001
        check(False, f'`hpdtool info` on a file with no sectors, not {type(e).__name__}: {e}')


def mesh_writes():
    m = mesh()
    m.tiles[1] = []                                   # area 1 in no tile
    try:
        kymesh.write_mesh(m)
        check(False, 'kymesh: an area in no tile cannot be written')
    except ValueError as e:
        check('no tile' in str(e), f'kymesh: an area in no tile is a ValueError ({e})')
    except Exception as e:                            # noqa: BLE001
        check(False, f'kymesh: an area in no tile is a ValueError, not {type(e).__name__}: {e}')


def samples(tmp):
    for version in (1, 2):
        out = bytearray(navsample.HEADER.pack(b'ONS1', version, 0.8, 0.0, 0.0, 4, 1, 100, -100, 0.4, 0.3, 1.8, 0.1, 0))
        for c in range(4):
            out.append(2)
            for k in range(2):
                out += struct.pack('<f3bB', float(k), 0, 127, 0, 0) + (struct.pack('<3I', 1, 2, 3) if version == 2 else b'')
        p = os.path.join(tmp, f'v{version}.bin')
        with open(p, 'wb') as f:
            f.write(bytes(out[:-5]))                  # the run stopped inside the last column's last record
        try:
            s, cols = navsample.load(p)
            check(cols == 3 and len(s.y) == 6 and s.start[-1] == 6,
                  f'navsample v{version}: an interrupted run keeps its 3 finished columns ({cols}, {len(s.y)} surfaces)')
        except Exception as e:                        # noqa: BLE001
            check(False, f'navsample v{version}: an interrupted run loads, not {type(e).__name__}: {e}')


def havok16():
    words = np.array([0, 1, 2, 0, 2, 1, 3, 0, 3, 1, 4], np.uint16)     # two padded triangles, a last one unpadded
    t = levelobjects.triangles16(words) if hasattr(levelobjects, 'triangles16') else None
    check(t is not None and t.tolist() == [[0, 1, 2], [2, 1, 3], [3, 1, 4]],
          f'levelobjects: a last 16-bit triangle without padding is kept ({None if t is None else t.tolist()})')
    pf = types.SimpleNamespace(read_object=lambda off, name: {'vertices': [(0, 0, 0, 0)] * 5, 'indices16': list(words),
                                                              'indices32': []})
    _v, tris = havok461.triangles(pf, 0)
    check(tris == [(0, 1, 2), (2, 1, 3), (3, 1, 4)], f'havok461: a last 16-bit triangle without padding is kept ({tris})')
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, 'x_havok.res')
        with open(p, 'wb') as f:
            f.write(b'not a packfile')
        check(levelobjects.triangles(p) is None, 'levelobjects.triangles: a file that is no packfile gives None')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        hpd_files(tmp)
        mesh_writes()
        samples(tmp)
    havok16()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
