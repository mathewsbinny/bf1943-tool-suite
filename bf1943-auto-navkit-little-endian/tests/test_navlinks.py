r"""NavKit little-endian: route links and traversal annotations on a made-up build (navlinks, navtraversal)

On a .hpd built here with navbuild from made-up samples (a 48 m square with a 6 m gap: a 4-node and a 9-node route
island; no game data): navlinks.apply's link-areas (two edges with the generator's cost tags, the DistEdgeMax
warning, a second time adds nothing), route-edge add one-way and remove (and a remove with nothing to remove), a point
with no route node near it (not applied, with the reason), operations that are not graph operations (ignored), the
first-edge indices after a change, and the file written back reads the same; navtraversal.write's annotations
(islands numbered by size, the node at each end, joins_islands, an end with no node near it, the file's hash).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import hashlib
import json
import math

import numpy as np

import hpdtool
import navbuild
import navlinks
import navsample
import navtraversal

LEFT, RIGHT = [9.6, 0.0, 19.2], [32.8, 0.0, 19.2]       # beside a node of each island (nodes stand 0.95 m higher)


def flat(pitch, min_x, min_z, nx, nz, y=0.0, ground=None):
    """One flat surface per column; ground(x, z) -> False leaves the column empty (test_navbuild_edges.py)."""
    counts = np.ones(nx * nz, np.int64)
    if ground:
        for j in range(nz):
            for i in range(nx):
                counts[j * nx + i] = bool(ground(min_x + i * pitch, min_z + j * pitch))
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    n = int(start[-1])
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                             np.full(n, y, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.zeros(n, np.uint8), None)


def build(tmp_path):
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=lambda x, z: not (20 < x < 26))
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    path = str(tmp_path / 'gap.hpd')
    try:
        navbuild.build('s.bin', None, 0, 0, path, log=lambda *a: None)
    finally:
        navsample.load = load
    return path


def parsed(path):
    return hpdtool.parse(open(path, 'rb').read())


def first_edges_hold(sector):
    """Edges sorted by source; each node's a = its first outgoing edge + 1, 0 without one."""
    srcs = [e.src for e in sector.edges]
    if srcs != sorted(srcs):
        return False
    return all(n.a == (srcs.index(k) + 1 if k in srcs else 0) for k, n in enumerate(sector.nodes))


def edges_between(sector, a, b):
    return [(e.src, e.dst) for e in sector.edges if {e.src, e.dst} == {a, b}]


def test_cost_tag():
    assert navlinks.cost_tag((0, 0, 0), (3, 0, 4)) == 0x80000000 | 80
    assert navlinks.cost_tag((0, 0, 0), (0, 0, 0.06)) == 0x80000000, 'floor(distance * 16)'


def test_link_areas(tmp_path):
    f = parsed(build(tmp_path))
    s = f.sectors[0]
    before = len(s.edges)
    lines = []
    results = navlinks.apply(f, [{'op': 'link-areas', 'a': LEFT, 'b': RIGHT}], log=lines.append)
    assert len(results) == 1 and results[0][0] == 0 and results[0][1]
    assert '2 edge(s) added in sector 0x0000' in results[0][2] and "longer than the file's DistEdgeMax (15 m)" in results[0][2]
    assert len(s.edges) == before + 2 and lines == ['  ' + results[0][2]]
    ia = next(k for k, n in enumerate(s.nodes) if abs(n.pos[0] - 9.6) < 0.01 and abs(n.pos[2] - 19.2) < 0.01)
    ib = next(k for k, n in enumerate(s.nodes) if abs(n.pos[0] - 32.8) < 0.01 and abs(n.pos[2] - 19.2) < 0.01)
    assert sorted(edges_between(s, ia, ib)) == sorted([(ia, ib), (ib, ia)])
    tag = next(e.tag for e in s.edges if (e.src, e.dst) == (ia, ib))
    assert tag == 0x80000000 | int(math.floor(math.dist(s.nodes[ia].pos, s.nodes[ib].pos) * 16))
    assert first_edges_hold(s), 'first-edge indices recomputed'
    again = navlinks.apply(f, [{'op': 'link-areas', 'a': LEFT, 'b': RIGHT}], log=lambda *a: None)
    assert again[0][1] and '0 edge(s) added' in again[0][2] and '(already there)' in again[0][2]
    # written and read back: the same graph
    back = hpdtool.parse(hpdtool.write(f)).sectors[0]
    assert [(e.tag, e.src, e.dst) for e in back.edges] == [(e.tag, e.src, e.dst) for e in s.edges]
    assert [n.a for n in back.nodes] == [n.a for n in s.nodes]


def test_route_edge_add_and_remove(tmp_path):
    f = parsed(build(tmp_path))
    s = f.sectors[0]
    before = len(s.edges)
    ops = [{'op': 'route-edge', 'a': LEFT, 'b': RIGHT, 'action': 'add', 'two-way': False},
           {'op': 'traversal', 'kind': 'ladder', 'bottom': LEFT, 'top': RIGHT},           # not a graph operation
           {'op': 'route-edge', 'a': RIGHT, 'b': LEFT, 'action': 'remove', 'two-way': False}]
    results = navlinks.apply(f, ops, log=lambda *a: None)
    assert [k for k, _, _ in results] == [0, 2], 'the traversal is not a graph operation'
    assert results[0][1] and '1 edge(s) added' in results[0][2]
    assert not results[1][1] and 'no edge between those nodes' in results[1][2], 'the one-way edge runs the other way'
    assert len(s.edges) == before + 1
    removed = navlinks.apply(f, [{'op': 'route-edge', 'a': LEFT, 'b': RIGHT, 'action': 'remove'}],
                             log=lambda *a: None)
    assert removed[0][1] and '1 edge(s) removed' in removed[0][2]
    assert len(s.edges) == before and first_edges_hold(s)
    # an edge the generator made, removed both ways
    e = s.edges[0]
    a, b = s.nodes[e.src].pos, s.nodes[e.dst].pos
    out = navlinks.apply(f, [{'op': 'route-edge', 'a': [a[0], a[1] - 0.95, a[2]], 'b': [b[0], b[1] - 0.95, b[2]],
                              'action': 'remove'}], log=lambda *a: None)
    assert out[0][1] and '2 edge(s) removed' in out[0][2] and len(s.edges) == before - 2


def test_not_applied(tmp_path):
    f = parsed(build(tmp_path))
    lines = []
    results = navlinks.apply(f, [{'op': 'link-areas', 'a': LEFT, 'b': [500.0, 0.0, 500.0]},
                                 {'op': 'link-areas', 'a': LEFT, 'b': [9.7, 0.0, 19.3]}], log=lines.append)
    assert not results[0][1] and 'no sector has route nodes near both points (within 15 m)' in results[0][2]
    assert not results[1][1], 'both points nearest the same node: no edge'
    assert all(line.startswith('  NOT APPLIED: ') for line in lines)
    assert navlinks.find_pair(f, [-200.0, 0.0, -200.0], LEFT) is None, 'outside every sector'
    assert navlinks.find_pair(f, LEFT, [9.6, 10.0, 19.2]) is None, 'b 10 m above the nodes: none within 4 m'
    empty = hpdtool.Sector.__new__(hpdtool.Sector)
    empty.nodes = []
    assert navlinks._nearest(empty, LEFT) is None


def test_recompute_first_edges():
    nodes = [hpdtool.Node.__new__(hpdtool.Node) for _ in range(3)]
    sector = type('S', (), {})()
    sector.nodes = nodes
    sector.edges = [hpdtool.Edge(1, 2, 0), hpdtool.Edge(2, 0, 1), hpdtool.Edge(3, 2, 1), hpdtool.Edge(4, 0, 2)]
    navlinks.recompute_first_edges(sector)
    assert [(e.tag, e.src) for e in sector.edges] == [(2, 0), (4, 0), (1, 2), (3, 2)], 'stable within a source'
    assert [n.a for n in nodes] == [1, 0, 3]


def test_traversal(tmp_path):
    path = build(tmp_path)
    f = parsed(path)
    g = navtraversal.graph(f)
    ids, _secs, pos, island = g
    assert len(ids) == 13 and pos.shape == (13, 3)
    assert sorted(island).count(0) == 9 and sorted(island).count(1) == 4, 'islands numbered by size'
    out = tmp_path / 'sp_01.traversal.json'
    lines = []
    ops = [{'op': 'traversal', 'kind': 'ladder', 'bottom': LEFT, 'top': [32.8, 6.0, 19.2], 'by': 'me', 'when': 'now'},
           {'op': 'traversal', 'kind': 'jump', 'two-way': False, 'bottom': RIGHT, 'top': [500.0, 0.0, 500.0]},
           {'op': 'link-areas', 'a': LEFT, 'b': RIGHT}]
    links = navtraversal.write(f, path, ops, 'Levels/SP_01', str(out), log=lines.append)
    assert len(links) == 2 and len(lines) == 2
    ladder, jump = links
    assert ladder['bottom_node']['island'] == 1 and ladder['top_node'] is None, 'the top is 5 m above the nodes'
    assert ladder['rise'] == 6.0 and ladder['length'] == round(math.dist(LEFT, [32.8, 6.0, 19.2]), 2)
    assert ladder['two-way'] is True and ladder['by'] == 'me' and not ladder['joins_islands']
    assert jump['top_node'] is None and 'no route node within 15 m' in lines[1] and 'two-way' not in lines[1]
    doc = json.loads(out.read_text(encoding='utf-8'))
    assert doc['format'] == 'bfbc2-traversal' and doc['version'] == 1 and doc['pathdata'] == 'gap.hpd'
    assert doc['pathdata_sha1'] == hashlib.sha1(open(path, 'rb').read()).hexdigest()
    assert doc['links'] == links
    assert not (tmp_path / 'sp_01.traversal.json.tmp').exists()
    joins = navtraversal.write(f, path, [{'op': 'traversal', 'kind': 'vault', 'bottom': LEFT, 'top': RIGHT}],
                               'Levels/SP_01', str(out), log=lines.append)
    assert joins[0]['joins_islands'] and lines[-1].endswith(' - joins two islands')
    assert navtraversal.write(f, path, [], 'L', str(out), log=lines.append) == []
    assert json.loads(out.read_text(encoding='utf-8'))['links'] == []


def test_traversal_nearest_empty():
    g = ([], [], np.zeros((0, 3)), [])
    assert navtraversal.nearest(g, LEFT) is None
