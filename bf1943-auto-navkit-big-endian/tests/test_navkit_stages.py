"""NavKit (big-endian): navkit.py's steps keep to one generation of work (no game data: the level's objects, the
samplers, the builder and the checks are stood in for, in a temporary work folder). 2026-10-30: a build went ahead
on the samples of an earlier preparation, without a sampling stamp, and with another game copy; --pitch 0.4 ran no
sampler and still stamped the level sampled; resampling kept the old build's and check's stamps; the navigation
audit and the RSNV export read the default game copy, not the one the level was prepared from.

  1. prepare, sample, build, check: each step's stamp, and the provenance it records (the preparation's digest and
     game folder in the sampling and build stamps);
  2. prepare again: the later stamps are gone, and build refuses the samples on disk (their stamp is another
     preparation's); a step with another --game than the preparation's is refused;
  3. --pitch 0.4: refused before anything runs, no stamp; a new 0.8 m pass drops the 0.2 m pass, the build and the
     check, and the next build makes the detailed sectors at 0.8 m (the 0.2 m files on disk are the old pass's);
  4. check refuses a build of another preparation; a level prepared by an older NavKit (no digest) must be prepared
     again;
  5. navaudit1943_checks.load and navexport1943.export ask for the prepared game copy;
  6. navexport1943.export refuses the .hpd left on disk after a new preparation or sampling, and a build not checked
     (2026-10-30: it looked only for the file); a new sampling drops the export's and the vehicle worlds' stamps;
  7. 2026-10-30 review: the build runs jumplinks1943.level_links itself (only the game-data readers are stood in: it
     raised TypeError on a level without water, which a stand-in for level_links hid); sampling, the build and the
     check keep what the vehicle steps stamped while they ran (they wrote back the copy read at their start), and drop
     again what they invalidate; a build is told apart by its .hpd's SHA-256 (a quick rebuild has the same 'built'
     minute: the export of the build before it was stamped as this one's); check refuses an .hpd not the stamped one;
  8. build's guard on the 0.8 m pass's stamp (another preparation, another game copy) and stamp()'s guard on a level
     prepared again while a step ran (2's refusals come from a missing stamp, so neither was reached).

  python -B test_navkit_stages.py
"""
import json
import os
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path[:0] = [KIT, os.path.join(KIT, 'tools')]
import navkit  # noqa: E402
import navbuild  # noqa: E402  (this variant's, before core navdata puts the little-endian tools first)
import detailed1943  # noqa: E402
import hpdcheck  # noqa: E402
import jumplinks1943  # noqa: E402
import levelobjects1943  # noqa: E402
import navkitjson1943  # noqa: E402
import navsample  # noqa: E402
import spawnmap  # noqa: E402  (on the path from jumplinks1943)
import routecheck1943  # noqa: E402
from bfbc2core import game1943  # noqa: E402

failures = []
LV = 'made_up'
STAGES = ('prepared', 'sampled', 'built', 'checked')


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def refused(f, *a, **k):
    """The NavKitError's message, or None when the step went ahead."""
    try:
        f(*a, **k)
    except navkit.NavKitError as e:
        return str(e)
    return None


HEIGHTFIELD = struct.pack('>6f6IB', -1100.0, 0.0, -1100.0, 1100.0, 100.0, 1100.0, 4, 0, 0, 0, 0, 0, 0) + bytes(32)
# a navsample file of one column and no surfaces (what jumplinks1943 and the audit read with navaudit1943.load_samples)
NO_SAMPLES = navsample.HEADER.pack(b'ONS1', 1, 0.8, 0.0, 0.0, 1, 1, 1.0, -1.0, 0.35, 0.37, 1.8, 0.0, 0) + bytes(1)
OBJECT_FILES = ('ladders.json', 'soldier_spawns.json', 'objects.json', 'group_instances.json', 'object_boxes.json')
runs = []
any_game = lambda given=None: given or 'the default copy'               # noqa: E731  (game1943.find_game: no folder)
during = {}            # step -> a function the stood-in sampler / builder / route check calls while the step runs


def stand_ins():
    def objects(argv):
        out = argv[3]
        os.makedirs(out, exist_ok=True)
        for name in OBJECT_FILES:
            open(os.path.join(out, name), 'wb').write(b'[]')
        open(os.path.join(out, 'forbid_tris.npy'), 'wb').write(argv[1].encode())
        return 0

    def run_parallel(jobs, log=print):
        for label, argv, _log in jobs:
            runs.append(label)
            open(next(a for a in argv if a.endswith('.bin')), 'wb').write(NO_SAMPLES)
        during.get('sample', lambda: None)()

    def build(samples, *a, detailed=None, **k):
        builds.append(detailed)
        open(a[3], 'wb').write(b'hpd %d' % len(builds))          # each build its own file: told apart by its hash
        during.get('build', lambda: None)()

    def routes(hpd, lv, game):
        during.get('check', lambda: None)()
        return {'ok': True}
    builds = []
    levelobjects1943.main = objects
    game1943.Game = lambda game, level: SimpleNamespace(data=lambda n: HEIGHTFIELD if 'heightfield' in n else None)
    game1943.find_game = any_game
    navkit.run_parallel = run_parallel
    detailed1943.choose = lambda o, samples, xoff, zoff: ([(1, 2)], {'stairs': 1})
    # jumplinks1943.level_links runs: only the game's spawn map (read from the bundles) is stood in
    spawnmap.spawn_map = lambda game, lv: {'objectives': [], 'unowned_vehicle_spawns': []}
    navbuild.build = build
    hpdcheck.check = lambda hpd: {}
    routecheck1943.check = routes
    routecheck1943.report = lambda r: 'routes: ok'
    return builds


def stamps():
    s = json.load(open(navkit.paths(LV)['settings'], encoding='utf-8'))
    return [k for k in STAGES if k in s], s


if __name__ == '__main__':
    work = tempfile.mkdtemp(prefix='navkit_stages_')
    os.environ['BF1943_WORK'] = work
    quiet = lambda *a: None                                                 # noqa: E731
    game, other = os.path.join(work, 'game'), os.path.join(work, 'other game')
    real_game, real_find = game1943.Game, game1943.find_game
    try:
        builds = stand_ins()
        # 1. the four steps, and what each records
        navkit.prepare(game, LV, log=quiet)
        navkit.sample(game, LV, log=quiet)
        navkit.build(game, LV, log=quiet)
        navkit.check(game, LV, log=quiet)
        done, s = stamps()
        prep = s.get('preparation')
        passes = s.get('samples', {})
        check(done == list(STAGES) and prep and passes.get('0.8', {}).get('preparation') == prep
              and passes.get('0.2', {}).get('preparation') == prep and passes['0.8']['game'] == game
              and s['build_from']['preparation'] == prep and builds[-1] is not None,
              f'prepare, sample, build, check: stamps {done}; the samples and the build carry the preparation and game')
        check(set(s.get('objects_sha256', ())) == {'heightfield.res', 'forbid_tris.npy', *OBJECT_FILES},
              f'the preparation hashes every objects file ({sorted(s.get("objects_sha256", ()))})')
        check(s.get('sea_level') is None and os.path.isfile(os.path.join(navkit.paths(LV)['navkit'], 'jump_links.json')),
              'a level without water (sea_level None) builds: jumplinks1943.level_links takes soldier1943.sea_level')
        check(s['build_from'].get('hpd_sha256') == navkitjson1943.sha256(navkit.paths(LV)['hpd']),
              'the build stamp names its .hpd by SHA-256')
        # 2. a new preparation: the samples on disk are refused
        navkit.prepare(game, LV, log=quiet)
        done, s = stamps()
        check(done == ['prepared'], f'prepare again: only its own stamp is left ({done})')
        why = refused(navkit.build, game, LV, log=quiet)
        check(why is not None and os.path.isfile(os.path.join(navkit.paths(LV)['samples'], 'samples.bin')),
              f'build refuses the earlier preparation\'s samples on disk ({why})')
        why = refused(navkit.sample, other, LV, log=quiet)
        check(why is not None and 'prepared from' in why and not runs[4:], f'sampling with another game copy is refused ({why})')
        navkit.prepare(other, LV, log=quiet)
        prep_other = stamps()[1].get('preparation')
        check(prep_other is not None and prep_other != prep,'a preparation from another game copy has another digest')
        navkit.sample(other, LV, log=quiet)
        why = refused(navkit.build, game, LV, log=quiet)
        check(why is not None and 'prepared from' in why, f'build with another game copy than the preparation\'s is refused ({why})')
        # 3. pitches
        navkit.build(other, LV, log=quiet)
        navkit.check(other, LV, log=quiet)
        before, n = stamps(), len(runs)
        why = refused(navkit.sample, other, LV, pitch=0.4, log=quiet)
        check(why is not None and len(runs) == n and stamps() == before,
              f'--pitch 0.4 is refused before anything runs, the stamps unchanged ({why})')
        navkit.sample(other, LV, pitch=0.8, log=quiet)
        done, s = stamps()
        check(done == ['prepared', 'sampled'] and '0.2' not in s['samples'] and 'build_from' not in s,
              f'a new 0.8 m pass drops the 0.2 m pass, the build and the check ({done}, passes {sorted(s["samples"])})')
        why = refused(navkit.check, other, LV, log=quiet)
        check(why is not None and 'not built' in why, f'check refuses: the build is not of these samples ({why})')
        navkit.build(other, LV, log=quiet)
        check(builds[-1] is None and os.path.isfile(os.path.join(navkit.paths(LV)['samples'], 'samples_02.bin')),
              'the next build makes the detailed sectors at 0.8 m: the 0.2 m files on disk are the old pass\'s')
        # 4. a build of another preparation; a level prepared by an older NavKit
        st = stamps()[1]
        st['build_from']['preparation'] = '0' * 64
        navkit.save_settings(LV, st)
        why = refused(navkit.check, other, LV, log=quiet)
        check(why is not None and 'another preparation' in why, f'check refuses a build of another preparation ({why})')
        for k in ('preparation', 'objects_sha256'):
            st.pop(k)
        navkit.save_settings(LV, st)
        why = refused(navkit.sample, other, LV, log=quiet)
        check(why is not None and 'prepare again' in why, f'a level prepared without the stamps: prepare again ({why})')
        # 5. the audit and the export use the game copy the level was prepared from (a checked build of it)
        navkit.prepare(other, LV, log=quiet)
        navkit.sample(other, LV, log=quiet)
        navkit.build(other, LV, log=quiet)
        navkit.check(other, LV, log=quiet)

        class Asked(Exception):
            pass

        def find_game(given=None):
            raise Asked(given)
        import navaudit1943_checks
        import navexport1943

        def export_reaches():
            """What navexport1943.export did: the game copy it asked for (it went on to load the build), or its refusal."""
            game1943.find_game = find_game
            try:
                navexport1943.export(LV, log=quiet)
                return 'not asked'
            except Asked as e:
                return e.args[0]
            except navexport1943.NotCurrent as e:
                return f'refused: {e}'
            finally:
                game1943.find_game = any_game
        game1943.find_game = find_game
        for name, f in (('navaudit1943_checks.load', lambda: navaudit1943_checks.load(LV, log=quiet)),
                        ('navexport1943.export', lambda: navexport1943.export(LV, log=quiet)),
                        ('navexport1943.prepared_terrain (test_navexport1943\'s other levels)',
                         lambda: navexport1943.prepared_terrain(LV, stamps()[1]))):
            try:
                f()
                asked = 'not asked'
            except Asked as e:
                asked = e.args[0]
            check(asked == other, f'{name} asks for the prepared game copy ({asked})')
        game1943.find_game = any_game
        # 6. the export takes only the checked build of the current preparation (2026-10-30: it looked only for the
        # .hpd, which a new preparation or sampling leaves on disk, and put the old graph with the current terrain)
        hpd = navkit.paths(LV)['hpd']
        navkit.prepare(other, LV, log=quiet)
        got = export_reaches()
        check(got.startswith('refused') and os.path.isfile(hpd), f'prepare again: the export refuses the old .hpd ({got})')
        navkit.sample(other, LV, log=quiet)
        navkit.build(other, LV, log=quiet)
        navkit.check(other, LV, log=quiet)
        st = stamps()[1]
        st['exported'] = {'built': st['built']}
        st['vehicles'] = {'collision': {'preparation': st['preparation']}, 'samples': {'jeep': {}}, 'worlds': {'jeep': {}}}
        navkit.save_settings(LV, st)
        navkit.sample(other, LV, pitch=0.8, log=quiet)
        got = export_reaches()
        st = stamps()[1]
        check(got.startswith('refused') and os.path.isfile(hpd), f'sample again: the export refuses the old .hpd ({got})')
        check('exported' not in st and 'worlds' not in st['vehicles'] and 'samples' in st['vehicles'],
              f'sample again: the export\'s and the vehicle worlds\' stamps go, the vehicle samples\' stay '
              f'({sorted(st.get("vehicles", {}))})')
        navkit.build(other, LV, log=quiet)
        got = export_reaches()
        check(got.startswith('refused') and 'not checked' in got, f'a build not checked yet is refused ({got})')
        navkit.check(other, LV, log=quiet)
        got = export_reaches()
        check(got == other, f'the checked build of this preparation is exported ({got})')

        # 7. the long steps keep the vehicle steps' stamps written while they ran, and drop again what they invalidate
        import vehicleclasses1943 as vc
        navkitjson1943.update(LV, lambda st: st.pop('vehicles', None))     # each vehicle stamp below is written meanwhile

        def vehicle_stamps(*kinds):
            st = stamps()[1]
            return {k: sorted(vc.stamp(st, k) or {}) if k != 'collision' else vc.stamp(st, k) is not None for k in kinds}

        def meanwhile(kind, cls=None, also=None):
            def f():
                st = navkitjson1943.read(LV)
                vc.record(LV, kind, vc.inputs(st, LV, cls, sampling=kind == 'samples'), cls)
                if also:
                    navkitjson1943.update(LV, also)
            return f

        def exported_meanwhile(st):
            st['exported'] = {'preparation': st['preparation'], 'hpd_sha256': 'of the build before'}
            st.setdefault('vehicles', {}).setdefault('worlds', {})['jeep'] = {'preparation': st['preparation']}
        passes = [meanwhile('collision'), meanwhile('samples', 'plane')]       # one stamp during each pass
        during['sample'] = lambda: passes.pop(0)()
        navkit.sample(other, LV, log=quiet)
        got = vehicle_stamps('collision', 'samples')
        check(got == {'collision': True, 'samples': ['plane']},
              f'sample keeps the vehicle stamps written while its 0.8 m and 0.2 m passes ran ({got})')
        during.clear()
        during['build'] = meanwhile('samples', 'jeep', exported_meanwhile)
        navkit.build(other, LV, log=quiet)
        st = stamps()[1]
        got = vehicle_stamps('collision', 'samples', 'worlds')
        check(got == {'collision': True, 'samples': ['jeep', 'plane'], 'worlds': []} and 'exported' not in st and 'built' in st,
              f'build keeps the vehicle samples stamped while it ran, and drops the export and worlds stamped meanwhile ({got})')
        during.clear()
        during['check'] = meanwhile('samples', 'tank')
        navkit.check(other, LV, log=quiet)
        got = vehicle_stamps('samples')
        check(got == {'samples': ['jeep', 'plane', 'tank']} and 'checked' in stamps()[1],
              f'check keeps the vehicle samples stamped while it ran ({got})')
        during.clear()
        during['build'] = lambda: navkit.prepare(other, LV, log=quiet)
        why = refused(navkit.build, other, LV, log=quiet)
        done, _ = stamps()
        # (the same objects give the same preparation digest: refused for its samples, which the preparation dropped)
        check(why is not None and 'not stamped' in why and done == ['prepared'],
              f'a build whose level was prepared again while it ran is not stamped ({why})')
        during.clear()

        # a quick rebuild (the same 'built' minute): the export of the build before it is not stamped as this one's
        navkit.sample(other, LV, log=quiet)
        navkit.build(other, LV, log=quiet)
        navkit.check(other, LV, log=quiet)
        before = stamps()[1]                       # what an export started now reads
        navkit.build(other, LV, log=quiet)
        rsnv_path = os.path.join(navkit.paths(LV)['navkit'], f'{LV}.rsnv')
        open(rsnv_path, 'wb').write(b'the export of the build before')
        stamped = navexport1943.stamp_export(LV, before, rsnv_path)
        st = stamps()[1]
        check(not stamped and 'exported' not in st and st['build_from']['hpd_sha256'] != before['build_from']['hpd_sha256'],
              f'an export of the build before a rebuild is not stamped as the new build\'s (built {before["built"]} / '
              f'{st["built"]})')
        open(navkit.paths(LV)['hpd'], 'wb').write(b'another hpd')
        why = refused(navkit.check, other, LV, log=quiet)
        check(why is not None and 'not the build stamped' in why, f'check refuses an .hpd that is not the stamped build ({why})')

        # 8. 2026-10-30 review: the refusals in 2 come from a missing stamp (prepare drops them), so build's own guard on
        # the samples' stamp, and stamp()'s on a preparation made while a step ran, were never reached
        navkit.sample(other, LV, log=quiet)
        for key, value in (('preparation', '0' * 64), ('game', game)):
            good = stamps()[1]['samples']['0.8']
            navkitjson1943.update(LV, lambda st: st['samples']['0.8'].update({key: value}))
            why = refused(navkit.build, other, LV, log=quiet)
            check(why is not None and 'another preparation' in why,
                  f'build refuses a 0.8 m pass stamped with another {key} ({why})')
            navkitjson1943.update(LV, lambda st: st['samples'].__setitem__('0.8', good))
        during['build'] = lambda: navkit.prepare(game, LV, log=quiet)      # another copy: another digest
        why = refused(navkit.build, other, LV, log=quiet)
        during.clear()
        check(why is not None and 'prepared again while build ran' in why and stamps()[0] == ['prepared'],
              f'a build whose level was prepared again (another digest) while it ran is not stamped ({why})')
    finally:
        game1943.Game, game1943.find_game = real_game, real_find
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
