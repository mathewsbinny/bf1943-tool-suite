"""BF1943 data tests: the suite's readers on the game folder bf1943-xenia-kit extracts from your own package.

  python -B tests\\test_bf1943_data.py [bundles | dbx | terrain | apt | all]

  bundles  fbrb1943.py on every bundle: 82 of the 85 read (three use another layout), and each rebuilds with an identical
           directory and decompressed payload
  dbx      core dbx.py parses every .dbx partition of the readable bundles
  terrain  core terrain.py / water.py on every multiplayer level (big-endian): Wake Island's ground spawns stand on
           the terrain (within 1 m), and each level's water is flat
  apt      bf1943-apt-tools apt.py reads and validates the three menu movies the Xenia kit edits

BF1943_GAME overrides the game folder (default: bf1943-xenia-kit\\output\\title\\dist\\xenon); skipped when absent.
"""
import os
import sys

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-fbrb-tool'), os.path.join(SUITE, 'bf1943-apt-tools'),
                os.path.join(SUITE, 'bf1943-spawn-map')]
GAME = os.environ.get('BF1943_GAME', os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon'))
LEVELS = ['wake_island_s', 'guadal_canal', 'iwo_jima_s', 'coral_sea']
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def bundle_paths():
    return sorted(os.path.join(dp, f) for dp, _, fs in os.walk(GAME) for f in fs if f.endswith('.fbrb'))


def bundles():
    from fbrb1943 import Bundle
    read, refused, differ = [], [], []
    for p in bundle_paths():
        raw = open(p, 'rb').read()
        try:
            b = Bundle(raw)
        except ValueError:
            refused.append(os.path.relpath(p, GAME))
            continue
        read.append(b)
        again = Bundle(b.build()[0])                  # compressed bytes differ (zlib is not DICE's encoder); the
        if again.directory != b.directory or again.payload != b.payload:      # directory and content must not
            differ.append(os.path.relpath(p, GAME))
    check(len(read) == 82 and len(refused) == 3, f'{len(read)} bundles read, {len(refused)} refused ({refused})')
    check(not differ, f'every readable bundle rebuilds with an identical directory and payload ({len(differ)} differ: {differ[:3]})')
    return read


def dbx_partitions():
    from bfbc2core import dbx
    from fbrb1943 import Bundle
    count, bad = 0, []
    for p in bundle_paths():
        try:
            b = Bundle(open(p, 'rb').read())
        except ValueError:
            continue
        for e in b.entries:
            if e['name'].lower().endswith('.dbx'):
                count += 1
                try:
                    dbx.parse(b.data(e['name']))
                except Exception as ex:          # any parse error is a failure of the reader
                    bad.append(f'{e["name"]}: {ex}')
    check(count > 1000 and not bad, f'{count} .dbx partitions parse ({len(bad)} failed: {bad[:2]})')


def terrain_water():
    import spawnmap
    from bfbc2core import terrain, water
    from fbrb1943 import Bundle
    for lv in LEVELS:
        b = Bundle(open(os.path.join(GAME, 'levels', lv, 'terrain-00.fbrb'), 'rb').read())
        names = {e['name'].lower(): e['name'] for e in b.entries}
        hf = terrain.read(b.data(names[f'terrains/{lv}/{lv}.heightfield-000.res']))
        mesh = os.path.join(SUITE, f'.test-{lv}-water.res')
        try:
            open(mesh, 'wb').write(b.data(names[f'terrains/{lv}/{lv}.water.mesh.res']))
            v, t = water.read_mesh(mesh)
        finally:
            os.remove(mesh)
        check(hf.size in (1024, 2048) and len(t) and float(v[:, 1].max() - v[:, 1].min()) < 0.01,
              f'{lv}: terrain {hf.size} samples per side, water flat at {float(v[:, 1].mean()):.1f} m ({len(t)} triangles)')
        if lv == 'wake_island_s':
            m = spawnmap.spawn_map(GAME, lv)
            flags = [s for o in m['objectives'] if o['role'] == 'flag' for s in o['spawns'] if s['kind'] == 'soldier']
            gaps = [round(s['position'][1] - float(hf.height(s['position'][0], s['position'][2])), 2) for s in flags]
            check(all(-0.1 <= g <= 1.0 for g in gaps), f'{lv}: flag spawns stand on the terrain (spawn - ground {gaps} m)')


def apt_movies():
    import apt
    from fbrb1943 import Bundle
    for bundle, entry in (('menu/main/mainmenu-00.fbrb', 'Menu/main_nav.res'), ('menu/main/mainmenu-00.fbrb', 'Menu/main_root.res'),
                          ('menu/hud/hud_multiplayer_cq-00.fbrb', 'Menu/igm_mp_cq.res')):
        b = Bundle(open(os.path.join(GAME, *bundle.split('/')), 'rb').read())
        a = apt.Apt(b.data(entry))
        problems = a.validate()[2]
        check(not problems and a.characters(), f'{entry}: {len(a.characters())} characters, {len(a.action_items())} action blocks, valid')


if __name__ == '__main__':
    if not os.path.isdir(os.path.join(GAME, 'levels')):
        print(f'skip: no BF1943 game folder at {GAME} (run bf1943-xenia-kit build first, or set BF1943_GAME)')
        sys.exit(0)
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what in ('bundles', 'all'):
        bundles()
    if what in ('dbx', 'all'):
        dbx_partitions()
    if what in ('terrain', 'all'):
        terrain_water()
    if what in ('apt', 'all'):
        apt_movies()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
