"""NavKit's RSNV exporter (navexport1943.py) on your BF1943 builds (bf1943 tier; skipped without a Wake Island build):
Wake Island exported to a temporary folder in both byte orders - the two read back identical, the file validates, its
graph equals the build's (nodes, edges, integer costs), every node lies in an area, 2,000 random walkable cells locate
to their own area, and the 10 flag-pair routes of the build's check are found by the reference search. Other levels
with an export in their navkit folder: graph parity and validation.

  python -B test_navexport1943.py
"""
import json
import os
import random
import shutil
import sys
import tempfile

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import navexport1943  # noqa: E402
from bfbc2core import game1943, rsnv  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


if __name__ == '__main__':
    lv = 'wake_island_s'
    W = game1943.level_work(lv)
    if not os.path.isfile(os.path.join(W, 'navkit', f'{lv}.hpd')):
        print(f'skipped: no {lv} build (navkit.py all {lv})')
        sys.exit(0)
    tmp = tempfile.mkdtemp(prefix='navexport_')
    try:
        le, nav = navexport1943.export(lv, os.path.join(tmp, 'le.rsnv'), log=lambda *a: None)
        be, _ = navexport1943.export(lv, os.path.join(tmp, 'be.rsnv'), big_endian=True, log=lambda *a: None)
        problems, w = navexport1943.check(le, nav, log=lambda *a: None)
        check(not problems, f'Wake Island: validates, graph equals the build ({problems or "no problems"})')
        b = rsnv.load(be)
        same = all(np.array_equal(getattr(w, k), getattr(b, k)) for k in ('node_pos', 'edge_target', 'edge_cost', 'edge_kind',
                                                                          'node_area', 'hght', 'glst', 'anod'))
        check(same and w.areas == b.areas and w.bmap == b.bmap, 'big- and little-endian exports read back identical')
        check(int((w.node_area == 0xFFFF).sum()) == 0, 'every node lies in an area')
        check(int((w.edge_kind == rsnv.EDGE_JUMP).sum()) == 8, 'the 4 carrier AA jump links are jump edges both ways')
        rng = random.Random(1943)
        bad = 0
        for _ in range(2000):
            k = rng.randrange(len(w.areas))
            a = w.areas[k]
            cells = [(i, j) for j in range(a.height) for i in range(a.width) if w.walkable(a, i, j)]
            if not cells:
                continue
            i, j = rng.choice(cells)
            x, z = a.x0 + i * a.pitch, a.z0 + j * a.pitch
            loc = w.locate(x, w.ground(a, x, z), z, max_dist=0.05)
            bad += loc is None or w.areas[loc[0]].sector != a.sector and loc[2] > 0.0
        check(bad <= 20, f'random walkable cells locate to an area at their spot ({2000 - bad} of 2000)')
        chk = json.load(open(os.path.join(W, 'navkit', 'check.json'), encoding='utf-8'))['routes']
        found = 0
        for r in chk['routes']:
            pa = [s['position'] for s in chk['spawns'] if s['objective'] == r['a'] and s['point'] == 0][0]
            pb = [s['position'] for s in chk['spawns'] if s['objective'] == r['b'] and s['point'] == 0][0]
            na = int(np.argmin(np.linalg.norm(w.node_pos - np.asarray(pa), axis=1)))
            nb = int(np.argmin(np.linalg.norm(w.node_pos - np.asarray(pb), axis=1)))
            cost, path, _ = rsnv.astar(w, na, nb)
            found += cost is not None
        check(found == len(chk['routes']), f"flag-pair routes found by the reference search: {found} of {len(chk['routes'])}")
        for other in ('guadal_canal', 'iwo_jima_s', 'coral_sea'):
            p = os.path.join(game1943.level_work(other), 'navkit', f'{other}.rsnv')
            h = os.path.join(game1943.level_work(other), 'navkit', f'{other}.hpd')
            if os.path.isfile(p) and os.path.isfile(h) and os.path.getmtime(p) >= os.path.getmtime(h):
                from bfbc2core import navdata, terrain
                g = game1943.Game(game1943.find_game(None), other)
                navdata.Y_OFFSET = 0.9
                nv = navdata.load(h, terrain.read(g.data(f'terrains/{other}/{other}.heightfield-000.res')))
                probs, _ = navexport1943.check(p, nv, log=lambda *a: None)
                check(not probs, f'{other}: its export validates and equals its build ({probs or "no problems"})')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
