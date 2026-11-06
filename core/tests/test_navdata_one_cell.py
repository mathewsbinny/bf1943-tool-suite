r"""Navigation reader (navdata): the island of an area of one cell, from its centre

navdata.load on made-up pathdata.hpd files (no game data; test_navdata_islands.py's writer): an area takes its island
from its sector's node nearest its centre (horizontal distance plus three times the height difference, the node's
height less y_offset), the centre being the mean of the area's cells (x, z and ground height). An area of exactly one
cell is centred on that cell: the mean of one value is the value itself, so the island is the node nearest the cell.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import fuzzcheck
import numpy as np

from bfbc2core import navdata

ISL = fuzzcheck.fixtures('core/tests/test_navdata_islands.py')
CELL = 40 * navdata.GRID                    # a sample point of the 0.8 m grid: 32 m


def one_cell(y):
    """A closed square outline 0.4 m wide around the grid point (CELL, CELL): it holds that one sample."""
    y += navdata.Y_OFFSET
    lo, hi = CELL - 0.2, CELL + 0.2
    return [[(lo, y, lo), (hi, y, lo), (hi, y, hi), (lo, y, hi), (lo, y, lo)]]


def test_one_cell_island_by_position(tmp_path):
    yo = navdata.Y_OFFSET
    # three nodes by the cell (the larger island, ranked 0), two at half its x and z (ranked 1)
    points = [(CELL, 1 + yo, CELL + 1.0), (CELL + 1, 1 + yo, CELL + 1.0), (CELL + 2, 1 + yo, CELL + 1.0),
              (CELL / 2, 1 + yo, CELL / 2), (CELL / 2 + 1, 1 + yo, CELL / 2)]
    nav = navdata.load(ISL.write_file(str(tmp_path / 'pathdata.hpd'), points, [(0, 1), (1, 2), (3, 4)],
                                      [one_cell(1.0)]))
    assert nav.areas[0].cells == 1 and np.allclose(nav.cell_xz, [[CELL, CELL]]), nav.cell_xz
    assert nav.node_island.tolist() == [0, 0, 0, 1, 1]
    assert nav.areas[0].island == 0, 'the node 1 m from its one cell, not the ones at half its position'


def test_one_cell_island_by_height(tmp_path):
    yo = navdata.Y_OFFSET
    # the cell's ground is 10 m: three nodes 2 m away on that ground (2 + 0), two right over the cell at 5 m (0 + 15)
    points = [(CELL, 10 + yo, CELL + 2.0), (CELL + 1, 10 + yo, CELL + 2.0), (CELL + 2, 10 + yo, CELL + 2.0),
              (CELL, 5 + yo, CELL), (CELL - 1, 5 + yo, CELL)]
    nav = navdata.load(ISL.write_file(str(tmp_path / 'pathdata.hpd'), points, [(0, 1), (1, 2), (3, 4)],
                                      [one_cell(10.0)]))
    assert nav.areas[0].cells == 1 and np.allclose(nav.cell_y, [10.0], atol=1e-6), nav.cell_y
    assert nav.node_island.tolist() == [0, 0, 0, 1, 1]
    assert nav.areas[0].island == 0, 'the node on its ground, not the one under it'
