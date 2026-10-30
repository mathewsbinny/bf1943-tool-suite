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
  5. navaudit1943_checks.load and navexport1943.export ask for the prepared game copy.

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
runs = []


def stand_ins():
    def objects(argv):
        out = argv[3]
        os.makedirs(out, exist_ok=True)
        for name, data in (('ladders.json', b'[]'), ('soldier_spawns.json', b'[]'), ('forbid_tris.npy', argv[1].encode())):
            open(os.path.join(out, name), 'wb').write(data)
        return 0

    def run_parallel(jobs, log=print):
        for label, argv, _log in jobs:
            runs.append(label)
            open(next(a for a in argv if a.endswith('.bin')), 'wb').write(b'samples')

    def build(samples, *a, detailed=None, **k):
        builds.append(detailed)
        open(a[3], 'wb').write(b'hpd')
    builds = []
    levelobjects1943.main = objects
    game1943.Game = lambda game, level: SimpleNamespace(data=lambda n: HEIGHTFIELD if 'heightfield' in n else None)
    navkit.run_parallel = run_parallel
    detailed1943.choose = lambda o, samples, xoff, zoff: ([(1, 2)], {'stairs': 1})
    jumplinks1943.level_links = lambda lv, log, game=None: []
    navbuild.build = build
    hpdcheck.check = lambda hpd: {}
    routecheck1943.check = lambda hpd, lv, game: {'ok': True}
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
        check(set(s.get('objects_sha256', ())) == {'heightfield.res', 'ladders.json', 'soldier_spawns.json', 'forbid_tris.npy'},
              f'the preparation hashes every objects file ({sorted(s.get("objects_sha256", ()))})')
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
        # 5. the audit and the export use the game copy the level was prepared from
        st['game'] = other
        navkit.save_settings(LV, st)
        open(navkit.paths(LV)['hpd'], 'wb').write(b'hpd')

        class Asked(Exception):
            pass

        def find_game(given=None):
            raise Asked(given)
        game1943.find_game = find_game
        import navaudit1943_checks
        import navexport1943
        for name, f in (('navaudit1943_checks.load', lambda: navaudit1943_checks.load(LV, log=quiet)),
                        ('navexport1943.export', lambda: navexport1943.export(LV, log=quiet))):
            try:
                f()
                asked = 'not asked'
            except Asked as e:
                asked = e.args[0]
            check(asked == other, f'{name} asks for the prepared game copy ({asked})')
    finally:
        game1943.Game, game1943.find_game = real_game, real_find
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
