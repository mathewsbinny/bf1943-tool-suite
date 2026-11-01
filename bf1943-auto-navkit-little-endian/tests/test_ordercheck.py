r"""NavKit little-endian: AI order positions against made-up path data (ordercheck, navcompare)

ordercheck.order_points on a made-up level partition (area / patrol / interlude / fire-at-area orders, a patrol's
waypoint chain followed through its Next fields and stopped at a loop, positions at the origin left out, duplicates
dropped, either attribute order); drop_cracks; PathData on .hpd files built here with navbuild from made-up samples
(a 48 m square with a 6 m gap: two route islands; and the same square without the gap: one): areas, route groups,
the clip box and dropped nodes, on_area and nearest_group; ordercheck's command line with one file (self-check) and
two (pairwise check, differing points); navcompare.compare, report and its command line (--json).

No game data. A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json

import numpy as np

import hpdtool
import navbuild
import navcompare
import navsample
import ordercheck


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


def build(path, ground=None):
    """A real pathdata file from a 60 x 60 grid of flat samples at 0.8 m."""
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=ground)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(path), log=lambda *a: None)
    finally:
        navsample.load = load
    return str(path)


def two_islands(tmp_path):
    return build(tmp_path / 'gap.hpd', ground=lambda x, z: not (20 < x < 26))


def one_island(tmp_path):
    return build(tmp_path / 'whole.hpd')


def transform(x, y, z):
    return '1/0/0/0/0/1/0/0/0/0/1/0/' + f'{x}/{y}/{z}' + '/1'


def instance(typ, guid, body, napisal=False):
    attrs = f'type="{typ}" guid="{guid}"' if napisal else f'guid="{guid}" type="{typ}"'
    return f'<instance {attrs}>{body}</instance>\n'


def order(typ, guid, name, xyz, extra='', napisal=False):
    return instance(f'Entity.{typ}EntityData', guid,
                    f'<field name="Name">{name}</field><complex name="Transform">{transform(*xyz)}</complex>{extra}',
                    napisal)


W1 = '11111111-1111-1111-1111-111111111111'
W2 = '22222222-2222-2222-2222-222222222222'
W3 = '33333333-3333-3333-3333-333333333333'


def waypoint(guid, xyz, next_guid):
    return instance('Entity.AreaGeometryEntityData', guid,
                    f'<complex name="Transform">{transform(*xyz)}</complex>'
                    f'<field ref="{next_guid}" name="Next" />')


def level_xml(tmp_path):
    """A made-up level partition: 7 positions after duplicates and positions at the origin are dropped."""
    text = '<partition>\n' + ''.join([
        order('AreaOrder', 'a0000000-0000-0000-0000-000000000001', 'left', (10.0, 0.0, 10.0)),
        order('AreaOrder', 'a0000000-0000-0000-0000-000000000002', 'left again', (10.02, 0.0, 10.01)),  # duplicate
        order('AreaOrder', 'a0000000-0000-0000-0000-000000000003', 'right', (30.0, 0.0, 30.0), napisal=True),
        order('FireAtAreaOrder', 'a0000000-0000-0000-0000-000000000004', 'fire', (45.0, 0.0, 45.0)),
        order('InterludeOrder', 'a0000000-0000-0000-0000-000000000005', 'at origin', (0.0, 0.0, 0.0)),
        order('AreaOrder', 'a0000000-0000-0000-0000-000000000006', 'in the gap', (23.0, 0.0, 30.0)),
        order('AreaOrder', 'a0000000-0000-0000-0000-000000000007', 'far away', (500.0, 0.0, 500.0)),
        order('PatrolOrder', 'a0000000-0000-0000-0000-000000000008', 'patrol', (5.0, 0.0, 40.0),
              extra=f'<array name="Waypoints"><item ref="{W1}" /></array>'),
        waypoint(W1, (12.0, 0.0, 30.0), W2),
        waypoint(W2, (35.0, 0.0, 10.0), W1),                    # back to the first: the chain stops there
        waypoint(W3, (40.0, 0.0, 40.0), 'null'),                # referenced by no patrol: not a position
        instance('Entity.SoldierSpawnEntityData', 'a0000000-0000-0000-0000-000000000009',
                 f'<complex name="Transform">{transform(15.0, 0.0, 15.0)}</complex>'),
    ]) + '</partition>\n'
    path = tmp_path / 'level.xml'
    path.write_text(text, encoding='utf-8')
    return str(path)


def run_main(fn, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn(argv)
    return code, out.getvalue()


def test_order_points(tmp_path):
    pts = ordercheck.order_points(level_xml(tmp_path))
    kinds = sorted((k, n) for k, n, _ in pts)
    assert kinds == sorted([('AreaOrder', 'left'), ('AreaOrder', 'right'), ('FireAtAreaOrder', 'fire'),
                            ('AreaOrder', 'in the gap'), ('AreaOrder', 'far away'), ('PatrolOrder', 'patrol'),
                            ('PatrolWaypoint', 'patrol'), ('PatrolWaypoint', 'patrol')]), kinds
    where = {(k, p) for k, _, p in pts}
    assert ('PatrolWaypoint', (12.0, 0.0, 30.0)) in where and ('PatrolWaypoint', (35.0, 0.0, 10.0)) in where, \
        'the waypoint chain is followed through Next'
    assert not any(p == (40.0, 0.0, 40.0) for _, _, p in pts), 'a waypoint no patrol references is not a position'
    assert not any(n == 'at origin' for _, n, _ in pts), 'an order at the origin has no position'
    assert sum(1 for _, n, _ in pts if n.startswith('left')) == 1, 'a duplicate (same kind, same 0.1 m spot) is dropped'


def test_order_points_without_transform(tmp_path):
    path = tmp_path / 'bare.xml'
    path.write_text(instance('Entity.AreaOrderEntityData', 'a0000000-0000-0000-0000-00000000000a',
                             '<field name="Name">no transform</field>'), encoding='utf-8')
    assert ordercheck.order_points(str(path)) == []


def test_drop_cracks():
    a, b, c = (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 1.0, 1.0)
    segs = [(a, b), (b, c), (b, a), (c, a)]          # (a, b) twice, once each way: a crack inside the area
    assert ordercheck.drop_cracks(segs) == [(b, c), (c, a)]
    assert ordercheck.drop_cracks([(a, b), (b, a), (a, b)]) == [(a, b), (b, a), (a, b)], \
        'an odd count of the same segment keeps every copy'


def test_path_data_groups(tmp_path):
    data = ordercheck.PathData(two_islands(tmp_path))
    assert len(data.areas) == 6, len(data.areas)
    assert len(data.nodes) == 13
    assert sorted(data.group_size.values()) == [4, 9], dict(data.group_size)
    left = {nid for nid, pos in data.nodes if pos[0] < 20}
    assert len({data.group[n] for n in left}) == 1 and len(left) == 4, 'the left island is one group'
    whole = ordercheck.PathData(one_island(tmp_path))
    assert len(whole.group_size) == 1, 'no gap: one route group'


def test_path_data_clip_and_drop(tmp_path):
    path = two_islands(tmp_path)
    clipped = ordercheck.PathData(path, clip=(0.0, 0.0, 20.0, 100.0))
    assert sorted(clipped.group_size.values()) == [4], 'the clip box keeps only the left island'
    first = ordercheck.PathData(path).nodes[0][0]
    dropped = ordercheck.PathData(path, drop={first})
    assert first not in dropped.ids and len(dropped.nodes) == 12


def test_on_area_and_nearest_group(tmp_path):
    data = ordercheck.PathData(two_islands(tmp_path))
    assert data.on_area((10.0, 0.0, 10.0)) and data.on_area((30.0, 0.0, 30.0))
    assert not data.on_area((23.0, 0.0, 30.0)), 'the gap is not walkable'
    assert not data.on_area((10.0, 20.0, 10.0)), 'far above the area: not on it'
    assert not data.on_area((500.0, 0.0, 500.0))
    left, size_left = data.nearest_group((10.0, 0.0, 10.0))
    right, size_right = data.nearest_group((30.0, 0.0, 30.0))
    assert (size_left, size_right) == (4, 9) and left != right
    assert data.nearest_group((500.0, 0.0, 500.0)) == (None, None), 'no node within 15 m'
    assert data.nearest_group((10.0, 30.0, 10.0)) == (None, None), 'nodes more than 4 m below do not count'


def test_nearest_group_no_nodes():
    data = ordercheck.PathData.__new__(ordercheck.PathData)
    data.ids, data.pos, data.group, data.group_size = [], np.zeros((0, 3)), {}, {}
    assert data.nearest_group((0.0, 0.0, 0.0)) == (None, None)


def test_ordercheck_main_one_file(tmp_path):
    xml = level_xml(tmp_path)
    code, out = run_main(ordercheck.main, ['ordercheck.py', xml, two_islands(tmp_path)])
    assert code == 0, out
    assert '8 order / waypoint positions' in out, out
    # on areas: left, right, fire, patrol, both waypoints (not the gap, not far away)
    assert 'on an area: 6/8' in out, out
    # of the 6 on areas: left + patrol + waypoint (12, 30) on the left, right + fire + waypoint (35, 10) on the right
    assert 'self-check: 6/15 point pairs on areas are route-connected (40.0%)' in out, out


def test_ordercheck_main_two_files(tmp_path):
    xml = level_xml(tmp_path)
    ref, cand = two_islands(tmp_path), one_island(tmp_path)
    code, out = run_main(ordercheck.main, ['ordercheck.py', xml, ref, cand])
    assert code == 0, out
    assert 'pairwise route check: 6/6 reference-connected pairs also connected (100.0%); 9 pairs connected only ' \
           'in the second file' in out, out
    assert 'points that differ (1)' in out and 'in the gap' in out, 'the gap point is on an area only in the second'


def test_ordercheck_main_usage():
    code, out = run_main(ordercheck.main, ['ordercheck.py', 'only.xml'])
    assert code == 1 and 'Usage' in out


def test_navcompare(tmp_path):
    xml = level_xml(tmp_path)
    ref, cand = two_islands(tmp_path), one_island(tmp_path)
    r = navcompare.compare(xml, ref, cand)
    assert r['positions'] == 8
    assert r['reference_covers'] == 6 and r['candidate_covers_of_those'] == 6 and r['coverage_percent'] == 100.0
    assert r['candidate_only'] == 1, 'the gap point'
    assert r['pairwise'] == {'connected': 6, 'pairs': 6, 'percent': 100.0}
    # main network: positions near a group >= 25 % of the largest (9 nodes): the 4-node island counts too
    assert r['main_network']['reference'] == 7 and r['main_network']['candidate'] == 7, r['main_network']
    f = hpdtool.parse(open(ref, 'rb').read())
    assert r['files']['reference'] == {'file': ref, 'sectors': 1, 'nodes': 13,
                                       'edges': sum(len(s.edges) for s in f.sectors)}
    back = navcompare.compare(xml, cand, ref)
    assert back['reference_covers'] == 7 and back['candidate_covers_of_those'] == 6
    assert back['coverage_percent'] == round(6 / 7 * 100, 1)
    assert back['pairwise']['pairs'] == 21 and back['pairwise']['connected'] == 6
    lines = []
    navcompare.report(r, log=lines.append)
    assert len(lines) == 3 and '(100.0 %) + 1 more' in lines[0] and lines[2].startswith('sectors 1 / 1'), lines


def test_navcompare_main(tmp_path):
    xml = level_xml(tmp_path)
    ref, cand = two_islands(tmp_path), one_island(tmp_path)
    code, out = run_main(navcompare.main, ['navcompare.py', xml, ref, cand, '--json'])
    assert code == 0 and json.loads(out)['coverage_percent'] == 100.0
    code, out = run_main(navcompare.main, ['navcompare.py', xml, ref, cand])
    assert code == 0 and out.startswith('8 order positions'), out
    code, out = run_main(navcompare.main, ['navcompare.py', xml, ref])
    assert code == 1 and 'Usage' in out


def test_main_network_empty():
    data = ordercheck.PathData.__new__(ordercheck.PathData)
    data.ids, data.pos, data.group, data.group_size = [], np.zeros((0, 3)), {}, {}
    assert navcompare.main_network(data, [('AreaOrder', 'x', (0.0, 0.0, 0.0))]) == 0
