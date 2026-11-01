r"""NavKit (big-endian): vehicle classes - stamps, blueprint reading and the table check on made-up data

vehicleclasses1943 without game data: inputs refuses a level prepared by an older NavKit; current and stamp; the
soldier world's refusals (missing, a build of another preparation, a check that failed, the default file not the
export) and a file elsewhere taken by its hash; record refuses (and does not stamp) a world on a soldier world that
changed, and stamps nothing over another preparation; drop and absent; prepared_game; read() on made-up blueprints
(wheels, entries, exits, gears, the water jet, a missing partition) with the game copy, DBX reader and collision
stood in; collision_box joins mesh and convex points; check() names a wrong half-width and slope; main prints the
table, writes its JSON and exits 1 on a mismatch.
"""
import contextlib
import io
import json
import os
from types import SimpleNamespace

import navkitjson1943 as nj
import vehicleclasses1943 as vc
from bfbc2core import dbx, game1943

LV = 'made_up_vc'


def refusal(f, *a, **k):
    try:
        f(*a, **k)
    except (vc.StaleError, FileNotFoundError) as e:
        return f'{type(e).__name__}: {e}'
    return None


class Work:
    """A temporary BF1943_WORK holding the level's navkit folder (restored by close())."""

    def __init__(self, tmp):
        self.saved = os.environ.get('BF1943_WORK')
        os.environ['BF1943_WORK'] = str(tmp)
        self.navkit = nj.paths(LV)['navkit']
        os.makedirs(self.navkit)
        self.soldier = os.path.join(self.navkit, f'{LV}.rsnv')
        with open(self.soldier, 'wb') as f:
            f.write(b'a soldier world')
        h = nj.sha256(self.soldier)
        self.st = {'preparation': 'p', 'game': 'G:/copy', 'built': 't', 'checked': 't ok',
                   'build_from': {'preparation': 'p', 'hpd_sha256': 'h'}, 'exported': {'sha256': h, 'hpd_sha256': 'h'}}
        nj.write(LV, self.st)

    def close(self):
        if self.saved is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = self.saved


def test_inputs_and_stamps():
    why = refusal(vc.inputs, {}, LV, 'jeep')
    assert why and 'prepared without a preparation stamp' in why
    st = {'preparation': 'p', 'game': 'g'}
    assert vc.inputs(st, LV) == {'preparation': 'p', 'game': 'g'}
    full, sampling = vc.inputs(st, LV, 'jeep'), vc.inputs(st, LV, 'jeep', sampling=True, extra=1)
    assert full['params'] == vc.params_id('jeep') and sampling['params'] == vc.sampling_params_id('jeep')
    assert full['params'] != sampling['params'] and sampling['extra'] == 1
    st['vehicles'] = {'collision': {'preparation': 'p', 'time': 't'}, 'worlds': {'jeep': dict(full, time='t')}}
    assert vc.stamp(st, 'collision') == {'preparation': 'p', 'time': 't'}
    assert vc.stamp(st, 'worlds', 'tank') is None and vc.stamp({}, 'worlds', 'jeep') is None
    assert vc.current(st, 'worlds', full, 'jeep'), 'the time aside, the stamp is the inputs'
    assert not vc.current(st, 'worlds', dict(full, preparation='q'), 'jeep')
    assert not vc.current(st, 'worlds', full, 'tank')


def test_prepared_game():
    st = {'game': 'G:/copy'}
    assert vc.prepared_game(st, None, LV) == 'G:/copy'
    assert vc.prepared_game(st, 'G:/copy', LV) == 'G:/copy'
    why = refusal(vc.prepared_game, st, 'G:/other', LV)
    assert why and 'prepared from G:/copy, not G:/other' in why


def test_soldier_world(tmp_path):
    w = Work(tmp_path)
    try:
        st = w.st
        assert vc.soldier_world(st, LV, w.soldier) == nj.sha256(w.soldier)
        why = refusal(vc.soldier_world, st, LV, os.path.join(w.navkit, 'missing.rsnv'))
        assert why.startswith('FileNotFoundError') and 'no soldier world' in why
        why = refusal(vc.soldier_world, dict(st, build_from={'preparation': 'q', 'hpd_sha256': 'h'}), LV, w.soldier)
        assert why and 'not of the current preparation' in why
        why = refusal(vc.soldier_world, dict(st, checked='t FAILED'), LV, w.soldier)
        assert why and 'check did not pass' in why
        why = refusal(vc.soldier_world, dict(st, exported={'sha256': '0' * 64, 'hpd_sha256': 'h'}), LV, w.soldier)
        assert why and 'is not the export of the current build' in why
        other = os.path.join(str(tmp_path), 'elsewhere.rsnv')
        with open(other, 'wb') as f:
            f.write(b'another soldier world')
        assert vc.soldier_world(dict(st, exported={}), LV, other) == nj.sha256(other), \
            'a soldier world elsewhere (--soldier) is taken by its hash, not as the export'
        # soldier_changed: the stamp may only go on while the export is the current, passed build's
        h = nj.sha256(w.soldier)
        assert vc.soldier_changed(st, LV, h) is None
        for bad in (dict(st, checked='t FAILED'), dict(st, exported={'sha256': h, 'hpd_sha256': 'other'}),
                    {k: v for k, v in st.items() if k != 'built'}):
            assert 'changed while the vehicle world was built' in vc.soldier_changed(bad, LV, h)
        assert vc.soldier_changed(st, LV, '0' * 64)
    finally:
        w.close()


def test_record_drop_absent(tmp_path):
    w = Work(tmp_path)
    try:
        want = vc.inputs(w.st, LV, 'jeep')
        h = nj.sha256(w.soldier)
        vc.record(LV, 'worlds', dict(want, soldier=h), 'jeep')
        assert vc.current(nj.read(LV), 'worlds', dict(want, soldier=h), 'jeep'), 'a world on the current export'
        vc.record(LV, 'collision', {'preparation': 'p', 'game': 'G:/copy'})
        assert vc.stamp(nj.read(LV), 'collision')['game'] == 'G:/copy'
        vc.drop(LV, 'worlds', 'jeep')
        vc.drop(LV, 'collision')
        st = nj.read(LV)
        assert vc.stamp(st, 'worlds', 'jeep') is None and vc.stamp(st, 'collision') is None
        why = refusal(vc.record, LV, 'worlds', dict(want, soldier='0' * 64), 'jeep')
        assert why and 'it is not stamped' in why and vc.stamp(nj.read(LV), 'worlds', 'jeep') is None, \
            'a world on a soldier world that is no longer the export is refused, unstamped'
        other = os.path.join(str(tmp_path), 'elsewhere.rsnv')
        with open(other, 'wb') as f:
            f.write(b'x')
        vc.record(LV, 'worlds', dict(want, soldier='0' * 64), 'jeep', soldier_path=other)
        assert vc.stamp(nj.read(LV), 'worlds', 'jeep')['soldier'] == '0' * 64, 'another soldier file: stamped'
        vc.record(LV, 'worlds', dict(want, preparation='q'), 'tank')
        assert vc.stamp(nj.read(LV), 'worlds', 'tank') is None, 'nothing stamped over another preparation'
        # absent: the old world goes, the stamp says why
        path = os.path.join(w.navkit, f'{LV}.tank.rsnv')
        with open(path, 'wb') as f:
            f.write(b'old')
        logged = []
        vc.absent(LV, 'tank', vc.inputs(w.st, LV, 'tank'), path, 'no tank spawns', logged.append)
        assert not os.path.isfile(path) and vc.stamp(nj.read(LV), 'worlds', 'tank')['absent'] == 'no tank spawns'
        assert logged and 'of an earlier run removed' in logged[0]
        os.remove(nj.paths(LV)['settings'])
        vc.drop(LV, 'worlds', 'jeep')                  # no navkit.json: nothing to drop, no error
    finally:
        w.close()


def transform(x, y, z):
    return '/'.join(['1', '0', '0', '0', '0', '1', '0', '0', '0', '0', '1', '0', str(x), str(y), str(z), '1'])


def instance(kind, *fields):
    return f'<instance guid="g" type="Entity.{kind}">' + ''.join(fields) + '</instance>'


BLUEPRINT = ''.join([
    instance('WheelComponentData', '<field name="Name">Wheel FL</field>', f'<complex name="Transform">{transform(-0.8, 0.4, 1.2)}</complex>'),
    instance('WheelComponentData', '<field name="Name">Wheel FR</field>', f'<complex name="Transform">{transform(0.8, 0.4, 1.2)}</complex>'),
    instance('WheelComponentData', '<field name="Name">Wheel RL</field>', f'<complex name="Transform">{transform(-0.8, 0.4, -1.1)}</complex>'),
    instance('WheelComponentData', '<field name="Name">Wheel RR</field>', f'<complex name="Transform">{transform(0.8, 0.4, -1.1)}</complex>'),
    instance('PlayerEntryComponentData', '<field name="Name">Driver</field>', '<field name="EntryRadius">4.5</field>',
             f'<complex name="Transform">{transform(-1.0, 0.5, 0.0)}</complex>'),
    instance('VehicleExitPointComponentData', f'<complex name="Transform">{transform(1.8, 0.0, 0.0)}</complex>'),
    instance('WheelConfigData', '<field name="Radius">0.42</field>', '<field name="SlopeGripMinAngle">19</field>',
             '<field name="SlopeGripMaxAngle">25</field>', '<complex name="Spring"><field name="Length">0.3</field></complex>'),
    instance('ChassisData', '<field name="BodyMass">1200</field>', '<field name="SteeringType">1</field>',
             '<array name="ForwardGearSpeeds">20/60/100</array>', '<field name="IsWaterJetEngine">false</field>'),
    instance('BoxFloatPhysicsData', '<field name="Density">0.5</field>'),
])
BOAT = instance('JetEngineConfigData', '<field name="IsWaterJetEngine">true</field>', '<array name="ForwardGearSpeeds">80</array>') \
    + instance('BoatFloatPhysicsData', '<field name="Depth">0</field>')


def test_read_made_up_blueprints():
    files = {vc.VEHICLES['willys-mb'][1] + '.dbx': b'jeep', vc.VEHICLES['lcvp-landingcraft'][1] + '.dbx': b'boat',
             vc.VEHICLES['willys-mb'][2]: b'havok'}
    xml = {b'jeep': BLUEPRINT, b'boat': BOAT.encode()}
    saved = (game1943.Game, game1943.find_game, dbx.to_xml, vc.collision_box)
    seen = []
    try:
        game1943.find_game = lambda given=None: given or 'G:/default'
        game1943.Game = lambda game, level: seen.append((game, level)) or SimpleNamespace(data=files.get)
        dbx.to_xml = lambda data: xml[data]
        vc.collision_box = lambda g, res: ([-1.06, 0.0, -1.97], [0.84, 1.6, 1.83]) if g.data(res) else None
        table = vc.read('G:/copy')
    finally:
        game1943.Game, game1943.find_game, dbx.to_xml, vc.collision_box = saved
    assert seen == [('G:/copy', 'wake_island_s')]
    jeep = table['willys-mb']
    assert jeep['class'] == 'jeep' and jeep['size'] == [1.9, 1.6, 3.8]
    assert jeep['wheelbase'] == 2.3 and jeep['track'] == 1.6, 'from the four wheels\' translations'
    assert jeep['entries'] == [{'name': 'Driver', 'local': [-1.0, 0.5, 0.0], 'radius': 4.5}]
    assert jeep['exits'] == [[1.8, 0.0, 0.0]]
    assert jeep['top_gear_speed'] == 100.0 and jeep['forward_gear_speeds'] == [[20.0, 60.0, 100.0]]
    assert jeep['slope_grip_min'] == [19.0] and jeep['slope_grip_max'] == [25.0] and jeep['body_mass'] == [1200.0]
    assert jeep['spring_length'] == [0.3] and jeep['steering_type'] == [1] and jeep['wheel_radius'] == [0.42]
    assert not jeep['water_jet'] and jeep['float_physics'] == ['BoxFloatPhysicsData']
    assert jeep['collision_sha256'] is not None
    boat = table['lcvp-landingcraft']
    assert boat['water_jet'] and boat['float_physics'] == ['BoatFloatPhysicsData'] and boat['wheels'] == {}
    assert boat['collision_box'] is None and boat['size'] is None and 'wheelbase' not in boat
    assert table['kurogane'] == {'class': 'jeep', 'missing': vc.VEHICLES['kurogane'][1]}, 'a partition not in the copy'
    assert vc._translation('<complex name="Transform">1/2/3</complex>') is None, 'not a 4 x 4 matrix'
    assert vc._translation('<field name="Name">x</field>') is None


def test_collision_box():
    import havok461
    import hkpack

    class Packfile:
        def __init__(self, data, classes=None):
            pass

        def objects_of(self, cls):
            return {'hkStorageExtendedMeshShapeMeshSubpartStorage': [1, 2], 'hkConvexVerticesShape': [3, 4]}[cls]
    saved = (hkpack.Packfile, havok461.triangles, havok461.convex_box)
    try:
        hkpack.Packfile = Packfile
        havok461.triangles = lambda pf, off: ([(0, 0, 0, 1), (2, 1, -1, 1)], [(0, 1, 0)]) if off == 1 else ([], [])
        havok461.convex_box = lambda pf, off: ([[-3, 0.5, 0], [0, 0.5, 4]], None, None) if off == 3 else (None, None, None)
        assert vc.collision_box(SimpleNamespace(data=lambda r: b'x'), 'r') == ([-3.0, 0.0, -1.0], [2.0, 1.0, 4.0]), \
            'the box of the meshes\' and convex shapes\' points'
        havok461.triangles = lambda pf, off: ([], [])
        havok461.convex_box = lambda pf, off: (None, None, None)
        assert vc.collision_box(SimpleNamespace(data=lambda r: b'x'), 'r') is None, 'no shapes'
    finally:
        hkpack.Packfile, havok461.triangles, havok461.convex_box = saved
    assert vc.collision_box(SimpleNamespace(data=lambda r: None), 'r') is None, 'no collision file'


def good_table():
    return {'willys-mb': {'class': 'jeep', 'collision_box': [[-1.06, 0, -1.97], [0.84, 1.6, 1.83]],
                          'slope_grip_max': [25.0]},
            'm4-sherman': {'class': 'tank', 'collision_box': [[-1.29, 0, -3.0], [1.37, 2.6, 2.96]], 'slope_grip_max': [52.0]},
            'kurogane': {'class': 'jeep', 'missing': 'x'}}


def test_check_table():
    assert vc.check(good_table()) == []
    t = good_table()
    t['willys-mb']['collision_box'] = [[-1.5, 0, -2], [1.0, 1.6, 2]]
    t['willys-mb']['slope_grip_max'] = [30.0]
    problems = vc.check(t)
    assert any(p.startswith('jeep: half-width 1.06 against the collision\'s 1.50') for p in problems), problems
    assert 'jeep: slope 25.0 against SlopeGripMaxAngle 30.0' in problems


def test_main(tmp_path):
    saved = vc.read
    out = io.StringIO()
    target = str(tmp_path / 'classes.json')
    try:
        vc.read = lambda game=None: good_table()
        with contextlib.redirect_stdout(out):
            assert vc.main(['vehicleclasses1943.py', '--json', target]) == 0
        written = json.load(open(target, encoding='utf-8'))
        assert written['vehicles']['willys-mb']['class'] == 'jeep'
        assert written['classes']['jeep']['radius'][0] == vc.value('jeep', 'radius')
        bad = good_table()
        bad['willys-mb']['slope_grip_max'] = [30.0]
        vc.read = lambda game=None: bad
        with contextlib.redirect_stdout(out):
            assert vc.main(['vehicleclasses1943.py']) == 1
        assert 'MISMATCH jeep: slope' in out.getvalue() and 'willys-mb' in out.getvalue()
    finally:
        vc.read = saved
