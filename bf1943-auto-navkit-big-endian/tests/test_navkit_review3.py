"""NavKit (big-endian): the user's review of 2026-10-30 stays fixed (no game data: made-up files and stand-ins in a
temporary work folder).

  1. a vehicle world is stamped with its own SHA-256, and the vehicle check refuses a file that is not the one its
     builder wrote, and a world whose header and META name another level, class or parameters (a valid world of
     another level with radius 99 put in the jeep world's place passed with no problem);
  2. the soldier export (navexport1943.prepared_terrain), the audit and the jump links take the height field the
     level was prepared with (objects\\heightfield.res, checked against the preparation's stamp), and refuse a game
     copy whose height field changed since (they reread the game's: another terrain under the prepared build);
  3. rsnvtool's pockets: the pieces are weakly connected components, disjoint (a directed edge 1 -> 0 alone gave
     [[0, 1], [0]]), their shares add up, and the one-way share is the largest piece outside its strongly connected
     core.

  python -B test_navkit_review3.py
"""
import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_navkit_stages as T  # noqa: E402  (puts the kit and its tools on sys.path)
import navkitjson1943 as nj  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
import navexport_vehicles1943 as nv  # noqa: E402
import navexport1943  # noqa: E402
import rsnvtool  # noqa: E402
from bfbc2core import game1943, rsnv, rsnvtrip  # noqa: E402

failures = []
q = lambda *a: None                                                          # noqa: E731


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def heightfield(top):
    """A made-up height field file whose bounds reach `top` m up (T.HEIGHTFIELD's layout)."""
    return struct.pack('>6f6IB', -1100.0, 0.0, -1100.0, 1100.0, top, 1100.0, 4, 0, 0, 0, 0, 0, 0) + bytes(32)


def prepared(lv, st):
    p = nj.paths(lv)
    for k in ('navkit', 'objects'):
        os.makedirs(p[k], exist_ok=True)
    with open(p['settings'], 'w', encoding='utf-8', newline='\n') as f:
        json.dump(st, f)
    return p


def world(level, cls, radius, params=None):
    """A stand-in RSNV world (what rsnv.load gives) with its header's level id and radius and its META."""
    return SimpleNamespace(level_id=rsnv.fnv1a(level), radius=radius, islands=[], node_pos=np.zeros((0, 3)),
                           node_island=np.zeros(0, np.int64), node_area=np.zeros(0, np.int64), offmesh=[], areas=[],
                           edge_target=np.zeros(0, np.int64), validate=lambda: [],
                           meta={'level': level, 'class': cls, 'layer': vc.CLASS_IDS[cls],
                                 'params': vc.params_id(cls) if params is None else params, 'counts': {}})


def graph(n, edges, tight=()):
    """A stand-in RSNV graph of n nodes and directed edges [(a, b)] (CSR by source)."""
    edges = sorted(edges)
    first = np.zeros(n + 1, np.int64)
    for a, _b in edges:
        first[a + 1] += 1
    first = np.cumsum(first)
    flags = np.array([rsnv.EDGEF_TIGHT if e in tight else 0 for e in edges], np.int64)
    return SimpleNamespace(node_first_edge=first, edge_target=np.array([b for _a, b in edges], np.int64),
                           edge_flags=flags, node_island=np.zeros(n, np.int64), node_pos=np.zeros((n, 3)))


if __name__ == '__main__':
    work = tempfile.mkdtemp(prefix='navkit_review3_')
    os.environ['BF1943_WORK'] = work
    real = (game1943.Game, game1943.find_game, rsnv.load, rsnvtrip.transfers)
    try:
        # 1. the vehicle world's identity
        lv = 'made_up'
        p = prepared(lv, {'level': lv, 'preparation': 'P1', 'game': 'G', 'built': 't', 'checked': 't ok',
                          'build_from': {'preparation': 'P1', 'hpd_sha256': 'H1'}})
        soldier = os.path.join(p['navkit'], f'{lv}.rsnv')
        with open(soldier, 'wb') as f:
            f.write(b'the soldier world')
        s_sha = nj.sha256(soldier)
        jeep = nv.world_path(lv, 'jeep')
        with open(jeep, 'wb') as f:
            f.write(b'a valid world of another level, radius 99')

        def stamp_jeep(**extra):
            nj.update(lv, lambda st: st.update(
                exported={'preparation': 'P1', 'hpd_sha256': 'H1', 'sha256': s_sha},
                vehicles={'worlds': {'jeep': dict(vc.inputs(st, lv, 'jeep'), soldier=s_sha, time='t', **extra)}}))
        worlds = {}
        rsnv.load = lambda path: worlds.get(os.path.normcase(path)) or world(lv, 'jeep', 0.0)
        rsnvtrip.transfers = lambda S, V: []
        key = os.path.normcase(jeep)

        def jeep_problems():
            rep = nv.check(lv, ['jeep'], log=q)
            return [m for m in rep['problems'] if m.startswith('jeep')]
        worlds[key] = world('wake_island_s', 'jeep', 99.0)
        stamp_jeep()                                     # stamped from its inputs only, as before the fix
        got = jeep_problems()
        check(any('SHA-256' in m for m in got),
              f'a jeep world not stamped with its own SHA-256 is not taken as the built one ({got})')
        stamp_jeep(sha256=nj.sha256(jeep))               # the file is the stamped one, but it is of another level
        got = jeep_problems()
        check(any('another level' in m for m in got) and any('radius 99' in m for m in got),
              f'a world of another level with radius 99 is refused by its header and META ({got})')
        worlds[key] = world(lv, 'tank', vc.value('jeep', 'radius'))
        got = jeep_problems()
        check(any('another class' in m for m in got), f'... a world of another class too ({got})')
        worlds[key] = world(lv, 'jeep', vc.value('jeep', 'radius'), params='0' * 64)
        got = jeep_problems()
        check(any('other jeep parameters' in m for m in got), f'... and of other class parameters ({got})')
        worlds[key] = world(lv, 'jeep', vc.value('jeep', 'radius'))
        got = jeep_problems()
        check(got == [], f'the jeep world its builder wrote, of this level, class and parameters: no problem ({got})')
        rsnv.load, rsnvtrip.transfers = real[2], real[3]
        import rsnvgrid1943
        src = open(rsnvgrid1943.__file__, encoding='utf-8').read()
        check("meta['params'] = vc.params_id(cls)" in src, 'the vehicle world writer puts the class parameters\' digest '
                                                         'in META')

        # 2. the prepared terrain
        lv = 'terrain_lv'
        prep_hf, game_hf = heightfield(100.0), heightfield(999.0)
        import hashlib
        p = prepared(lv, {'level': lv, 'preparation': 'P1', 'game': 'G',
                          'objects_sha256': {'heightfield.res': hashlib.sha256(prep_hf).hexdigest()}})
        with open(os.path.join(p['objects'], 'heightfield.res'), 'wb') as f:
            f.write(prep_hf)
        game1943.find_game = lambda given=None: given or 'G'
        data = {'hf': prep_hf}
        game1943.Game = lambda game, level: SimpleNamespace(data=lambda n: data['hf'] if 'heightfield' in n else None)
        st = nj.read(lv)
        try:
            hf = navexport1943.prepared_terrain(lv, st)[3]
            got = f'terrain up to {float(hf.max[1]):g}'
        except nj.NavKitError as e:
            got = f'refused: {e}'
        check(got == 'terrain up to 100', f'the prepared terrain, the game copy unchanged ({got})')
        data['hf'] = game_hf                             # the game copy's height field changed after the preparation
        try:
            hf = navexport1943.prepared_terrain(lv, st)[3]
            got = f'terrain up to {float(hf.max[1]):g}'
        except nj.NavKitError as e:
            got = f'refused: {e}'
        check(got.startswith('refused') and 'changed since' in got,
              f'a game copy whose height field changed since the preparation is refused, not mixed in ({got[:140]})')
        data['hf'] = prep_hf
        with open(os.path.join(p['objects'], 'heightfield.res'), 'wb') as f:
            f.write(game_hf)                             # the saved one replaced
        try:
            navexport1943.prepared_terrain(lv, st)
            got = 'taken'
        except nj.NavKitError as e:
            got = f'refused: {e}'
        check(got.startswith('refused') and 'not the height field' in got,
              f'a saved height field that is not the stamped one is refused ({got[:140]})')
        import navaudit1943_checks
        import jumplinks1943
        for name, mod in (('navaudit1943_checks', navaudit1943_checks), ('jumplinks1943', jumplinks1943)):
            src = open(mod.__file__, encoding='utf-8').read()
            check('nj.prepared_heightfield(st, lv,' in src, f'{name} reads the prepared height field too')
        game1943.Game, game1943.find_game = real[0], real[1]

        # 3. pockets
        w = graph(2, [(1, 0)])
        check(rsnvtool.pieces(w, [0, 1]) == [[0, 1]], f'1 -> 0 alone: one piece ({rsnvtool.pieces(w, [0, 1])})')
        w = graph(5, [(0, 1), (2, 1), (3, 4), (4, 3)])
        got = rsnvtool.pieces(w, list(range(5)))
        flat = [n for piece in got for n in piece]
        check(got == [[0, 1, 2], [3, 4]] and sorted(flat) == list(range(5)),
              f'directed edges: weakly connected pieces, disjoint, every node once ({got})')
        w = graph(4, [(0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2)], tight={(1, 2), (2, 1)})
        check(rsnvtool.pieces(w, list(range(4))) == [[0, 1], [2, 3]], 'tight edges are left out')
        w = graph(4, [(0, 1), (1, 0), (1, 2), (2, 1), (2, 3)])
        check(rsnvtool.strong_core(w, list(range(4))) == [0, 1, 2],
              f'the strongly connected core leaves out a node reached only one way ({rsnvtool.strong_core(w, list(range(4)))})')
        w = graph(6, [(0, 1), (1, 0), (1, 2), (2, 1), (3, 2), (4, 5), (5, 4)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rsnvtool.cmd_pockets(w, SimpleNamespace(top=5))
        text = buf.getvalue()
        check('6 nodes; without tight edges 2 pieces, the largest 4; 2 nodes (33.33 %) outside it' in text
              and '1 nodes (16.67 %) are outside its strongly connected core' in text,
              f'pockets: shares of disjoint pieces, and the one-way share ({text.strip()!r})')
    finally:
        game1943.Game, game1943.find_game, rsnv.load, rsnvtrip.transfers = real
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
