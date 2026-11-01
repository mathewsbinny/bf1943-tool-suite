r"""NavKit (big-endian): RSNV worlds from cells on made-up grids (rsnvgrid1943)

The vehicle worlds' writer on small grids built here (no game data): grid_links' straight neighbours; areas_of splits
cells by sector and by column, and refuses cells outside the sector grid; soldier_node's reach, height and main-island
rules; write_world's islands (largest first, MAIN and the flags given), node flags, one-way and drop edges (a given
cost never under 16 x the length), transfers sorted by island (a node-less one last), tight edges where no line of
sight joins two strips, both byte orders read back alike; export_surfaces' boarding and alighting transfers and its
shallow / wading node flags.
"""
import os
from types import SimpleNamespace

import numpy as np

import rsnvgrid1943 as rg
import vehicleclasses1943 as vc
from bfbc2core import rsnv

QUIET = lambda *a: None                                                 # noqa: E731
HEADER = {'sea_level': 0.0, 'radius': 1.5, 'height': 2.0, 'step': 0.3, 'water_depth': 0.8, 'sha256': '0' * 8}


def cell_set(cells, pitch=2.0, y=10.0, name='cells'):
    """A CellSet of the given (i, j) cells at height y (a number, or {cell: y}), linked to straight neighbours."""
    cells = sorted(cells)
    idx = {c: k for k, c in enumerate(cells)}
    links = {}
    for (i, j), k in idx.items():
        for di, dj in rg.STRAIGHT:
            n = idx.get((i + di, j + dj))
            if n is not None:
                links[(k, di, dj)] = n
    ys = [y[c] if isinstance(y, dict) else y for c in cells]
    return rg.CellSet(pitch, 0.0, 0.0, [c[0] for c in cells], [c[1] for c in cells], ys, links, name=name), idx


def test_grid_links_are_straight_neighbours():
    index = np.array([[0, 1, -1], [2, -1, 3]])
    links = rg.grid_links(index)
    assert links == {(0, 1, 0): 1, (1, -1, 0): 0, (0, 0, 1): 2, (2, 0, -1): 0}
    assert rg.grid_links(index, ((1, 1),)) == {(1, 1, 1): 3}, "cell 0's diagonal is no cell; cell 1's is cell 3"
    assert rg.grid_links(np.array([[0, -1], [-1, 1]]), ((1, 1),)) == {(0, 1, 1): 1}


def test_areas_split_by_sector_and_column():
    # a strip of 2 m cells from x 90 to 110: across the sector line at 100 m
    cs, _ = cell_set([(i, 0) for i in range(45, 56)])
    areas, area_of = rg.areas_of(cs, (0, 0))
    assert [sector for sector, _ in areas] == [0, 256], 'sector 0 (x 0..100) and sector 1 << 8'
    assert sorted(len(m) for _, m in areas) == [5, 6]
    assert (area_of >= 0).all()
    # two cells in one column (a bridge over a road), linked: one area may hold one of them only
    cs = rg.CellSet(2.0, 0.0, 0.0, [1, 1, 2], [1, 1, 1], [10.0, 14.0, 10.0],
                    {(0, 1, 0): 2, (2, -1, 0): 1, (1, 1, 0): 2}, name='bridge')
    areas, area_of = rg.areas_of(cs, (0, 0))
    assert len(areas) == 2 and area_of[0] != area_of[1]
    # sector offsets move the grid: the same cells with offset (1, 0) lie west of it
    try:
        rg.areas_of(cell_set([(1, 1)])[0], (1, 0))
        raise AssertionError('a cell outside the sector grid was accepted')
    except ValueError as e:
        assert 'cells outside the sector grid' in str(e) and 'X -1..-1' in str(e)


def test_soldier_node_rules():
    S = SimpleNamespace(node_pos=np.array([[0.0, 10.0, 0.0], [3.0, 10.0, 0.0], [1.0, 20.0, 0.0], [1.5, 10.0, 0.5]]),
                        node_island=np.array([1, 0, 0, 1]))
    assert rg.soldier_node(S, (1.0, 10.0, 0.0), 5.0) == 3, 'nearest within reach and height; node 2 is 10 m up'
    assert rg.soldier_node(S, (1.0, 10.0, 0.0), 5.0, main_only=True) == 1
    assert rg.soldier_node(S, (1.0, 20.0, 0.0), 5.0) == 2
    assert rg.soldier_node(S, (50.0, 10.0, 0.0), 5.0) is None
    assert rg.soldier_node(S, (1.0, 14.0, 0.0), 5.0, dy=3.5) is None


def two_strips(path, extra_edges=(), offm=(), **k):
    """Strip A (x 0..10 m) and strip B (x 20..30 m), no cells between; nodes 0, 1 on A, 2, 3 on B, 4 alone on A's far
    end without edges."""
    cs, idx = cell_set([(i, j) for i in list(range(0, 6)) + list(range(10, 16)) for j in range(0, 3)])
    nodes = [(0, idx[(0, 1)]), (0, idx[(5, 1)]), (0, idx[(10, 1)]), (0, idx[(15, 1)]), (0, idx[(3, 2)])]
    edges = [(0, 1, rsnv.EDGE_WALK, None, True), (2, 3, rsnv.EDGE_WALK, None, True)] + list(extra_edges)
    stats = rg.write_world('strips', 'jeep', [cs], nodes, edges, list(offm), path, (0, 0), HEADER, {'note': 'x'},
                           log=QUIET, **k)
    return stats, rsnv.load(path)


def test_islands_flags_and_transfers(tmp_path):
    board = (rsnv.OFFM_BOARD_VEHICLE, 1, 1, 3, 30.0, 10.0, 2.0, 0.0, 4.0, 0xFFFFFFFF)
    loose = (rsnv.OFFM_BOARD_VEHICLE, 2, 1, 0xFFFFFFFF, 0.0, 10.0, 0.0, 0.0, 4.0, 0xFFFFFFFF)
    first = (rsnv.OFFM_BOARD_VEHICLE, 1, 1, 0, 0.0, 10.0, 2.0, 0.0, 4.0, 0xFFFFFFFF)
    edges = [(1, 2, rsnv.EDGE_WALK, None, False)]            # one way, A to B: one island, the tight check runs
    stats, w = two_strips(str(tmp_path / 'a.rsnv'), edges, [loose, board, first],
                          island_flags={1: rsnv.ISLD_CARRIER}, node_flags=[0, 0, 0, 0, rsnv.NAUX_SHALLOW])
    assert not w.validate(), w.validate()
    assert stats['nodes'] == 5 and stats['islands'] == 2 and stats['island sizes'] == [4, 1]
    assert [int(r[2]) for r in w.islands] == [rsnv.ISLD_MAIN, rsnv.ISLD_CARRIER]
    assert w.node_island.tolist() == [0, 0, 0, 0, 1]
    assert int(w.node_flags[4]) == rsnv.NAUX_SHALLOW and int(w.node_flags[0]) == 0
    # transfers by island: the two on island 0 as given, the node-less one last
    assert [int(o[3]) for o in w.offmesh] == [3, 0, 0xFFFFFFFF]
    assert int(w.islands[0][3]) == 2 and int(w.islands[0][4]) == 0 and int(w.islands[1][3]) == 0
    src = np.repeat(np.arange(len(w.node_pos)), np.diff(w.node_first_edge))
    pairs = set(zip(src.tolist(), w.edge_target.tolist()))
    assert (1, 2) in pairs and (2, 1) not in pairs, 'a one-way edge'
    # the edge from strip A to strip B crosses 8 m without cells: tight; the strips' own edges are not
    tight = {(int(a), int(b)) for a, b, f in zip(src, w.edge_target, w.edge_flags) if int(f) & rsnv.EDGEF_TIGHT}
    assert tight == {(1, 2)} and stats['tight edges'] == 1
    assert w.meta['class'] == 'jeep' and w.meta['layer'] == vc.CLASS_IDS['jeep'] and w.meta['note'] == 'x'
    assert w.meta['params'] == vc.params_id('jeep') and w.meta['counts']['transfers'] == 3


def test_given_cost_and_byte_order(tmp_path):
    drop = [(1, 2, rsnv.EDGE_DROP, 1.0, False)]            # a drop given 1 m over a 10 m run: costs the run
    _, w = two_strips(str(tmp_path / 'le.rsnv'), drop, flag_tight=False)
    _, b = two_strips(str(tmp_path / 'be.rsnv'), drop, flag_tight=False, big_endian=True)
    src = np.repeat(np.arange(len(w.node_pos)), np.diff(w.node_first_edge))
    k = next(k for k, (a, t) in enumerate(zip(src.tolist(), w.edge_target.tolist())) if (a, t) == (1, 2))
    assert int(w.edge_kind[k]) == rsnv.EDGE_DROP and int(w.edge_cost[k]) == 160
    assert not int(w.edge_flags[k]) & rsnv.EDGEF_TIGHT, 'flag_tight=False flags nothing'
    _, w2 = two_strips(str(tmp_path / 'long.rsnv'), [(1, 2, rsnv.EDGE_DROP, 25.0, False)], flag_tight=False)
    assert int(w2.edge_cost[k]) == 400, 'a given cost longer than the run is kept'
    assert b.big_endian and not w.big_endian
    for name in ('node_pos', 'edge_target', 'edge_cost', 'edge_kind', 'node_area', 'hght', 'glst', 'anod'):
        assert np.array_equal(getattr(w, name), getattr(b, name)), name
    assert w.areas == b.areas and w.bmap == b.bmap
    assert w.h_scale_q > 0


def test_cells_and_heights_round_trip(tmp_path):
    heights = {(i, j): 10.0 + 0.25 * i for i in range(4) for j in range(3)}
    cs, idx = cell_set(list(heights), y=heights)
    path = str(tmp_path / 'slope.rsnv')
    rg.write_world('slope', 'tank', [cs], [(0, idx[(0, 0)]), (0, idx[(3, 2)])], [(0, 1, rsnv.EDGE_WALK, None, True)],
                   [], path, (0, 0), HEADER, {}, log=QUIET)
    w = rsnv.load(path)
    assert not w.validate()
    area = w.areas[0]
    for (i, j), y in heights.items():
        x, z = i * 2.0, j * 2.0
        loc = w.locate(x, y, z, max_dist=0.5)
        assert loc is not None and loc[0] == 0
        assert abs(w.ground(area, x, z) - y) < 0.011, (i, j, w.ground(area, x, z), y)
    assert 'OFFM' not in w.sections, 'no transfers: no OFFM section'


def surfaces():
    """A ground grid of 0.8 m cells: 10 x 4 at y 10, the last 3 columns lower (9.9 and 9.0)."""
    ii, jj, yy = [], [], []
    for j in range(4):
        for i in range(10):
            ii.append(i)
            jj.append(j)
            yy.append(10.0 if i < 7 else (9.9 if i < 9 else 9.0))
    g = SimpleNamespace(n=len(ii), pitch=0.8, x0=0.0, z0=0.0, i=np.array(ii), j=np.array(jj), y=np.array(yy))
    table = {}
    for k in range(g.n):
        for di, dj in rg.STRAIGHT + ((1, 1),):
            i, j = ii[k] + di, jj[k] + dj
            if 0 <= i < 10 and 0 <= j < 4:
                table[(k, di, dj)] = j * 10 + i
    return g, table


def test_export_surfaces_transfers_and_flags(tmp_path):
    g, table = surfaces()
    kept = np.ones(g.n, bool)
    kept[[30, 31]] = False                       # two cells not kept: no links to them
    nodes = [10, 14, 17, 19]                     # j = 1: x 0, 3.2 (ground 10), 5.6 (9.9), 7.2 (9.0)
    edges = [(10, 14), (14, 17), (17, 19)]
    # the soldier world: a strip of 0.8 m cells along the yard, nodes at x 0 and 4
    soldier_cs, idx = cell_set([(i, j) for i in range(10) for j in range(4)], pitch=0.8)
    soldier = str(tmp_path / 'soldier.rsnv')
    rg.write_world('yard', 'jeep', [soldier_cs], [(0, idx[(0, 1)]), (0, idx[(5, 1)])],
                   [(0, 1, rsnv.EDGE_WALK, None, True)], [], soldier, (0, 0), HEADER, {}, log=QUIET)
    seeds = [{'class': 'jeep', 'pos': [0.2, 10.0, 0.8], 'team': 1, 'yaw': 0.5, 'vehicle': 'willys-mb', 'index': 4,
              'entries': [{'point': [0.0, 10.0, 0.8], 'radius': 3.0, 'order': 0},
                          {'point': [1.0, 10.0, 0.8], 'radius': 3.0, 'order': 1}]},
             {'class': 'jeep', 'pos': [5.6, 10.0, 0.8], 'team': 0, 'yaw': 0.0, 'vehicle': 'kurogane', 'index': 5,
              'entries': []},
             {'class': 'tank', 'pos': [0.0, 10.0, 0.0], 'team': 2, 'yaw': 0.0, 'vehicle': 'm4', 'index': 6,
              'entries': []}]
    sources = {'samples': soldier, 'missing': str(tmp_path / 'none.bin'), 'empty': None}
    objectives = [{'name': 'flag A', 'position': [3.2, 10.0, 0.8]}, {'name': 'no position'}]
    out = str(tmp_path / 'yard.jeep.rsnv')
    stats = rg.export_surfaces('yard', 'jeep', g, kept, table, nodes, edges, seeds, out, {'sector_offsets': (0, 0)},
                               sources, 10.0, log=QUIET, soldier=soldier, objectives=objectives)
    w = rsnv.load(out)
    assert not w.validate(), w.validate()
    assert stats['nodes'] == 4 and stats['cells'] == g.n - 2
    flags = np.asarray(w.node_flags).tolist()
    assert flags[0] == 0 and flags[1] == 0
    assert flags[2] == rsnv.NAUX_SHALLOW, 'ground 0.1 m under the sea: shallow'
    assert flags[3] == rsnv.NAUX_WADE, 'ground 1 m under the sea: wading'
    board = [o for o in w.offmesh if int(o[0]) == rsnv.OFFM_BOARD_VEHICLE]
    alight = [o for o in w.offmesh if int(o[0]) == rg.OFFM_ALIGHT_ZONE]
    # the willys' driver entry only (order 1 is a passenger's); the kurogane's spawn point as its entry; no tank
    assert len(board) == 2
    by_team = {int(o[1]): o for o in board}
    assert set(by_team) == {1, 3}
    assert int(by_team[1][3]) == 0 and int(by_team[1][9]) == 0, 'the vehicle node and the soldier node by the entry'
    assert int(by_team[3][3]) == 2 and abs(float(by_team[3][8]) - 4.0) < 1e-6
    # alight: at the flag and at each boarding spawn, onto a main-island soldier node within reach
    assert len(alight) == 3 and all(int(o[9]) in (0, 1) for o in alight)
    assert [a['target'] for a in w.meta['alighting']] == ['flag A', 'spawn 4', 'spawn 5']
    assert set(w.meta['sources']) == {'samples'}, 'only sources that are files'
    assert w.meta['boarding'][0]['soldier island'] == 0
    assert abs(w.sea_level - 10.0) < 1e-6 and abs(w.radius - vc.value('jeep', 'radius')) < 1e-6
    # without a soldier world: boarding with no soldier node, no alighting
    out2 = str(tmp_path / 'alone.jeep.rsnv')
    rg.export_surfaces('yard', 'jeep', g, kept, table, nodes, edges, seeds, out2, {'sector_offsets': (0, 0)},
                       {}, 10.0, log=QUIET, soldier=str(tmp_path / 'missing.rsnv'))
    w2 = rsnv.load(out2)
    assert len(w2.offmesh) == 2 and all(int(o[9]) == 0xFFFFFFFF for o in w2.offmesh)
    assert os.path.isfile(out2)
