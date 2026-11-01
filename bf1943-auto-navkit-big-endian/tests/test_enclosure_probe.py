r"""NavKit big-endian: the enclosure probe on a made-up walled room

enclosure_probe1943.py on a made-up work folder (BF1943_WORK): 0.2 m samples (a version-1 navsample file) of a room
whose walls stand on blocked samples, the walls' collision and object boxes. The fill stops at the walls, its blocked
edge is counted, each blocker is named by the smallest object box holding it (or none), the picture is drawn; a point
with no open surface near it, a missing sampling and a short command line are refused; a fill that reaches the
sample grid's edge stays on the grid (2026-10-31: it wrapped to the other side of the grid, or raised IndexError).
"""
import contextlib
import io
import json
import os
import struct
import unittest

import numpy as np

import enclosure_probe1943 as ep
import navsample1943 as ns

LV = 'made_up_room'
N = 41                   # columns per side, 0.2 m: x and z 0..8 m


def samples_file(path, blocked):
    """A version-1 navsample file: one surface at y 0 per column, blocked(i, j) sets its BLOCKED flag."""
    out = bytearray(struct.pack('<4sI3f2i6fI', b'ONS1', 1, 0.2, 0.0, 0.0, N, N, 50.0, -50.0, 0.35, 0.37, 1.8,
                                0.0, 0))
    for j in range(N):
        for i in range(N):
            out += bytes([1]) + struct.pack('<f3bB', 0.0, 0, 127, 0, 1 if blocked(i, j) else 0)
    with open(path, 'wb') as f:
        f.write(bytes(out))


def wall(x0, z0, x1, z1, h=2.5):
    a, b, c, d = (x0, 0.0, z0), (x1, 0.0, z1), (x1, h, z1), (x0, h, z0)
    return [[a, b, c], [a, c, d]]


def level(tmp_path, blocked, walls=(), boxes=()):
    work = tmp_path / 'work'
    level_dir = work / 'levels' / LV
    (level_dir / 'samples').mkdir(parents=True)
    (level_dir / 'objects').mkdir(parents=True)
    samples_file(str(level_dir / 'samples' / 'samples_02.bin'), blocked)
    tris = [t for w in walls for t in w]
    np.save(str(level_dir / 'objects' / 'all_tris.npy'), np.array(tris, np.float32).reshape(-1, 3, 3))
    with open(level_dir / 'objects' / 'object_boxes.json', 'w', encoding='utf-8', newline='') as f:
        json.dump(list(boxes), f)
    return str(work)


def run(work, *args):
    saved = os.environ.get('BF1943_WORK')
    os.environ['BF1943_WORK'] = work
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = ep.main(['enclosure_probe1943.py', *args])
    finally:
        if saved is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = saved
    return code, out.getvalue()


def ring(i, j):
    return (i in (10, 30) and 10 <= j <= 30) or (j in (10, 30) and 10 <= i <= 30)


ROOM_WALLS = (wall(2.0, 2.0, 2.0, 6.0), wall(6.0, 2.0, 6.0, 6.0), wall(2.0, 2.0, 6.0, 2.0), wall(2.0, 6.0, 6.0, 6.0))
ROOM_BOXES = (
    {'bp': 'objects/props/west_wall', 'destructible': False, 'min': [1.9, 0.0, 1.9], 'max': [2.1, 2.5, 6.1]},
    {'bp': 'objects/props/fence', 'destructible': True, 'min': [5.9, 0.0, 1.9], 'max': [6.1, 2.5, 6.1]},
    {'bp': 'objects/props/compound', 'destructible': False, 'min': [1.0, 0.0, 1.0], 'max': [7.0, 3.0, 7.0]},
)


def test_fill_stops_at_the_walls(tmp_path):
    work = level(tmp_path, ring, ROOM_WALLS, ROOM_BOXES)
    code, out = run(work, LV, '4', '0', '4', '5')
    assert code == 0, out
    assert 'fill: 361 open surfaces, x 2.2..5.8, z 2.2..5.8, y 0.00..0.00; 76 blocked surfaces on its edge' in out, out
    lines = [line.strip() for line in out.splitlines() if 'edge surfaces blocked by' in line]
    by = {line.split('blocked by ')[1].split(':')[0]: int(line.split()[0]) for line in lines}
    # the west wall by its own box, the east by the destructible fence; the south and north walls only by the
    # compound's large box (the smallest holding them); each wall also blocks the two edge surfaces beside its ends
    # on the walls across it (0.2 m away, within the capsule's radius)
    assert by == {'west_wall': 21, 'fence [destructible]': 21, 'compound': 42}, by
    assert 'not enclosed' not in out


def test_blocker_without_a_box(tmp_path):
    work = level(tmp_path, ring, ROOM_WALLS, ROOM_BOXES[:2])
    code, out = run(work, LV, '4', '0', '4', '5')
    assert code == 0
    assert '42 edge surfaces blocked by (no object box: terrain or a group member)' in out, out


def test_start_moves_off_a_blocked_sample(tmp_path):
    # the point's own column blocked (within a wall's radius): the nearest open surface around it is taken
    work = level(tmp_path, lambda i, j: ring(i, j) or (i, j) == (20, 20), ROOM_WALLS, ROOM_BOXES)
    code, out = run(work, LV, '4', '0', '4', '5')
    assert code == 0 and 'fill: 360 open surfaces' in out and '77 blocked surfaces on its edge' in out, out


def test_picture(tmp_path):
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        raise unittest.SkipTest('no matplotlib') from None
    work = level(tmp_path, ring, ROOM_WALLS, ROOM_BOXES)
    png = str(tmp_path / 'probe.png')
    code, out = run(work, LV, '4', '0', '4', '3', '--png', png)
    assert code == 0 and f'wrote {png}' in out
    assert open(png, 'rb').read(8) == b'\x89PNG\r\n\x1a\n'


def test_refusals(tmp_path):
    work = level(tmp_path, ring, ROOM_WALLS, ROOM_BOXES)
    code, out = run(work, LV, '4', '0')
    assert code == 1 and 'enclosure_probe1943.py <level>' in out, 'usage printed'
    code, out = run(work, LV, '4', '5', '4')
    assert code == 1 and 'no open surface near the point' in out
    try:
        run(work, 'other_level', '4', '0', '4')
    except FileNotFoundError as e:
        assert 'samples_02.bin: run navkit.py prepare and sample other_level first' in str(e)
    else:
        raise AssertionError('a level without samples was probed')


def test_fill_stays_on_the_grid(tmp_path):
    """Regression (2026-10-31): open ground to the grid's edge and a radius past it. The fill read column -1 as the
    previous row's last column (and past the last row raised IndexError); now it covers the grid once."""
    work = level(tmp_path, lambda i, j: False)
    code, out = run(work, LV, '0.4', '0', '0.4', '20')
    assert code == 0, out
    assert 'fill: 1681 open surfaces, x 0.0..8.0, z 0.0..8.0' in out and '0 blocked surfaces on its edge' in out, out


def test_picture_past_the_grid(tmp_path):
    """Regression (2026-10-31): the picture of a radius past the sample grid drew columns off the grid."""
    work = level(tmp_path, ring, ROOM_WALLS, ROOM_BOXES)
    png = str(tmp_path / 'wide.png')
    code, out = run(work, LV, '4', '0', '4', '6', '--png', png)
    assert code == 0 and os.path.isfile(png), out


def test_blockers_rule():
    # a wall 0.1 m beside the column blocks a surface at its foot; a low kerb (under the step) does not; a roof over
    # the column 1 m up blocks it as overhead
    T = np.array([t for w in (wall(0.1, -1, 0.1, 1), wall(-0.1, -1, -0.1, 1, h=0.2)) for t in w]
                 + [[[-1, 1.0, -1], [1, 1.0, -1], [0, 1.0, 1]]], np.float32)
    N_, _ = ns.normals(T)
    steep = N_[:, 1] <= ns.FLOOR_MIN
    slope = np.sqrt(np.maximum(1 - N_[:, 1] ** 2, 0)) / np.maximum(N_[:, 1], 0.05)
    got = ep.blockers(T, N_, steep, slope, T[:, :, 1].min(1), T[:, :, 1].max(1), 0.0, 0.0, 0.0)
    tris = {int(t): (round(float(lo), 2), round(float(hi), 2), over) for t, lo, hi, over in got}
    assert set(tris) == {0, 1, 4}, tris
    assert tris[4] == (1.0, 1.0, True) and tris[0][2] is False
