"""NavKit (big-endian): the 2026-10-30 review's findings stay fixed (no game data: the level's objects, the samplers,
the builder and the checks are stood in for as in test_navkit_stages.py, in a temporary work folder).

  1. the build's jump links read samples_02.bin only when the build uses that 0.2 m pass (a new 0.8 m pass left the
     old one on disk, and level_links read it);
  2. navkit.json: two processes updating it at once lose nothing and never leave it unreadable (lock file, atomic
     replace); a damaged or missing file is a NavKitError in words; a held lock fails in words after its wait (the
     lock itself: test_navkit_review2.py); a step whose navkit.json went while it ran (prepare) refuses in words;
  3. prepare empties objects\\ (a water_mesh.res of an earlier preparation was hashed into this one and read by the
     build of a level without water);
  4. the audit takes only the checked build of the current preparation, and makes again a mask of other samples or
     another build (it kept any file there);
  5. a build is not stamped when a 0.2 m pass was made while it ran; each pass has an id of its own;
  6. navkit.py's arguments: --pitch=0.2, a trailing --pitch, unknown arguments, --pitch outside sample, help;
  7. routecheck1943.py's arguments: options before the positionals;
  lower: an export of a build whose check failed is not stamped and exits 1, and no vehicle world is built on it;
  the sampler's and the plane world's box without land above the sea.

  python -B test_navkit_review.py
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import test_navkit_stages as T  # noqa: E402  (its stand-ins; it puts the kit and its tools on sys.path)
import navkit  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import navaudit1943  # noqa: E402
import navbuild  # noqa: E402
from bfbc2core import game1943, terrain  # noqa: E402

failures = []
LV = T.LV
q = lambda *a: None                                                          # noqa: E731


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def refused(f, *a, **k):
    try:
        f(*a, **k)
    except nj.NavKitError as e:
        return str(e)
    return None


def steps(game, pitch=None):
    navkit.prepare(game, LV, log=q)
    navkit.sample(game, LV, pitch=pitch, log=q)
    navkit.build(game, LV, log=q)
    navkit.check(game, LV, log=q)


CHILD = '''
import sys
sys.dont_write_bytecode = True
sys.path[:0] = [%r, %r]
import navkitjson1943 as nj
who, n = sys.argv[1], int(sys.argv[2])
for i in range(n):
    nj.update('lv', lambda st: st.setdefault(who, []).append(i))
print('done')
'''


def concurrency(work):
    """Two processes, N read-modify-writes each, at once."""
    n = 150
    env = dict(os.environ, BF1943_WORK=os.path.join(work, 'concurrent'))
    os.makedirs(os.path.join(env['BF1943_WORK'], 'levels', 'lv', 'navkit'))
    with open(os.path.join(env['BF1943_WORK'], 'levels', 'lv', 'navkit', 'navkit.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write('{"pad": "%s"}' % ('x' * 200000))       # a big file: a write takes long enough to be met
    script = os.path.join(work, 'child.py')
    with open(script, 'w', encoding='utf-8', newline='\n') as f:
        f.write(CHILD % (os.path.join(KIT, 'tools'), os.path.join(os.path.dirname(KIT), 'core')))
    ps = [subprocess.Popen([sys.executable, '-B', script, w, str(n)], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True) for w in ('a', 'b')]
    outs = [p.communicate(timeout=300)[0].strip() for p in ps]
    try:
        with open(os.path.join(env['BF1943_WORK'], 'levels', 'lv', 'navkit', 'navkit.json'), encoding='utf-8') as f:
            st = json.load(f)
        got = {w: len(st.get(w, [])) for w in ('a', 'b')}
    except ValueError as e:
        got = f'unreadable: {e}'
    # navkit.json.lock stays in place (an OS lock on it: second review); no temporary file is left
    left = [n_ for n_ in os.listdir(os.path.join(env['BF1943_WORK'], 'levels', 'lv', 'navkit'))
            if n_ not in ('navkit.json', 'navkit.json.lock')]
    check(all(o.endswith('done') for o in outs) and got == {'a': n, 'b': n} and not left,
          f'two processes updating navkit.json {n} times each at once: every update kept, no error, no '
          f'temporary file left ({got}; {[o[-120:] for o in outs]}; left {left})')


if __name__ == '__main__':
    work = tempfile.mkdtemp(prefix='navkit_review_')
    os.environ['BF1943_WORK'] = work
    game = os.path.join(work, 'game')
    real_game, real_find = game1943.Game, game1943.find_game
    try:
        builds = T.stand_ins()

        # 1. jump links on the samples the build uses
        steps(game)
        read = []
        real_load = navaudit1943.load_samples
        navaudit1943.load_samples = lambda path: read.append(os.path.basename(path)) or real_load(path)
        navkit.build(game, LV, log=q)
        check(builds[-1] is not None and 'samples_02.bin' in read,
              f'a build on a current 0.2 m pass reads its jump links on it too ({read})')
        navkit.sample(game, LV, pitch=0.8, log=q)                # the 0.2 m stamp goes, samples_02.bin stays
        read.clear()
        navkit.build(game, LV, log=q)
        check(builds[-1] is None and 'samples_02.bin' not in read and 'samples.bin' in read,
              f'a build without a current 0.2 m pass reads its jump links on the 0.8 m samples only ({read})')
        navaudit1943.load_samples = real_load

        # 2. navkit.json
        concurrency(work)
        settings = navkit.paths(LV)['settings']
        with open(settings, 'w', encoding='utf-8', newline='\n') as f:
            f.write('{"level": "made_up", "samp')                # half a file
        why = refused(navkit.sample, game, LV, log=q)
        check(why is not None and 'damaged' in why, f'a damaged navkit.json is refused in words ({why})')
        check('damaged' in navkit.stamps_of(LV).get('unreadable', ''), 'status and levels show it as damaged')
        os.remove(settings)
        why = refused(navkit.build, game, LV, log=q)
        check(why is not None and 'not prepared' in why, f'a missing navkit.json is refused in words ({why})')
        steps(game)
        lock = settings + '.lock'
        held = nj._Lock(LV)                                      # held through another handle (test_navkit_review2.py:
        held.__enter__()                                         # by another process, a killed one, an old file)
        saved_wait = nj.LOCK_WAIT
        nj.LOCK_WAIT = 0.3
        t0 = time.time()
        try:
            why = refused(nj.update, LV, lambda st: st.update(x=1))
        finally:
            nj.LOCK_WAIT = saved_wait
            held.__exit__(None, None, None)
        check(why is not None and 'locked' in why and time.time() - t0 < 5 and 'x' not in nj.read(LV),
              f'a held lock: the update waits, then fails in words, writing nothing ({why})')
        nj.update(LV, lambda st: st.update(x=1))
        check(nj.read(LV).get('x') == 1 and os.path.isfile(lock), 'once released, the lock is taken again; its file stays')
        st = nj.read(LV)
        nj.remove(LV)                                            # prepare has started meanwhile
        why = refused(navkit.stamp, LV, st['preparation'], 'build', lambda s: None)
        check(why is not None and 'went while build ran' in why, f'a step whose navkit.json went while it ran ({why})')

        # 3. prepare empties objects\
        mesh = struct.pack('<II9fI3I', 1, 3, 0, 5, 0, 1, 5, 0, 0, 5, 1, 3, 0, 1, 2)
        game1943.Game = lambda g, l: SimpleNamespace(data=lambda n: T.HEIGHTFIELD if 'heightfield' in n else
                                                     (mesh if 'water' in n else None))
        s = navkit.prepare(game, LV, log=q)
        wet = s['sea_level']
        game1943.Game = lambda g, l: SimpleNamespace(data=lambda n: T.HEIGHTFIELD if 'heightfield' in n else None)
        open(os.path.join(navkit.paths(LV)['objects'], 'left_over.npy'), 'wb').close()
        s = navkit.prepare(game, LV, log=q)
        objs = navkit.paths(LV)['objects']
        check(wet == 5.0 and s['sea_level'] is None and not os.path.isfile(os.path.join(objs, 'water_mesh.res'))
              and 'water_mesh.res' not in s['objects_sha256'] and 'left_over.npy' not in s['objects_sha256'],
              f'a preparation without water leaves no water_mesh.res of the one before, nor any other old file '
              f'({sorted(s["objects_sha256"])})')
        waters = []
        stand_in_build = navbuild.build
        navbuild.build = lambda *a, water=None, **k: waters.append(water) or stand_in_build(*a, water=water, **k)
        open(os.path.join(objs, 'water_mesh.res'), 'wb').write(mesh)     # put back by hand: still not this level's
        navkit.sample(game, LV, log=q)
        navkit.build(game, LV, log=q)
        navbuild.build = stand_in_build
        check(waters == [None], 'the build of a level prepared without water reads no water mesh')

        # 4. the audit
        why = refused(navaudit1943.current_build, LV)
        check(why is not None and 'not checked' in why, f'the audit refuses a build not checked ({why})')
        navkit.check(game, LV, log=q)
        kept = []
        real_keep = navaudit1943.stage_keep
        navaudit1943.stage_keep = lambda lv, which, v='base', **k: kept.append((which, v)) or \
            (SimpleNamespace(y=np.zeros(4)), np.ones(4, bool))
        navaudit1943.masks(LV, ['base', 'lips'], ['normal', 'detailed'], log=q)
        first = list(kept)
        kept.clear()
        navaudit1943.masks(LV, ['base', 'lips'], ['normal', 'detailed'], log=q)
        check(len(first) == 4 and kept == [], f'masks of these samples and this build are kept ({first}, then {kept})')
        navkit.build(game, LV, log=q)                             # another .hpd (the stand-in numbers its builds)
        navkit.check(game, LV, log=q)
        navaudit1943.masks(LV, ['base', 'lips'], ['normal'], log=q)
        check(kept == [('normal', 'base'), ('normal', 'lips')],
              f'masks of another build are made again ({kept})')
        navkit.sample(game, LV, pitch=0.8, log=q)                 # no current 0.2 m pass: the build is 0.8 m only
        navkit.build(game, LV, log=q)
        navkit.check(game, LV, log=q)
        kept.clear()
        navaudit1943.masks(LV, ['base'], ['normal', 'detailed'], log=q)
        check(kept == [('normal', 'base')], f'no detailed masks for a build without a current 0.2 m pass ({kept})')
        mp = os.path.join(navaudit1943.level_paths(LV)['audit'], 'masks', 'normal_base.npz')
        hashes = navaudit1943.Hashes(LV)
        check(navaudit1943.mask_stale(mp, 'samples.bin', hashes) is None, 'a mask of these samples is current')
        with open(os.path.join(navkit.paths(LV)['samples'], 'samples.bin'), 'ab') as f:
            f.write(b'\0')
        why = navaudit1943.mask_stale(mp, 'samples.bin', navaudit1943.Hashes(LV))
        check(why is not None and 'other samples' in why, f'a mask of other samples is not ({why})')
        navaudit1943.stage_keep = real_keep

        # 5. a 0.2 m pass made while the build ran; the passes' ids
        steps(game)
        st = nj.read(LV)
        ids = (st['samples']['0.8'].get('id'), st['samples']['0.2'].get('id'))
        check(all(ids) and ids[0] != ids[1] and st['build_from'].get('samples_08_id') == ids[0]
              and st['build_from'].get('samples_02_id') == ids[1],
              f'each sampling pass has an id, and the build names the ones it used ({ids})')
        T.during['build'] = lambda: navkit.sample(game, LV, pitch=0.2, log=q)
        why = refused(navkit.build, game, LV, log=q)
        T.during.clear()
        check(why is not None and 'sampled again' in why and 'built' not in nj.read(LV),
              f'a build during which the 0.2 m pass was made again is not stamped ({why})')
        navkit.sample(game, LV, pitch=0.8, log=q)
        T.during['build'] = lambda: navkit.sample(game, LV, pitch=0.2, log=q)
        navkit.build(game, LV, log=q)                             # no 0.2 m pass when it started: built at 0.8 m
        T.during.clear()
        st = nj.read(LV)
        check('built' in st and st['build_from']['samples_02_id'] is None,
              'a build made at 0.8 m is stamped as such, whatever 0.2 m pass came meanwhile')

        # 6. navkit.py's arguments
        got = navkit.parse(['navkit.py', 'sample', 'wake_island_s', '--pitch=0.2'])
        check(got == ('sample', 'wake_island_s', None, 0.2), f'--pitch=0.2 is the 0.2 m pass ({got})')
        got = navkit.parse(['navkit.py', '--game', 'D:/x', 'sample', 'wake_island_s', '--pitch', '0.8'])
        check(got == ('sample', 'wake_island_s', 'D:/x', 0.8), f'options before the command ({got})')
        for argv, says in ((['sample', 'wake_island_s', '--pitch'], 'expected one argument'),
                           (['sample', 'wake_island_s', '--pitch', '0.4'], 'not a sampling pass'),
                           (['build', 'wake_island_s', '--pitch', '0.2'], 'option of sample'),
                           (['build', 'wake_island_s', 'extra'], 'unrecognized'),
                           (['build', 'wake_island_s', '--bogus'], 'unrecognized'),
                           (['frobnicate', 'wake_island_s'], 'invalid choice'),
                           (['build'], 'which level')):
            why = refused(navkit.parse, ['navkit.py'] + argv)
            check(why is not None and says in why, f'navkit.py {" ".join(argv)}: refused in words ({why})')
        for argv in ([], ['-h'], ['--help'], ['help'], ['sample', 'x', '--help']):
            check(navkit.parse(['navkit.py'] + argv) is None, f'navkit.py {" ".join(argv)}: the help')
        r = subprocess.run([sys.executable, '-B', os.path.join(KIT, 'navkit.py'), 'sample', 'x', '--pitch'],
                           capture_output=True, text=True, cwd=work, env=dict(os.environ, BF1943_GAME=work))
        check(r.returncode == 1 and 'error:' in r.stdout and 'Traceback' not in r.stdout + r.stderr,
              f'navkit.py with a trailing --pitch: exit 1, the reason in words ({r.returncode}: {r.stdout.strip()[:120]})')

        # 7. routecheck1943.py's arguments
        import routecheck1943
        a = routecheck1943.parse(['routecheck1943.py', '--game', 'D:/bf1943/dist/xenon', '--json', 'o.json', 'wake.hpd',
                                  'wake_island_s'])
        check((a.hpd, a.level, a.game, a.json) == ('wake.hpd', 'wake_island_s', 'D:/bf1943/dist/xenon', 'o.json'),
              f'routecheck1943.py: options before the build and level ({vars(a)})')
        check(routecheck1943.parse(['routecheck1943.py', '-h']) is None, 'routecheck1943.py -h: the help')

        # lower: an export of a build whose check failed
        import navexport1943
        import vehicleclasses1943 as vc
        steps(game)
        nj.update(LV, lambda st: st.update(checked=st['checked'].replace(' ok', ' FAILED')))
        rsnv_path = os.path.join(navkit.paths(LV)['navkit'], f'{LV}.rsnv')
        with open(rsnv_path, 'wb') as f:
            f.write(b'an export of a failed build')
        st = nj.read(LV)
        check(not navexport1943.stamp_export(LV, st, rsnv_path) and 'exported' not in nj.read(LV),
              'the export of a build whose check failed is not stamped as the soldier world')
        why = None
        try:
            vc.soldier_world(nj.read(LV), LV, rsnv_path)
        except vc.StaleError as e:
            why = str(e)
        check(why is not None and 'did not pass' in why, f'no vehicle world on a build whose check failed ({why})')
        real_export = navexport1943.export
        navexport1943.export = lambda lv, out=None, big_endian=False, log=print, compare=False: (rsnv_path, None)
        try:
            code = navexport1943.main(['navexport1943.py', LV])
        finally:
            navexport1943.export = real_export
        check(code == 1, f'navexport1943.py exits 1 after exporting a build whose check failed ({code})')

        # lower: the boxes without land above the sea
        import navsample1943
        import airnav1943
        hf = terrain.read(T.HEIGHTFIELD)
        d = os.path.join(work, 'box')
        os.makedirs(d)
        for name in ('soldier_spawns.json', 'object_boxes.json'):
            with open(os.path.join(d, name), 'w', encoding='utf-8', newline='\n') as f:
                f.write('[]')
        try:
            box = navsample1943.box_from_level(d, hf, 1000.0, log=q)
            got = 'None' if box is None else 'a box'
        except ValueError as e:
            got = f'ValueError {e}'
        check(got == 'None', f'the sampler\'s land box with no terrain above the sea: none, no error ({got})')
        lo, hi = navsample1943.extent_box(np.zeros((0, 3, 3)), hf)
        check(bool((lo <= hi).all()), f'the extent box without collision: the terrain ({lo}, {hi})')
        lo, hi = airnav1943.flight_box(hf, 1000.0, [[10.0, 0.0, 20.0]])
        check(np.allclose(lo, [10 - airnav1943.MARGIN, 20 - airnav1943.MARGIN]),
              f'the plane world\'s box with no land above the sea: around the points ({lo}, {hi})')
        try:
            airnav1943.flight_box(hf, 1000.0, [])
            got = 'no error'
        except ValueError as e:
            got = str(e)
        check('no plane world' in got, f'... and with no point either: an error in words ({got})')
    finally:
        game1943.Game, game1943.find_game = real_game, real_find
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
