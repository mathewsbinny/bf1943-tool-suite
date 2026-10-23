"""levelobjects1943.py on Wake Island, from the game folder bf1943-xenia-kit extracts (BF1943_GAME overrides; skipped
when absent): placements, collision (meshes and convex shapes), flags, the carrier decks where soldiers stand, and
the ladders (LadderEntityData).
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, 'tools')]
import levelobjects1943  # noqa: E402

GAME = os.environ.get('BF1943_GAME', os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon'))
failures = []
# where the host's soldier stood on each carrier in game (session bots-9/-10 reports, 2026-10-23)
DECKS = {'US carrier': (-548.03, 228.77, 14.99), 'Japanese carrier': (-159.65, 230.25, -554.31)}


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


if __name__ == '__main__':
    if not os.path.isdir(os.path.join(GAME, 'levels', 'wake_island_s')):
        print(f'skip: no BF1943 game folder at {GAME}')
        sys.exit(0)
    with tempfile.TemporaryDirectory() as out:
        levelobjects1943.main(['levelobjects1943.py', GAME, 'wake_island_s', out])
        objs = json.load(open(os.path.join(out, 'objects.json')))
        groups = json.load(open(os.path.join(out, 'group_instances.json')))
        boxes = json.load(open(os.path.join(out, 'object_boxes.json')))
        T = np.load(os.path.join(out, 'all_tris.npy'))
        check(len(objs) == 1374 and len(groups) == 2999, f'{len(objs)} placed objects, {len(groups)} group members')
        check(len(boxes) == 4309 and len(T) > 2_600_000, f'{len(boxes)} with collision, {len(T)} triangles')
        sandbags = [b for b in boxes if b['bp'].endswith('/barriersandbag_1')]
        check(sandbags and all(b['destructible'] and b['detailed'] for b in sandbags),
              f'sandbag walls ({len(sandbags)}): collision from their convex shapes, destructible, detailed')
        ladders = json.load(open(os.path.join(out, 'ladders.json')))
        kinds = sorted({l['bp'].rsplit('/', 1)[-1] for l in ladders})
        beach = [l for l in ladders if abs(l['lo'][0] - 136.15) < 0.1 and abs(l['lo'][2] - 234.2) < 0.1]
        check(len(ladders) == 16 and kinds == ['airtowerladder_t01', 'laddermetal_t03v01'] and beach
              and abs(beach[0]['hi'][1] - 222.26) < 0.05,
              f'{len(ladders)} ladders ({kinds}); the one at the Beach bunker reaches its roof (222.26 m)')
        tower = [l for l in ladders if abs(l['lo'][0] - 21.02) < 0.1 and abs(l['lo'][2] - 144.55) < 0.1]
        check(tower and abs(tower[0]['lo'][1] - 222.81) < 0.05 and abs(tower[0]['box_lo_y'] - 218.65) < 0.05,
              f'a guard tower ladder buried 4 m: its foot raised to the terrain (222.81 m, box bottom 218.65 m)')
        c = T.mean(1)
        for name, (x, y, z) in DECKS.items():
            near = (np.abs(c[:, 0] - x) < 10) & (np.abs(c[:, 2] - z) < 10) & (np.abs(c[:, 1] - y) < 0.05)
            check(near.any(), f'{name}: collision at the deck height a soldier stood at ({y} m)')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
