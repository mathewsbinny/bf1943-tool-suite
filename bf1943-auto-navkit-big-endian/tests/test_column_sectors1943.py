r"""NavKit big-endian: BF1943's builds put a sample column on a sector line in the sector it starts

A column's 100 m sector (navbuild.column_sectors, detailed.stacked_columns) from whole centimetres
(soldier1943.SETTINGS.column_sectors_cm, as NavKit's default): on Wake Island's 0.8 m grid (its header's float32 minimum
-758.4 and pitch 0.8), min + i x pitch in floats lands just below the sector line for each column on one, and the
float rule put those in the sector before. No game data: the grid is made here from those header values.
"""
from types import SimpleNamespace

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import navbuild  # noqa: E402
import navsample  # noqa: E402
import soldier1943  # noqa: E402


def grid():
    f32 = lambda v: float(np.float32(v))
    return SimpleNamespace(nx=1502, nz=1, min_x=f32(-758.4), min_z=f32(0.0), pitch=f32(0.8))


def test_settings():
    assert soldier1943.SETTINGS.column_sectors_cm, "BF1943's builds take the whole-centimetre sector rule"


def test_line_columns():
    s = grid()
    # the columns on a sector line: -758.4 + i x 0.8 = 100 k
    line = [i for i in range(s.nx) if round(-75840 + i * 80) % 10000 == 0]
    assert len(line) == 12, line
    want = [(-75840 + i * 80) // 10000 for i in line]
    X, _ = navbuild.column_sectors(s, line, 0, 0, soldier1943.SETTINGS, s.pitch)
    assert X == want, list(zip(line, X, want))
    cm_x, _ = navsample.column_sectors(s, np.array(line))
    assert cm_x.tolist() == want
    # the float rule (BF1943's builds until 2026-11) put every one of them in the sector before:
    # -758.40002 + i x 0.80000001 is just under the line
    old, _ = navbuild.column_sectors(s, line, 0, 0, navbuild.Settings(column_sectors_cm=False), s.pitch)
    assert old == [x - 1 for x in want], list(zip(line, old, want))
