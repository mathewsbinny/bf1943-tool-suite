"""run_xenia_probe.ps1 on a made-up kit build and a stand-in Xenia (no Xenia, no game).

  python -B test_run_xenia_probe.py

The kit build (title folder, manifests, patch file) is made in a folder whose path holds a space, and the session
folder (BF1943_WORK) too; the stand-in "Xenia" (a .cmd) records the arguments it was given. Checked:
- each flag reaches Xenia as one argument, its path whole (Start-Process quotes nothing: before 2026-10-30 a space
  split --log_file and the other paths in two);
- -StopAfterSeconds stops a Xenia that keeps running and still writes manifest.json with the exit code.
Windows only (PowerShell); skips elsewhere.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(os.path.dirname(HERE), 'run_xenia_probe.ps1')
failures = []
RECORDER = r'''import json, os, sys, time
open(os.environ['FAKE_XENIA_OUT'], 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))
if os.environ.get('FAKE_XENIA_SLEEP'):
    time.sleep(float(os.environ['FAKE_XENIA_SLEEP']))
    open(os.environ['FAKE_XENIA_OUT'] + '.done', 'w').write('done')
'''


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def main():
    if os.name != 'nt':
        print('skip run_xenia_probe.ps1: Windows PowerShell only')
        return
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, 'kit build')
        title, xenia = os.path.join(base, 'title'), os.path.join(base, 'xenia')
        os.makedirs(title)
        os.makedirs(os.path.join(xenia, 'patches'))
        json.dump(dict(files=[]), open(os.path.join(title, 'title-manifest.json'), 'w', encoding='utf-8'))
        open(os.path.join(title, 'default.xex'), 'wb').write(b'XEX2 made up')
        recorder = os.path.join(base, 'recorder.py')
        open(recorder, 'w', encoding='utf-8').write(RECORDER)
        fake = os.path.join(xenia, 'xenia_canary_netfix.cmd')
        open(fake, 'w', encoding='ascii').write(f'@"{sys.executable}" -B "{recorder}" %*\r\n')
        patch = os.path.join(xenia, 'patches', 'kit.patch.toml')
        open(patch, 'w', encoding='utf-8').write('# made up\n')
        json.dump(dict(netfix=fake, netfix_sha256=sha(fake), patch=patch, patch_sha256=sha(patch), title=title, xenia=xenia,
                       built='made up', bots_patch=None, bots_patch_sha256=None),
                  open(os.path.join(base, 'kit-manifest.json'), 'w', encoding='utf-8'))
        work = os.path.join(tmp, 'work dir')
        out = os.path.join(tmp, 'argv.json')

        def run(session, *extra, sleep=None):
            env = dict(os.environ, BF1943_WORK=work, FAKE_XENIA_OUT=out)
            env.pop('FAKE_XENIA_SLEEP', None)
            if sleep:
                env['FAKE_XENIA_SLEEP'] = str(sleep)
            return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PROBE, '-Session',
                                   session, '-KitOut', base, *extra], env=env, capture_output=True, text=True)
        r = run('spaces', '-FunctionTrace')
        check(r.returncode == 0, 'a session on the made-up build completes' + ('' if r.returncode == 0 else ': ' + r.stderr[-400:]))
        if r.returncode == 0:
            argv = json.load(open(out, encoding='utf-8'))
            session = os.path.join(work, 'xenia-probes', os.listdir(os.path.join(work, 'xenia-probes'))[0])
            log = [a for a in argv if a.startswith('--log_file=')]
            check(len(log) == 1 and os.path.normcase(os.path.normpath(log[0].split('=', 1)[1]))
                  == os.path.normcase(os.path.join(session, 'xenia.log')),
                  f'--log_file reaches Xenia whole, a space in its path ({log})')
            paths = [a for a in argv if a.split('=', 1)[0] in ('--trace_gpu_prefix', '--dump_shaders', '--trace_function_data_path')]
            check(len(paths) == 3 and all(' ' in a for a in paths)
                  and os.path.normcase(os.path.normpath(argv[-1])) == os.path.normcase(os.path.join(title, 'default.xex')),
                  f'the other path flags and the title arrive whole ({len(argv)} arguments)')
        r = run('stopped', '-StopAfterSeconds', '1', sleep=4)
        sessions = sorted(os.listdir(os.path.join(work, 'xenia-probes')))
        stopped = [s for s in sessions if s.endswith('-stopped')]
        manifest = os.path.join(work, 'xenia-probes', stopped[0], 'manifest.json') if stopped else ''
        ok = r.returncode == 0 and os.path.isfile(manifest)
        m = json.load(open(manifest, encoding='utf-8-sig')) if ok else {}
        check(ok and m.get('stopped_by_probe') is True and 'exit_code' in m,
              '-StopAfterSeconds stops a running Xenia and writes manifest.json with its exit code'
              + ('' if ok else ': ' + r.stderr[-400:]))
        # the stand-in's own child (python, under the killed cmd.exe) finishes its sleep; wait so the folder can go
        for _ in range(100):
            if os.path.exists(out + '.done'):
                break
            time.sleep(0.1)
        time.sleep(0.3)


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
