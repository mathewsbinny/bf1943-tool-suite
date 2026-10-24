"""The executable tools' research workspace lookup (no game data): workspace.py and workspace.ps1 accept a folder with
work\\ and research\\ (from BF1943_RESEARCH, or the current folder) and refuse one without, saying what to do.

  python -B test_workspace.py
"""
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import workspace  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def ps(cwd, env):
    return subprocess.run(['powershell', '-NoProfile', '-Command', f"& '{os.path.join(HERE, 'workspace.ps1')}'"], cwd=cwd, env=env,
                          capture_output=True, text=True)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as good, tempfile.TemporaryDirectory() as bad:
        for d in ('work', 'research'):
            os.makedirs(os.path.join(good, d))
        env = {k: v for k, v in os.environ.items() if k != 'BF1943_RESEARCH'}
        old = dict(os.environ)
        os.environ.pop('BF1943_RESEARCH', None)
        cwd = os.getcwd()
        try:
            os.chdir(good)
            check(os.path.samefile(workspace.research_root(), good), 'workspace.py: the current folder when it has work\\ and research\\')
            os.chdir(bad)
            try:
                workspace.research_root()
                check(False, 'workspace.py refuses a folder without work\\ and research\\')
            except SystemExit as e:
                check('BF1943_RESEARCH' in str(e), 'workspace.py refuses a folder without them, naming BF1943_RESEARCH')
            os.environ['BF1943_RESEARCH'] = good
            check(os.path.samefile(workspace.research_root(), good), 'workspace.py: BF1943_RESEARCH wins over the current folder')
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(old)
        if os.name == 'nt':
            r = ps(good, env)
            check(r.returncode == 0 and os.path.samefile(r.stdout.strip(), good), 'workspace.ps1: the current folder')
            r = ps(bad, env)
            check(r.returncode != 0 and 'BF1943_RESEARCH' in (r.stdout + r.stderr), 'workspace.ps1 refuses a folder without them')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
