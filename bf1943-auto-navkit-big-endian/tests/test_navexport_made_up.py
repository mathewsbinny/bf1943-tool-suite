r"""NavKit (big-endian): the soldier RSNV export on a made-up level

navexport1943.export on a level built here (no game data): a flat 120 x 48 m yard sampled by hand and built by
navbuild with BF1943's soldier settings, a flat height field written as the preparation saves it, a navkit.json whose
stamps say the build is current and checked, a jump link, two ladders (one by the route nodes, one far off) and a jeep
spawn's entry point (the game copy and the level's entities stood in). Checks: the export reads back valid with the
build's graph (navexport1943.check), every node in an area, the jump link's edge a jump and its LADR record, the far
ladder left out, the entry point's OFFM on its nearest node; the export is stamped as the soldier world; big-endian
output reads back the same; the build's cells file is used, compared with the outlines (--compare), and rasterised
from the outlines when missing or not matching; node flags and the carrier flag follow the sea level; a level without
water gets its sea under the terrain; a failed check exports to <level>.failed.rsnv, unstamped, exit code 1; a
missing build, a build not stamped and a changed .hpd are refused; the helpers (admissible_costs, height_grid,
grid_error, output_path, stamp_export) on small inputs.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
from types import SimpleNamespace

import numpy as np

import navbuild
import navexport1943 as ne
import navkitjson1943 as nj
import navsample
import soldier1943
from bfbc2core import game1943, level1943, navdata, rsnv, vehicles1943

LV = 'made_up'
QUIET = lambda *a: None                                                 # noqa: E731
GROUND = 10.0
_BUILD = {}


def samples(pitch, min_x, min_z, nx, nz, y):
    """One flat surface per column (as test_navbuild_edges.flat)."""
    n = nx * nz
    start = np.arange(n + 1, dtype=np.int64)
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                             np.full(n, y, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.zeros(n, np.uint8), None)


def built():
    """(hpd bytes, cells.npz bytes) of the yard, built once."""
    if not _BUILD:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'b.hpd')
            saved = navsample.load
            navsample.load = lambda p: (samples(0.8, 0.0, 0.0, 150, 60, GROUND), 0)
            try:
                navbuild.build('s.bin', None, 0, 0, out, log=QUIET, settings=soldier1943.SETTINGS)
            finally:
                navsample.load = saved
            _BUILD['hpd'] = open(out, 'rb').read()
            _BUILD['cells'] = open(navbuild.cells_path(out), 'rb').read()
    return _BUILD['hpd'], _BUILD['cells']


def heightfield(y=GROUND, size=4):
    """A flat little-endian TerrainHeightfield over -200..200 m, heights 0..50 m."""
    raw = int(round(y / 50.0 * 65535))
    return struct.pack('<6f6IB', -200.0, 0.0, -200.0, 200.0, 50.0, 200.0, size, 0, 0, 0, 0, 0, 0) + \
        struct.pack(f'<{size * size}H', *([raw] * size * size))


class Level:
    """A made-up level in tmp (BF1943_WORK), its game copy and entities stood in; restore() puts everything back."""

    def __init__(self, tmp, sea_level=0.0, checked='2026-10-31 10:00 AM ok', cells=True, spawn=True):
        self.saved_env = os.environ.get('BF1943_WORK')
        os.environ['BF1943_WORK'] = str(tmp)
        self.saved = (game1943.Game, game1943.find_game, level1943.load, vehicles1943.vehicle_partition,
                      vehicles1943.entry_points)
        self.p = nj.paths(LV)
        for k in ('objects', 'navkit'):
            os.makedirs(self.p[k], exist_ok=True)
        hpd, cells = built()
        with open(self.p['hpd'], 'wb') as f:
            f.write(hpd)
        if cells:
            with open(navbuild.cells_path(self.p['hpd']), 'wb') as f:
                f.write(cells)
        self.hf = heightfield()
        with open(os.path.join(self.p['objects'], 'heightfield.res'), 'wb') as f:
            f.write(self.hf)

        class HF:                           # navdata's reader of the build (for picking nodes)
            min, max, raw = np.array([-200.0, 0, -200.0]), np.array([200.0, 50, 200.0]), None

            def height(self, x, z):
                return np.full(np.shape(np.asarray(x, float)), GROUND)
        self.nav = navdata.load(self.p['hpd'], HF(), y_offset=soldier1943.SETTINGS.y_offset)
        ground = self.nav.node_pos.copy()
        ground[:, 1] -= soldier1943.SETTINGS.y_offset
        a, b = (int(v) for v in self.nav.edges[0])
        self.jump = (a, b)
        jumps = [{'platform': ground[a].tolist(), 'deck': ground[b].tolist(), 'bp': 'made_up_jump'}]
        x, _y, z = ground[3]
        ladders = [{'lo': [x - 0.2, GROUND, z - 0.2], 'hi': [x + 0.2, GROUND + 0.5, z + 0.2], 'bp': 'near/ladder'},
                   {'lo': [500.0, 0.0, 500.0], 'hi': [501.0, 4.0, 501.0], 'bp': 'far/ladder'}]
        self.write_json(os.path.join(self.p['navkit'], 'jump_links.json'), jumps)
        self.write_json(os.path.join(self.p['objects'], 'ladders.json'), ladders)
        self.entry = ground[5] + np.array([0.5, 0.9, 0.0])
        self.st = {'preparation': 'p' * 64, 'game': os.path.join(str(tmp), 'game'), 'sector_offsets': [0, 0],
                   'sea_level': sea_level, 'built': '2026-10-31 10:00 AM', 'checked': checked,
                   'objects_sha256': {'heightfield.res': hashlib.sha256(self.hf).hexdigest()},
                   'build_from': {'preparation': 'p' * 64, 'hpd_sha256': nj.sha256(self.p['hpd']),
                                  'jump_links_sha256': nj.sha256(os.path.join(self.p['navkit'], 'jump_links.json'))}}
        self.write_json(self.p['settings'], self.st)
        hf_bytes = self.hf
        game1943.Game = lambda game, lv: SimpleNamespace(data=lambda n: hf_bytes if 'heightfield' in n else None)
        game1943.find_game = lambda given=None: given
        entity = SimpleNamespace(category='vehicle spawn', position=tuple(self.entry), team=1, index=7)
        level1943.load = lambda lv, game=None, log=None: SimpleNamespace(
            entities=[entity, SimpleNamespace(category='soldier spawn', position=(0, 0, 0))] if spawn else [])
        vehicles1943.vehicle_partition = lambda e: 'objects/vehicles/beach/willys-mb/willys-mb'
        vehicles1943.entry_points = lambda g, e: [{'point': [float(v) for v in self.entry], 'radius': 3.0, 'order': 0}]

    @staticmethod
    def write_json(path, value):
        with open(path, 'w', encoding='utf-8', newline='') as f:
            json.dump(value, f)

    def settings(self, **change):
        st = nj.read(LV)
        st.update(change)
        self.write_json(self.p['settings'], st)

    def restore(self):
        (game1943.Game, game1943.find_game, level1943.load, vehicles1943.vehicle_partition,
         vehicles1943.entry_points) = self.saved
        if self.saved_env is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = self.saved_env


@contextlib.contextmanager
def level(tmp, **k):
    lv = Level(tmp, **k)
    try:
        yield lv
    finally:
        lv.restore()


def test_export_reads_back_with_the_builds_graph(tmp_path):
    with level(tmp_path) as L:
        lines = []
        out, nav = ne.export(LV, log=lines.append)
        assert out == os.path.join(L.p['navkit'], f'{LV}.rsnv')
        problems, w = ne.check(out, nav, log=QUIET)
        assert problems == [], problems
        assert len(w.node_pos) == len(nav.node_pos) > 10
        assert int((w.node_area == 0xFFFF).sum()) == 0, 'every node lies in an area'
        assert w.meta['cells'] == 'build' and 'cells.npz' in w.meta['sources']
        a, b = L.jump
        src = np.repeat(np.arange(len(w.node_pos)), np.diff(w.node_first_edge))
        kinds = {(int(s), int(t)): int(k) for s, t, k in zip(src, w.edge_target, w.edge_kind)}
        assert kinds[(a, b)] == rsnv.EDGE_JUMP and kinds[(b, a)] == rsnv.EDGE_JUMP
        assert sum(1 for k in kinds.values() if k == rsnv.EDGE_JUMP) == 2
        assert int(w.node_flags[a]) & rsnv.NAUX_JUMP_END and int(w.node_flags[b]) & rsnv.NAUX_JUMP_END
        assert len(w.ladders) == 2, 'the near ladder and the jump link; the far ladder is left out'
        assert any('1 ladder / jump record(s)' in m and 'far/ladder' not in m and 'ladder 1' in m for m in lines), lines
        assert len(w.offmesh) == 1
        offm = w.offmesh[0]
        assert int(offm[0]) == rsnv.OFFM_BOARD_VEHICLE and int(offm[1]) == 1
        assert int(offm[2]) == vehicles1943.CLASSES.index('ground vehicle')
        ground = w.node_pos
        nearest = int(np.argmin(np.linalg.norm(ground - (L.entry - [0, soldier1943.SETTINGS.y_offset, 0]), axis=1)))
        assert int(offm[3]) == nearest
        assert int(w.islands[0][2]) & rsnv.ISLD_MAIN and not int(w.islands[0][2]) & rsnv.ISLD_CARRIER
        assert abs(w.sea_level - 0.0) < 1e-6
        # stamped as the soldier world
        ex = nj.read(LV).get('exported') or {}
        assert ex.get('sha256') == nj.sha256(out) and ex.get('hpd_sha256') == L.st['build_from']['hpd_sha256']


def test_big_endian_reads_back_the_same(tmp_path):
    with level(tmp_path) as L:
        le, _ = ne.export(LV, log=QUIET)
        be, nav = ne.export(LV, big_endian=True, log=QUIET)
        assert be == os.path.join(L.p['navkit'], f'{LV}.be.rsnv')
        a, b = rsnv.load(le), rsnv.load(be)
        assert b.big_endian and not a.big_endian
        for k in ('node_pos', 'edge_target', 'edge_cost', 'edge_kind', 'node_area', 'hght', 'glst', 'anod'):
            assert np.array_equal(getattr(a, k), getattr(b, k)), k
        assert a.areas == b.areas and a.bmap == b.bmap
        assert ne.check(be, nav, log=QUIET)[0] == []
        # the big-endian file is not the soldier world: the stamp names the little-endian export
        assert nj.read(LV)['exported']['sha256'] == nj.sha256(le)


def test_compare_and_rasterised_outlines(tmp_path):
    with level(tmp_path) as L:
        lines = []
        out, nav = ne.export(LV, out=os.path.join(str(tmp_path), 'cmp.rsnv'), compare=True, log=lines.append)
        said = next(m for m in lines if 'bitmaps' in m)
        assert 'against the outlines' in said and "'cells':" in said, said
        assert 'exported' not in nj.read(LV), 'an export to another path is not stamped'
        cells_file = navbuild.cells_path(L.p['hpd'])
        os.remove(cells_file)
        lines = []
        out, nav = ne.export(LV, log=lines.append)
        assert any('no cells file' in m for m in lines)
        w = rsnv.load(out)
        assert w.meta['cells'] == 'rasterised outlines' and 'cells.npz' not in w.meta['sources']
        problems, w = ne.check(out, nav, log=QUIET)
        assert problems == [], problems
        assert int((w.node_area == 0xFFFF).sum()) == 0, 'the rasterised cells hold every node too'
        # a cells file of another build (one area fewer): not used
        with open(cells_file, 'wb') as f:
            f.write(built()[1])
        real = navbuild.read_cells
        navbuild.read_cells = lambda hpd: real(hpd)[:-1]
        try:
            lines = []
            ne.export(LV, log=lines.append)
        finally:
            navbuild.read_cells = real
        assert any('does not match the areas' in m for m in lines)


def test_sea_level_sets_node_flags_and_carriers(tmp_path):
    with level(tmp_path, sea_level=GROUND + 0.3) as L:
        out, _ = ne.export(LV, log=QUIET)
        w = rsnv.load(out)
        flags = np.asarray(w.node_flags)
        assert (flags & rsnv.NAUX_SHALLOW).all() and not (flags & rsnv.NAUX_WADE).any()
        L.settings(sea_level=GROUND + 1.0)
        out, _ = ne.export(LV, log=QUIET)
        w = rsnv.load(out)
        flags = np.asarray(w.node_flags)
        assert (flags & rsnv.NAUX_WADE).all() and not (flags & rsnv.NAUX_SHALLOW).any()
        L.settings(sea_level=GROUND + 30.0)
        out, _ = ne.export(LV, log=QUIET)
        w = rsnv.load(out)
        assert int(w.islands[0][2]) & rsnv.ISLD_CARRIER, 'an island over deep water is a carrier'
        L.settings(sea_level=None)
        out, _ = ne.export(LV, log=QUIET)
        w = rsnv.load(out)
        assert abs(w.sea_level - (GROUND - soldier1943.NO_WATER_BELOW)) < 0.01, w.sea_level


def test_failed_check_is_for_inspection_only(tmp_path):
    with level(tmp_path, checked='2026-10-31 10:00 AM FAILED') as L:
        lines = []
        out, _ = ne.export(LV, log=lines.append)
        assert out == os.path.join(L.p['navkit'], f'{LV}.failed.rsnv')
        assert any('exported for inspection only' in m for m in lines)
        assert 'exported' not in nj.read(LV)
        assert not os.path.isfile(os.path.join(L.p['navkit'], f'{LV}.rsnv'))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = ne.main(['navexport1943.py', LV])
        assert code == 1 and 'check failed' in printed.getvalue()
        # stamp_export does not stamp a failed build, even on the soldier world's own path
        soldier = os.path.join(L.p['navkit'], f'{LV}.rsnv')
        shutil.copyfile(out, soldier)
        assert ne.stamp_export(LV, nj.read(LV), soldier) is False


def test_main_with_check(tmp_path):
    with level(tmp_path, spawn=False):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = ne.main(['navexport1943.py', LV, '--check'])
        assert code == 0, printed.getvalue()
        assert 'check: 0 problem(s)' in printed.getvalue()
        assert len(rsnv.load(os.path.join(nj.paths(LV)['navkit'], f'{LV}.rsnv')).offmesh) == 0


def test_refusals(tmp_path):
    with level(tmp_path) as L:
        hpd = L.p['hpd']
        with open(hpd, 'ab') as f:
            f.write(b'changed')
        try:
            ne.export(LV, log=QUIET)
            raise AssertionError('a changed .hpd was exported')
        except ne.NotCurrent as e:
            assert 'not the build stamped' in str(e)
        L.settings(build_from={})
        try:
            ne.export(LV, log=QUIET)
            raise AssertionError('a build of no preparation was exported')
        except ne.NotCurrent as e:
            assert 'not of the current preparation' in str(e)
        os.remove(hpd)
        try:
            ne.export(LV, log=QUIET)
            raise AssertionError('no build, no export')
        except FileNotFoundError as e:
            assert 'build the level first' in str(e)


def test_changed_heightfield_is_refused(tmp_path):
    with level(tmp_path) as L:
        with open(os.path.join(L.p['objects'], 'heightfield.res'), 'wb') as f:
            f.write(heightfield(y=12.0))
        try:
            ne.export(LV, log=QUIET)
            raise AssertionError('a height field not the prepared one was used')
        except nj.NavKitError as e:
            assert 'not the height field the level was prepared with' in str(e)


def test_output_path():
    st_ok = {'checked': 't ok'}
    st_failed = {'checked': 't FAILED'}
    work = game1943.level_work(LV)
    assert ne.output_path(LV, st_ok, 'x.rsnv') == 'x.rsnv'
    assert ne.output_path(LV, st_ok) == os.path.join(work, 'navkit', f'{LV}.rsnv')
    assert ne.output_path(LV, st_ok, big_endian=True).endswith(f'{LV}.be.rsnv')
    assert ne.output_path(LV, st_failed).endswith(f'{LV}.failed.rsnv')
    assert ne.output_path(LV, st_failed, big_endian=True).endswith(f'{LV}.be.failed.rsnv')


def test_admissible_costs_raise_rounded_down_costs():
    pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 3.0)]
    got = ne.admissible_costs(pos, [0, 1, 0], [1, 0, 2], [15, 40, 48])
    assert got.tolist() == [16, 40, 48], 'only a cost under 16 x the length is raised'


def test_height_grid_and_its_error():
    ii = np.array([0, 1, 2, 3, 4, 5])
    jj = np.zeros(6, np.int64)
    y = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 12.0])
    grid, hw, hh = ne.height_grid(ii, jj, y, 5, 10.0)
    assert (hw, hh) == (2, 1) and grid.tolist() == [[0, 200]]
    grid, hw, hh = ne.height_grid(np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0), 5, 0.0)
    assert (hw, hh) == (1, 1) and grid.tolist() == [[0xFFFF]], 'no cells: one empty block'
    grid, hw, hh = ne.height_grid(ii, jj, y, 1, 10.0)
    assert ne.grid_error(ii, jj, y, grid, hw, hh, 1, 0.2, 10.0) < 1e-6, 'one block per cell reads every cell exactly'
    # a 1 m block over a 2 m step misreads its cells
    ii = np.arange(10)
    jj = np.zeros(10, np.int64)
    y = np.where(ii < 5, 10.0, 12.0)
    grid, hw, hh = ne.height_grid(ii, jj, y, 5, 10.0)
    assert ne.grid_error(ii, jj, y, grid, hw, hh, 5, 0.2, 10.0) > ne.HEIGHT_TOL


def test_rasterise_an_outline():
    hf = SimpleNamespace(height=lambda x, z: np.full(np.shape(np.asarray(x, float)), 5.0))
    square = [[(0.0, 5.9, 0.0), (2.0, 5.9, 0.0), (2.0, 5.9, 2.0), (0.0, 5.9, 2.0), (0.0, 5.9, 0.0)]]
    area = SimpleNamespace(outline=square, portals=[], borders=[], min_y=5.0, max_y=5.0 + ne.AREA_GROUND_TOP)
    ii, jj, y = ne.rasterise(area, 0.8, 0.9, hf)
    assert sorted(zip(ii.tolist(), jj.tolist())) == [(i, j) for i in range(3) for j in range(3)], 'x, z 0, 0.8, 1.6'
    assert np.allclose(y, 5.0), 'the outline 0.9 m over the terrain: on it'
    low = SimpleNamespace(height=lambda x, z: np.full(np.shape(np.asarray(x, float)), 1.0))
    _ii, _jj, y = ne.rasterise(area, 0.8, 0.9, low)
    assert np.allclose(y, 5.0), "terrain 4 m under the outline: the outline's height less the offset"
    empty = SimpleNamespace(outline=[], portals=[], borders=[], min_y=0.0, max_y=1.0)
    ii, jj, y = ne.rasterise(empty, 0.8, 0.9, hf)
    assert len(ii) == len(jj) == len(y) == 0


def test_check_finds_a_graph_unlike_the_build(tmp_path):
    with level(tmp_path):
        out, nav = ne.export(LV, log=QUIET)
        moved = SimpleNamespace(node_pos=nav.node_pos + [0.01, 0.0, 0.0], edges=nav.edges, edge_cost=nav.edge_cost)
        lines = []
        problems, _ = ne.check(out, moved, log=lines.append)
        assert problems == ['node positions differ by up to 0.0100 m'] and lines[1] == '  ' + problems[0], problems
        dearer = SimpleNamespace(node_pos=nav.node_pos, edges=nav.edges, edge_cost=nav.edge_cost + 1.0)
        assert ne.check(out, dearer, log=QUIET)[0] == ['edges or costs differ from the build']
        fewer = SimpleNamespace(node_pos=nav.node_pos[:-1], edges=nav.edges, edge_cost=nav.edge_cost)
        assert ne.check(out, fewer, log=QUIET)[0] == [f'{len(nav.node_pos)} nodes, the build has {len(nav.node_pos) - 1}']
