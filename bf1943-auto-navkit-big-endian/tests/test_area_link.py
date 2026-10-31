r"""NavKit's area link rule (navbuild.Level.connected with rule 'terrain' / 'raw', navbuild.components) on a sample
grid built here (no game data): straight neighbours join an area up to a raw 0.6 m step, diagonal ones when a
route through a side column (walkable or not) has both steps within 0.6 m; the slope rule still joins a steep
plane, so portals and routes keep it; object surfaces use the slope rule under 'terrain'.

  python test_area_link.py
"""
import sys
from types import SimpleNamespace

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE, suitepath.NAVKIT_LE)
import navbuild  # noqa: E402
import navwalk  # noqa: E402


def level(heights, keep=None, terrain=None, pitch=0.8, **settings):
    """One surface per column; heights[j][i]. Normals from the height differences (a plane gets its slope). The
    Level is on the grid at pitch (its step rule), with NavKit's defaults but for settings (navbuild.Settings')."""
    h = np.array(heights, float)
    nz, nx = h.shape
    gx = np.gradient(h, 0.8, axis=1) if nx > 1 else np.zeros_like(h)
    gz = np.gradient(h, 0.8, axis=0) if nz > 1 else np.zeros_like(h)
    n = np.stack([-gx.ravel(), np.ones(h.size), -gz.ravel()], 1)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    hit = None
    if terrain is not None:
        hit = np.zeros((h.size, 2), np.int64)
        hit[np.array(terrain, bool).ravel(), 0] = navwalk.TERRAIN_SHAPE_VTABLE
    s = SimpleNamespace(nx=nx, nz=nz, pitch=0.8, min_x=0.0, min_z=0.0, start=np.arange(h.size + 1),
                        y=h.ravel().astype(np.float32), normal=n.astype(np.float32), hit=hit)
    k = np.ones(h.size, bool) if keep is None else np.array(keep, bool).ravel()
    cfg = navbuild.Settings(**settings)
    return navbuild.Level(s, k, 0.28, grid=navbuild.grid_at(pitch, cfg), settings=cfg)


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    lv = level([[0.0, 0.55, 1.2]])
    check(lv.connected(0, 1, 1, 0, 'raw'), 'straight step 0.55 m links')
    check(not lv.connected(1, 2, 1, 0, 'raw'), 'straight step 0.65 m does not')

    plane = level([[0.0, 0.66, 1.32, 1.98]])           # a 39.5 deg plane: walkable, steps of 0.66 m
    check(lv is not None and plane.connected(0, 1, 1, 0, 'slope'), 'the slope rule joins a steep plane')
    check(not plane.connected(0, 1, 1, 0, 'raw'), 'the raw rule cuts it')
    check(len(navbuild.components(plane, {k: (k, 0) for k in range(4)}, 0, 0)) == (4 if navbuild.AREA_LINK != 'slope' else 1),
          'components: the steep plane gives one area per cell')

    # diagonal (0,0) -> (1,1), step 0.9: through side (1,0) at 0.45 (both steps 0.45) - links, even unwalkable
    d = level([[0.0, 0.45], [5.0, 0.9]], keep=[[1, 0], [0, 1]])
    check(d.connected(0, 3, 1, 1, 'raw'), 'diagonal links through an unwalkable side cell with steps within 0.6 m')
    d2 = level([[0.0, 5.0], [5.0, 0.9]])
    check(not d2.connected(0, 3, 1, 1, 'raw'), 'diagonal without such a side route does not link')
    d3 = level([[0.0, 0.0], [0.0, 0.0]])
    check(d3.connected(0, 3, 1, 1, 'raw'), 'flat diagonal links')

    obj = level([[0.0, 0.66, 1.32]], terrain=[[1, 0, 0]])
    check(obj.connected(1, 2, 1, 0, 'terrain'), "'terrain': two object surfaces use the slope rule")
    check(obj.connected(0, 1, 1, 0, 'terrain'), "'terrain': terrain next to an object uses the slope rule")
    ter = level([[0.0, 0.66, 1.32]], terrain=[[1, 1, 1]])
    check(not ter.connected(0, 1, 1, 0, 'terrain'), "'terrain': two terrain surfaces use the raw rule")
    check(navbuild.LINK_RULE == 'slope', 'portals and routes use the slope rule')

    # AREA_CLIFF: a ramp that turns back beside its own foot (row 0 rises to 0.9, row 1 climbs on to 2.1 above row
    # 0's start): one area without the rule; with it, no two side-by-side cells of one area over 1 m apart
    ramp = [[0.0, 0.3, 0.6, 0.9], [2.1, 1.8, 1.5, 1.2]]
    members = {k: (k % 4, k // 4) for k in range(8)}
    check(len(navbuild.components(level(ramp, area_cliff=None), members, 0, 0)) == 1,
          'AREA_CLIFF off: the turning ramp is one area')
    cliff = level(ramp, area_cliff=1.0)
    comps = navbuild.components(cliff, members, 0, 0)
    y = cliff.y
    worst = max((abs(y[a[2]] - y[b[2]]) for c in comps for a in c for b in c
                 if abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1), default=0.0)
    check(len(comps) >= 2 and worst <= 1.0 + 1e-6, f'AREA_CLIFF 1.0: {len(comps)} areas, side-by-side cells of one '
                                                   f'area at most {worst:.2f} m apart')
    # BF1943's 0.6 m (2026-10-29) on 0.2 m cells: a ramp turning back with its foot 0.7 m beside its upper
    # leg (between 0.6 and the old 1.0) is one area at 1.0 and split at 0.6; no straight pair over 0.6 m remains
    ramp2 = [[0.0, 0.1, 0.2, 0.3], [0.7, 0.6, 0.5, 0.4]]
    one = navbuild.components(level(ramp2, pitch=0.2, area_cliff=1.0), members, 0, 0)
    tight = level(ramp2, pitch=0.2, area_cliff=0.6)
    comps = navbuild.components(tight, members, 0, 0)
    worst = max((abs(tight.y[a[2]] - tight.y[b[2]]) for c in comps for a in c for b in c
                 if abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1), default=0.0)
    check(len(one) == 1 and len(comps) >= 2 and worst <= 0.6 + 1e-6,
          f'AREA_CLIFF 0.6 at 0.2 m: {len(one)} area at 1.0, {len(comps)} at 0.6, side-by-side cells at most '
          f'{worst:.2f} m apart')
    # AREA_CLIFF_REACH 2 (2026-10-29) on 0.2 m cells: a floor reached round a ramp lies one empty row from the floor
    # 1.4 m below it; no two cells of an area are side by side beyond the step, so the reach of 1 keeps one area,
    # the reach of 2 splits it, and no two cells of one area two apart across an empty cell are over 0.77 m apart
    h3 = [[0.0, 0.05, 0.1, 0.15], [9.0, 9.0, 9.0, 0.4], [1.4, 1.15, 0.9, 0.65]]
    k3 = [[1, 1, 1, 1], [0, 0, 0, 1], [1, 1, 1, 1]]
    mem3 = {k: (k % 4, k // 4) for k in range(12) if k3[k // 4][k % 4]}
    one = navbuild.components(level(h3, keep=k3, pitch=0.2, area_cliff=0.6, area_cliff_reach=1), mem3, 0, 0)
    turn = level(h3, keep=k3, pitch=0.2, area_cliff=0.6, area_cliff_reach=2)
    comps = navbuild.components(turn, mem3, 0, 0)
    worst = max((abs(turn.y[a[2]] - turn.y[b[2]]) for c in comps for a in c for b in c
                 if {abs(a[0] - b[0]), abs(a[1] - b[1])} <= {0, 2} and (a[0] != b[0] or a[1] != b[1])
                 and ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2) not in {(x[0], x[1]) for x in c}), default=0.0)
    check(len(one) == 1 and len(comps) >= 2 and worst <= 0.77 + 1e-6,
          f'AREA_CLIFF_REACH 2: {len(one)} area at reach 1, {len(comps)} at reach 2, cells two apart across an '
          f'empty cell at most {worst:.2f} m apart')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
