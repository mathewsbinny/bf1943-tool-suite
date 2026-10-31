r"""tools\import_opfor.py --update on made-up trees only: a temporary git repository stands in for OPFOR's suite and
a temporary folder for this suite (the module's SOURCE_SUITE, SUITE and MANIFEST are pointed at them; the real
suites are never read or written). An updated single-file import (run_tests.py, and a renamed one) replaces that
file, not <file>\<file>; a folder import updates the changed file in the folder; a file changed here too is left
as a conflict. The command line on runs that cannot finish (partial_runs): checked before anything is written, and
what a run stopped part-way wrote is recorded (marked incomplete).

  python -B tests\test_import_opfor.py
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SUITE, 'tools'))
import import_opfor  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, '-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                           '-c', 'commit.gpgsign=false', *args], capture_output=True, check=True).stdout


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def record(rel, data):
    return {'path': rel, 'bytes': len(data), 'git_blob': import_opfor.blob_id(data), 'sha256': hashlib.sha256(data).hexdigest()}


def partial_runs(tmp, src):
    """The tool's command line (a copy of it in a made-up suite, so its SUITE and MANIFEST are there) on runs that
    cannot finish: a plan whose later item has no source, and an --update naming a destination never imported, write
    nothing; an update stopped part-way records what it wrote, marked incomplete (2026-10-30: such files were left
    without a record, and the next update took them for local edits)."""
    import importlib.util
    import json
    suite = os.path.join(tmp, 'cli-suite')
    os.makedirs(os.path.join(suite, 'tools'))
    os.makedirs(os.path.join(suite, 'provenance'))
    tool = os.path.join(suite, 'tools', 'import_opfor.py')
    shutil.copy(os.path.join(SUITE, 'tools', 'import_opfor.py'), tool)
    manifest = os.path.join(suite, 'provenance', 'opfor-imports.json')
    with open(manifest, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'imports': []}, f)
    env = {**os.environ, 'BFBC2_TOOL_SUITE': src}

    def run(*args):
        r = subprocess.run([sys.executable, '-B', tool, *args], capture_output=True, text=True, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()

    def imports():
        with open(manifest, encoding='utf-8') as f:
            return json.load(f)['imports']
    plan = os.path.join(tmp, 'plan.json')
    with open(plan, 'w', encoding='utf-8', newline='\n') as f:
        json.dump([{'source': 'tool', 'destination': 'copied-tool'}, {'source': 'no-such-folder', 'destination': 'other'}], f)
    code, out = run('--plan', plan)
    check(code != 0 and not os.path.exists(os.path.join(suite, 'copied-tool')) and imports() == [],
          f'a plan whose later item has no source writes nothing (exit {code}: {out[-70:]})')
    code, out = run('tool', 'copied-tool')
    check(code == 0 and len(imports()) == 1, f'an import is copied and recorded (exit {code})')
    write(os.path.join(src, 'tool', 'a.py'), b'third a\n')
    git(src, 'commit', '-q', '-am', 'third')
    code, out = run('--update', 'copied-tool', 'never-imported')
    with open(os.path.join(suite, 'copied-tool', 'a.py'), 'rb') as f:
        kept = f.read()
    check(code != 0 and kept == b'new a\n' and len(imports()) == 1,
          f'an --update naming a destination never imported writes nothing (exit {code}, a.py {kept!r})')
    code, out = run('--update', 'copied-tool')
    last = imports()[-1]['items'][0]
    check(code == 0 and [f['path'] for f in last['files']] == ['a.py'] and not last['conflicts'],
          f'the update then brings the file and records it, no conflict (exit {code}: {out[-70:]})')
    # stopped part-way: the second blob read back wrong, after the first file was written
    write(os.path.join(src, 'tool', 'a.py'), b'fourth a\n')
    write(os.path.join(src, 'tool', 'c.py'), b'new c\n')
    git(src, 'add', '-A')
    git(src, 'commit', '-q', '-m', 'fourth')
    spec = importlib.util.spec_from_file_location('import_opfor_copy', tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SOURCE_SUITE = src
    real_git, reads = mod.git, []

    def failing_git(*args):
        out_ = real_git(*args)
        if args[:2] == ('cat-file', 'blob'):
            reads.append(args)
            if len(reads) == 2:
                return out_ + b'damaged'
        return out_
    mod.git = failing_git
    saved_argv, sys.argv = sys.argv, ['import_opfor.py', '--update', 'copied-tool']
    try:
        mod.main()
        stopped = False
    except SystemExit:
        stopped = True
    finally:
        sys.argv = saved_argv
    last = imports()[-1]
    check(stopped and bool(last.get('incomplete')) and [f['path'] for f in last['items'][0]['files']] == ['a.py'],
          f'an update stopped part-way records the file it wrote, marked incomplete ({last.get("incomplete")!r})')


def second_review(tmp):
    """2026-10-30 second review, on a copy of the tool in its own made-up suite and a made-up source: a write that
    fails part-way (a full disk, made up by failing the write of one file's bytes, whichever way the tool opens it)
    leaves no cut file; a failure on the first file still records the import (its folder exists), and --update then
    finishes it with no conflict; a plan naming a folder and a folder inside it writes nothing; an update whose write
    fails leaves the old file whole."""
    import builtins
    import importlib.util
    import json
    src = os.path.join(tmp, 'sr-source')
    os.makedirs(os.path.join(src, 'tool'))
    git(src, 'init', '-q')
    data = {'a.py': b'a' * 300, 'b.py': b'b' * 300}
    for n, d in data.items():
        write(os.path.join(src, 'tool', n), d)
    git(src, 'add', '-A')
    git(src, 'commit', '-q', '-m', 'one')
    env = {**os.environ, 'BFBC2_TOOL_SUITE': src}

    def suite_copy(name):
        suite = os.path.join(tmp, name)
        os.makedirs(os.path.join(suite, 'tools'))
        os.makedirs(os.path.join(suite, 'provenance'))
        tool = os.path.join(suite, 'tools', 'import_opfor.py')
        shutil.copy(os.path.join(SUITE, 'tools', 'import_opfor.py'), tool)
        with open(os.path.join(suite, 'provenance', 'opfor-imports.json'), 'w', encoding='utf-8', newline='\n') as f:
            json.dump({'imports': []}, f)
        return suite, tool

    def imports(suite):
        with open(os.path.join(suite, 'provenance', 'opfor-imports.json'), encoding='utf-8') as f:
            return json.load(f)['imports']

    def cli(tool, *args):
        r = subprocess.run([sys.executable, '-B', tool, *args], capture_output=True, text=True, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()

    def in_process(tool, argv, fail_bytes):
        """main() with the write of fail_bytes failing after 10 bytes (through open or os.fdopen alike)."""
        spec = importlib.util.spec_from_file_location('import_opfor_sr', tool)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.SOURCE_SUITE = src
        real_open, real_fdopen = builtins.open, os.fdopen

        def wrap(f):
            class Failing:
                def __enter__(self):
                    return self

                def __exit__(self, *e):
                    f.close()
                    return False

                def write(self, b):
                    if bytes(b) == fail_bytes:
                        f.write(b[:10])
                        raise OSError(28, 'made-up full disk')
                    return f.write(b)

                def __getattr__(self, k):
                    return getattr(f, k)
            return Failing()

        def fake_open(file, mode='r', *a, **k):
            f = real_open(file, mode, *a, **k)
            return wrap(f) if ('w' in mode or 'x' in mode) and 'b' in mode else f

        def fake_fdopen(fd, mode='r', *a, **k):
            f = real_fdopen(fd, mode, *a, **k)
            return wrap(f) if 'w' in mode and 'b' in mode else f
        mod.open = fake_open
        saved_argv, sys.argv = sys.argv, ['import_opfor.py', *argv]
        os.fdopen = fake_fdopen
        try:
            mod.main()
            return None
        except BaseException as e:
            return e
        finally:
            os.fdopen = real_fdopen
            sys.argv = saved_argv

    for which in ('a.py', 'b.py'):
        suite, tool = suite_copy(f'sr-suite-{which[0]}')
        err = in_process(tool, ['tool', 'copied'], data[which])
        dst = os.path.join(suite, 'copied')
        cut = [n for n in (os.listdir(dst) if os.path.isdir(dst) else []) if open(os.path.join(dst, n), 'rb').read()
               != data.get(n)]
        rec = imports(suite)
        check(err is not None and not cut and rec and rec[-1].get('incomplete'),
              f'an import whose {which} write fails leaves no cut file and is recorded as incomplete '
              f'(cut: {cut}, entries: {len(rec)})')
        code, out = cli(tool, '--update', 'copied')
        got = {n: open(os.path.join(dst, n), 'rb').read() for n in os.listdir(dst)} if os.path.isdir(dst) else {}
        check(code == 0 and got == data and 'conflict' not in out,
              f'--update then finishes the import whose {which} write failed, no conflict (exit {code}: {out[-80:]})')

    suite, tool = suite_copy('sr-suite-nested')
    plan = os.path.join(tmp, 'sr-plan.json')
    with open(plan, 'w', encoding='utf-8', newline='\n') as f:
        json.dump([{'source': 'tool', 'destination': 'x'}, {'source': 'tool', 'destination': 'x/inner'}], f)
    code, out = cli(tool, '--plan', plan)
    check(code != 0 and not os.path.exists(os.path.join(suite, 'x')) and imports(suite) == [],
          f'a plan naming a folder and one inside it writes nothing (exit {code}: {out[-80:]})')

    suite, tool = suite_copy('sr-suite-update')
    code, out = cli(tool, 'tool', 'copied')
    write(os.path.join(src, 'tool', 'a.py'), b'A' * 400)
    git(src, 'commit', '-q', '-am', 'two')
    err = in_process(tool, ['--update', 'copied'], b'A' * 400)
    kept = open(os.path.join(suite, 'copied', 'a.py'), 'rb').read()
    left = [n for n in os.listdir(os.path.join(suite, 'copied')) if n.endswith('.tmp')]
    check(code == 0 and err is not None and kept == data['a.py'] and not left,
          f'an update whose write fails leaves the old file whole, no temporary file ({len(kept)} bytes, {left})')


def main():
    tmp = tempfile.mkdtemp(prefix='import_opfor_')
    saved = import_opfor.SOURCE_SUITE, import_opfor.SUITE, import_opfor.MANIFEST
    try:
        src, dst = os.path.join(tmp, 'source-suite'), os.path.join(tmp, 'this-suite')
        os.makedirs(src)
        os.makedirs(dst)
        git(src, 'init', '-q')
        old = {'run_tests.py': b'old runner\n', 'Old Suite.pyw': b'old hub\n', 'tool/a.py': b'old a\n', 'tool/b.py': b'old b\n'}
        for rel, data in old.items():
            write(os.path.join(src, *rel.split('/')), data)
        git(src, 'add', '-A')
        git(src, 'commit', '-q', '-m', 'first')
        first = git(src, 'rev-parse', 'HEAD').decode().strip()
        # this suite as an import would have left it
        write(os.path.join(dst, 'run_tests.py'), old['run_tests.py'])
        write(os.path.join(dst, 'New Suite.pyw'), old['Old Suite.pyw'])
        write(os.path.join(dst, 'tool', 'a.py'), old['tool/a.py'])
        write(os.path.join(dst, 'tool', 'b.py'), b'changed here\n')
        manifest = {'imports': [{'commit': first, 'items': [
            {'source': 'run_tests.py', 'destination': 'run_tests.py', 'files': [record('run_tests.py', old['run_tests.py'])]},
            {'source': 'Old Suite.pyw', 'destination': 'New Suite.pyw', 'files': [record('Old Suite.pyw', old['Old Suite.pyw'])]},
            {'source': 'tool', 'destination': 'tool', 'files': [record('a.py', old['tool/a.py']), record('b.py', old['tool/b.py'])]}]}]}
        new = {'run_tests.py': b'new runner\n', 'Old Suite.pyw': b'new hub\n', 'tool/a.py': b'new a\n', 'tool/b.py': b'new b\n'}
        for rel, data in new.items():
            write(os.path.join(src, *rel.split('/')), data)
        git(src, 'commit', '-q', '-am', 'second')
        second = git(src, 'rev-parse', 'HEAD').decode().strip()

        import_opfor.SOURCE_SUITE, import_opfor.SUITE = src, dst
        import_opfor.MANIFEST = os.path.join(dst, 'provenance', 'opfor-imports.json')
        for destination, rel, label in (('run_tests.py', 'run_tests.py', 'a single-file import'),
                                        ('New Suite.pyw', 'Old Suite.pyw', 'a renamed single-file import')):
            try:
                item = import_opfor.update_one(second, manifest, destination)
                got = open(os.path.join(dst, destination), 'rb').read() if os.path.isfile(os.path.join(dst, destination)) else None
                ok = got == new[rel] and [f['path'] for f in item['files']] == [rel] and not item['conflicts']
                why = f'{destination} holds {got!r}'
            except (Exception, SystemExit) as e:
                ok, why = False, f'{type(e).__name__}: {e}'
            check(ok, f'update of {label} replaces the file itself ({why})')
        item = import_opfor.update_one(second, manifest, 'tool')
        check(open(os.path.join(dst, 'tool', 'a.py'), 'rb').read() == new['tool/a.py'] and item['conflicts'] == ['b.py']
              and open(os.path.join(dst, 'tool', 'b.py'), 'rb').read() == b'changed here\n',
              f'update of a folder import: the file changed only there is replaced, the one changed here too is a conflict')
        check(sorted(os.listdir(dst)) == ['New Suite.pyw', 'run_tests.py', 'tool'], f'nothing else written ({sorted(os.listdir(dst))})')
        partial_runs(tmp, src)
        second_review(tmp)
    finally:
        import_opfor.SOURCE_SUITE, import_opfor.SUITE, import_opfor.MANIFEST = saved
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
