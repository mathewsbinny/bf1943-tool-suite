"""NavKit (big-endian): the second 2026-10-30 review's findings stay fixed (no game data: the soldier steps are stood
in for as in test_navkit_stages.py, in a temporary work folder).

  1. navkit.json.lock is an OS lock (msvcrt.locking / fcntl.flock) on a file left in place: a lock file left from
     earlier, even read-only or old, does not block; another process's held lock is never taken over, whatever its
     file's age (the age rule removed a live lock: two holders at once); a killed holder's lock frees itself; a lock
     file that cannot be opened fails in words after LOCK_WAIT (the stale branch spun forever without its deadline);
  2. update() of a level whose navkit folder is missing is NotPrepared (a raw FileNotFoundError on the lock file);
     remove() of one is nothing;
  3. a vehicle world finished after the soldier was built again is not stamped (record() checked only the
     preparation), and the vehicle check reports a soldier world that is not the current export;
  4. the build stamps its jump links' SHA-256 (build_from 'jump_links_sha256'); the export and the audit refuse a
     jump_links.json changed since, or a build without the stamp; jumplinks1943.py with another --game refuses to
     write the build's jump_links.json;
  5. the export of a build whose check failed goes to <level>.failed.rsnv when no --out is given (it replaced the
     soldier world <level>.rsnv).

  python -B test_navkit_review2.py
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
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
from bfbc2core import game1943  # noqa: E402

failures = []
LV = T.LV
q = lambda *a: None                                                          # noqa: E731


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def bounded(f, seconds):
    """('done', result) / ('error', exception) / ('hung', None) of f() run in a thread for at most seconds."""
    out = {}

    def go():
        try:
            out['r'] = ('done', f())
        except BaseException as e:                     # noqa: BLE001  (reported to the test)
            out['r'] = ('error', e)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    return out.get('r', ('hung', None))


HOLDER = '''
import sys, time
sys.dont_write_bytecode = True
sys.path[:0] = [%r, %r]
import navkitjson1943 as nj
with nj._Lock(sys.argv[1]):
    print('held', flush=True)
    time.sleep(60)
'''


def holder(work, lv):
    """A process holding lv's navkit.json lock (it prints 'held' once it has it)."""
    script = os.path.join(work, 'holder.py')
    with open(script, 'w', encoding='utf-8', newline='\n') as f:
        f.write(HOLDER % (os.path.join(KIT, 'tools'), os.path.join(os.path.dirname(KIT), 'core')))
    p = subprocess.Popen([sys.executable, '-B', script, lv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env=dict(os.environ))
    line = p.stdout.readline().strip()
    return p, line


def prepared(lv, st):
    p = nj.paths(lv)
    os.makedirs(p['navkit'], exist_ok=True)
    with open(p['settings'], 'w', encoding='utf-8', newline='\n') as f:
        json.dump(st, f)
    return p


def kill(p):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
    else:
        p.kill()
    p.wait(30)


def lock_tests(work):
    saved = nj.LOCK_WAIT
    old = time.time() - 3600
    try:
        # a read-only lock file left from earlier, an hour old
        lv = 'lock_ro'
        p = prepared(lv, {'level': lv})
        lock = p['settings'] + '.lock'
        with open(lock, 'w', encoding='utf-8', newline='\n') as f:
            f.write('pid 1\n')
        os.utime(lock, (old, old))
        os.chmod(lock, stat.S_IREAD)
        nj.LOCK_WAIT = 1.0
        got = bounded(lambda: nj.update(lv, lambda st: st.update(x=1)), 8)
        os.chmod(lock, stat.S_IREAD | stat.S_IWRITE)
        check(got[0] == 'done' and nj.read(lv).get('x') == 1,
              f'a read-only lock file left from earlier does not block an update ({got[0]}: {got[1]!r:.120})')

        # a lock that cannot be opened at all (a folder of its name), old: fails in words within LOCK_WAIT
        lv = 'lock_dir'
        p = prepared(lv, {'level': lv})
        lock = p['settings'] + '.lock'
        os.makedirs(lock)
        os.utime(lock, (old, old))
        nj.LOCK_WAIT = 0.5
        t0 = time.time()
        got = bounded(lambda: nj.update(lv, lambda st: st.update(x=1)), 8)
        check(got[0] == 'error' and isinstance(got[1], nj.Locked) and lock in str(got[1]) and time.time() - t0 < 5,
              f'a lock that cannot be opened fails in words after LOCK_WAIT, never spins ({got[0]}: {got[1]!r:.160})')
        os.rmdir(lock)

        # another process holds the lock: never taken over, however old its file looks; freed when it is killed
        lv = 'lock_held'
        p = prepared(lv, {'level': lv})
        lock = p['settings'] + '.lock'
        proc, line = holder(work, lv)
        try:
            os.utime(lock, (old, old))                      # a clock jump, a share's clock, a sleep: an old-looking file
            nj.LOCK_WAIT = 1.0
            t0 = time.time()
            got = bounded(lambda: nj.update(lv, lambda st: st.update(x=1)), 10)
            check(line == 'held' and got[0] == 'error' and isinstance(got[1], nj.Locked) and lock in str(got[1])
                  and 'x' not in nj.read(lv) and time.time() - t0 < 6,
                  f'a lock another process holds is not taken over, whatever its age: Locked in words, nothing written '
                  f'({line!r}; {got[0]}: {got[1]!r:.160})')
        finally:
            kill(proc)
        nj.LOCK_WAIT = 10.0
        t0 = time.time()
        got = bounded(lambda: nj.update(lv, lambda st: st.update(x=2)), 15)
        check(got[0] == 'done' and nj.read(lv).get('x') == 2 and time.time() - t0 < 10,
              f'the lock of a killed step frees itself ({got[0]}: {got[1]!r:.120}; {time.time() - t0:.1f} s)')
    finally:
        nj.LOCK_WAIT = saved


if __name__ == '__main__':
    work = tempfile.mkdtemp(prefix='navkit_review2_')
    os.environ['BF1943_WORK'] = work
    game = os.path.join(work, 'game')
    real_game, real_find = game1943.Game, game1943.find_game
    try:
        # 1. the lock
        lock_tests(work)

        # 2. a level without a navkit folder
        got = bounded(lambda: nj.update('no_level', lambda st: None), 5)
        check(got[0] == 'error' and isinstance(got[1], nj.NotPrepared),
              f'update() of a level without a navkit folder: NotPrepared ({got[1]!r:.140})')
        got = bounded(lambda: nj.remove('no_level'), 5)
        check(got[0] == 'done', f'remove() of a level without a navkit folder: nothing ({got[1]!r:.140})')

        # 3. a vehicle world finished after the soldier was built again
        import vehicleclasses1943 as vc
        import navexport_vehicles1943 as nv
        from bfbc2core import rsnv
        lv = 'world_race'
        p = prepared(lv, {'level': lv, 'preparation': 'P1', 'game': 'G', 'built': 't', 'checked': 't ok',
                          'build_from': {'preparation': 'P1', 'hpd_sha256': 'H1'}})
        soldier = os.path.join(p['navkit'], f'{lv}.rsnv')
        with open(soldier, 'wb') as f:
            f.write(b'export of build H1')
        s1 = nj.sha256(soldier)
        nj.update(lv, lambda st: st.update(exported={'preparation': 'P1', 'hpd_sha256': 'H1', 'sha256': s1}))
        st = nj.read(lv)
        want = vc.inputs(st, lv, 'jeep', soldier=vc.soldier_world(st, lv, soldier))     # the world build starts
        vc.drop(lv, 'worlds', 'jeep')

        def rebuild(s):                                     # navkit.py build and check meanwhile
            navkit.invalidate(s, 'built', 'checked')
            s.update(built='t2', checked='t2 ok', build_from={'preparation': 'P1', 'hpd_sha256': 'H2'})
        nj.update(lv, rebuild)
        try:
            vc.record(lv, 'worlds', want, 'jeep')
            got = 'stamped'
        except vc.StaleError as e:
            got = f'refused: {e}'
        check(got.startswith('refused') and 'jeep' not in ((nj.read(lv).get('vehicles') or {}).get('worlds') or {}),
              f'a world built on the export of a build since replaced is not stamped, and the caller is told ({got:.160})')
        real_load = rsnv.load
        rsnv.load = lambda path: SimpleNamespace(islands=[], node_pos=np.zeros((0, 3)), node_island=np.zeros(0, np.int64))
        try:
            rep = nv.check(lv, ['jeep'], log=q)
        finally:
            rsnv.load = real_load
        check(any('soldier world' in m and 'export again' in m for m in rep['problems']),
              f'the vehicle check reports a soldier world that is not the current export ({rep["problems"]})')

        # 4. the jump links the build used
        builds = T.stand_ins()
        navkit.prepare(game, LV, log=q)
        navkit.sample(game, LV, log=q)
        navkit.build(game, LV, log=q)
        navkit.check(game, LV, log=q)
        st = nj.read(LV)
        jp = os.path.join(navkit.paths(LV)['navkit'], 'jump_links.json')
        hpd = navkit.paths(LV)['hpd']
        check(st['build_from'].get('jump_links_sha256') == nj.sha256(jp),
              f'the build stamps its jump links\' SHA-256 ({st["build_from"].get("jump_links_sha256")})')
        check(nj.build_refusal(st, LV, hpd) is None, 'the checked build with its own jump links may be exported')
        original = open(jp, 'rb').read()
        with open(jp, 'w', encoding='utf-8', newline='\n') as f:
            f.write('[{"rule": "written later"}]')
        why = nj.build_refusal(nj.read(LV), LV, hpd)
        check(why is not None and 'jump_links.json' in why, f'a jump_links.json changed since the build is refused ({why})')
        try:
            navaudit1943.current_build(LV)
            why = None
        except nj.NavKitError as e:
            why = str(e)
        check(why is not None and 'jump_links.json' in why, f'... by the audit too ({why})')
        with open(jp, 'wb') as f:
            f.write(original)
        nj.update(LV, lambda s: s['build_from'].pop('jump_links_sha256', None))
        why = nj.build_refusal(nj.read(LV), LV, hpd)
        check(why is not None and 'jump' in why, f'a build stamped without its jump links (older NavKit) is refused ({why})')
        navkit.build(game, LV, log=q)
        navkit.check(game, LV, log=q)
        import jumplinks1943
        before = open(jp, 'rb').read()
        code = jumplinks1943.main(['jumplinks1943.py', LV, '--game', os.path.join(work, 'other game')])
        check(code == 1 and open(jp, 'rb').read() == before,
              f'jumplinks1943.py with another --game refuses to write the build\'s jump_links.json (exit {code})')
        elsewhere = os.path.join(work, 'links_other.json')
        code = jumplinks1943.main(['jumplinks1943.py', LV, '--game', os.path.join(work, 'other game'), '--json', elsewhere])
        check(code == 0 and os.path.isfile(elsewhere), f'... and writes them with --json elsewhere (exit {code})')
        code = jumplinks1943.main(['jumplinks1943.py', LV])
        check(code == 0 and nj.build_refusal(nj.read(LV), LV, hpd) is None,
              f'jumplinks1943.py on the prepared copy writes the same links: the build stays exportable (exit {code})')

        # 5. the export of a build whose check failed
        import navexport1943
        st = nj.read(LV)
        nd = navkit.paths(LV)['navkit']
        check(os.path.normcase(navexport1943.output_path(LV, st)) == os.path.normcase(os.path.join(nd, f'{LV}.rsnv')),
              'a checked build is exported to <level>.rsnv')
        nj.update(LV, lambda s: s.update(checked=s['checked'].replace(' ok', ' FAILED')))
        st = nj.read(LV)
        got = [navexport1943.output_path(LV, st), navexport1943.output_path(LV, st, big_endian=True),
               navexport1943.output_path(LV, st, out=os.path.join(work, 'mine.rsnv'))]
        check([os.path.basename(g) for g in got] == [f'{LV}.failed.rsnv', f'{LV}.be.failed.rsnv', 'mine.rsnv'],
              f'a build whose check failed is exported to <level>.failed.rsnv unless --out says where ({got})')
        soldier = os.path.join(nd, f'{LV}.rsnv')
        with open(soldier, 'wb') as f:
            f.write(b'the soldier world')
        real_export = navexport1943.export
        navexport1943.export = lambda lv, out=None, big_endian=False, log=print, compare=False: \
            (navexport1943.output_path(lv, nj.read(lv), out, big_endian), None)
        try:
            code = navexport1943.main(['navexport1943.py', LV])
        finally:
            navexport1943.export = real_export
        check(code == 1 and open(soldier, 'rb').read() == b'the soldier world',
              f'navexport1943.py on a failed build exits 1 and leaves <level>.rsnv alone ({code})')
    finally:
        game1943.Game, game1943.find_game = real_game, real_find
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
