"""navdata's cell heights use navdata.Y_OFFSET (no game data): a made-up area whose outline stands 0.9 m above
ground at y 10 gives cells at y 10 when Y_OFFSET is BF1943's 0.9, and the cache key changes with the offset and with
the terrain's contents.
navdiff's own default (DICE's 0.95) is unchanged for the little-endian variant's BFBC2 comparisons.

  python -B test_navdata_offset.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import navdata  # noqa: E402
import navdiff  # noqa: E402  (navdata puts its folder on the path)

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


if __name__ == '__main__':
    ring = [(0.0, 10.9, 0.0), (8.0, 10.9, 0.0), (8.0, 10.9, 8.0), (0.0, 10.9, 8.0), (0.0, 10.9, 0.0)]
    area = SimpleNamespace(outline=[ring], portals=[], border_portals=[])
    c9 = navdiff.area_cells(area, 0.9)
    check(len(c9) > 50 and all(abs(v - 10.0) < 1e-6 for v in c9.values()), f'{len(c9)} cells at ground y 10.0 with offset 0.9')
    c95 = navdiff.area_cells(area)
    check(all(abs(v - 9.95) < 1e-6 for v in c95.values()), "navdiff's default offset is still DICE's 0.95")
    # terrain snapping: a floor 1.0 m over flat terrain keeps its height (Wake Island's beach house, South Base spawn
    # alternate 2: the reader put the house's area at the terrain under it); terrain areas, sloped too, take the
    # terrain's exact height
    class Flat:
        def height(self, x, z):
            return np.zeros(np.shape(x))

    class Slope:                                     # 20 degrees along x (steeper: the 1.5 m rule, unchanged,
                                                     # keeps estimates 3 m inside an 8 m area)
        def height(self, x, z):
            return np.asarray(x, float) * 0.364

    floor = SimpleNamespace(outline=[[(0.0, 1.9, 0.0), (8.0, 1.9, 0.0), (8.0, 1.9, 8.0), (0.0, 1.9, 8.0), (0.0, 1.9, 0.0)]],
                            portals=[], border_portals=[])
    c = navdiff.area_cells(floor, 0.9, points=True)
    v = np.array(list(c.values()))
    xz = np.array(list(c.keys()), float) * navdata.GRID
    y = navdata._ground(xz, v[:, 0], Flat(), v[:, 1:])
    check(np.allclose(y, 1.0), f'a floor 1.0 m over flat terrain stays at 1.0 (got {y.min():.2f}..{y.max():.2f})')
    y_old = navdata._ground(xz, v[:, 0], Flat())
    check(np.allclose(y_old, 0.0), 'without the boundary points the old rule snaps it to the terrain (the bug reproduced)')
    ring = [(t, 0.0) for t in np.arange(0, 8, 0.4)] + [(8.0, t) for t in np.arange(0, 8, 0.4)] + \
           [(8.0 - t, 8.0) for t in np.arange(0, 8, 0.4)] + [(0.0, 8.0 - t) for t in np.arange(0, 8.4, 0.4)]
    edge = [(x, 0.364 * x + 0.9, z) for x, z in ring]     # vertices along the edge, as built outlines have
    hill = SimpleNamespace(outline=[edge], portals=[], border_portals=[])
    c = navdiff.area_cells(hill, 0.9, points=True)
    v = np.array(list(c.values()))
    xz = np.array(list(c.keys()), float) * navdata.GRID
    y = navdata._ground(xz, v[:, 0], Slope(), v[:, 1:])
    check(np.allclose(y, xz[:, 0] * 0.364), 'cells of an area on 20-degree terrain take the exact terrain height')
    old = navdata.Y_OFFSET
    try:
        path = os.path.abspath(__file__)
        navdata.Y_OFFSET = 0.95
        k95 = navdata._key(path)
        navdata.Y_OFFSET = 0.9
        check(navdata._key(path) != k95, 'the cache key changes with Y_OFFSET (no stale 0.95 heights)')
    finally:
        navdata.Y_OFFSET = old
    # the cache key holds the terrain's contents (2026-10-30: only whether there was one - a changed terrain under
    # the same .hpd got the old terrain's heights back from the cache)
    import tempfile
    from bfbc2core import terrain

    def field(lift):
        raw = np.full((4, 4), 1000 + lift, np.uint16)
        return terrain.Heightfield(np.array([0.0, 0.0, 0.0]), np.array([8.0, 100.0, 8.0]), 4, raw,
                                   (raw * (100.0 / 65535.0)).astype(np.float32))
    path = os.path.abspath(__file__)
    check(navdata._key(path, field(0)) == navdata._key(path, field(0)), 'the same terrain contents give the same key')
    check(navdata._key(path, field(0)) != navdata._key(path, field(500)), 'other terrain contents give another key')
    real_load = navdata._load
    try:
        navdata._load = lambda p, hf, cache_dir: SimpleNamespace(top=float(hf.height(np.zeros(1), np.zeros(1)).max()))
        with tempfile.TemporaryDirectory() as d:
            a = navdata.load(path, field(0), cache_dir=d)
            b = navdata.load(path, field(500), cache_dir=d)
            again = navdata.load(path, field(0), cache_dir=d)
            check(b.top > a.top and again.top == a.top, f'a changed terrain is read again, not the cached result '
                                                         f'({a.top:.3f}, then {b.top:.3f}; the first again {again.top:.3f})')
            n = len(os.listdir(d))
            navdata.load(path, Flat(), cache_dir=d)
            check(len(os.listdir(d)) == n, 'a height source without contents to hash is not cached')
    finally:
        navdata._load = real_load
    # a cell cache cut short (the tool closed while writing it): rebuilt, not an error on every later load (2026-10-30)
    with tempfile.TemporaryDirectory() as d:
        hpd = os.path.join(d, 'lv.hpd')
        with open(hpd, 'wb') as fh:
            fh.write(b'made-up')
        cp = os.path.join(d, navdata._key(hpd) + '.npz')
        good = os.path.join(d, 'good.npz')
        np.savez(good, xz=np.zeros((5, 2)), y=np.zeros(5), area=np.zeros(5, np.int32), est=np.zeros(5), near=np.zeros((5, 2)))
        with open(good, 'rb') as fh, open(cp, 'wb') as out:
            out.write(fh.read()[:200])
        try:
            xz, y, area, est = navdata._cells(SimpleNamespace(sectors=[]), None, d, hpd)
            with np.load(cp) as z:
                again = len(z['xz'])
            got = f'rebuilt: {len(xz)} cells, the cache readable again ({again} cells)'
            ok = len(xz) == 0 and again == 0
        except Exception as e:
            got, ok = f'{type(e).__name__}: {e}', False
        leftovers = [n for n in os.listdir(d) if n.endswith('.tmp')]
        check(ok and not leftovers, f'a cut-short cell cache is rebuilt ({got}; temporary files {leftovers})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
