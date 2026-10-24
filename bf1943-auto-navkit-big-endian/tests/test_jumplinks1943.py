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


if __name__ == '__main__':
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
