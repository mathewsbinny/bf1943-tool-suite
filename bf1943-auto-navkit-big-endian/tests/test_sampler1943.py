"""Tests for navsample1943.py on a small made-up level (no game data): a flat terrain, a wall and a raised platform.

Checks the surfaces a column gets (terrain height, a platform above it), the capsule test (blocked next to the wall
within the soldier's radius, clear farther away and under nothing), a ramp of two triangles open along its
diagonal, a slab under the terrain blocked, a vertical wall (a line seen from above) holding no point, BF1943's
soldier settings, a ladder's ends on each piece of ground, the file NavKit's own reader loads, and the sector filter
(tiles outside the chosen sectors left empty).
"""
import json
import math
import os
import struct
import sys
import tempfile

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(HERE, 'tools')]
import navsample  # noqa: E402
import navsample1943  # noqa: E402

failures = []
GROUND = 10.0


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def heightfield(path, size=256, extent=100.0, height=GROUND):
    """A flat big-endian TerrainHeightfield (BF1943's layout): bounds, size, then u16 heights."""
    lo, hi = (-extent, 0.0, -extent), (extent, 100.0, extent)
    raw = int(round((height - lo[1]) / (hi[1] - lo[1]) * 65535))
    with open(path, 'wb') as f:
        f.write(struct.pack('>6f6IB', *lo, *hi, size, 0, 0, 0, 0, 0, 0))
        f.write(struct.pack(f'>{size * size}H', *([raw] * (size * size))))


def box(x0, x1, y0, y1, z0, z1):
    c = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return np.array([[c[a], c[b], c[k]] for a, b, k in faces], np.float32)


def column_at(s, x, z):
    i, j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
    rng = s.column(i, j)
    return [(float(s.y[k]), int(s.flags[k])) for k in rng]


def level(d):
    wall = box(0.0, 0.4, GROUND, GROUND + 3.0, -5.0, 5.0)              # a 3 m wall along z at x 0..0.4
    platform = box(10.0, 14.0, GROUND + 1.2, GROUND + 1.5, -2.0, 2.0)  # a platform 1.5 m up
    # a ramp of two triangles, 0 to 1.2 m over x 30..34 (17 deg), split along its diagonal z = x - 32
    c = [(30.0, GROUND, -2.0), (34.0, GROUND + 1.2, -2.0), (34.0, GROUND + 1.2, 2.0), (30.0, GROUND, 2.0)]
    ramp = np.array([[c[0], c[1], c[2]], [c[0], c[2], c[3]]], np.float32)
    buried = box(50.0, 54.0, GROUND - 2.3, GROUND - 2.0, 10.0, 14.0)      # a slab 2 m under the terrain (foundations)
    np.save(os.path.join(d, 'all_tris.npy'), np.concatenate([wall, platform, ramp, buried]))
    json.dump([[20.0, GROUND, 20.0]], open(os.path.join(d, 'soldier_spawns.json'), 'w'))
    heightfield(os.path.join(d, 'hf.res'))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as d:
        level(d)
        out = os.path.join(d, 'samples.bin')
        navsample1943.sample(d, os.path.join(d, 'hf.res'), out, pitch=0.2, margin=10.0, log=lambda m: None)
        s, sampled = navsample.load(out)
        check(s.nx * s.nz == sampled and abs(s.pitch - 0.2) < 1e-6, 'NavKit reader loads the file, every column')
        open_ground = column_at(s, 6.0, 8.0)
        check(len(open_ground) == 1 and abs(open_ground[0][0] - GROUND) < 0.05 and open_ground[0][1] == 0,
              f'open ground: one surface at the terrain height, not blocked ({open_ground})')
        r = navsample1943.RADIUS
        near = column_at(s, -0.2, 0.0)                       # 0.2 m from the wall face
        far = column_at(s, -1.2, 0.0)                        # 1.2 m away
        check(near and near[-1][1] & navsample1943.BLOCKED, f'ground within the radius ({r} m) of a wall is blocked')
        check(far and not far[-1][1] & navsample1943.BLOCKED, 'ground beyond the radius is clear')
        top = column_at(s, 0.2, 0.0)
        check(any(abs(y - GROUND - 3.0) < 0.05 for y, _ in top), 'the wall top is a surface of its column')
        plat = column_at(s, 12.0, 0.0)
        ys = sorted(y for y, _ in plat)
        flags = {round(y - GROUND, 2): f for y, f in plat}
        check(set(flags) == {0.0, 1.2, 1.5}, f'under the platform: the ground, the platform underside and top ({ys})')
        check(flags.get(0.0, 0) & navsample1943.BLOCKED, 'ground under a 1.5 m platform is blocked (head height 1.8 m)')
        check(flags.get(1.2, 0) & navsample1943.BLOCKED, 'the platform underside is not standable (two-sided collision)')
        check(flags.get(1.5, 1) == 0, 'the platform top is standable')
        on_ramp = [(y, f) for y, f in column_at(s, 31.0, -0.8) if abs(y - GROUND - 0.3) < 0.05]
        check(on_ramp and on_ramp[0][1] == 0,
              f'a ramp next to its own diagonal is open (the other triangle counts at its plane height, not 0-1.2 m) '
              f'({on_ramp})')
        under = [(y, f) for y, f in column_at(s, 52.0, 12.0) if y < GROUND - 1]
        check(under and all(f & navsample1943.BLOCKED for _, f in under),
              f'surfaces 2 m under the terrain are buried, not floor ({under})')
        # a vertical wall seen from above is a line: with float32 corners at level coordinates (Wake Island's storeroom,
        # 2026-10-23) a fixed epsilon missed it and put a point 1.26 m away inside it
        wall = np.array([[[343.0523, 220.7378], [344.5623, 220.7378], [345.5982, 220.7378]]], np.float32)
        u, v, w, ins = navsample1943.inside(wall, np.array([345.4]), np.array([222.0]))
        dw = float(navsample1943.dist2d(wall, np.array([345.4]), np.array([222.0]))[0, 0])
        check(not ins.any() and abs(dw - 1.26) < 0.01,
              f'a wall that is a line from above holds no point; its distance is its edge ({dw:.2f} m)')
        # BF1943's soldier over NavKit's BFBC2 settings
        import detailed
        import navbuild
        import navwalk
        import soldier1943
        soldier1943.configure()
        check((navbuild.SLOPE_MAX, detailed.SLOPE_MAX, navbuild.DETAILED_LINK_STEP, navbuild.Y_OFFSET,
               navwalk.OUTLINE_Y_OFFSET, navwalk.HEADROOM_BAND, navsample1943.STEP) == (45.0, 45.0, soldier1943.STEP, 0.9, 0.9,
                                                                                      (0.5, 1.8), soldier1943.STEP)
              and abs(soldier1943.STEP - 0.37) < 1e-9,
              'soldier1943.configure: 45 deg (build and sector choice), detailed link step 0.37, nodes 0.9 m up, headroom 1.8')
        # a ladder on the 3 m wall: ground on both sides of the wall lies within reach of its foot - one end each
        keep = (s.flags & navsample1943.BLOCKED) == 0
        zero = np.zeros(len(s.y))
        lad = {'lo': [0.0, GROUND, -0.25], 'hi': [0.4, GROUND + 3.0, 0.25]}
        feet, tops = navwalk.ladder_end_options(s, keep, zero, zero, np.zeros(len(s.y), bool), navsample1943.STEP, lad,
                                                local=1.0)
        xs = sorted(round(float(s.min_x + (np.searchsorted(s.start, k, side='right') - 1) % s.nx * s.pitch), 1) for k in feet)
        check(len(feet) == 2 and xs[0] < 0 < 0.4 < xs[1] and len(tops) >= 1,
              f'a ladder links to the ground on each side of its foot (x {xs}) and its top ({len(tops)})')
        check(navbuild.LADDER_ALL_GROUND, 'soldier1943.configure: ladders link to every piece of ground beside their ends')
        # the sector filter: only the sector holding (50, 50)
        out2 = os.path.join(d, 'sectors.bin')
        navsample1943.sample(d, os.path.join(d, 'hf.res'), out2, pitch=0.4, margin=10.0, sectors=((0, 0), [(0, 0)]),
                             log=lambda m: None)
        s2, _ = navsample.load(out2)
        inside, outside = column_at(s2, 50.0, 50.0), column_at(s2, -50.0, -50.0)
        check(s2.min_x < -50 and s2.min_z < -50 and inside and not outside,
              f'the sector filter samples only the chosen sector (inside {len(inside)}, outside {len(outside)} surfaces)')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
