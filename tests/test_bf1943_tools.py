"""The suite's tools on your BF1943 copy, as bf1943-xenia-kit extracts it (BF1943_GAME overrides; skipped when absent).

  python -B tests\\test_bf1943_tools.py [level | dbxedit | fbrb | navkit | hub | viewers | all]

  level    core level1943.py loads every multiplayer map: entities, collision, terrain, water, two bases and
           their flags, spawns with sides (a base's and a carrier's); Wake Island's 16 ladders
  dbxedit  DbxEdit and LevelDiff (built with their build.bat if needed) on Wake Island's partitions: every one
           round-trips byte for byte, DbxEdit's XML equals core dbx.py's, the level check passes, LevelDiff finds
           no change between a partition and itself
  fbrb     fbrb1943.py's command line: info, list and extract with a manifest; replace writes a copy whose entry
           reads back, and refuses to write into the game folder
  navkit   bf1943-auto-navkit-big-endian navkit.py prepare on Wake Island, into a temporary work folder: the
           settings (sea level 215 m, sector offsets -11 / -11) and the object files
  hub      the suite window opens with your copy's levels in its Levels tab
  viewers  the Map Inspector draws Wake Island (a screenshot); the Navigation Studio draws it with a navigation
           build when there is one (work folder, or BF1943_HPD) and routes between two flags
  probe    NavKit's enclosure_probe1943.py on Wake Island's samples (when navkit.py has sampled it): from the Beach
           bunker's spawn the fill leaves the bunker
  xex      bf1943-xex-tools' ppcdis.py from the research workspace (BF1943_RESEARCH, else operation-rising-sun next to
           the suite; skipped without a decoded image)
"""
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(SUITE, 'core')]
from bfbc2core import game1943  # noqa: E402

PY = sys.executable
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def level():
    from bfbc2core import level1943
    for lv in game1943.MP_LEVELS:
        L = level1943.load(lv, log=lambda m: None)
        cats = {}
        for e in L.entities:
            cats[e.category] = cats.get(e.category, 0) + 1
        bases = [o for o in L.objectives if o['role'] == 'base']
        sided = {e.team for e in L.entities if e.category == 'soldier spawn' and e.team in (1, 2)}
        check(len(L.meshes) > 1000 and L.heightfield is not None and L.water is not None and len(L.water)
              and len(bases) == 2 and cats.get('flag', 0) >= 3 and sided == {1, 2},
              f'{lv}: {len(L.entities)} entities, {len(L.meshes)} with collision, terrain and water, {len(bases)} bases, '
              f'{cats.get("flag", 0)} flags, soldier spawns of both sides')
        if lv == 'wake_island_s':
            carrier = [e for e in L.entities if e.category == 'vehicle spawn' and e.team in (1, 2)]
            check(cats.get('ladder') == 16 and len(carrier) == 16,
                  f'{lv}: 16 ladders; the carriers\' 16 vehicle spawns take their carrier\'s side ({len(carrier)})')


def dbxedit():
    from bfbc2core import dbx
    exe = os.path.join(SUITE, 'bf1943-dbx-tools', 'DbxEdit.exe')
    ldiff = os.path.join(SUITE, 'bf1943-diff-workbench', 'level-diff', 'LevelDiff.exe')
    for e, bat in ((exe, os.path.join(SUITE, 'bf1943-dbx-tools', 'build.bat')),
                   (ldiff, os.path.join(SUITE, 'bf1943-diff-workbench', 'level-diff', 'build.bat'))):
        if not os.path.isfile(e):
            subprocess.run(['cmd', '/c', bat], capture_output=True)
    work = os.path.join(game1943.work_root(), 'test-dbx')            # short: .NET Framework's 260-character limit
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    g = game1943.Game(None, 'wake_island_s')
    n = 0
    for name in g.names():
        if name.endswith('.dbx') and (name.startswith('levels/wake_island_s') or n < 600):
            p = os.path.join(work, *name.split('/'))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').write(g.data(name))
            n += 1
    r = subprocess.run([exe, 'roundtrip', work], capture_output=True, text=True, errors='replace')
    check(f'{n} identical, 0 different' in r.stdout, f'DbxEdit round trip: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]}')
    subprocess.run([exe, 'xml', work], capture_output=True)
    same = bad = 0
    for dp, _, fs in os.walk(work):
        for f in fs:
            if f.endswith('.dbx'):
                p = os.path.join(dp, f)
                mine = dbx.to_xml(open(p, 'rb').read())
                theirs = open(p[:-4] + '.xml', 'rb').read() if os.path.isfile(p[:-4] + '.xml') else b''
                ok = (mine if isinstance(mine, bytes) else mine.encode('utf-8')) == theirs
                same, bad = same + ok, bad + (not ok)
    check(same == n and not bad, f'DbxEdit XML equals core dbx.py\'s on {same} of {n} partitions')
    lvp = os.path.join(work, 'levels', 'wake_island_s.dbx')
    r = subprocess.run([exe, 'check', lvp], capture_output=True, text=True, errors='replace')
    check(r.returncode == 0 and '0 error(s)' in r.stdout, f'DbxEdit level check on Wake Island: {r.stdout.strip()[-80:]}')
    r = subprocess.run([ldiff, lvp, lvp, '--summary'], capture_output=True, text=True, errors='replace')
    check(r.returncode == 0 and 'changed: 0' in r.stdout, 'LevelDiff: a partition against itself has no change')
    shutil.rmtree(work, ignore_errors=True)


def fbrb():
    tool = os.path.join(SUITE, 'bf1943-fbrb-tool', 'fbrb1943.py')
    game = game1943.find_game()
    b = os.path.join(game, 'levels', 'wake_island_s', 'terrain-00.fbrb')
    r = subprocess.run([PY, '-B', tool, 'info', b], capture_output=True, text=True)
    check(r.returncode == 0 and '8 entries' in r.stdout, 'info: Wake Island\'s terrain bundle, 8 entries')
    r = subprocess.run([PY, '-B', tool, 'list', b, 'heightfield'], capture_output=True, text=True)
    check(r.returncode == 0 and 'TerrainHeightfield' in r.stdout and len(r.stdout.strip().splitlines()) == 1, 'list with a filter')
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([PY, '-B', tool, 'extract', b, d, 'water'], capture_output=True, text=True)
        man = json.load(open(os.path.join(d, 'fbrb.manifest.json')))
        check(r.returncode == 0 and len(man['entries']) == 2 and all(len(e['sha256']) == 64 for e in man['entries']),
              'extract with a filter: two water entries and their hashes')
        src = os.path.join(d, *man['entries'][0]['name'].split('/'))
        out = os.path.join(d, 'copy.fbrb')
        new = os.path.join(d, 'new.bin')
        open(new, 'wb').write(b'replaced')
        r = subprocess.run([PY, '-B', tool, 'replace', b, out, f'{man["entries"][0]["name"]}={new}'], capture_output=True, text=True)
        sys.path.insert(0, os.path.join(SUITE, 'bf1943-fbrb-tool'))
        from fbrb1943 import Bundle
        check(r.returncode == 0 and Bundle(open(out, 'rb').read()).data(man['entries'][0]['name']) == b'replaced'
              and os.path.isfile(src), 'replace: the copy holds the new entry')
        r = subprocess.run([PY, '-B', tool, 'replace', b, os.path.join(game, 'levels', 'x.fbrb'), f'{man["entries"][0]["name"]}={new}'],
                           capture_output=True, text=True)
        check(r.returncode != 0 and not os.path.exists(os.path.join(game, 'levels', 'x.fbrb')), 'replace refuses the game folder')


def navkit():
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, BF1943_WORK=d)
        r = subprocess.run([PY, '-B', os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'navkit.py'), 'prepare', 'wake_island_s'],
                           capture_output=True, text=True, env=env)
        w = os.path.join(d, 'levels', 'wake_island_s')
        s = json.load(open(os.path.join(w, 'navkit', 'navkit.json'))) if os.path.isfile(os.path.join(w, 'navkit', 'navkit.json')) else {}
        files = [f for f in ('all_tris.npy', 'ladders.json', 'soldier_spawns.json', 'heightfield.res', 'water_mesh.res')
                 if os.path.isfile(os.path.join(w, 'objects', f))]
        check(r.returncode == 0 and s.get('sea_level') == 215.0 and s.get('sector_offsets') == [-11, -11] and len(files) == 5,
              f'navkit.py prepare wake_island_s: sea level {s.get("sea_level")}, offsets {s.get("sector_offsets")}, {len(files)} of 5 files')
        r = subprocess.run([PY, '-B', os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'navkit.py'), 'build', 'wake_island_s'],
                           capture_output=True, text=True, env=env)
        check(r.returncode == 1 and 'not sampled yet' in r.stdout, 'navkit.py build before sampling says what to do')


def hub():
    import importlib.util
    import tkinter as tk
    spec = importlib.util.spec_from_file_location('hub', os.path.join(SUITE, 'BF1943 Tool Suite.pyw'))
    hubmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hubmod)
    from bfbc2core import errors
    errors.setup('test-hub', gui=False)
    root = tk.Tk()
    root.withdraw()
    hubmod.Hub.notify = lambda self, kind, title, text: None          # no message windows in a test
    h = hubmod.Hub(root)
    rows = h.tree.get_children()
    tabs = [h.tabs.tab(t, 'text') for t in h.tabs.tabs()]
    check(list(rows) == game1943.levels() and tabs == ['Levels', 'Bundles', 'Level diff', 'Xenia run', 'Xbox files', 'Tests', 'More tools'],
          f'the hub: {len(rows)} levels; tabs {tabs}')
    root.destroy()


def viewers():
    with tempfile.TemporaryDirectory() as d:
        shot = os.path.join(d, 'inspector.png')
        r = subprocess.run([PY, '-B', os.path.join(SUITE, 'bf1943-map-inspector', 'inspector.py'), 'wake_island_s',
                            '--screenshot', shot, '--frames', '20'], capture_output=True, text=True, timeout=600)
        check(r.returncode == 0 and os.path.isfile(shot) and os.path.getsize(shot) > 50000, 'Map Inspector: Wake Island drawn')
        hpd = os.environ.get('BF1943_HPD') or os.path.join(game1943.level_work('wake_island_s'), 'navkit', 'wake_island_s.hpd')
        if not os.path.isfile(hpd):
            print(f'skip Navigation Studio: no build at {hpd}')
            return
        shot = os.path.join(d, 'studio.png')
        r = subprocess.run([PY, '-B', os.path.join(SUITE, 'bf1943-navigation-studio', 'studio.py'), 'wake_island_s', '--hpd', hpd,
                            '--path', '-20.93,222.57,115.82;322.12,233.77,262.58', '--screenshot', shot, '--frames', '20'],
                           capture_output=True, text=True, timeout=600)
        routed = 'path: cost' in r.stdout
        check(r.returncode == 0 and os.path.isfile(shot) and routed,
              'Navigation Studio: Wake Island and its build drawn; South Base to the airfield routed')


def probe():
    samples = os.path.join(game1943.level_work('wake_island_s'), 'samples', 'samples_02.bin')
    if not os.path.isfile(samples):
        print(f'skip enclosure probe: no Wake Island samples at {samples} (navkit.py all wake_island_s)')
        return
    tool = os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tools', 'enclosure_probe1943.py')
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, 'probe.png')
        # the Beach bunker's spawn: inside, reached over the door's 0.33 m lip - the fill leaves the bunker
        r = subprocess.run([PY, '-B', tool, 'wake_island_s', '134.33', '218.9', '239.53', '15', '--png', png],
                           capture_output=True, text=True, timeout=900)
        n = int(r.stdout.split('fill: ', 1)[1].split()[0]) if 'fill: ' in r.stdout else 0
        check(r.returncode == 0 and n > 5000 and os.path.isfile(png),
              f'enclosure probe: from the Beach bunker\'s spawn the fill leaves the bunker ({n} surfaces), and a map')


def xex():
    root = os.environ.get('BF1943_RESEARCH') or os.path.join(os.path.dirname(SUITE), 'operation-rising-sun')
    if not os.path.isfile(os.path.join(root, 'work', 'xex-analysis', 'default.checked.memory.bin')):
        print(f'skip executable tools: no research workspace with a decoded image ({root})')
        return
    r = subprocess.run([PY, '-B', os.path.join(SUITE, 'bf1943-xex-tools', 'ppcdis.py'), '82391988', '1'], cwd=root,
                       capture_output=True, text=True)
    check(r.returncode == 0 and 'bl' in r.stdout and '0x8238eb68' in r.stdout,
          'executable tools: ppcdis.py from the research workspace (the server player update\'s call at 82391988)')


if __name__ == '__main__':
    try:
        game1943.find_game()
    except FileNotFoundError as e:
        print(f'skip: {e}')
        sys.exit(0)
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    for name, fn in (('level', level), ('dbxedit', dbxedit), ('fbrb', fbrb), ('navkit', navkit), ('hub', hub),
                     ('viewers', viewers), ('probe', probe), ('xex', xex)):
        if what in (name, 'all'):
            fn()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
