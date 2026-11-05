r"""Path test (navpath): joins, routes, one-way edges and islands on made-up graphs

navpath.route over small made-up route graphs (no game data): a point joins the nearest node within 15 m
horizontally and 4 m in height, both limits included; a point too far away is reported with its nearest distance;
the route is the cheapest over the directed edges (a node reached twice keeps its cheaper cost), its length the 3-D
length of the chain; a start on its goal node is a route of one node; two islands are reported with the pair of
nodes where they come closest (more than 512 nodes on one side, so the search goes in chunks); one island without
a route along the edges' directions is reported as one-way; the cost estimate's scale (the least cost per metre,
between 0 and 1) and the adjacency are cached on the graph.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
from types import SimpleNamespace

import numpy as np

from bfbc2core import navdata, navpath

Y = navdata.Y_OFFSET


def graph(points, edges, costs=None, islands=None):
    """A made-up path-data graph: nodes at the given ground points (stored Y above them, as navdata loads them),
    directed edges with their stored costs (default: their length)."""
    pos = np.asarray(points, float) + [0, Y, 0]
    edges = np.asarray(edges, np.int64).reshape(-1, 2)
    if costs is None:
        costs = [float(np.linalg.norm(pos[b] - pos[a])) for a, b in edges]
    return SimpleNamespace(node_pos=pos, edges=edges, edge_cost=np.asarray(costs, float),
                           node_island=np.asarray(islands if islands is not None else [0] * len(pos), np.int32),
                           y_offset=Y)


def test_join_limits_are_inclusive():
    nav = graph([[0, 0, 0], [100, 0, 0]], [])
    assert navpath.join(nav, np.array([15.0, 0, 0])) == (0, 15.0), 'exactly 15 m joins'
    node, dist = navpath.join(nav, np.array([15.5, 0, 0]))
    assert node is None and dist == 15.5, (node, dist)
    assert navpath.join(nav, np.array([0, 4.0, 0]))[0] == 0, 'exactly 4 m above joins'
    assert navpath.join(nav, np.array([0, -4.0, 0]))[0] == 0, 'exactly 4 m below joins'
    node, dist = navpath.join(nav, np.array([0, 4.5, 0]))
    assert node is None and dist == np.inf, 'both nodes are 4.5 m below: neither is in reach'
    nav = graph([[0, 0, 0], [100, 4.5, 0]], [])
    node, dist = navpath.join(nav, np.array([0, 4.5, 0]))
    assert node is None and dist == 100.0, 'the node 4.5 m below is out; the one level with it is 100 m off'
    assert navpath.join(nav, np.array([99, 4.5, 1]))[0] == 1, 'the nearest node in reach'


def test_join_on_an_empty_graph():
    nav = graph(np.zeros((0, 3)), [])
    node, dist = navpath.join(nav, np.array([0.0, 0, 0]))
    assert node is None and dist == np.inf


def test_cheapest_route_and_its_length():
    # 0 -> 1 -> 3 costs 2 + 2 = 4; 0 -> 2 -> 3 costs 1 + 5 = 6; 0 -> 3 directly costs 10
    nav = graph([[0, 0, 0], [3, 0, 4], [0, 0, 1], [3, 0, 8]], [[0, 1], [1, 3], [0, 2], [2, 3], [0, 3]],
                [2.0, 2.0, 1.0, 5.0, 10.0])
    r = navpath.route(nav, (0, 0, 0), (3, 0, 8))
    assert r.found and r.nodes == [0, 1, 3] and r.cost == 4.0, (r.nodes, r.cost)
    assert r.start_node == 0 and r.end_node == 3 and r.start_island == 0 and r.end_island == 0
    assert abs(r.length - 9.0) < 1e-9, f'5 + 4 m along the chain, not {r.length}'
    assert abs(r.straight - np.hypot(3, 8)) < 1e-9 and r.reason == ''
    assert r.expanded >= 3


def test_a_node_reached_again_more_cheaply():
    # node 2 is first reached from 0 at cost 10 (pushed), then from 1 at 1 + 1 = 2: the cheaper one wins, and the
    # stale heap entry is skipped
    nav = graph([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]], [[0, 2], [0, 1], [1, 2], [2, 3]],
                [10.0, 1.0, 1.0, 1.0])
    r = navpath.route(nav, (0, 0, 0), (3, 0, 0))
    assert r.found and r.nodes == [0, 1, 2, 3] and r.cost == 3.0, (r.nodes, r.cost)


def test_start_on_the_goal_node():
    nav = graph([[0, 0, 0], [5, 0, 0]], [[0, 1]])
    r = navpath.route(nav, (0.5, 0, 0), (0, 0, 0.5))
    assert r.found and r.nodes == [0] and r.cost == 0.0 and r.length == 0.0, (r.nodes, r.cost, r.length)
    assert r.start_join == 0.5 and r.end_join == 0.5


def test_a_point_too_far_away():
    nav = graph([[0, 0, 0], [5, 0, 0]], [[0, 1]])
    r = navpath.route(nav, (0, 0, 0), (40, 0, 0))
    assert not r.found and r.end_node is None and r.start_node == 0
    assert r.reason == 'no route node within 15 m of the end point (nearest 35 m)', r.reason
    r = navpath.route(nav, (0, 0, 30), (5, 0, 0))
    assert r.reason == 'no route node within 15 m of the start point (nearest 30 m)', r.reason
    r = navpath.route(nav, (1e6, 0, 0), (5, 0, 0))
    assert r.reason.endswith('(nearest 9999 m)'), 'the nearest distance is capped: ' + r.reason


def test_one_way_edges():
    nav = graph([[0, 0, 0], [5, 0, 0]], [[1, 0]])
    r = navpath.route(nav, (0, 0, 0), (5, 0, 0))
    assert not r.found and r.gap is None
    assert r.reason == 'same island, but no route along the edges\' directions (one-way edges)', r.reason
    assert navpath.route(nav, (5, 0, 0), (0, 0, 0)).found, 'the other way round'


def test_two_islands_closest_pair():
    # island 0: 600 nodes along x from 0 (more than one chunk of 512); island 1: two nodes. The closest pair is
    # node 550 (x = 55) and node 601 (x = 55, z = 7)
    points = [[0.1 * k, 0, 0] for k in range(600)] + [[200, 0, 0], [55, 0, 7]]
    islands = [0] * 600 + [1, 1]
    nav = graph(points, [[k, k + 1] for k in range(599)] + [[600, 601]], islands=islands)
    r = navpath.route(nav, (0, 0, 0), (200, 0, 0))
    assert not r.found and (r.start_island, r.end_island) == (0, 1)
    assert r.gap is not None and r.gap[:2] == (550, 601) and abs(r.gap[2] - 7.0) < 1e-9, r.gap
    assert r.reason == 'the points are on different islands (0 and 1); they come closest 7.0 m apart', r.reason


def test_islands_without_a_pair():
    nav = graph([[0, 0, 0], [5, 0, 0]], [], islands=[0, 1])
    assert navpath.closest_between(nav, 0, 2) is None, 'island 2 has no nodes'
    assert navpath.closest_between(nav, 3, 1) is None, 'island 3 has no nodes'
    gap = navpath.closest_between(nav, 0, 1)
    assert gap == (0, 1, 5.0), gap


def test_islands_reported_without_a_gap():
    # the end node's island holds no node by node_island's count once it is changed after the join: the reason
    # then names the islands alone
    nav = graph([[0, 0, 0], [5, 0, 0]], [], islands=[0, 1])
    real = navpath.closest_between
    navpath.closest_between = lambda *_: None
    try:
        r = navpath.route(nav, (0, 0, 0), (5, 0, 0))
    finally:
        navpath.closest_between = real
    assert r.reason == 'the points are on different islands (0, 1)', r.reason


def test_cost_scale():
    assert navpath.cost_scale(graph([[0, 0, 0]], [])) == 1.0, 'no edges'
    assert navpath.cost_scale(graph([[0, 0, 0], [0, 0, 0]], [[0, 1]], [3.0])) == 1.0, 'only zero-length edges'
    assert navpath.cost_scale(graph([[0, 0, 0], [2, 0, 0]], [[0, 1]], [0.0])) == 0.0, 'a free edge'
    assert navpath.cost_scale(graph([[0, 0, 0], [2, 0, 0]], [[0, 1]], [8.0])) == 1.0, 'at most 1'
    scale = navpath.cost_scale(graph([[0, 0, 0], [2, 0, 0], [6, 0, 0]], [[0, 1], [1, 2]], [1.5, 4.0]))
    assert 0.75 * (1 - 1e-8) < scale < 0.75, f'the least cost per metre, just under: {scale}'
    nav = graph([[0, 0, 0], [2, 0, 0]], [[0, 1]], [1.0])
    first = navpath.cost_scale(nav)
    nav.edge_cost = np.array([0.0])
    assert navpath.cost_scale(nav) == first, 'kept on the graph'


def test_adjacency():
    nav = graph([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[2, 0], [0, 1], [0, 2]], [7.0, 1.0, 2.0])
    first, targets, costs = navpath._adjacency(nav)
    assert first.tolist() == [0, 2, 2, 3], first
    assert targets.tolist() == [1, 2, 0] and costs.tolist() == [1.0, 2.0, 7.0], (targets, costs)
    assert navpath._adjacency(nav)[0] is first, 'kept on the graph'
    first, targets, costs = navpath._adjacency(graph([[0, 0, 0], [1, 0, 0]], []))
    assert first.tolist() == [0, 0, 0] and len(targets) == 0 and len(costs) == 0


def test_ground_nodes():
    nav = graph([[1, 2, 3]], [])
    assert navpath.ground_nodes(nav).tolist() == [[1, 2, 3]]


def test_cost_scale_mixed_edges():
    # a zero-length edge (both ends at one point) costing 0 beside
    # a real one: only the real one sets the scale; an edge exactly 1 m long counts like any other
    nav = graph([[0, 0, 0], [0, 0, 0], [2, 0, 0]], [[0, 1], [1, 2]], [0.0, 1.0])
    assert navpath.cost_scale(nav) == 0.5 * (1 - 1e-9)
    nav = graph([[0, 0, 0], [1, 0, 0], [3, 0, 0]], [[0, 1], [1, 2]], [0.25, 2.0])
    assert navpath.cost_scale(nav) == 0.25 * (1 - 1e-9)


def test_the_estimate_guides_the_search():
    # start 0 at x 0, goal 2 at x 10 through node 1 at x 5; node 3 at x -5 hangs off the start, away from the goal:
    # the straight-line estimate keeps the search off it (start, node 1, goal: 3 nodes expanded)
    nav = graph([[0, 0, 0], [5, 0, 0], [10, 0, 0], [-5, 0, 0]], [[0, 1], [1, 2], [0, 3]])
    r = navpath.route(nav, (0, 0, 0), (10, 0, 0))
    assert r.found and r.nodes == [0, 1, 2] and r.expanded == 3, (r.nodes, r.expanded)


def test_equal_routes_keep_the_first_found():
    # two routes of the same cost: 0 -> 1 -> 3 and 0 -> 2 -> 3; the first relaxation of node 3 is kept
    nav = graph([[0, 0, 0], [5, 0, 5], [5, 0, -5], [10, 0, 0]], [[0, 1], [0, 2], [1, 3], [2, 3]], [5.0, 5.0, 5.0, 5.0])
    r = navpath.route(nav, (0, 0, 0), (10, 0, 0))
    assert r.found and r.cost == 10.0 and r.nodes == [0, 1, 3], r.nodes


def test_route_details():
    nav = graph([[1, 0, 1], [4, 0, 5]], [[0, 1]])
    r = navpath.route(nav, (1, 0, 1), (4, 0, 5))
    assert r.found and r.nodes == [0, 1] and abs(r.length - 5.0) < 1e-9 and abs(r.straight - 5.0) < 1e-9, r
    far = navpath.route(nav, (1, 0, 1), (90, 0, 90))
    assert not far.found and far.expanded == 0 and far.cost == 0.0 and far.nodes == [] and far.length == 0.0


def test_closest_pair_in_the_second_chunk_and_ties():
    # island 0: 600 nodes along x at z 1; island 1: one node at (51.2, 8): closest to node 512, the first of the
    # second chunk of 512, 7 m away (z counts with its sign: 8 - 1, not 8 + 1)
    points = [[0.1 * k, 0, 1] for k in range(600)] + [[51.2, 0, 8]]
    nav = graph(points, [], islands=[0] * 600 + [1])
    gap = navpath.closest_between(nav, 0, 1)
    assert gap is not None and gap[:2] == (512, 600) and abs(gap[2] - 7.0) < 1e-9, gap
    # two nodes of island 0 at the same place, one in each chunk: the first is kept
    points = [[100.0 + k, 0, 0] for k in range(1030)] + [[0, 0, 3]]
    points[5] = points[1025] = [0, 0, 0]
    nav = graph(points, [], islands=[0] * 1030 + [1])
    assert navpath.closest_between(nav, 0, 1) == (5, 1030, 3.0)
