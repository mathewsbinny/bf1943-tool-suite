r"""Navigation reader (navdata): islands, ladder links, ladder ground and costs on a made-up pathdata.hpd

navdata.load on a made-up file (no game data): one sector of three areas and two islands - a chain of 25 nodes, and
a group of five whose node n2 is reached only by a ladder link. The islands are ranked by size, their node and cell
counts and centres given; a route edge is a ladder link when it climbs more than LADDER_DY and more than LADDER_SLOPE
metres per metre across (edges at exactly those limits are not); an area takes its island from the sector's node
nearest it, and is ladder ground when that node is on a walking part of its island other than the largest; edge
costs are the tag's low 31 bits in 1/16 m; an edge naming a node past its sector's is refused; summary counts all of
it (an island of 25 nodes is large). Also the cache key's parts and the height field digest.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import os
from types import SimpleNamespace

import hpdtool
import kymesh
import numpy as np

from bfbc2core import navdata

SID = 0x0101


def square(x0, z0, size, y):
    """An area's outline: a square at outline height y (its ground is y - navdata.Y_OFFSET)."""
    return [[(x0, y, z0), (x0 + size, y, z0), (x0 + size, y, z0 + size), (x0, y, z0 + size)]]


def made_up_file(path, extra_edges=()):
    y = navdata.Y_OFFSET
    areas = [kymesh.Area(SID, 0, 0, 0, 0.4, 0.8, 2.0, 0.0, outline=square(10, 9, 4, 1 + y)),      # by n0, n1
             kymesh.Area(SID, 0, 0, 0, 0.4, 0.8, 7.0, 5.0, outline=square(9, 10, 2, 6 + y)),      # ladder top, n2
             kymesh.Area(SID, 1, 1, 0, 0.4, 0.8, 2.0, 0.0, outline=square(60, 49, 4, 1 + y))]     # on the chain
    tiles = [[kymesh.TileItem(0, 0.0, 2.0), kymesh.TileItem(1, 5.0, 7.0)], [], [kymesh.TileItem(2, 0.0, 2.0)], []]
    mesh = kymesh.Mesh(2, 0, 0, SID, 0.025, 0.8, (0, 100, 0, 100), 2, 2, 0.4, tiles, areas, [], b'\0' * 4)
    points = [(10, 1, 10), (14, 1, 10), (10, 6, 10.5),     # n0 - n1 walking; n0 - n2 a ladder (5 m up, 0.5 across)
              (14, 3.5, 10),                               # n3: 2.5 m above n1, straight up: not a ladder
              (16, 4.0, 10)]                               # n4: 3 m above n1, 2 m across (1.5 / m): not a ladder
    points += [(40 + k, 1, 50) for k in range(25)]         # n5 .. n29: the chain
    links = [(0, 1), (0, 2), (1, 3), (1, 4)] + [(5 + k, 6 + k) for k in range(24)] + list(extra_edges)
    nodes = [hpdtool.Node(1 + k, p, 0x80000000, 0.0, 0, 0, 0) for k, p in enumerate(points)]
    edges = []
    for a, b in links:
        tag = 0x80000000 | int(np.floor(16 * np.linalg.norm(np.subtract(points[a], points[b]))))
        edges += [hpdtool.Edge(tag, a, b), hpdtool.Edge(tag, b, a)]
    sector = hpdtool.Sector(SID, (0, 100, 0, 10, 0, 100), 0, 100007, 13305600, nodes, edges, kymesh.write_mesh(mesh))
    with open(path, 'wb') as f:
        f.write(hpdtool.write(hpdtool.HpdFile((2, 2, 1, 1) + (0,) * 32, [sector])))
    return path


def test_islands_and_ladders(tmp_path):
    nav = navdata.load(made_up_file(str(tmp_path / 'pathdata.hpd')))
    assert len(nav.node_pos) == 30 and len(nav.edges) == 2 * 28
    assert nav.node_island.tolist() == [1] * 5 + [0] * 25, 'the chain (25 nodes) first, then the group of five'
    assert [i.nodes for i in nav.islands] == [25, 5]
    assert nav.node_island[nav.islands[0].group] == 0 and nav.node_island[nav.islands[1].group] == 1, 'a node of each'
    assert np.allclose(nav.islands[0].centre, [52, 1, 50]) and np.allclose(nav.islands[1].centre, [12.8, 3.1, 10.1])
    ladder = {tuple(e) for e, lad in zip(nav.edges.tolist(), nav.ladder_edge.tolist()) if lad}
    assert ladder == {(0, 2), (2, 0)}, ladder
    assert [a.island for a in nav.areas] == [1, 1, 0]
    assert [a.ladder for a in nav.areas] == [False, True, False], 'the ladder top is ladder ground'
    assert all(a.cells > 0 for a in nav.areas)
    assert nav.islands[1].cells == nav.areas[0].cells + nav.areas[1].cells and nav.islands[0].cells == nav.areas[2].cells
    k = nav.edges.tolist().index([0, 1])
    assert nav.edge_cost[k] == 4.0 and nav.edge_cost[nav.edges.tolist().index([0, 2])] == np.floor(16 * np.hypot(5, 0.5)) / 16
    s = navdata.summary(nav)
    assert s == {'areas': 3, 'detailed areas': 0, 'cells': len(nav.cell_xz), 'nodes': 30, 'edges': 56, 'islands': 2,
                 'large islands (25+ nodes)': 1, 'sectors': 1, 'ladder links': 1,
                 'ladder ground cells': nav.areas[1].cells}, s


def test_without_ladders(tmp_path):
    nav = navdata.load(made_up_file(str(tmp_path / 'pathdata.hpd')))
    nav.ladder_edge = None
    assert navdata.summary(nav)['ladder links'] == 0
    nav.areas[0].pitch = 0.2
    assert navdata.summary(nav)['detailed areas'] == 1 and nav.areas[0].detailed and not nav.areas[1].detailed


def test_an_edge_past_its_nodes(tmp_path):
    path = made_up_file(str(tmp_path / 'pathdata.hpd'))
    data = hpdtool.parse(open(path, 'rb').read())
    for src, dst, bad in ((29, 0, False), (30, 0, True), (0, 30, True)):
        data.sectors[0].edges[0] = hpdtool.Edge(0x80000010, src, dst)
        with open(path, 'wb') as f:
            f.write(hpdtool.write(data))
        try:
            navdata.load(path)
            refused = None
        except ValueError as e:
            refused = str(e)
        assert (refused is not None) is bad, (src, dst, refused)
        if bad:
            assert f'has an edge {src} -> {dst} past its 30 nodes' in refused


def test_cache_key_and_digest(tmp_path):
    path = made_up_file(str(tmp_path / 'pathdata.hpd'))
    flat = SimpleNamespace(raw=np.zeros((2, 2), np.uint16), min=(0, 0, 0), max=(1, 1, 1), size=2)
    keys = {navdata._key(path), navdata._key(path, flat), navdata._key(path, y_offset=0.9),
            navdata._key(path, SimpleNamespace(raw=np.ones((2, 2), np.uint16), min=(0, 0, 0), max=(1, 1, 1), size=2))}
    assert len(keys) == 4 and all(len(k) == 20 for k in keys)
    assert navdata._key(path) == navdata._key(os.path.join(os.path.dirname(path), '.', 'PATHDATA.HPD'))
    d = navdata.terrain_digest(flat)
    assert d is not None and len(d) == 20
    by_heights = SimpleNamespace(heights=np.zeros((2, 2), np.float32), min=None, max=None, size=None)
    assert navdata.terrain_digest(by_heights) not in (None, d)
    moved = SimpleNamespace(raw=flat.raw, min=(0, 0, 1), max=flat.max, size=2)
    assert navdata.terrain_digest(moved) != d, 'the bounds count'
    assert navdata.terrain_digest(SimpleNamespace(raw=flat.raw, min=flat.min, max=flat.max, size=4)) != d
    assert navdata.CACHE_VERSION == 6


def write_file(path, points, links, outlines, tiles=None):
    """A one-sector pathdata.hpd: nodes at points, walk / ladder edges both ways, an area per (outline square)."""
    areas = [kymesh.Area(SID, 0, 0, 0, 0.4, 0.8, 9.0, 0.0, outline=o) for o in outlines]
    if tiles is None:
        tiles = [[kymesh.TileItem(k, 0.0, 9.0) for k in range(len(areas))], [], [], []]
    mesh = kymesh.Mesh(2, 0, 0, SID, 0.025, 0.8, (0, 100, 0, 100), 2, 2, 0.4, tiles, areas, [], b'\0' * 4)
    nodes = [hpdtool.Node(1 + k, p, 0x80000000, 0.0, 0, 0, 0) for k, p in enumerate(points)]
    edges = []
    for a, b in links:
        tag = 0x80000000 | int(np.floor(16 * np.linalg.norm(np.subtract(points[a], points[b]))))
        edges += [hpdtool.Edge(tag, a, b), hpdtool.Edge(tag, b, a)]
    sector = hpdtool.Sector(SID, (0, 100, 0, 10, 0, 100), 0, 100007, 13305600, nodes, edges, kymesh.write_mesh(mesh))
    with open(path, 'wb') as f:
        f.write(hpdtool.write(hpdtool.HpdFile((2, 2, 1, 1) + (0,) * 32, [sector])))
    return path


def test_walking_parts_in_any_order(tmp_path):
    y = navdata.Y_OFFSET
    points = [(10, 6, 10.5),                               # 0: the ladder top, first in node order
              (10, 1, 10), (14, 1, 10), (14, 3.5, 10),     # 1 - 3: the ground it is reached from
              (30, 1, 30), (30, 6, 30.5),                  # 4, 5: two walking parts of one node each, a ladder
              (70, 1, 70), (71, 1, 70)]                    # 6, 7: an island without areas
    links = [(1, 2), (1, 0), (2, 3), (4, 5), (6, 7)]
    path = write_file(str(tmp_path / 'pathdata.hpd'), points, links,
                      [square(10, 9, 4, 1 + y), square(9, 10, 2, 6 + y), square(29, 29.5, 2, 6 + y)])
    nav = navdata.load(path)
    assert [i.nodes for i in nav.islands] == [4, 2, 2] and nav.node_island.tolist() == [0, 0, 0, 0, 1, 1, 2, 2]
    assert [(a.island, a.ladder) for a in nav.areas] == [(0, False), (0, True), (1, True)], \
        'the largest walking part is the ground, wherever it comes; of two equal parts the first'
    assert nav.islands[2].cells == 0, 'no area on it'
    assert np.allclose(nav.cell_y[nav.cell_area == 0], 1.0) and np.allclose(nav.cell_y[nav.cell_area == 1], 6.0)
    xz = nav.cell_xz[nav.cell_area == 0]
    assert np.allclose(xz / navdata.GRID, np.round(xz / navdata.GRID)) and 10 - 2 * navdata.GRID <= xz[:, 0].min() < 10


def test_a_file_without_areas_or_edges(tmp_path):
    path = write_file(str(tmp_path / 'pathdata.hpd'), [(1, 1, 1)], [], [], tiles=[[], [], [], []])
    nav = navdata.load(path)
    assert nav.cell_xz.shape == (0, 2) and nav.cell_y.shape == (0,) and nav.cell_area.shape == (0,)
    assert nav.edges.shape == (0, 2) and nav.ladder_edge is not None and len(nav.ladder_edge) == 0
    assert nav.node_pos.shape == (1, 3) and [i.nodes for i in nav.islands] == [1]


def test_area_defaults():
    a = navdata.NavArea(SID, 0, (0, 0), 0, 0.8, 0.0, 1.0)
    assert (a.island, a.cells, a.ladder, a.outline, a.portals, a.borders) == (-1, 0, False, [], [], [])
    assert not a.detailed and not navdata.NavArea(SID, 0, (0, 0), 0, 0.5, 0.0, 1.0).detailed
    assert navdata.NavArea(SID, 0, (0, 0), 0, 0.2, 0.0, 1.0).detailed and navdata.DETAILED == 0.5


def test_compare_cells_stacked_floors():
    def cells(*rows):
        return SimpleNamespace(cell_xz=np.array([[i * navdata.GRID, j * navdata.GRID] for i, j, _ in rows],
                                                float).reshape(-1, 2), cell_y=np.array([y for _, _, y in rows], float))
    mine, theirs = navdata.compare_cells(cells((5, 5, 10.0), (5, 5, 20.0)), cells((5, 5, 10.0), (5, 5, 20.0)))
    assert mine.tolist() == [True, True] and theirs.tolist() == [True, True], 'two floors each, paired by height'
    # the other file's last cell (in key order) is the one to pair with
    mine, _ = navdata.compare_cells(cells((9, 9, 0.0), (9, 9, 3.0)), cells((1, 1, 0.0), (9, 9, 0.0)))
    assert mine.tolist() == [True, True], mine
    # the next key's floor is nearer in height, but it is another cell
    mine, _ = navdata.compare_cells(cells((5, 5, 0.0), (5, 5, 30.0)), cells((5, 5, 20.0), (6, 5, 29.0)))
    assert mine.tolist() == [False, False], mine
    # three floors there, two here: each pairs with its nearest
    mine, theirs = navdata.compare_cells(cells((5, 5, 0.0), (5, 5, 9.5)), cells((5, 5, 0.0), (5, 5, 4.9), (5, 5, 10.0)))
    assert mine.tolist() == [True, True] and theirs.tolist() == [True, False, True], (mine, theirs)


def test_ground_on_the_terrain():
    class Slope:                                    # 1 m up per metre in x, 0.5 m per metre in z
        def height(self, x, z):
            return np.asarray(x, float) + 0.5 * np.asarray(z, float)
    s, h = Slope(), navdata.GRID / 2
    xz = np.array([[10.0, 0.0]])
    t = float(s.height(10.0, 0.0))
    for est, on in ((t + 1.49, True), (t - 1.49, True), (t + 1.5, False), (t + 1.6, False)):
        got = navdata._ground(xz, np.array([est]), s)[0]
        assert got == (t if on else est), (est, got)
    near = np.array([[10.0, 0.0]])
    lo = float(s.height(10.0 - h, 0.0)) - navdata.ON_TERRAIN       # the ring's lowest point, less ON_TERRAIN
    hi = float(s.height(10.0 + h, 0.0)) + navdata.ON_TERRAIN
    for est, on in ((lo, True), (hi, True), (lo - 1e-9, False), (hi + 1e-9, False), (t + 0.45, True),
                    (t + 0.55, False), (t - 0.55, False)):
        got = navdata._ground(xz, np.array([est]), s, near)[0]
        assert got == (t if on else est), (est - t, got)
    near = np.array([[10.0, 1.0]])                  # the boundary point 1 m along z: its ring is 0.5 m higher
    assert navdata._ground(xz, np.array([t + 0.95]), s, near)[0] == t
    assert navdata._ground(xz, np.array([t + 1.05]), s, near)[0] == t + 1.05
    assert navdata._ground(np.zeros((0, 2)), np.zeros(0), s).shape == (0,)


def test_cache_with_a_height_field(tmp_path):
    path = made_up_file(str(tmp_path / 'pathdata.hpd'))

    class Field:
        raw = np.zeros((2, 2), np.uint16)
        min, max, size = (0, 0, 0), (100, 1, 100), 2

        def height(self, x, z):
            return np.zeros(np.shape(x))
    cache = str(tmp_path / 'cache')
    first = navdata.load(path, Field(), cache_dir=cache)
    assert sum(n.endswith('.navdata.pkl') for n in os.listdir(cache)) == 1, 'cached, keyed by the field'
    assert np.array_equal(navdata.load(path, Field(), cache_dir=cache).cell_y, first.cell_y)
