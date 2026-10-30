"""Jump links for ships' AA gun platforms (no game data): a made-up carrier side - a deck at 10.0 over deep water, a
gun platform 0.84 m under it beyond a band the deck edge blocks, and the platform's outer rail at the deck's height -
as on Wake Island's carriers (2026-10-24: the four AA guns were off the network).

  python -B test_jumplinks1943.py
"""
import os
import sys

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
sys.path.insert(0, os.path.dirname(HERE))
import jumplinks1943 as J  # noqa: E402
import navsample  # noqa: E402
import navwalk  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def side(pitch=0.2, n=50):
    """Columns along x: deck (x < 2.0), the edge band at platform height, blocked (2.0-2.6), the platform (2.6-6.0),
    its rail top at the deck's height (6.0-6.2), open water beyond; the sea bed at -20 under everything."""
    cols = []
    for j in range(n):
        for i in range(n):
            x = i * pitch
            s = []
            if x < 2.0:
                s.append((10.0, 0))
            elif x < 2.2:                                   # the deck's overhang, the platform blocked under it
                s += [(10.0, 0), (9.16, 1)]
            elif x < 2.6:                                   # beside the deck edge: blocked by its wall
                s.append((9.16, 1))
            elif x < 6.0:
                s.append((9.16, 0))
            elif x < 6.2:
                s.append((10.0, 0))
                s.append((9.16, 1))
            s.append((-20.0, 0))
            cols.append(s)
    start = np.zeros(n * n + 1, np.int64)
    np.cumsum([len(c) for c in cols], out=start[1:])
    y = np.array([v for c in cols for v, _ in c], np.float32)
    f = np.array([fl for c in cols for _, fl in c], np.uint8)
    normal = np.tile(np.array([0, 1, 0], np.float32), (len(y), 1))
    return navsample.Samples(pitch, 0.0, 0.0, n, n, 12.0, -22.0, 0.35, 0.37, 1.8, 0.0, start, y, normal, f, None)


def ship(pitch=0.2, n=60):
    """A made-up carrier (ship_ledge_links): along x, the deck at 10.0 (x < 6), a band its edge blocks (6.0-6.4), a
    gallery 0.58 m under it (6.4-8.0, z < 6); beside it (z >= 6) a gallery behind a wall 1.2 m above the deck; a
    superstructure floor 4 m up (x 0-2, z 0-2) and a lower gallery 3.5 m down (x 8.4-10, z < 4)."""
    cols = []
    for j in range(n):
        for i in range(n):
            x, z = i * pitch, j * pitch
            s = []
            if x < 2.0 and z < 2.0:
                s += [(10.0, 1), (14.0, 0)]                 # the deck under the superstructure blocked, its floor 4 m up
            elif x < 6.0:
                s.append((10.0, 0))
            elif x < 6.4:
                s += [(9.42, 1), (11.2 if z >= 6.0 else 10.0, 0)]   # the deck edge (a wall where z >= 6)
            elif x < 8.0:
                s.append((9.42, 0))
            elif 8.4 <= x < 10.0 and z < 4.0:
                s.append((6.5, 0))
            s.append((-20.0, 0))
            cols.append(sorted(s, reverse=True))           # a column's surfaces from the top down
    start = np.zeros(n * n + 1, np.int64)
    np.cumsum([len(c) for c in cols], out=start[1:])
    y = np.array([v for c in cols for v, _ in c], np.float32)
    f = np.array([fl for c in cols for _, fl in c], np.uint8)
    normal = np.tile(np.array([0, 1, 0], np.float32), (len(y), 1))
    return navsample.Samples(pitch, 0.0, 0.0, n, n, 16.0, -22.0, 0.35, 0.37, 1.8, 0.0, start, y, normal, f, None)


def ship_checks():
    s = ship()
    box = {'bp': 'objects/x/ship', 'min': [0.0, -20.0, 0.0], 'max': [11.8, 15.0, 11.8]}
    bed = lambda x, z: -20.0
    check(J.ship_boxes([box], bed, 0.0) == [] and J.ship_boxes([dict(box, max=[80.0, 15.0, 80.0])], bed, 0.0) != [],
          'a ship: a box of SHIP_MIN_FOOTPRINT or more over deep water')
    check(J.ship_boxes([dict(box, max=[80.0, 15.0, 80.0])], lambda x, z: -1.0, 0.0) == [],
          'no ship over water shallower than WATER_DEPTH')
    links, rep = J.ship_ledge_links(s, [box], bed, log=lambda *a: None)
    check(len(links) >= 1 and all(abs(L['rise'] - 0.58) < 1e-3 for L in links), f'the gallery 0.58 m down is linked '
          f'({len(links)} link(s), rises {sorted({L["rise"] for L in links})})')
    check(all(L['platform'][2] < 6.0 for L in links), 'no link through the wall (z >= 6)')
    check(all(L['kind'] == 'jump' and L['rule'] == 'ship ledge' for L in links), "records of kind 'jump', rule 'ship ledge'")
    ys = sorted(r['y'] for r in rep[0]['not linked']) if rep else []
    check(any(abs(v - 14.0) < 1e-3 for v in ys) and any(abs(v - 6.5) < 1e-3 for v in ys),
          f'the superstructure floor (4 m up) and the lower gallery (3.5 m down) are not linked ({ys})')
    taken = [tuple(links[0]['platform'])] if links else []
    again, _ = J.ship_ledge_links(s, [box], bed, taken=taken, log=lambda *a: None)
    check(again == [], 'a piece already linked (a gun platform) gets no ledge link')
    none, _ = J.ship_ledge_links(s, [box], bed, usable=lambda x, z: False, log=lambda *a: None)
    check(none == [], 'no link where the grid does not build (usable)')


def game_checks():
    """navkit.py's --game reaches jump-link generation (2026-10-26: level_links always took the default copy)."""
    import navkit
    from bfbc2core import game1943
    seen = []

    class Stop(Exception):
        pass

    def find_game(g=None):
        seen.append(g)
        raise Stop()
    saved = game1943.find_game
    game1943.find_game = find_game
    try:
        try:
            J.level_links('wake_island_s', log=lambda *a: None, game='X:/custom/dist/xenon')
        except (Stop, FileNotFoundError, OSError):
            pass
        check(seen[:1] == ['X:/custom/dist/xenon'], f'level_links uses the game folder it is given ({seen[:1]})')
        calls = []
        saved_links = J.level_links

        def level_links(lv, log=print, game=None):
            calls.append(game)
            raise Stop()
        J.level_links = level_links
        # a level prepared and sampled from the custom copy, in a temporary work folder (build refuses samples
        # without the stamps of its preparation, and a --game other than the preparation's)
        import json
        import shutil
        import tempfile
        work = tempfile.mkdtemp(prefix='jumplinks_')
        saved_work = os.environ.get('BF1943_WORK')
        os.environ['BF1943_WORK'] = work
        try:
            p = navkit.paths('wake_island_s')
            for k in ('objects', 'samples', 'navkit'):
                os.makedirs(p[k])
            open(os.path.join(p['samples'], 'samples.bin'), 'wb').write(b'samples')
            stamp = dict(preparation='p', game='X:/custom/dist/xenon', time='-')
            with open(p['settings'], 'w', encoding='utf-8') as f:
                json.dump(dict(level='wake_island_s', game='X:/custom/dist/xenon', preparation='p', sector_offsets=[-11, -11],
                               samples={'0.8': stamp}, sampled='-'), f)
            try:
                navkit.build('X:/custom/dist/xenon', 'wake_island_s', log=lambda *a: None)
            except (Stop, Exception):
                pass
        finally:
            J.level_links = saved_links
            if saved_work is None:
                os.environ.pop('BF1943_WORK', None)
            else:
                os.environ['BF1943_WORK'] = saved_work
            shutil.rmtree(work, ignore_errors=True)
        check(calls[:1] == ['X:/custom/dist/xenon'], f"navkit.py build passes its --game to level_links ({calls[:1]})")
    finally:
        game1943.find_game = saved


if __name__ == '__main__':
    ship_checks()
    game_checks()
    s = side()
    sea = lambda c: 0.0                     # water at 0, the sea bed at -20: deep
    bed = lambda x, z: -20.0
    links = J.platform_links(s, [('AA', (4.0, 9.4, 5.0))], sea, bed, log=lambda *a: None)
    check(len(links) == 1, f'one link for the ship gun ({len(links)})')
    if links:
        L = links[0]
        check(L['kind'] == 'jump' and abs(L['rise'] - 0.84) < 1e-3, f"kind jump, rise {L['rise']} m")
        check(L['deck'][0] < 2.0, f"the deck end is the deck (x {L['deck'][0]}), not the platform's rail (x 6.0)")
        check(L['gap'] <= J.JUMP_GAP and abs(L['platform'][1] - 9.16) < 1e-3, f"platform end at 9.16, gap {L['gap']} m")
        keep = (np.abs(s.y - 10.0) < 1e-3) & (np.arange(len(s.y)) < 0)   # nothing kept yet
        ok = (s.flags & 1) == 0
        foot, top = navwalk.ladder_ends(s, ok, keep, L)
        check(foot is not None and abs(s.y[foot] - 9.16) < 1e-3, 'ladder_ends: the foot is the platform floor')
        check(top is not None and abs(s.y[top] - 10.0) < 1e-3, 'ladder_ends: the top is at the deck height')
        cx = (top is not None) and (np.searchsorted(s.start, top, side='right') - 1) % s.nx * s.pitch
        check(top is not None and cx < 2.2, f'ladder_ends: the top lies on the deck side (x {cx:.1f}), not the rail')
    land = J.platform_links(s, [('AA', (4.0, 9.4, 5.0))], sea, lambda x, z: 9.16, log=lambda *a: None)
    check(land == [], 'no link for a gun standing on terrain')
    shallow = J.platform_links(s, [('AA', (4.0, 9.4, 5.0))], lambda c: -19.0, bed, log=lambda *a: None)
    check(shallow == [], 'no link for a gun over water shallower than WATER_DEPTH')
    lad = {'lo': [0, 1.0, 0], 'hi': [0.5, 6.0, 0.5]}
    check(navwalk._bands(lad, navwalk.LADDER_REACH) == (((0.0, 2.0), (4.5, 6.5)), navwalk.LADDER_REACH),
          "a ladder keeps LADDER_FOOT, LADDER_TOP and LADDER_REACH")
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
