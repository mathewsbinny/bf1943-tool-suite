"""NavKit's seams between a 0.8 m sector and a 0.2 m sector (navbuild.merge_detailed), on sample files made here (no
game data): two sectors side by side, a wall along the seam with one opening. The 0.8 m samples see a 10 m opening
(z 20-30), the 0.2 m samples the real one, 0.8 m wide (z 28.6-29.4) behind a thicker wall - the coarse run misses a
post, as on BF1943's carriers and islets. The shared seam nodes come from the 0.8 m build (one per 10 m of ground
along the seam), and the 0.2 m build has no ground within CARRY_RADIUS of them: without SEAM_GAP_NODES the two
sectors are not joined at all (Wake Island's Japanese carrier, Coral Sea's islets, 2026-10-24).

  python -B test_mixed_seam.py
"""
import os
import shutil
import struct
import sys
import tempfile
from collections import defaultdict

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import hpdcheck  # noqa: E402
import hpdtool  # noqa: E402
import navbuild  # noqa: E402
import soldier1943  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def write_samples(path, pitch, x0, z0, nx, nz, blocked):
    """A navsample version-1 file: flat ground at y 0, one surface per column; blocked(x, z) marks the wall."""
    out = bytearray(struct.Struct('<4sI3f2i6fI').pack(b'ONS1', 1, pitch, x0, z0, nx, nz, 2.0, -2.0, 0.35, 0.37, 1.8, 0.0, 0))
    rec = struct.Struct('<f3bB')
    for j in range(nz):
        for i in range(nx):
            x, z = x0 + i * pitch, z0 + j * pitch
            out.append(1)
            out += rec.pack(0.0, 0, 127, 0, 1 if blocked(x, z) else 0)
    open(path, 'wb').write(bytes(out))


def islands(path):
    """Union of the route graph over all sectors (border ids are one node): {node id: root}."""
    f = hpdtool.parse(open(path, 'rb').read())
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    pos = {}
    for s in f.sectors:
        ids = [nd.id & 0xFFFFFFFF for nd in s.nodes]
        for nd, i in zip(s.nodes, ids):
            pos[i] = nd.pos
            find(i)
        for e in s.edges:
            parent[find(ids[e.src])] = find(ids[e.dst])
    return {i: find(i) for i in pos}, pos


def build(tmp, gap_nodes):
    navbuild.SEAM_GAP_NODES = gap_nodes
    out = os.path.join(tmp, f'seam_{int(gap_nodes)}.hpd')
    lines = []
    navbuild.build(os.path.join(tmp, 's08.bin'), os.path.join(tmp, 'forbid.npy'), 0, 0, out, step=soldier1943.STEP,
                   seeds=[(50.0, 0.0, 50.0), (150.0, 0.0, 50.0)], max_water_depth=None,
                   detailed={'samples': os.path.join(tmp, 's02.bin'), 'high': None, 'sectors': {(1, 0)}},
                   log=lines.append)
    return out, lines


if __name__ == '__main__':
    soldier1943.configure()
    tmp = tempfile.mkdtemp(prefix='mixed_seam_')
    try:
        np.save(os.path.join(tmp, 'forbid.npy'), np.zeros((0, 3, 3), np.float32))
        # 0.8 m: sectors (0, 0) and (1, 0); a wall 99.2-100.8 with a 10 m opening at z 20-30
        write_samples(os.path.join(tmp, 's08.bin'), 0.8, 0.0, 0.0, 250, 125,
                      lambda x, z: 99.1 <= x <= 100.9 and not 20.0 <= z <= 30.0)
        # 0.2 m: sector (1, 0) and 4 m of its neighbour; the wall 99.2-102.4 with the real opening at z 28.6-29.4
        write_samples(os.path.join(tmp, 's02.bin'), 0.2, 96.0, 0.0, 520, 500,
                      lambda x, z: 99.1 <= x <= 102.5 and not 28.55 <= z <= 29.45)
        old = navbuild.SEAM_GAP_NODES
        try:
            for gap_nodes in (False, True):
                path, log = build(tmp, gap_nodes)
                isl, pos = islands(path)
                west = min(pos, key=lambda i: (pos[i][0] - 50) ** 2 + (pos[i][2] - 50) ** 2)
                east = min(pos, key=lambda i: (pos[i][0] - 150) ** 2 + (pos[i][2] - 50) ** 2)
                joined = isl[west] == isl[east]
                if not gap_nodes:
                    check(not joined, 'without seam gap nodes the two sectors are not joined (the break reproduced)')
                else:
                    check(joined, 'with seam gap nodes the two sectors are joined through the 0.8 m opening')
                    added = [l for l in log if 'seam gap nodes' in l]
                    check(bool(added), f"the build reports them: {added[0] if added else 'nothing'}")
                    bad = {k: v for k, v in hpdcheck.check(path).items() if v}
                    check(not bad, f'hpdcheck passes ({bad or "clean"})')
        finally:
            navbuild.SEAM_GAP_NODES = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
