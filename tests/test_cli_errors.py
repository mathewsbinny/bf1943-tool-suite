r"""Every command-line tool's error handling (no game data): each is run with input that must fail - a file that is
missing, or not the kind the tool reads - and must exit with an error code, tell the cause and what to do in
words, name its log, and record the error in that log (%LOCALAPPDATA%\bf1943-tool-suite\logs; a temporary folder
here). The BF1943 tools: the STFS reader, the spawn map, NavKit's BF1943 steps (level objects, the sampler, the
detailed sectors) and the test runner.

  python tests\test_cli_errors.py
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
PY = sys.executable


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    tmp = tempfile.mkdtemp(prefix='cli_errors_')
    env = {**os.environ, 'LOCALAPPDATA': tmp}
    logs = os.path.join(tmp, 'bf1943-tool-suite', 'logs')
    junk = os.path.join(tmp, 'junk.bin')
    open(junk, 'wb').write(b'this is not any kind of game file')
    missing = os.path.join(tmp, 'missing.file')
    cases = [
        ('STFS reader, a missing package', [PY, os.path.join(SUITE, 'bf1943-console-readers', 'stfs', 'stfs.py'), 'info', missing], 'stfs', 'missing'),
        ('spawn map, a missing game folder', [PY, os.path.join(SUITE, 'bf1943-spawn-map', 'spawnmap.py'), missing, 'wake_island_s'], 'spawnmap', 'missing'),
        ('level objects, a missing game folder', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tools', 'levelobjects1943.py'), missing, 'wake_island_s', tmp], 'levelobjects1943', 'missing'),
        ('sampler, a level folder without objects', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tools', 'navsample1943.py'), tmp, missing, junk + '.out'], 'navsample1943', 'missing'),
        ('detailed sectors, a level folder without objects', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tools', 'detailed1943.py'), tmp, missing, '0', '0'], 'detailed1943', 'missing'),
        ('test runner, an unknown test name', [PY, os.path.join(SUITE, 'run_tests.py'), 'quick', '--only'], 'run-tests', 'what to do'),
    ]
    try:
        for label, args, log_name, says in cases:
            r = subprocess.run(args, capture_output=True, text=True, errors='replace', env=env, timeout=300)
            out = r.stdout + r.stderr
            log = sorted(glob.glob(os.path.join(logs, f'{log_name}-*.log')))
            text = open(log[-1], encoding='utf-8', errors='replace').read() if log else ''
            ok = r.returncode != 0 and says in out.lower() and 'details' in out.lower() and 'ERROR' in text
            check(ok, f'{label}: exit {r.returncode}, the cause in words, the log ({os.path.basename(log[-1]) if log else "none"})')
            if not ok:
                print('       ' + out.strip().replace('\n', '\n       ')[:600])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
