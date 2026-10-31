"""Tests for studio.py without game data or a window: the ground colours of areas on no island, the --path option, and
the --screenshot exit code when the viewer took no picture.

  python -B test_studio.py
"""
import os
import sys
import tempfile
import types

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import studio  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def colours():
    """An area with no route node in its sector (navdata leaves its island -1) is not painted as the main network."""
    areas = [types.SimpleNamespace(island=0, sector=1, detailed=False), types.SimpleNamespace(island=-1, sector=1, detailed=True),
             types.SimpleNamespace(island=1, sector=2, detailed=False)]
    nav = types.SimpleNamespace(cell_area=np.array([0, 1, 2]), areas=areas,
                                islands=[types.SimpleNamespace(nodes=100), types.SimpleNamespace(nodes=3)])
    for mode in ('island', 'small islands'):
        c = studio.Studio.cell_colors(types.SimpleNamespace(nav=nav, color=mode))
        check(c.shape == (3, 3) and np.allclose(c[0], studio.MAIN) and np.allclose(c[1], studio.NO_ISLAND)
              and not np.allclose(c[2], studio.MAIN), f'{mode}: main network green, an area on no island not green')
    for mode in ('sector', 'pitch', 'island'):
        empty = types.SimpleNamespace(cell_area=np.zeros(0, int), areas=[], islands=[])
        c = studio.Studio.cell_colors(types.SimpleNamespace(nav=empty, color=mode))
        check(c.shape == (0, 3), f'{mode}: a build without cells gives no colours, shaped (0, 3)')


def options():
    check(studio.path_points('1,2,3;4,5,6') == [[1, 2, 3], [4, 5, 6]], '--path: two points')
    for bad in ('1,2,3', '1,2,3;4,5,6;7,8,9', '1,2;3,4', 'a,b,c;1,2,3'):
        try:
            studio.path_points(bad)
            check(False, f'--path {bad!r} is refused')
        except ValueError:
            check(True, f'--path {bad!r} is refused')
    check(studio.main(['studio.py', 'wake_island_s', '--path', '1,2,3']) == 2, 'a bad --path ends with exit code 2 at once')
    if not hasattr(studio, 'check_options'):              # an older studio would load the level and open a window
        check(False, 'the other options are checked before the level loads (no check_options)')
        return
    good = studio.check_options({'--island': '2', '--frames': '5', '--focus': '1,2,3', '--color': 'small-islands',
                                 '--yaw': '0.5', '--pitch': '1.2', '--distance': '30'})
    check(good['island'] == 2 and good['frames'] == 5 and good['focus'] == [1, 2, 3] and good['color'] == 'small islands'
          and good['yaw'] == 0.5 and good['pitch'] == 1.2 and good['distance'] == 30, f'good options are read ({good})')
    for key, bad in (('--island', 'x'), ('--island', '-1'), ('--frames', '0'), ('--frames', 'many'), ('--focus', '1,2'),
                     ('--yaw', 'left'), ('--pitch', 'nan'), ('--distance', 'far'), ('--color', 'rainbow'),
                     ('--path', 'nan,0,0;1,2,3')):
        try:
            studio.check_options({key: bad})
            check(False, f'{key} {bad!r} is refused')
        except ValueError:
            check(True, f'{key} {bad!r} is refused')
    for args in (['--island', 'x'], ['--frames', '0'], ['--screenshot']):
        check(studio.main(['studio.py', 'wake_island_s'] + args) == 2, f'{" ".join(args)}: exit code 2 before the level loads')


def screenshot():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'shot.png')
        check(studio.save_screenshot(out, None) == 1 and not os.path.exists(out),
              'no screenshot from the viewer: exit code 1, nothing written')
        check(studio.save_screenshot(out, np.zeros((4, 4, 3), np.uint8)) == 0 and os.path.isfile(out),
              'a screenshot: written, exit code 0')


if __name__ == '__main__':
    colours()
    options()
    screenshot()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
