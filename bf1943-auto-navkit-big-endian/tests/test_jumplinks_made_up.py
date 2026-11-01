r"""NavKit (big-endian): jump links on made-up samples and a made-up level

jumplinks1943 without game data: raised floors (a boardwalk 0.6 m over the ground gets links along its edge, a crate
top and a floor a step over the ground get none); the gun platform rule's refusals (a gun off the grid, under every
surface, on blocked floor, on a platform with no deck in reach); a ship box over no samples; current_detailed; and
level_links / main on a prepared level in a temporary work folder (navkit.json, a height field, object boxes and a
samples file written here, the game copy and its spawn map stood in): a carrier's ledge links come from the 0.8 m or
the 0.2 m samples as navkit.json says, a ship whose blueprint is ForbidPathfinding gets none, and main refuses
another game copy's links over the build's.
"""
import hashlib
import json
import math
import os
import struct
from types import SimpleNamespace

import numpy as np

import jumplinks1943 as J
import navsample
import spawnmap
from bfbc2core import game1943

quiet = lambda *a: None                                                 # noqa: E731


def samples(cols, pitch, n):
    """navsample.Samples from columns [(y, flags)] (any order: sorted top down), n x n, from (0, 0)."""
    cols = [sorted(c, reverse=True) for c in cols]
    start = np.zeros(n * n + 1, np.int64)
    np.cumsum([len(c) for c in cols], out=start[1:])
    y = np.array([v for c in cols for v, _ in c], np.float32)
    f = np.array([fl for c in cols for _, fl in c], np.uint8)
    normal = np.tile(np.array([0, 1, 0], np.float32), (len(y), 1))
    return navsample.Samples(pitch, 0.0, 0.0, n, n, 16.0, -22.0, 0.35, 0.37, 1.8, 0.0, start, y, normal, f, None)


def samples_file(path, cols, pitch, n):
    """A navsample version-1 file of the columns (what navaudit1943.load_samples reads)."""
    out = bytearray(navsample.HEADER.pack(b'ONS1', 1, pitch, 0.0, 0.0, n, n, 16.0, -22.0, 0.35, 0.37, 1.8, 0.0, 0))
    for c in cols:
        c = sorted(c, reverse=True)
        out.append(len(c))
        for y, fl in c:
            out += struct.pack('<f3bB', y, 0, 127, 0, fl)
    with open(path, 'wb') as f:
        f.write(bytes(out))


def yard(pitch=0.2, n=60):
    """Ground at 0 everywhere; a boardwalk floor at 0.6 (x, z 3..8), a crate top at 0.6 (x 9.6..10.4, z 1..1.8), a
    low platform at 0.3 (x 0.6..2.6, z 9..11.4)."""
    cols = []
    for j in range(n):
        for i in range(n):
            x, z = i * pitch, j * pitch
            c = [(0.0, 0)]
            if 3.0 <= x < 8.0 and 3.0 <= z < 8.0:
                c.append((0.6, 0))
            elif 9.6 <= x < 10.4 and 1.0 <= z < 1.8:
                c.append((0.6, 0))
            elif 0.6 <= x < 2.6 and 9.0 <= z < 11.4:
                c.append((0.3, 0))
            cols.append(c)
    return samples(cols, pitch, n)


def test_raised_floor_links():
    s = yard()
    boxes = [{'bp': 'objects/made_up/boardwalk', 'min': [3.0, -0.5, 3.0], 'max': [8.0, 1.0, 8.0]},
             {'bp': 'objects/made_up/crate', 'min': [9.6, 0.0, 1.0], 'max': [10.4, 0.7, 1.8]},
             {'bp': 'objects/made_up/low', 'min': [0.6, 0.0, 9.0], 'max': [2.6, 0.5, 11.4]},
             {'bp': 'objects/made_up/far', 'min': [40.0, 0.0, 40.0], 'max': [50.0, 1.0, 50.0]}]
    logged = []
    links = J.raised_floor_links(s, lambda x, z: 0.0, boxes, logged.append)
    assert links, 'the boardwalk 0.6 m over the ground gets jump links'
    assert len(links) <= J.LINK_MAX
    for L in links:
        assert L['kind'] == 'jump' and L['rule'] == 'raised floor' and L['bp'] == 'jump link: raised floor (boardwalk)'
        assert abs(L['deck'][1] - 0.6) < 1e-3 and abs(L['platform'][1]) < 1e-3, 'from the ground up to the floor'
        assert abs(L['rise'] - 0.6) < 1e-3 and L['gap'] <= J.LINK_REACH + 1e-9
        assert 3.0 - 1e-6 <= L['deck'][0] < 8.0 and 3.0 - 1e-6 <= L['deck'][2] < 8.0, 'the upper end on the boardwalk'
        outside = not (3.0 <= L['platform'][0] < 8.0 and 3.0 <= L['platform'][2] < 8.0)
        assert outside, 'the lower end on the ground beside it, not under it (no headroom there)'
    ends = [(L['deck'][0], L['deck'][2]) for L in links]
    assert all(math.hypot(a[0] - b[0], a[1] - b[1]) >= J.LINK_SPACING - 1e-9
               for k, a in enumerate(ends) for b in ends[k + 1:]), 'links along one floor at least LINK_SPACING apart'
    assert any('boardwalk' in m and 'jump link' in m for m in logged)
    assert not any('crate' in L['bp'] or 'low' in L['bp'] for L in links), \
        'no link onto a crate top (under FLOOR_MIN_AREA) nor onto a floor a step over the ground'


def test_platform_refusals():
    pitch, n = 0.2, 40
    deep = lambda c: 0.0                                                # noqa: E731  water at 0
    bed = lambda x, z: -20.0                                            # noqa: E731  sea bed far under it
    flat = samples([[(9.16, 0), (-20.0, 0)]] * (n * n), pitch, n)
    logged = []
    assert J.platform_links(flat, [('off', (-50.0, 9.4, 2.0))], deep, bed, logged.append) == [], 'a gun off the grid'
    assert J.platform_links(flat, [('under', (4.0, -30.0, 4.0))], deep, bed, logged.append) == [], \
        'a gun under every surface of its column'
    assert logged == []
    assert J.platform_links(flat, [('AA', (4.0, 9.4, 4.0))], deep, bed, logged.append) == []
    assert any('no deck within' in m for m in logged), f'a platform with no deck in reach is said so ({logged})'
    blocked = samples([[(9.16, 1), (-20.0, 0)]] * (n * n), pitch, n)
    logged.clear()
    assert J.platform_links(blocked, [('AA', (4.0, 9.4, 4.0))], deep, bed, logged.append) == []
    assert any('no clear floor beside the gun' in m for m in logged), logged


def test_ship_over_no_samples():
    s = samples([[] for _ in range(100)], 0.2, 10)
    links, report = J.ship_ledge_links(s, [{'bp': 'objects/x/ship', 'min': [0, -20, 0], 'max': [2, 15, 2]}],
                                       lambda x, z: -20.0, log=quiet)
    assert links == [] and report == [], 'a ship box over empty columns: no floor, no report'


def test_current_detailed():
    st = {'preparation': 'p', 'detailed_sectors': ['0a0b'], 'samples': {'0.2': {'preparation': 'p', 'sectors': ['0a0b']}}}
    assert J.current_detailed(st)
    assert not J.current_detailed(dict(st, preparation='q')), 'a 0.2 m pass of another preparation'
    assert not J.current_detailed(dict(st, detailed_sectors=['0a0c'])), 'over other detailed sectors'
    assert not J.current_detailed({'samples': {}}), 'no preparation'


# ---- a prepared level -------------------------------------------------------------------------------------------
LV = 'made_up_jump'
SHIP = {'bp': 'objects/made_up/carrier', 'min': [0.0, -20.0, 0.0], 'max': [80.0, 15.0, 80.0]}


def ship_columns(n=60, pitch=0.2):
    """test_jumplinks1943.ship's carrier: a deck at 10.0, a gallery 0.58 m under it beyond a blocked edge band."""
    cols = []
    for _j in range(n):
        for i in range(n):
            x = i * pitch
            if x < 6.0:
                c = [(10.0, 0)]
            elif x < 6.4:
                c = [(9.42, 1), (10.0, 0)]
            elif x < 8.0:
                c = [(9.42, 0)]
            else:
                c = []
            cols.append(c + [(-20.0, 0)])
    return cols


def heightfield(height=-20.0, size=4):
    """A TerrainHeightfield over 0..100 m, flat at height."""
    head = struct.pack('<6f6IB', 0.0, height - 1.0, 0.0, 100.0, height + 1.0, 100.0, size, 0, 0, 0, 0, 0, 0)
    return head + struct.pack(f'<{size * size}H', *([32768] * (size * size)))


class Level:
    """The work folder of a prepared level, the game copy and the spawn map stood in (restored by close())."""

    def __init__(self, tmp, objects=(), detailed=False, guns=()):
        self.saved = (os.environ.get('BF1943_WORK'), game1943.find_game, game1943.Game, spawnmap.spawn_map,
                      dict(J.SHIP_REPORT))
        os.environ['BF1943_WORK'] = str(tmp)
        hf = heightfield()
        game1943.find_game = lambda given=None: given or 'the default copy'
        game1943.Game = lambda game, lv: SimpleNamespace(data=lambda name: hf if 'heightfield' in name else None)
        spawnmap.spawn_map = lambda game, lv: {'objectives': [{'spawns': [{'name': 'gun', 'vehicle': 'aa_bofors',
                                                                           'position': list(g)} for g in guns]}],
                                               'unowned_vehicle_spawns': []}
        w = os.path.join(str(tmp), 'levels', LV)
        self.objects, self.samples, self.navkit = (os.path.join(w, k) for k in ('objects', 'samples', 'navkit'))
        for d in (self.objects, self.samples, self.navkit):
            os.makedirs(d)
        with open(os.path.join(self.objects, 'heightfield.res'), 'wb') as f:
            f.write(hf)
        for name, value in (('objects.json', list(objects)), ('group_instances.json', []), ('object_boxes.json', [SHIP])):
            with open(os.path.join(self.objects, name), 'w', encoding='utf-8', newline='') as f:
                json.dump(value, f)
        st = {'level': LV, 'game': 'G:/prepared', 'preparation': 'p', 'sector_offsets': [0, 0], 'sea_level': 0.0,
              'objects_sha256': {'heightfield.res': hashlib.sha256(hf).hexdigest()}}
        if detailed:
            st.update(detailed_sectors=['0000'], samples={'0.2': {'preparation': 'p', 'sectors': ['0000']}})
        with open(os.path.join(self.navkit, 'navkit.json'), 'w', encoding='utf-8', newline='') as f:
            json.dump(st, f)

    def close(self):
        work, game1943.find_game, game1943.Game, spawnmap.spawn_map, report = self.saved
        J.SHIP_REPORT.clear()
        J.SHIP_REPORT.update(report)
        if work is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = work


def test_level_links_ship_ledges(tmp_path):
    level = Level(tmp_path, guns=[(3.0, 10.5, 3.0)])
    try:
        samples_file(os.path.join(level.samples, 'samples.bin'), ship_columns(), 0.2, 60)
        logged = []
        links = J.level_links(LV, log=logged.append)
        assert links and all(L['rule'] == 'ship ledge' and abs(L['rise'] - 0.58) < 1e-3 for L in links), \
            f'the carrier\'s gallery 0.58 m under its deck is linked ({len(links)} links)'
        assert J.SHIP_REPORT.get('normal') and J.SHIP_REPORT['normal'][0]['ship'] == 'carrier'
        assert any('1 AA gun(s)' in m for m in logged), 'the gun is looked at (no water mesh: no platform link)'
        assert any('1 ship(s)' in m for m in logged)
    finally:
        level.close()


def test_level_links_forbid_pathfinding(tmp_path):
    level = Level(tmp_path, objects=[{'bp': SHIP['bp'], 'flags': [1, 0, 0, 0]}])
    try:
        samples_file(os.path.join(level.samples, 'samples.bin'), ship_columns(), 0.2, 60)
        assert J.level_links(LV, log=quiet) == [], 'a ship whose blueprint is ForbidPathfinding is no floor: no links'
    finally:
        level.close()


def test_level_links_detailed(tmp_path):
    level = Level(tmp_path, detailed=True)
    try:
        samples_file(os.path.join(level.samples, 'samples_02.bin'), ship_columns(), 0.2, 60)
        # the 0.8 m samples: the same deck with nothing under its edge (they would make no link)
        samples_file(os.path.join(level.samples, 'samples.bin'), [[(10.0, 0), (-20.0, 0)]] * 3600, 0.2, 60)
        links = J.level_links(LV, log=quiet)
        assert links and J.SHIP_REPORT.get('detailed'), 'the ship\'s detailed sector is read from the 0.2 m samples'
        assert J.level_links(LV, log=quiet, detailed=False) == [], \
            'told the build reads no 0.2 m samples, the 0.8 m file only'
    finally:
        level.close()


def test_main(tmp_path):
    level = Level(tmp_path)
    saved = J.level_links
    try:
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = J.main(['jumplinks1943.py', LV, '--game', 'G:/another'])
        assert code == 1 and 'prepared from G:/prepared, not G:/another' in out.getvalue(), \
            'another copy\'s links are not written over the build\'s'
        assert not os.path.isfile(os.path.join(level.navkit, 'jump_links.json'))
        J.level_links = lambda lv, game=None, **k: [{'kind': 'jump', 'game': game}]
        target = os.path.join(str(tmp_path), 'links.json')
        with contextlib.redirect_stdout(out):
            assert J.main(['jumplinks1943.py', LV, '--game', 'G:/another', '--json', target]) == 0
            assert J.main(['jumplinks1943.py', LV]) == 0
        assert json.load(open(target, encoding='utf-8')) == [{'kind': 'jump', 'game': 'G:/another'}], \
            'with --json, another copy\'s links go to that file'
        assert json.load(open(os.path.join(level.navkit, 'jump_links.json'), encoding='utf-8')) == \
            [{'kind': 'jump', 'game': None}], 'without, the build\'s jump_links.json'
    finally:
        J.level_links = saved
        level.close()
