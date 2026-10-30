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


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'shot.png')
        check(inspector.save_screenshot(out, None) == 1 and not os.path.exists(out),
              'no screenshot from the viewer: exit code 1, nothing written')
        check(inspector.save_screenshot(out, np.zeros((4, 4, 3), np.uint8)) == 0 and os.path.isfile(out),
              'a screenshot: written, exit code 0')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
