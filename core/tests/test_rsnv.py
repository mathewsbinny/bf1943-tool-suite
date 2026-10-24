"""RSNV, the navigation runtime's per-level file (core rsnv.py), on a file made here (no game data): written in both
byte orders it reads back identical; the Python reference search finds the cheapest path; locate finds the walkable
cell; damaged files (truncated, a section outside the file, a bad marker, a wrong stride) are rejected with a
ValueError, not a crash.

  python -B test_rsnv.py
"""
import os
import struct
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import rsnv  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def tiny():
    """Four nodes in a square (one sector, one 4 x 4 area at 0.8 m): 0-1-2 along the bottom and right, 0-3-2 by a
    detour, plus a jump edge."""
    nodes = [(0.0, 10.0, 0.0), (2.4, 10.0, 0.0), (2.4, 10.0, 2.4), (0.0, 10.0, 6.0)]
    edges = {0: [(1, 38), (3, 96)], 1: [(0, 38), (2, 38)], 2: [(1, 38), (3, 70, rsnv.EDGE_JUMP)], 3: [(0, 96), (2, 70, rsnv.EDGE_JUMP)]}
    node_rows, edge_rows = [], []
    for n, p in enumerate(nodes):
        node_rows.append((*p, len(edge_rows)))
        for e in edges[n]:
            kind = e[2] if len(e) > 2 else rsnv.EDGE_WALK
            edge_rows.append((e[0] | kind << 24, e[1], 0))
    node_rows.append((0.0, 0.0, 0.0, len(edge_rows)))
    bmap = bytes([0b1111, 0b1111, 0b1111, 0b0111])      # 4 x 4 cells, the last row's x = 3 cell missing
    sections = {
        'SECT': [(0x0B0B, 0, 1, 0.0, 0.0, 100.0, 100.0, 0, 0, 1, 0)],
        'NODE': node_rows,
        'NAUX': [(0, 0, 0, 0)] * 4,
        'EDGE': edge_rows,
        'AREA': [(0.0, 0.0, 2.4, 2.4, 10.0, 10.0, 0, 4, 4, 0, 0, 0, 0, 0, 0, 4, 800, 0, 1, 1, 0)],
        'BMAP': bmap,
        'HGHT': [(0,)],
        'GBKT': [(0, 1)] + [(1, 0)] * (rsnv.BUCKETS * rsnv.BUCKETS - 1),
        'GLST': [(0,)],
        'ANOD': [(0,), (1,), (2,), (3,)],
        'ISLD': [(4, 0, rsnv.ISLD_MAIN, 0, 0)],
        'META': b'{"exporter": "test"}',
    }
    header = {'level': 'tiny', 'sea_level': 5.0, 'radius': 0.35, 'height': 1.8, 'step': 0.37, 'water_depth': 1.4,
              'hpd_sha256': '00' * 32}
    return sections, header


if __name__ == '__main__':
    tmp = tempfile.mkdtemp(prefix='rsnv_')
    sections, header = tiny()
    le, be = os.path.join(tmp, 'le.rsnv'), os.path.join(tmp, 'be.rsnv')
    rsnv.write(le, sections, header)
    rsnv.write(be, sections, header, big_endian=True)
    a, b = rsnv.load(le), rsnv.load(be)
    check(not a.big_endian and b.big_endian, 'byte order recorded and detected')
    same = all((getattr(a, k) == getattr(b, k)).all() for k in ('node_pos', 'node_first_edge', 'edge_target', 'edge_kind',
                                                                'edge_cost', 'hght', 'gbkt', 'glst', 'anod'))
    check(same and a.areas == b.areas and a.bmap == b.bmap and a.islands == b.islands and a.meta == b.meta,
          'little- and big-endian files read back identical')
    check(a.validate() == [], f'validate: {a.validate() or "consistent"}')
    cost, path, exp = rsnv.astar(a, 0, 2)
    check(cost == 76 and path == [0, 1, 2], f'reference search: cost {cost} (1/16 m), path {path}')
    cost, path, _ = rsnv.astar(a, 0, 2, kind_mask=1 << rsnv.EDGE_WALK)
    check(path == [0, 1, 2], 'a kind mask without jumps keeps to walk edges')
    cost, path, _ = rsnv.astar(a, 1, 3, kind_mask=1 << rsnv.EDGE_WALK)
    check(path == [1, 0, 3] and cost == 134, f'without jump edges 1 -> 3 goes round (cost {cost})')
    loc = a.locate(1.7, 10.0, 1.7)
    check(loc is not None and loc[0] == 0 and loc[1] == (2, 2), f'locate a walkable cell: {loc}')
    loc = a.locate(2.4, 10.0, 2.4, max_dist=0.1)
    check(loc is None, 'the missing cell is not walkable (no area within 0.1 m)')
    loc = a.locate(1.0, 20.0, 1.0)
    check(loc is None, 'a point 10 m above the area is not located on it')
    raw = open(le, 'rb').read()
    for what, data in (('truncated', raw[:len(raw) // 2]),
                       ('bad marker', raw[:4] + b'\x00\x00\x00\x00' + raw[8:]),
                       ('section outside the file', raw[:0x44] + struct.pack('<I', len(raw) + 64) + raw[0x48:]),
                       ('wrong stride', raw[:0x4C] + struct.pack('<I', 31) + raw[0x50:]),
                       ('not RSNV', b'XXXX' + raw[4:])):
        try:
            rsnv.load(data)
            check(False, f'{what}: rejected')
        except ValueError as e:
            check(True, f'{what}: rejected ({e})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
