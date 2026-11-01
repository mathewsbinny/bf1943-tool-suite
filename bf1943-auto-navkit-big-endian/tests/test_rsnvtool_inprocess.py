r"""NavKit (big-endian): rsnvtool's commands in-process on a made-up level

rsnvtool.main on a world written here (no game data) in a temporary work folder: two strips of 0.8 m cells 4.8 m
apart joined by a walk edge (tight: no cells between) and a one-way ladder, with the build's samples on both grids and
their base masks (the gap's columns not kept). Checks each command's answer and exit code: info, validate, locate (a
hit and a miss), los, route (by nodes, by points, the soldier filter's tight-edge price, a one-way edge that has no way
back, a picture), node and point errors, pockets (the tight edge splits the main network; the ladder's top is outside
its strongly connected core), tight (the gap is a hole of its length, with the samples there, as JSON), cells (the
areas, both sample grids, the line), walk (the gap on both grids), picture (one floor), diff (same and other node
counts), a vehicle world by level name and --class, a missing export refused; and the helpers pieces / strong_core on
one-way edges.
"""
import contextlib
import io
import json
import os
import struct
from types import SimpleNamespace

import numpy as np

import navsample
import rsnvgrid1943 as rg
import rsnvtool
from bfbc2core import rsnv

LV = 'yard'
QUIET = lambda *a: None                                                 # noqa: E731
HEADER = {'sea_level': 0.0, 'radius': 0.35, 'height': 1.8, 'step': 0.37, 'water_depth': 1.4, 'sha256': '0' * 8}
GAP = range(10, 16)                                       # 0.8 m columns with no cells (x 8.0 .. 12.0)


def world(path, cls='jeep', extra_node=True):
    """Strip A (i 0..9) and strip B (i 16..25), j 0..4, y 10; nodes 0 (0, 2), 1 (9, 2) on A, 2 (16, 2), 3 (25, 2) on
    B, 4 (5, 4) on A; edges 0-1, 2-3, 1-2 (tight), and a ladder 0 -> 4 one way."""
    cells = sorted((i, j) for i in list(range(0, 10)) + list(range(16, 26)) for j in range(5))
    idx = {c: k for k, c in enumerate(cells)}
    links = {(k, di, dj): idx[(i + di, j + dj)] for (i, j), k in idx.items() for di, dj in rg.STRAIGHT
             if (i + di, j + dj) in idx}
    cs = rg.CellSet(0.8, 0.0, 0.0, [c[0] for c in cells], [c[1] for c in cells], [10.0] * len(cells), links)
    nodes = [(0, idx[(0, 2)]), (0, idx[(9, 2)]), (0, idx[(16, 2)]), (0, idx[(25, 2)])]
    edges = [(0, 1, rsnv.EDGE_WALK, None, True), (2, 3, rsnv.EDGE_WALK, None, True), (1, 2, rsnv.EDGE_WALK, None, True)]
    if extra_node:
        nodes.append((0, idx[(5, 4)]))
        edges.append((0, 4, rsnv.EDGE_LADDER_UP, None, False))
    rg.write_world(LV, cls, [cs], nodes, edges, [], path, (0, 0), HEADER, {'sources': {'hpd': {'sha256': 'ab' * 32}}},
                   log=QUIET)
    return path


def samples_file(path, pitch, nx, nz, keep_column):
    """A navsample version-1 file: one surface at y 10 per column, normal up; and the keep mask of its surfaces."""
    with open(path, 'wb') as f:
        f.write(navsample.HEADER.pack(b'ONS1', 1, pitch, 0.0, 0.0, nx, nz, 50.0, -50.0, 0.35, 0.37, 1.8, 0.0, 0))
        for _ in range(nx * nz):
            f.write(bytes([1]) + struct.pack('<f', 10.0) + bytes([0, 127, 0, 0]))
    return np.array([keep_column(i * pitch) for _j in range(nz) for i in range(nx)], bool)


class Level:
    def __init__(self, tmp):
        self.saved = os.environ.get('BF1943_WORK')
        os.environ['BF1943_WORK'] = str(tmp)
        root = os.path.join(str(tmp), 'levels', LV)
        self.navkit = os.path.join(root, 'navkit')
        for sub in ('navkit', 'samples', os.path.join('audit', 'masks')):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        self.path = world(os.path.join(self.navkit, f'{LV}.rsnv'))
        world(os.path.join(self.navkit, f'{LV}.tank.rsnv'), cls='tank', extra_node=False)
        not_gap = lambda x: not (7.6 < x < 12.4)                       # noqa: E731
        for name, mask, pitch, nx, nz in (('samples.bin', 'normal', 0.8, 30, 6), ('samples_02.bin', 'detailed', 0.2, 120, 20)):
            keep = samples_file(os.path.join(root, 'samples', name), pitch, nx, nz, not_gap)
            np.savez(os.path.join(root, 'audit', 'masks', f'{mask}_base.npz'), keep=keep, surfaces=len(keep),
                     hpd_sha256='cd' * 32)

    def restore(self):
        if self.saved is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = self.saved


@contextlib.contextmanager
def level(tmp):
    lv = Level(tmp)
    try:
        yield lv
    finally:
        lv.restore()


def run(*args):
    """(exit code, printed text) of rsnvtool.main."""
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = rsnvtool.main(['rsnvtool.py', *args])
    return code, out.getvalue()


def test_info_validate_locate_los(tmp_path):
    with level(tmp_path) as L:
        code, out = run('info', LV)
        assert code == 0, out
        assert '5 nodes, 7 edges (walk 6, ladder up 1); tight 2 of 6 walk edges (33.33 %)' in out, out
        assert '2 areas (0.8 m: 2), 0 portals' in out
        assert 'little-endian' in out and 'sources: hpd abababababab' in out and '1 islands' in out
        assert run('validate', L.path) == (0, f'{L.path}: 0 problem(s)\n')
        code, out = run('locate', LV, '4.0', '10.0', '1.6')
        assert code == 0 and 'cell (5, 2)' in out and 'nearest node' in out, out
        code, out = run('locate', LV, '10.0', '10.0', '1.6', '--max-dist', '0.5')
        assert code == 1 and 'no walkable cell within 0.5 m' in out
        code, out = run('los', LV, '0.0,10.0,1.6', '7.2,10.0,1.6')
        assert code == 0 and out.startswith('in sight')
        code, out = run('los', LV, '7.2,10.0,1.6', '12.8,10.0,1.6')
        assert code == 1 and out.startswith('not in sight')


def test_validate_reports_problems():
    w = SimpleNamespace(validate=lambda: ['node 3: no area', 'edge 2: target 99'])
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = rsnvtool.cmd_validate(w, 'x.rsnv')
    assert code == 1 and 'node 3: no area' in out.getvalue() and 'x.rsnv: 2 problem(s)' in out.getvalue()


def test_route(tmp_path):
    with level(tmp_path) as L:
        code, out = run('route', LV, '0', '3')
        assert code == 0 and '0 -> 3: done' in out and 'tight 1' in out and '4 nodes' in out, out
        plain = float(out.split('cost ')[1].split(' m')[0])
        code, out = run('route', LV, '0', '3', '--soldier')
        soldier = float(out.split('cost ')[1].split(' m')[0])
        assert code == 0 and '(soldier filter)' in out and abs(soldier - plain - 50.0) < 0.2, (plain, soldier)
        code, out = run('route', LV, '0.1,10.0,1.5', '19.9,10.0,1.7')
        assert code == 0 and '0 -> 3' in out
        code, out = run('route', LV, '0', '4')
        assert code == 0 and "edges {'ladder up': 1}, tight 0" in out, out
        code, out = run('route', LV, '4', '0')
        assert code == 1 and '4 -> 0' in out and 'done' not in out, 'the ladder is one way: no way back'
        code, out = run('route', LV, '0', '1', '--png')
        png = os.path.join(L.navkit, 'rsnvtool-route-0-1.png')
        assert code == 0 and os.path.isfile(png) and 'picture: ' + png in out
        mine = os.path.join(str(tmp_path), 'r.png')
        code, out = run('route', LV, '0', '3', '--png', mine)
        assert code == 0 and os.path.getsize(mine) > 0
        for bad, why in (('9', 'the file has nodes 0..4'), ('50.0,10.0,50.0', 'no walkable cell within 6 m')):
            try:
                run('route', LV, bad, '0')
                raise AssertionError(f'{bad} accepted')
            except ValueError as e:
                assert why in str(e)
        try:
            rsnvtool.point('1,2')
            raise AssertionError('a point of two values')
        except ValueError as e:
            assert 'a point is X,Y,Z' in str(e)


def test_pockets_and_pieces(tmp_path):
    with level(tmp_path):
        code, out = run('pockets', LV, '--top', '1')
        assert code == 0, out
        assert 'main network (island 0): 5 nodes; without tight edges 2 pieces, the largest 3; 2 nodes (40.00 %)' in out
        assert 'of the largest piece, 1 nodes (20.00 %) are outside its strongly connected core' in out
        assert '2 nodes around (16.4, 10.0, 1.6)' in out, out
    # pieces are disjoint whichever way an edge runs (1 -> 0 alone); strong_core is the cycle
    w = SimpleNamespace(node_first_edge=np.array([0, 1, 2, 3, 3]), edge_target=np.array([2, 0, 1]),
                        edge_flags=np.zeros(3, np.int64))
    assert rsnvtool.pieces(w, [0, 1, 2, 3]) == [[0, 1, 2], [3]]
    assert rsnvtool.strong_core(w, [0, 1, 2, 3]) == [0, 1, 2]
    w.edge_target = np.array([1, 2, 3])                     # a chain: every core is one node, the lowest wins
    w.node_first_edge = np.array([0, 1, 2, 3, 3])
    assert rsnvtool.strong_core(w, [0, 1, 2, 3]) == [0]


def test_tight_with_samples(tmp_path):
    with level(tmp_path):
        js = os.path.join(str(tmp_path), 't.json')
        code, out = run('tight', LV, '--samples', '--json', js)
        assert code == 0 and '1 tight pairs' in out and 'hole/hole 1' in out, out
        data = json.load(open(js, encoding='utf-8'))
        assert data['meta']['pairs'] == 1 and data['meta']['masks_hpd_sha256'] == ['cd' * 32, 'cd' * 32]
        row = data['pairs'][0]
        assert row['nodes'] == [1, 2] and abs(row['length'] - 5.6) < 1e-3
        assert row['ends'][0]['area'] is not None and row['ends'][0]['pitch'] == 0.8
        for way in ('ab', 'ba'):
            fail = row[way]
            assert fail['cause'] == 'hole' and abs(fail['gap'] - 4.248) < 1e-3, fail
            grids = fail['samples']
            assert set(grids) == {'0.8 m', '0.2 m'}
            assert all(not kept for _y, kept, _s in grids['0.8 m']['surfaces']), 'the gap is not kept'
        code, out = run('tight', LV)
        assert code == 0 and 'hole at' in out


def test_cells_walk_picture(tmp_path):
    with level(tmp_path) as L:
        code, out = run('cells', LV, '--at', '7.2,1.6', '--y', '10', '--radius', '1.0', '--samples',
                        '--line', '6.4,10,1.6', '9.6,10,1.6')
        assert code == 0, out
        assert 'area 0 (sector 0000, 0.8 m' in out
        assert 'samples 0.8 m (kept surfaces from the base mask of build cdcdcdcdcdcd)' in out and 'samples 0.2 m' in out
        maps = out.split('samples 0.8 m')[1]
        assert 'K' in maps and '.' in maps, 'kept samples and the gap'
        area_map = out.split('area 0')[1].split('samples')[0]
        assert 'X' in area_map and '*' in area_map, 'the point on a cell; the line over the gap'
        code, out = run('cells', LV, '--at', '7.2,1.6', '--y', '20', '--radius', '0.4')
        assert code == 0 and 'area ' not in out.split('\n', 1)[1], 'no floor near 20 m'
        code, out = run('walk', LV, '1', '2')
        assert code == 0 and 'not in sight' in out
        assert '0.2 m: largest step 0.0 m' in out and '0.8 m: largest step 0.0 m' in out
        assert out.count('gaps [4.8]') == 2, 'the six 0.8 m columns not kept, on both grids'
        code, out = run('walk', LV, '0.0,10.0,1.6', '30.0,10.0,1.6')
        assert '0.2 m: no samples there' in out and '0.8 m: no samples there' in out
        png = os.path.join(L.navkit, 'rsnvtool-10_2.png')
        code, out = run('picture', LV, '--at', '10,2', '--radius', '12', '--y', '10', '--route', '0', '3', '--soldier')
        assert code == 0 and os.path.isfile(png)
        code, out = run('picture', LV, '--at', '10,2', '--radius', '12', '--y', '30',
                        '--png', os.path.join(str(tmp_path), 'empty.png'))
        assert code == 0 and os.path.isfile(os.path.join(str(tmp_path), 'empty.png'))


def test_diff_and_vehicle_worlds(tmp_path):
    with level(tmp_path) as L:
        tank = os.path.join(L.navkit, f'{LV}.tank.rsnv')
        code, out = run('diff', L.path, tank)
        assert code == 0 and 'node counts differ' in out and 'nodes' in out and '-1' in out, out
        code, out = run('diff', L.path, L.path)
        assert code == 0 and 'same node count: 0 nodes moved' in out and 'only in a 0, only in b 0' in out
        code, out = run('info', LV, '--class', 'tank')
        assert code == 0 and '4 nodes' in out and tank in out
        try:
            run('info', LV, '--class', 'plane')
            raise AssertionError('a missing vehicle world was read')
        except FileNotFoundError as e:
            assert 'no such export' in str(e) and 'navexport_vehicles1943.py' in str(e)
        try:
            run('info', 'no_such_level')
            raise AssertionError('a missing export was read')
        except FileNotFoundError as e:
            assert 'no such export' in str(e) and 'navexport_vehicles1943' not in str(e)


def test_los_failure_causes(tmp_path):
    with level(tmp_path) as L:
        w = rsnv.load(L.path)
        assert rsnvtool.los_failure(w, (0.0, 10.0, 1.6), (7.2, 10.0, 1.6)) is None
        fail = rsnvtool.los_failure(w, (7.2, 10.0, 1.6), (12.8, 10.0, 1.6))
        assert fail['cause'] == 'hole' and fail['others'] == [] and fail['own'] is False
        assert rsnvtool.end_info(w, (50.0, 10.0, 50.0)) == {'area': None}
        assert rsnvtool._edge_colour(True, 0) == (230, 25, 75) and rsnvtool._edge_colour(False, 4) == (0, 200, 255)
        assert rsnvtool._on_line(((0, 0, 0), (2, 0, 0)), 1.0, 0.05, 0.1) and not rsnvtool._on_line(
            ((0, 0, 0), (0, 0, 0)), 1.0, 0.0, 0.1)
        assert rsnvtool.tight_pairs(w) == [(1, 2)]
