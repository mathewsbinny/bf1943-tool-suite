r"""tools\import_opfor.py's command line, in process, on made-up trees

import_opfor.main() and its helpers run in this process on made-up trees only: a temporary git repository stands in
for OPFOR's suite and a temporary folder for this suite (the module's SOURCE_SUITE, SUITE and MANIFEST point at them
for the test and are restored afterwards; the real suites are never read or written). Checked: a single-file import,
a plan, an --update (a changed file, a new file, a file removed here, a file new in the source that is already here),
what each records; each refusal before anything is written (a destination outside the suite, already there, named
twice or inside another, nothing committed at the source, nothing imported to an --update destination, no arguments);
symbolic links in the source are not copied; a blob read back wrong stops the copy; place() leaves no temporary file.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'import_opfor.py')
_spec = importlib.util.spec_from_file_location('import_opfor', TOOL)     # tools\ is not on sys.path
io_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(io_mod)


def git(repo, *args, stdin=None):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                           '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false', *args], input=stdin,
                          capture_output=True, check=True).stdout


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@contextlib.contextmanager
def trees(tmp):
    src, suite = tmp / 'source', tmp / 'suite'
    src.mkdir()
    (suite / 'provenance').mkdir(parents=True)
    git(src, 'init', '-q')
    for rel, data in {'single.txt': b'one file\n', 'tool/a.py': b'a\n', 'tool/b.py': b'b\n',
                      'other/c.py': b'c\n'}.items():
        write(src / rel, data)
    git(src, 'add', '-A')
    blob = git(src, 'hash-object', '-w', '--stdin', stdin=b'a.py').decode().strip()
    git(src, 'update-index', '--add', '--cacheinfo', f'120000,{blob},tool/link.py')      # a symbolic link
    git(src, 'commit', '-q', '-m', 'first')
    manifest = suite / 'provenance' / 'opfor-imports.json'
    manifest.write_text('{"imports": []}\n', encoding='utf-8')
    saved = io_mod.SOURCE_SUITE, io_mod.SUITE, io_mod.MANIFEST, sys.argv
    io_mod.SOURCE_SUITE, io_mod.SUITE, io_mod.MANIFEST = str(src), str(suite), str(manifest)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield src, suite
    finally:
        io_mod.SOURCE_SUITE, io_mod.SUITE, io_mod.MANIFEST, sys.argv = saved


def run(*argv):
    """main() with argv: None, or the SystemExit's message (or code)."""
    sys.argv = ['import_opfor.py', *argv]
    try:
        io_mod.main()
    except SystemExit as e:
        return str(e.code)
    return None


def imports():
    with open(io_mod.MANIFEST, encoding='utf-8') as f:
        return json.load(f)['imports']


def test_import_plan_and_update(tmp_path):
    with trees(tmp_path) as (src, suite):
        assert run('single.txt', 'copied.txt', '--note', 'why') is None
        assert (suite / 'copied.txt').read_bytes() == b'one file\n'
        item = imports()[0]['items'][0]
        assert item['note'] == 'why' and item['destination'] == 'copied.txt' and item['files'][0]['path'] == 'single.txt'
        plan = tmp_path / 'plan.json'
        plan.write_text(json.dumps([{'source': 'tool', 'destination': 'tools/tool'},
                                    {'source': 'other/', 'destination': 'other-copy', 'note': 'n'}]), encoding='utf-8')
        assert run('--plan', str(plan)) is None
        assert sorted(p.name for p in (suite / 'tools/tool').iterdir()) == ['a.py', 'b.py'], 'the link is not copied'
        assert len(imports()) == 2 and imports()[1]['items'][0].get('created')
        # the source moves on: a.py changed, d.py new, b.py removed here on purpose, e.py new there and here already
        write(src / 'tool/a.py', b'a two\n')
        write(src / 'tool/d.py', b'd\n')
        write(src / 'tool/e.py', b'e\n')
        write(src / 'tool/b.py', b'b two\n')
        git(src, 'add', '-A')
        git(src, 'commit', '-q', '-m', 'second')
        write(suite / 'tools/tool/e.py', b'mine\n')
        (suite / 'tools/tool/b.py').unlink()
        manifest = json.loads(open(io_mod.MANIFEST, encoding='utf-8').read())
        manifest['imports'].append({'kind': 'remove', 'destination': 'tools/tool', 'paths': ['b.py']})
        io_mod.save_manifest(manifest)
        assert run('--update', 'tools\\tool') is None
        last = imports()[-1]
        assert last['kind'] == 'update' and last['items'][0]['conflicts'] == ['e.py']
        files = {f['path']: f for f in last['items'][0]['files']}
        assert set(files) == {'a.py', 'd.py'} and files['d.py']['new'] and 'previous_git_blob' in files['a.py']
        assert (suite / 'tools/tool/a.py').read_bytes() == b'a two\n' and not (suite / 'tools/tool/b.py').exists()
        assert (suite / 'tools/tool/e.py').read_bytes() == b'mine\n'


def test_refusals_write_nothing(tmp_path):
    with trees(tmp_path) as (src, suite):
        (suite / 'there').mkdir()
        cases = [(('tool', '../outside'), 'destination is outside this suite'),
                 (('tool', '.'), 'destination is outside this suite'),
                 (('tool', 'there'), 'destination exists, refusing to overwrite'),
                 (('nothing-here', 'x'), 'nothing committed at nothing-here')]
        for argv, words in cases:
            why = run(*argv)
            assert why and words in why, (argv, why)
        for plan, words in (([{'source': 'tool', 'destination': 'p'}, {'source': 'other', 'destination': 'P'}],
                             'destination named twice in the plan'),
                            ([{'source': 'tool', 'destination': 'p'}, {'source': 'other', 'destination': 'p/q'}],
                             'lies inside')):
            path = tmp_path / 'plan.json'
            path.write_text(json.dumps(plan), encoding='utf-8')
            why = run('--plan', str(path))
            if os.name != 'nt' and words.startswith('destination named twice'):
                continue                                    # 'p' and 'P' are two folders there
            assert why and words in why, (plan, why)
        assert run() == '2', 'no source and destination: a usage error'
        why = run('--update', 'never-imported')
        assert why and 'nothing was imported to never-imported' in why
        assert imports() == [] and sorted(p.name for p in suite.iterdir()) == ['provenance', 'there']


def test_recorded_follows_renames_and_removals():
    manifest = {'imports': [
        {'items': [{'source': 'tool', 'destination': 'old', 'files': [{'path': 'a.py', 'git_blob': '1'},
                                                                       {'path': 'b.py', 'git_blob': '2'}]}]},
        {'kind': 'rename', 'from': 'old', 'to': 'new'},
        {'kind': 'remove', 'destination': 'old', 'paths': ['b.py']},
        {'kind': 'move', 'destination': 'new', 'paths': {'a.py': 'common/a.py'}},
        {'items': [{'source': 'tool', 'destination': 'new', 'files': [{'path': 'a.py', 'git_blob': '3'}]}]}]}
    moves = {}
    source, files, removed = io_mod.recorded(manifest, 'new', moves)
    assert source == 'tool' and files['a.py']['git_blob'] == '3' and files['b.py']['git_blob'] == '2'
    assert removed == {'a.py', 'b.py'} and moves == {'a.py': 'common/a.py'}


def test_blob_read_back_wrong(tmp_path):
    with trees(tmp_path) as (src, suite):
        real = io_mod.git

        def damaged(*args):
            out = real(*args)
            return out + b'x' if args[:2] == ('cat-file', 'blob') else out
        io_mod.git = damaged
        try:
            why = run('single.txt', 'copied.txt')
            assert why and 'blob mismatch reading single.txt' in why
            assert not (suite / 'copied.txt').exists() and imports() == [], 'nothing written, nothing recorded'
        finally:
            io_mod.git = real
        assert run('tool', 'tool') is None
        write(src / 'tool/a.py', b'changed\n')
        git(src, 'commit', '-q', '-am', 'changed')
        io_mod.git = damaged
        try:
            why = run('--update', 'tool')
        finally:
            io_mod.git = real
        assert why and 'blob mismatch reading tool/a.py' in why
        assert (suite / 'tool/a.py').read_bytes() == b'a\n' and len(imports()) == 1, 'an update that wrote nothing is not recorded'


def test_place(tmp_path):
    data = b'payload'
    blob = io_mod.blob_id(data)
    target = tmp_path / 'sub' / 'file.bin'
    io_mod.place(str(target), data, blob, new=True)
    assert target.read_bytes() == data
    for args, words in (((str(target), data, blob, True), 'destination appeared while importing'),
                        ((str(tmp_path / 'other.bin'), data, '0' * 40, True), 'written file differs from its source')):
        try:
            io_mod.place(*args)
            why = None
        except SystemExit as e:
            why = str(e.code)
        assert why and words in why, why
    io_mod.place(str(target), b'new', io_mod.blob_id(b'new'), new=False)
    assert target.read_bytes() == b'new'
    assert sorted(p.name for p in tmp_path.rglob('*') if p.is_file()) == ['file.bin'], 'no temporary file left'
    assert io_mod.blob_id(b'') == 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391', "git's empty blob"


def test_run_recorded_part_way(tmp_path):
    """A run stopped after writing a file is recorded, marked incomplete with the error; one that wrote nothing is not."""
    with trees(tmp_path):
        manifest = {'imports': []}

        def wrote_one(items):
            items.append({'destination': 'tool', 'files': [{'path': 'a.py'}]})
            raise OSError(28, 'made-up full disk')
        try:
            io_mod.run_recorded(manifest, {'commit': 'c', 'items': []}, wrote_one)
            raised = False
        except OSError:
            raised = True
        assert raised and imports()[-1]['incomplete'].startswith('OSError: ')
        assert imports()[-1]['items'][0]['files'] == [{'path': 'a.py'}]

        def wrote_nothing(items):
            items.append({'destination': 'tool'})
            raise SystemExit('stopped')
        try:
            io_mod.run_recorded({'imports': []}, {'commit': 'c', 'items': []}, wrote_nothing)
        except SystemExit:
            pass
        assert len(imports()) == 1, 'nothing written: nothing recorded'
