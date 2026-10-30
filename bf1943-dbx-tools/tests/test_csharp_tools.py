r"""The C# tools on made-up files (no game data):

- core csbuild.py: an exe is rebuilt when a source compiled into it (DbxEdit.cs in LevelDiff) is newer than it, or
  when the sources differ from those recorded at its build (a fake tool and build.bat in a temporary folder), and
  the hub's tool() goes through it; then DbxEdit.exe and LevelDiff.exe are built from the current sources;
- DbxEdit xml: a folder of sound partitions exits 0; one with a damaged partition names it and exits 1;
- LevelDiff: a list whose entries were only reordered is reported even when an earlier list of the same instance
  gained an entry;
- the regression checks of csharp_regressions.py (DbxEdit's reader against core dbx.py's, nesting, write guards, edit
  commands, the level check, LevelDiff, DbxEditGUI through reflection): see its docstring.

  python -B bf1943-dbx-tools\tests\test_csharp_tools.py
"""
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))
from bfbc2core import csbuild  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import csharp_regressions  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def partition(lists):
    """A binary partition (FORMAT.md sections 1-2): one instance whose arrays {name: [ref, ...]} hold ref items."""
    table = ['', 'partition', 'guid', 'instance', 'type', 'array', 'name', 'item', 'ref',
             'P0000000-0000-0000-0000-000000000000', 'I0000000-0000-0000-0000-000000000001', 'Test.Thing']
    idx = {s: i for i, s in enumerate(table)}

    def s(x):
        if x not in idx:
            idx[x] = len(table)
            table.append(x)
        return idx[x]

    body = bytes([s('partition'), 0xA1, s('guid'), s(table[9]), 0])
    body += bytes([s('instance'), 0xA2, s('guid'), s(table[10]), s('type'), s('Test.Thing'), 0])
    for name, refs in lists.items():
        body += bytes([s('array'), 0xA1, s('name'), s(name), 0])
        for r in refs:
            body += bytes([s('item'), 0x21, s('ref'), s(r), 0])        # text, empty
        body += bytes([0])
    body += bytes([0, 0, 0])                                            # end of instance, partition, file
    if len(table) > 127:
        raise ValueError('one-byte indexes only')
    offsets, blob = [], b''
    for t in table:
        offsets.append(len(blob))
        blob += t.encode('utf-8') + b'\0'
    area = 4 * len(table) + len(blob)
    return (b'{binary}' + struct.pack('>IIII', 24 + area, 0, area, len(table)) + struct.pack(f'>{len(table)}I', *offsets)
            + blob + body)


def fake_tool(d):
    """A fake tool in d: Fake.exe built by build.bat (Main.cs typed into it, so it is dated now; each build appends a
    line to builds.log) from Main.cs and a compiled-in Dep.cs."""
    exe, bat = os.path.join(d, 'Fake.exe'), os.path.join(d, 'build.bat')
    sources = [os.path.join(d, 'Main.cs'), os.path.join(d, 'Dep.cs')]
    for p in sources:
        open(p, 'w').write('// ' + os.path.basename(p))
    with open(bat, 'w', newline='\r\n') as f:
        f.write('@echo off\ntype "%~dp0Main.cs" > "%~dp0Fake.exe"\necho built>>"%~dp0builds.log"\n')
    return exe, bat, sources


def builds(d):
    p = os.path.join(d, 'builds.log')
    return len(open(p).read().split()) if os.path.isfile(p) else 0


def age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def build_checks(tmp):
    d = os.path.join(tmp, 'fake')
    os.makedirs(d)
    exe, bat, sources = fake_tool(d)
    for p in sources + [bat]:
        age(p, 100)                                                     # dates well before any build
    saved = csbuild.tools
    csbuild.tools = lambda: {'fake': (exe, bat, sources), 'leveldiff': (exe, bat, sources)}
    try:
        csbuild.ensure('fake')
        check(builds(d) == 1 and os.path.isfile(exe), 'csbuild: a missing exe is built')
        csbuild.ensure('fake')
        check(builds(d) == 1, 'csbuild: a current exe is not rebuilt')
        age(exe, 50)
        open(sources[1], 'a').write('\n// changed')                     # the compiled-in dependency, newer than the exe
        age(sources[1], 20)
        check(csbuild.stale(exe, bat, sources) == 'older than Dep.cs', f'csbuild: {csbuild.stale(exe, bat, sources)}')
        csbuild.ensure('fake')
        check(builds(d) == 2, 'csbuild: an exe older than a compiled-in source is rebuilt')
        open(sources[0], 'a').write('\n// changed')
        age(sources[0], 100)                                            # changed, but with an old date (a copy, a checkout)
        check(csbuild.stale(exe, bat, sources) == 'built from other sources', f'csbuild: {csbuild.stale(exe, bat, sources)}')
        csbuild.ensure('fake')
        check(builds(d) == 3, 'csbuild: an exe built from other sources (their hash) is rebuilt')
        os.remove(csbuild.stamp_path(exe))
        csbuild.ensure('fake')
        check(builds(d) == 4, 'csbuild: an exe with no record of its sources is rebuilt')

        # the hub builds its tools the same way
        spec = importlib.util.spec_from_file_location('hub', os.path.join(SUITE, 'BF1943 Tool Suite.pyw'))
        hub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hub)
        age(exe, 50)
        open(sources[1], 'a').write('\n// changed again')
        age(sources[1], 20)
        got = hub.tool('leveldiff')
        check(got == exe and builds(d) == 5, 'the hub rebuilds LevelDiff when DbxEdit.cs is newer than it')
    finally:
        csbuild.tools = saved


def main():
    tmp = tempfile.mkdtemp(prefix='csharp_tools_')
    try:
        build_checks(tmp)
        exe, ldiff, gui = csbuild.ensure('dbxedit'), csbuild.ensure('leveldiff'), csbuild.ensure('dbxgui')
        check(all(csbuild.stale(*csbuild.tools()[n]) is None for n in ('dbxedit', 'leveldiff', 'dbxgui')),
              'DbxEdit.exe, DbxEditGUI.exe and LevelDiff.exe built from the current sources')
        env = {**os.environ, 'LOCALAPPDATA': tmp}

        # DbxEdit xml: exit code
        good = os.path.join(tmp, 'x', 'good')
        os.makedirs(good)
        open(os.path.join(good, 'a.dbx'), 'wb').write(partition({'L': ['r1']}))
        r = subprocess.run([exe, 'xml', good], capture_output=True, text=True, errors='replace', env=env)
        check(r.returncode == 0 and os.path.isfile(os.path.join(good, 'a.xml')), f'DbxEdit xml, sound files: exit {r.returncode}, {r.stdout.strip()}')
        bad = os.path.join(tmp, 'x', 'bad')
        os.makedirs(bad)
        open(os.path.join(bad, 'a.dbx'), 'wb').write(partition({'L': ['r1']}))
        open(os.path.join(bad, 'broken.dbx'), 'wb').write(b'{binary}' + struct.pack('>IIII', 28, 0, 4, 1000) + bytes(8))
        r = subprocess.run([exe, 'xml', bad], capture_output=True, text=True, errors='replace', env=env)
        check(r.returncode == 1 and 'broken.dbx' in r.stderr and 'FAILED' in r.stdout,
              f'DbxEdit xml, a damaged file: exit {r.returncode}, named ({r.stdout.strip()})')

        # LevelDiff: a reorder after a list that changed
        a, b = os.path.join(tmp, 'a.dbx'), os.path.join(tmp, 'b.dbx')
        open(a, 'wb').write(partition({'First': ['r1', 'r2'], 'Second': ['r3', 'r4']}))
        open(b, 'wb').write(partition({'First': ['r1', 'r2', 'r5'], 'Second': ['r4', 'r3']}))
        r = subprocess.run([ldiff, a, b], capture_output=True, text=True, errors='replace', env=env)
        check(r.returncode == 1 and '+ First: r5' in r.stdout and 'Second: same 2 entries  ->  in another order' in r.stdout,
              'LevelDiff: a reorder is reported after an earlier list gained an entry'
              + ('' if r.returncode == 1 else '\n       ' + (r.stdout + r.stderr).strip().replace('\n', '\n       ')))
        open(b, 'wb').write(partition({'First': ['r1', 'r2'], 'Second': ['r4', 'r3']}))
        r = subprocess.run([ldiff, a, b], capture_output=True, text=True, errors='replace', env=env)
        check(r.returncode == 1 and 'Second: same 2 entries  ->  in another order' in r.stdout, 'LevelDiff: a reorder alone')
        r = subprocess.run([ldiff, a, a, '--summary'], capture_output=True, text=True, errors='replace', env=env)
        check(r.returncode == 0 and 'changed: 0' in r.stdout, 'LevelDiff: a file against itself has no change')

        tools = csharp_regressions.Tools(exe, ldiff, gui, os.path.join(tmp, 'reg'), check)
        os.makedirs(tools.tmp)
        for group in csharp_regressions.ALL:
            group(tools)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
