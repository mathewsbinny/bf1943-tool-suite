"""NavKit (big-endian): the vehicle steps keep to one generation of work (no game data: the soldier steps are stood in
for as in test_navkit_stages.py, the sampler and the level's spawns too, in a temporary work folder). 2026-10-30:

  1. the vehicle collision was made again only when its file was missing, so sampling after a new preparation used
     the old one; the ground build and the vehicle driver used samples on disk whatever they were made from. Now
     collision, samples and worlds are stamped (vehicleclasses1943: preparation, game, class parameters, soldier
     world), the build refuses samples of another preparation or other class parameters, sampling makes the collision
     again, and the driver samples again;
  2. the builders read the level's spawns from the default game copy (game=None), not the one it was prepared from;
  3. the vehicle check passed with a requested world missing (it skipped it); now a missing world fails unless its
     builder stamped the class absent from the level, and a world of another preparation fails;
  4. a level without water (sea_level None) raised TypeError in all three builders: the ground and air builders take
     soldier1943.sea_level (under all the terrain), the landing craft has no world (stamped absent, with a note);
  5. 2026-10-30 review: the samples' stamp hashes only the parameters the sampler reads (a new speed sampled again);
     the ground builder built on no soldier world when <level>.rsnv was missing; a stale world not asked for failed the
     check (a missing one was a note); the landing craft on a level without water needed a current soldier export to
     be stamped absent; the audit's sea without water was -1e9, not soldier1943.sea_level; the soldier world is the
     default file by identity (a hard link of it too), and is the export of the current build by the .hpd's SHA-256
     (not the 'built' minute); stamp_export stamps only the default soldier world, whatever path names it.

  python -B test_vehicle_stages.py
"""
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_navkit_stages as stages  # noqa: E402  (its stand-ins and paths)
import navkit  # noqa: E402
import levelobjects1943  # noqa: E402
import groundnav1943 as gn  # noqa: E402
import seanav1943 as sn  # noqa: E402
import airnav1943 as an  # noqa: E402
import navexport1943  # noqa: E402
import navexport_vehicles1943 as nv  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import soldier1943  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943, level1943, rsnv, terrain  # noqa: E402

LV = stages.LV
failures = []
check = stages.check
stages.failures = failures


class Stop(Exception):
    """A step went on to read the level's spawns (the stand-in level loader stops it there)."""


def outcome(f, *a, **k):
    """'refused: ...' (StaleError), 'spawns of <game>' (it read the level), 'TypeError: ...', or 'done'."""
    try:
        f(*a, **k)
        return 'done'
    except vc.StaleError as e:
        return f'refused: {e}'
    except Stop as e:
        return f'spawns of {e.args[0]}'
    except TypeError as e:
        return f'TypeError: {e}'


def settings():
    return json.load(open(navkit.paths(LV)['settings'], encoding='utf-8'))


def edit_settings(change):
    st = settings()
    change(st)
    navkit.save_settings(LV, st)


def soldier_steps(game):
    """prepare, sample, build, check (stood in), and an export of the build stamped as the soldier world."""
    navkit.prepare(game, LV, log=quiet)
    navkit.sample(game, LV, log=quiet)
    navkit.build(game, LV, log=quiet)
    navkit.check(game, LV, log=quiet)
    path = os.path.join(navkit.paths(LV)['navkit'], f'{LV}.rsnv')
    open(path, 'wb').write(b'soldier world ' + game.encode())
    navexport1943.stamp_export(LV, settings(), path)
    return path


quiet = lambda *a: None                                                 # noqa: E731

if __name__ == '__main__':
    work = tempfile.mkdtemp(prefix='vehicle_stages_')
    os.environ['BF1943_WORK'] = work
    game, other = os.path.join(work, 'game'), os.path.join(work, 'other game')
    real = (game1943.Game, game1943.find_game, level1943.load, gn.subprocess, gn.prepare, gn.Grid, gn.spawn_seeds, gn.build,
            rsnv.load, vc.CLASSES['jeep']['radius'])
    try:
        stages.stand_ins()
        objects = levelobjects1943.main

        def objects_and_collision(argv):
            objects(argv)
            for name, n in (('all_tris.npy', 2), ('forbid_tris.npy', 0)):        # files numpy reads
                np.save(os.path.join(argv[3], name), np.zeros((n, 3, 3), np.float32))
            with open(os.path.join(argv[3], 'object_boxes.json'), 'w', encoding='utf-8', newline='\n') as f:
                f.write('[]')
            return 0
        levelobjects1943.main = objects_and_collision
        game1943.find_game = lambda given=None: given or 'the default copy'
        loaded = []

        def load(lv, game=None, log=None):
            loaded.append(game)
            raise Stop(game)
        level1943.load = load
        sampled, prepared = [], []

        def run(args, **k):
            sampled.append(args[6])
            open(args[6], 'wb').write(b'samples')
            return SimpleNamespace(returncode=0)
        gn.subprocess = SimpleNamespace(run=run, STDOUT=-2)

        def counting_prepare(lv, log=print):
            prepared.append(lv)
            return real[4](lv, log)
        gn.prepare = counting_prepare
        flat = gn.Grid.from_columns(0.8, 0.0, 0.0, 3, 3, [[(1.0, (0.0, 1.0, 0.0), 0)]] * 9, 1.5, 0.3, 2.0)
        gn.Grid = lambda path: flat

        # 1. stamps: collision, samples, the build's refusal, the driver's resampling
        soldier = soldier_steps(game)
        gn.sample(LV, 'jeep', log=quiet)
        st = settings()
        s = vc.stamp(st, 'samples', 'jeep') or {}
        check(len(prepared) == 1 and vc.stamp(st, 'collision', None) is not None and s.get('preparation') == st['preparation']
              and s.get('game') == game and s.get('params') == vc.sampling_params_id('jeep'),
              f'sampling makes the vehicle collision and stamps both: preparation, game, the sampler\'s parameters ({sorted(s)})')
        got = outcome(gn.build, LV, 'jeep', log=quiet)
        check(got == f'spawns of {game}', f'the build takes the current samples ({got[:80]})')
        soldier = soldier_steps(other)          # a new preparation (another copy): the files on disk stay
        got = outcome(gn.build, LV, 'jeep', log=quiet)
        check(got.startswith('refused') and os.path.isfile(gn.paths(LV)['samples']('jeep')),
              f'after a new preparation the build refuses the samples on disk ({got[:110]})')
        gn.sample(LV, 'jeep', log=quiet)
        st = settings()
        check(len(prepared) == 2 and (vc.stamp(st, 'collision') or {}).get('game') == other,
              f'sampling after a new preparation makes the vehicle collision again, though its file is there '
              f'({len(prepared)} preparations)')
        gn.spawn_seeds = lambda lv, cls, game=None: [{'class': cls}]
        built = []
        gn.build = lambda lv, cls, log=print, game=None: built.append((cls, game)) or (None, {})
        n = len(sampled)
        nv.build(LV, ['jeep'], log=quiet)
        check(len(sampled) == n and built[-1] == ('jeep', other), f'the driver keeps current samples ({len(sampled) - n} '
                                                                  f'samplings) and builds on the prepared copy ({built[-1]})')
        vc.CLASSES['jeep']['radius'] = (1.6, 'changed')
        nv.build(LV, ['jeep'], log=quiet)
        vc.CLASSES['jeep']['radius'] = real[9]
        check(len(sampled) == n + 1, f'the driver samples again when the class parameters changed ({len(sampled) - n} samplings)')
        nv.build(LV, ['jeep'], log=quiet)            # back to the parameters: sampled again
        n = len(sampled)
        speed = vc.CLASSES['jeep']['speed']
        vc.CLASSES['jeep']['speed'] = (99.0, 'changed')
        nv.build(LV, ['jeep'], log=quiet)
        vc.CLASSES['jeep']['speed'] = speed
        check(len(sampled) == n, f'a parameter the sampler does not read (speed) samples nothing again '
                                 f'({len(sampled) - n} samplings)')
        n = len(sampled)
        edit_settings(lambda st: st.setdefault('vehicles', {}).setdefault('samples', {}).setdefault('jeep', {}).update(
            preparation='0' * 64))
        nv.build(LV, ['jeep'], log=quiet)
        check(len(sampled) == n + 1, 'the driver samples again when the samples are of another preparation')
        gn.spawn_seeds, gn.build = real[6], real[7]

        # 2. the game copy: every builder reads the level from the one it was prepared from
        loaded.clear()
        results = {'groundnav1943.build': outcome(gn.build, LV, 'jeep', log=quiet),
                   'navexport_vehicles1943.build (jeep)': outcome(nv.build, LV, ['jeep'], log=quiet),
                   'airnav1943.build': outcome(an.build, LV, log=quiet)}
        edit_settings(lambda st: st.update(sea_level=-50.0))          # water, so the landing craft reads its spawns
        results['seanav1943.build'] = outcome(sn.build, LV, log=quiet)
        results['navexport_vehicles1943.build (lcvp)'] = outcome(nv.build, LV, ['lcvp'], log=quiet)
        edit_settings(lambda st: st.update(sea_level=None))
        for name, got in results.items():
            check(got == f'spawns of {other}', f'{name} reads the spawns of the prepared copy ({got[:100]})')
        got = outcome(gn.build, LV, 'jeep', log=quiet, game=game)
        check(got.startswith('refused') and 'prepared from' in got, f'a builder given another copy refuses ({got[:90]})')

        # 3. the check: a world missing, absent from the level, or of another preparation
        S = SimpleNamespace(islands=[], node_pos=np.zeros((0, 3)), node_island=np.zeros(0, np.int64))

        def rsnv_load(path):
            if os.path.normcase(path) == os.path.normcase(soldier):
                return S
            raise Stop(path)
        rsnv.load = rsnv_load
        rep = nv.check(LV, ['jeep'], log=quiet)
        check(any(p.startswith('jeep: no world') for p in rep['problems']),
              f'the jeep world asked for and missing is a problem ({rep["problems"]})')
        rep = nv.check(LV, ['jeep', 'tank'], log=quiet, required=['tank'])
        check(not any(p.startswith('jeep') for p in rep['problems']) and 'jeep: not built' in rep.get('notes', []),
              'a world not asked for and missing is a note')
        st = settings()
        jeep = nv.world_path(LV, 'jeep')
        open(jeep, 'wb').write(b'an old jeep world')
        vc.absent(LV, 'jeep', vc.inputs(st, LV, 'jeep'), jeep, 'no jeep spawns on the level', quiet)
        rep = nv.check(LV, ['jeep'], log=quiet)
        check(not rep['problems'] and 'absent' in rep['worlds'].get('jeep', {}) and not os.path.isfile(jeep),
              f'a class the level has none of is no problem, and its old world goes ({rep["worlds"]})')
        open(jeep, 'wb').write(b'a jeep world')
        # stamped by hand: record() refuses a world on a soldier world other than the current export (second review)
        edit_settings(lambda s: s.setdefault('vehicles', {}).setdefault('worlds', {}).update(
            jeep=dict(vc.inputs(st, LV, 'jeep'), soldier='0' * 64, time='t')))
        rep = nv.check(LV, ['jeep'], log=quiet)
        check(any('not built from the current' in p for p in rep['problems']),
              f'a world on another soldier world is a problem, not checked as current ({rep["problems"]})')
        rep = nv.check(LV, ['jeep', 'tank'], log=quiet, required=['tank'])
        check(not any(p.startswith('jeep') for p in rep['problems'])
              and any(m.startswith('jeep') and 'not built from the current' in m for m in rep.get('notes', [])),
              f'a stale world not asked for is a note, as a missing one ({rep["problems"]})')
        rsnv.load = real[8]

        # 4. no water: the ground and air builders go on, the landing craft has no world
        st = settings()
        hf = terrain.read(os.path.join(navkit.paths(LV)['objects'], 'heightfield.res'))
        check(st['sea_level'] is None and soldier1943.sea_level(st, hf) == float(hf.heights.min()) - soldier1943.NO_WATER_BELOW,
              f'no water: the sea lies {soldier1943.NO_WATER_BELOW:g} m under the lowest terrain '
              f'({soldier1943.sea_level(st, hf)})')
        import navaudit1943_checks
        C = {'water': None, 'st': st, 'hf': hf}
        got = outcome(navaudit1943_checks.sea_level, C)
        check(got == 'done' and navaudit1943_checks.sea_level(C) == soldier1943.sea_level(st, hf),
              f'the audit\'s sea level on a level without water: the exporters\' ({got})')
        for name, f in (('groundnav1943.build', lambda: gn.build(LV, 'jeep', log=quiet)),
                        ('airnav1943.build', lambda: an.build(LV, log=quiet))):
            got = outcome(f)
            check(got == f'spawns of {other}', f'{name} on a level without water gets as far as the spawns ({got[:90]})')
        lcvp = nv.world_path(LV, 'lcvp')
        open(lcvp, 'wb').write(b'an old lcvp world')
        edit_settings(lambda st: st.pop('exported'))          # no current soldier export: not needed to say 'no water'
        notes = []
        got = sn.build(LV, log=notes.append)
        rep = (vc.stamp(settings(), 'worlds', 'lcvp') or {})
        check(got[0] is None and 'no water' in (rep.get('absent') or '') and not os.path.isfile(lcvp)
              and any('no LCVP world' in m for m in notes),
              f'seanav1943 on a level without water: no world, a note, stamped absent, with no soldier export ({got[1]})')

        # 5. the soldier world: required by the ground builder, the default file by identity, the current build by hash
        soldier = soldier_steps(other)
        gn.sample(LV, 'jeep', log=quiet)
        moved = soldier + '.away'
        os.replace(soldier, moved)
        try:
            gn.build(LV, 'jeep', log=quiet)
            got = 'went on'
        except FileNotFoundError as e:
            got = f'refused: {e}'
        except Stop:
            got = 'went on to the spawns'
        os.replace(moved, soldier)
        check(got.startswith('refused') and 'no soldier world' in got,
              f'groundnav1943.build without <level>.rsnv fails, as the air and sea builders ({got[:90]})')
        alias = os.path.join(work, 'alias.rsnv')
        os.link(soldier, alias)                        # the default soldier world under another name (a hard link)
        st = settings()
        check(nj.same_file(alias, soldier) and vc.soldier_world(st, LV, alias) == nj.sha256(soldier),
              'a hard link of the current export is accepted')
        edit_settings(lambda st: st['exported'].update(hpd_sha256='0' * 64))   # the export of another build, same minute
        st = settings()
        got = [outcome(vc.soldier_world, st, LV, p) for p in (soldier, alias)]
        check(all(g.startswith('refused') and 'not the export of the current build' in g for g in got),
              f'the export of another build (another .hpd, the same \'built\' minute) is refused, by either name ({got})')
        edit_settings(lambda st: st.pop('exported'))
        stamped = navexport1943.stamp_export(LV, settings(), alias)
        check(stamped and (settings().get('exported') or {}).get('sha256') == nj.sha256(soldier),
              'stamp_export stamps the default soldier world named by another path (navexport1943 --out)')
        os.remove(alias)
        copy = os.path.join(work, 'copy.rsnv')
        open(copy, 'wb').write(b'another soldier world')
        edit_settings(lambda st: st.pop('exported'))
        stamped = navexport1943.stamp_export(LV, settings(), copy)
        check(not stamped and 'exported' not in settings(), 'stamp_export does not stamp another file as the soldier world')
    finally:
        (game1943.Game, game1943.find_game, level1943.load, gn.subprocess, gn.prepare, gn.Grid, gn.spawn_seeds, gn.build,
         rsnv.load, vc.CLASSES['jeep']['radius']) = real
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
