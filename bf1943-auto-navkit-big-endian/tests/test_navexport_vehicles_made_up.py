r"""NavKit (big-endian): the vehicle worlds' driver and checks on a made-up level

navexport_vehicles1943 without game data, in a temporary work folder: a soldier world of a carrier deck and a beach
(rsnvgrid1943 writes every world), a landing-craft world between them and a jeep world on the beach, each stamped as
its builder stamps it. check() passes them (transfers resolved, the carrier -> flag trip takes the craft, the reports
written); a world file replaced after its stamp, a world asked for and missing, an alight onto the carrier, and the
soldier export of another build are problems (a world not asked for: a note); world_identity names another level,
class or parameters; on_cells counts nodes off their cells; main refuses an unknown class and a level without a
soldier world, and exits 1 on a problem; build() stamps a class without spawns absent, samples again only when the
samples are not current, and hands the landing craft and planes to their builders with the prepared copy.
"""
import json
import os
from types import SimpleNamespace

import numpy as np

import groundnav1943
import navexport_vehicles1943 as nv
import navkitjson1943 as nj
import rsnvgrid1943 as rg
import vehicleclasses1943 as vc
from bfbc2core import rsnv, rsnvtrip

LV = 'made_up_nv'
quiet = lambda *a: None                                                 # noqa: E731


def strip_world(path, lv, cls, cells_x, nodes_x, split=None, offm=(), island_flags=None):
    """A strip of 2 m cells (x indices cells_x, j 0..2) at y 10, nodes at x positions (z 2), edges between neighbours
    except across the cell index split (two islands)."""
    ii = [i for i in cells_x for _ in range(3)]
    jj = [j for _ in cells_x for j in range(3)]
    idx = {(i, j): k for k, (i, j) in enumerate(zip(ii, jj))}
    links = {}
    for (i, j), k in idx.items():
        for di, dj in rg.STRAIGHT:
            n = idx.get((i + di, j + dj))
            if n is not None and not (split and min(i, i + di) < split <= max(i, i + di)):
                links[(k, di, dj)] = n
    cs = rg.CellSet(2.0, 0.0, 0.0, ii, jj, [10.0] * len(ii), links, name=cls)
    nodes = [(0, idx[(int(round(x / 2.0)), 1)]) for x in nodes_x]
    edges = [(a, a + 1, rsnv.EDGE_WALK, None, True) for a in range(len(nodes_x) - 1)
             if not (split and nodes_x[a] / 2.0 < split <= nodes_x[a + 1] / 2.0)]
    header = {'sea_level': 0.0, 'radius': vc.value(cls, 'radius'), 'height': 2.0, 'step': 0.4, 'water_depth': 1.0,
              'sha256': '0' * 8}
    rg.write_world(lv, cls, [cs], nodes, edges, list(offm), path, (0, 0), header, {}, island_flags=island_flags,
                   flag_tight=False, log=quiet)


DECK_X = [0, 4, 8, 12, 16, 20]
BEACH_X = [80, 84, 88, 92, 96, 100, 104, 108, 112, 116, 120]


def lcvp_offm(alight_x=80.0):
    board = (rsnvtrip.OFFM_BOARD, 1, 3, 0, 20.0, 10.0, 2.0, 0.0, 8.0, 5)
    alight = (rsnvtrip.OFFM_ALIGHT, 3, 3, 6, alight_x, 10.0, 2.0, 0.0, 1.0, 6)
    return [board, alight]


class Level:
    """The made-up level in a temporary work folder; flags_of stood in (restored by close())."""

    def __init__(self, tmp, lv=LV):
        self.lv = lv
        self.saved = (os.environ.get('BF1943_WORK'), nv.flags_of)
        os.environ['BF1943_WORK'] = str(tmp)
        nv.flags_of = lambda lv, game=None: [('A', [120.0, 10.0, 2.0])]
        self.navkit = nj.paths(lv)['navkit']
        os.makedirs(self.navkit)
        self.soldier = os.path.join(self.navkit, f'{lv}.rsnv')
        strip_world(self.soldier, lv, 'jeep', list(range(0, 11)) + list(range(40, 61)), DECK_X + BEACH_X, split=30,
                    island_flags={1: rsnv.ISLD_CARRIER})
        h = nj.sha256(self.soldier)
        self.st = {'level': lv, 'preparation': 'p', 'game': 'G:/copy', 'built': 't', 'checked': 't ok',
                   'build_from': {'preparation': 'p', 'hpd_sha256': 'h'}, 'exported': {'sha256': h, 'hpd_sha256': 'h'}}
        nj.write(lv, self.st)

    def world(self, cls, cells_x, nodes_x, offm=(), stamp=True):
        path = nv.world_path(self.lv, cls)
        strip_world(path, self.lv, cls, cells_x, nodes_x, offm=offm)
        if stamp:
            vc.record(self.lv, 'worlds', dict(vc.inputs(self.st, self.lv, cls), soldier=nj.sha256(self.soldier),
                                              sha256=nj.sha256(path)), cls)
        return path

    def standard(self):
        self.world('lcvp', list(range(10, 41)), [20, 30, 40, 50, 60, 70, 80], lcvp_offm())
        self.world('jeep', list(range(40, 61)), [84, 92, 100, 108, 116],
                   [(rsnvtrip.OFFM_BOARD, 3, 1, 0, 84.0, 10.0, 2.0, 0.0, 4.0, 0xFFFFFFFF)])

    def close(self):
        work, nv.flags_of = self.saved
        if work is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = work


def test_check_passes(tmp_path):
    level = Level(tmp_path)
    try:
        level.standard()
        rep = nv.check(LV, ['jeep', 'lcvp'], log=quiet)
        assert rep['problems'] == [], rep['problems']
        assert rep['worlds']['lcvp']['transfers'] == {'board': 1, 'alight': 1} and rep['worlds']['lcvp']['nodes off cells'] == 0
        assert rep['worlds']['jeep']['transfers'] == {'board': 1}
        assert len(rep['trips']) == 1 and rep['trips'][0]['mode'] == 'vehicle' and rep['trips'][0]['team'] == 1, \
            'the carrier -> flag trip takes the craft'
        transfers = json.load(open(os.path.join(level.navkit, f'{LV}.transfers.json'), encoding='utf-8'))['transfers']
        alight = next(t for t in transfers if t['kind'] == 'alight')
        assert alight['soldier island'] == 0 and alight['carrier'] is False and alight['exporter soldier node'] == 6
        board = next(t for t in transfers if t['world'] == 'lcvp' and t['kind'] == 'board')
        assert board['carrier'] is True
        saved = json.load(open(os.path.join(level.navkit, f'{LV}.vehicles.json'), encoding='utf-8'))
        assert saved['problems'] == [] and set(saved['worlds']) == {'jeep', 'lcvp'}
        S, V = rsnv.load(level.soldier), rsnv.load(nv.world_path(LV, 'lcvp'))
        assert nv.carrier_team(S, V, 1) == 1 and nv.carrier_team(S, V, 0) == 3, 'a team only where a craft is boarded'
    finally:
        level.close()


def test_check_problems(tmp_path):
    level = Level(tmp_path)
    try:
        level.standard()
        with open(nv.world_path(LV, 'jeep'), 'ab') as f:
            f.write(b'\0')                                  # another file in the world's place
        rep = nv.check(LV, ['jeep', 'tank', 'lcvp'], log=quiet)
        assert any(p.startswith('jeep:') and 'not the world its builder wrote' in p for p in rep['problems'])
        assert any(p.startswith('tank: no world') for p in rep['problems']), 'a world asked for and missing'
        rep = nv.check(LV, ['jeep', 'tank', 'lcvp'], log=quiet, required=['lcvp'])
        assert rep['problems'] == [], 'worlds not asked for are notes'
        assert 'tank: not built' in rep['notes'] and any('not the world its builder wrote' in n for n in rep['notes'])
        # an alight onto the carrier's deck
        level.world('lcvp', list(range(10, 41)), [20, 30, 40, 50, 60, 70, 80], lcvp_offm(alight_x=16.0))
        rep = nv.check(LV, ['lcvp'], log=quiet)
        assert any('lcvp: alight 1 onto a carrier' in p for p in rep['problems']), rep['problems']
        assert any('trip from carrier island 1 to A' in p for p in rep['problems']), 'no ride to the flag now'
        # a world stamped on another soldier world
        level.world('lcvp', list(range(10, 41)), [20, 30, 40, 50, 60, 70, 80], lcvp_offm(), stamp=False)
        rep = nv.check(LV, ['lcvp'], log=quiet)
        assert any('is not the world its builder wrote' in p for p in rep['problems'])
        # the soldier world no longer the current export
        nj.update(LV, lambda st: st['exported'].update(hpd_sha256='other'))
        rep = nv.check(LV, ['lcvp'], log=quiet)
        assert any(p.startswith('soldier world:') and 'not the export of the current build' in p for p in rep['problems'])
        nj.update(LV, lambda st: st.pop('preparation'))
        rep = nv.check(LV, ['lcvp'], log=quiet)
        assert any(p.startswith('lcvp:') and 'prepared without a preparation stamp' in p for p in rep['problems'])
    finally:
        level.close()


def test_iwo_jima_no_go(tmp_path):
    lv = 'iwo_jima_s'
    level = Level(tmp_path, lv)
    saved = nv.IWO_NO_GO
    try:
        # the made-up worlds lie at x >= 0: the strip is moved over the alight's soldier node (x 80, z 2)
        nv.IWO_NO_GO = (78.0, 82.0, 0.0, 4.0)
        level.world('lcvp', list(range(10, 41)), [20, 30, 40, 50, 60, 70, 80], lcvp_offm())
        rep = nv.check(lv, ['lcvp'], log=quiet)
        assert any(p.startswith('lcvp: alight 1 in the no-go strip at (80.0, 2.0)') for p in rep['problems']), rep['problems']
    finally:
        nv.IWO_NO_GO = saved
        level.close()


def test_world_identity_and_on_cells(tmp_path):
    level = Level(tmp_path)
    try:
        W = rsnv.load(level.world('jeep', list(range(40, 61)), [84, 92, 100]))
        assert nv.world_identity(W, LV, 'jeep') == []
        other = nv.world_identity(W, 'another_level', 'jeep')
        assert len(other) == 1 and 'another level' in other[0]
        wrong = nv.world_identity(W, LV, 'tank')
        assert any('another class' in p for p in wrong) and any(p.startswith('radius 1.5 m') for p in wrong)
        assert any('other tank parameters' in p for p in wrong)
        assert nv.on_cells(W) == 0
        fake = SimpleNamespace(node_pos=np.array([[0.0, 10.0, 0.0], [500.0, 10.0, 500.0]]), node_area=np.array([0xFFFF, 0]),
                               areas=W.areas, walkable=W.walkable)
        assert nv.on_cells(fake) == 2, 'a node without an area and one off its area\'s cells'
    finally:
        level.close()


def test_main(tmp_path):
    level = Level(tmp_path)
    try:
        try:
            nv.main(['navexport_vehicles1943.py', LV, '--classes', 'jeep,boat'])
            raise AssertionError('an unknown class is refused')
        except ValueError as e:
            assert 'unknown class boat' in str(e)
        try:
            nv.main(['navexport_vehicles1943.py', 'no_such_level', '--check-only'])
            raise AssertionError('a level without a soldier world is refused')
        except FileNotFoundError as e:
            assert 'no soldier world' in str(e)
        level.standard()
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            assert nv.main(['navexport_vehicles1943.py', LV, '--classes', 'jeep,lcvp', '--check-only']) == 0
            assert nv.main(['navexport_vehicles1943.py', LV, '--classes', 'jeep,tank', '--check-only']) == 1
    finally:
        level.close()


def test_build_driver(tmp_path):
    level = Level(tmp_path)
    import airnav1943
    import seanav1943
    saved = (groundnav1943.spawn_seeds, groundnav1943.samples_current, groundnav1943.sample, groundnav1943.build,
             seanav1943.build, airnav1943.build)
    calls = []
    try:
        jeep_samples = groundnav1943.paths(LV)['samples']('jeep')
        os.makedirs(os.path.dirname(jeep_samples), exist_ok=True)
        with open(jeep_samples, 'wb') as f:
            f.write(b'old samples')
        groundnav1943.spawn_seeds = lambda lv, cls, game=None: [{'class': 'jeep'}] if cls == 'jeep' else []
        groundnav1943.samples_current = lambda lv, cls: False
        groundnav1943.sample = lambda lv, cls, log=print: calls.append(('sample', cls))
        groundnav1943.build = lambda lv, cls, log=print, game=None: calls.append(('ground', cls, game)) or ('j.rsnv', {'n': 1})
        seanav1943.build = lambda lv, game=None, log=print: calls.append(('sea', game)) or (None, {'spawns': 0})
        airnav1943.build = lambda lv, game=None, log=print: calls.append(('air', game)) or ('p.rsnv', {})
        tank = nv.world_path(LV, 'tank')
        with open(tank, 'wb') as f:
            f.write(b'an old tank world')
        logged = []
        out = nv.build(LV, ['jeep', 'tank', 'lcvp', 'plane'], log=logged.append)
        assert calls == [('sample', 'jeep'), ('ground', 'jeep', 'G:/copy'), ('sea', 'G:/copy'), ('air', 'G:/copy')]
        assert set(out) == {'jeep', 'plane'} and out['jeep']['path'] == 'j.rsnv', 'a builder that wrote nothing: not listed'
        assert any('samples on disk are of another preparation' in m for m in logged)
        assert not os.path.isfile(tank) and vc.stamp(nj.read(LV), 'worlds', 'tank')['absent'] == 'no tank spawns on the level'
        calls.clear()
        groundnav1943.samples_current = lambda lv, cls: True
        nv.build(LV, ['jeep'], log=quiet)
        assert calls == [('ground', 'jeep', 'G:/copy')], 'current samples are kept'
        calls.clear()
        nv.build(LV, ['jeep'], sample=True, log=quiet)
        assert calls[0] == ('sample', 'jeep'), '--sample samples again'
    finally:
        (groundnav1943.spawn_seeds, groundnav1943.samples_current, groundnav1943.sample, groundnav1943.build,
         seanav1943.build, airnav1943.build) = saved
        level.close()
