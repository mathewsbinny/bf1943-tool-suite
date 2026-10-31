"""Tests for inspector.py without game data or a window: the --screenshot exit code when the viewer took no picture.

  python -B test_inspector.py
"""
import os
import sys
import tempfile

import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspector  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def options():
    """--measure and --frames are checked before the level loads; an option without its value is exit code 2."""
    if not hasattr(inspector, 'measure_points'):         # an older inspector would load the level and open a window
        check(False, 'options are checked before the level loads (no measure_points)')
        return
    check(inspector.measure_points('1,2,3;4,5,6') == [[1, 2, 3], [4, 5, 6]] and inspector.frame_count('7') == 7,
          'good --measure and --frames are read')
    for f, bad in ((inspector.measure_points, '1,2'), (inspector.measure_points, 'a,b,c'), (inspector.measure_points, ''),
                   (inspector.measure_points, 'inf,0,0'), (inspector.frame_count, '0'), (inspector.frame_count, 'x')):
        try:
            f(bad)
            check(False, f'{f.__name__} {bad!r} is refused')
        except ValueError:
            check(True, f'{f.__name__} {bad!r} is refused')
    for args in (['--measure', '1,2'], ['--frames', 'x'], ['--screenshot']):
        check(inspector.main(['inspector.py', 'wake_island_s'] + args) == 2,
              f'{" ".join(args)}: exit code 2 before the level loads')


if __name__ == '__main__':
    options()
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'shot.png')
        check(inspector.save_screenshot(out, None) == 1 and not os.path.exists(out),
              'no screenshot from the viewer: exit code 1, nothing written')
        check(inspector.save_screenshot(out, np.zeros((4, 4, 3), np.uint8)) == 0 and os.path.isfile(out),
              'a screenshot: written, exit code 0')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
