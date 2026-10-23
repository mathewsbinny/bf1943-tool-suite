"""Tests for spawnmap.py, on the game folder bf1943-xenia-kit builds (BF1943_GAME overrides; skipped when absent)."""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import spawnmap

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


if __name__ == '__main__':
    game = os.environ.get('BF1943_GAME', spawnmap.DEFAULT_GAME)
    if not os.path.isdir(os.path.join(game, 'levels')):
        print(f'skip: no extracted game at {game} (run bf1943-xenia-kit build first)')
        sys.exit(0)
    for level in spawnmap.LEVELS:
        m = spawnmap.spawn_map(game, level)
        bases = [o for o in m['objectives'] if o['role'] == 'base']
        check(sorted(o['initial_owner'] for o in bases) == ['Team1', 'Team2']
              and all(any(s['kind'] == 'soldier' and s['team'] == o['initial_owner'] for s in o['spawns']) for o in bases),
              f'{level}: two bases, Team1 and Team2, each with a soldier spawn of its team')
        soldiers = [s for o in m['objectives'] for s in o['spawns'] if s['kind'] == 'soldier'] + m['unowned_soldier_spawns']
        check(soldiers and all(s['team'] in ('Team1', 'Team2', 'TeamNeutral') for s in soldiers),
              f'{level}: every soldier spawn has a team ({len(soldiers)})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
