"""map_project.ps1 judges Ghidra's post-script by its result, not only by java's exit code (no Ghidra: a stand-in java).

  python -B test_map_project.py

Ghidra's headless analyzer exits 0 when a post-script throws, and ValidateXex.java writes saved-validation.txt only on
a pass. Run against a scratch workspace with BF1943_JAVA naming a stand-in that exits 0 and does what each case says:
- writes nothing (the script threw): the step fails, and an old PASS left from an earlier run is gone;
- writes PASS (and a warning on stderr): the step passes;
- exits 1: the step fails.
Windows only (PowerShell); skips elsewhere.
"""
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
FAKE = r'''import os, sys
args = sys.argv[1:]
mode = os.environ['FAKE_JAVA_MODE']
root = args[args.index('-postScript') + 2]
sys.stderr.write('WARNING: a JVM warning on stderr\n')
if mode == 'pass':
    open(os.path.join(root, 'saved-validation.txt'), 'w').write('PASS functions=1 made up\n')
sys.exit(1 if mode == 'exit1' else 0)
'''


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def main():
    if os.name != 'nt':
        print('skip map_project.ps1: Windows PowerShell only')
        return
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, 'work', 'xex-analysis'))
        os.makedirs(os.path.join(root, 'research'))
        fake = os.path.join(root, 'fake_java.py')
        open(fake, 'w', encoding='utf-8').write(FAKE)
        cmd = os.path.join(root, 'java.cmd')
        open(cmd, 'w', encoding='ascii').write(f'@"{sys.executable}" -B "{fake}" %*\r\n')
        saved = os.path.join(root, 'work', 'xex-analysis', 'saved-validation.txt')

        def run(mode):
            env = dict(os.environ, BF1943_RESEARCH=root, BF1943_JAVA=cmd, FAKE_JAVA_MODE=mode)
            return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                   os.path.join(TOOLS, 'map_project.ps1'), '-ValidateOnly'], env=env, cwd=root,
                                  capture_output=True, text=True)
        open(saved, 'w').write('PASS functions=33232 from an earlier run\n')
        r = run('silent')
        check(r.returncode != 0 and not os.path.exists(saved),
              'ValidateXex.java threw (nothing written, java exit 0): the step fails and the earlier PASS is gone'
              + ('' if r.returncode else ': it passed'))
        r = run('pass')
        check(r.returncode == 0 and open(saved).read().startswith('PASS'),
              'a pass written this run (and a warning on stderr) passes' + ('' if r.returncode == 0 else ': ' + r.stderr[-300:]))
        r = run('exit1')
        check(r.returncode != 0, 'java exiting 1 fails the step')


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
