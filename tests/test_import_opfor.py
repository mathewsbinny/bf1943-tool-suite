r"""tools\import_opfor.py --update on made-up trees only: a temporary git repository stands in for OPFOR's suite and
a temporary folder for this suite (the module's SOURCE_SUITE, SUITE and MANIFEST are pointed at them; the real
suites are never read or written). An updated single-file import (run_tests.py, and a renamed one) replaces that
file, not <file>\<file>; a folder import updates the changed file in the folder; a file changed here too is left
as a conflict.

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
    finally:
        import_opfor.SOURCE_SUITE, import_opfor.SUITE, import_opfor.MANIFEST = saved
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
