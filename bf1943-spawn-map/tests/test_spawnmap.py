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


def navflags():
    """--navflags' lines on a made-up map: a base with a spawn and an alternate, a neutral flag."""
    m = dict(level='made_up', objectives=[
        dict(name='Base One', location='', index='0', role='base', initial_owner='Team1', capture_radius=20.0,
             position=[1.0, 2.0, 3.0], spawns=[dict(kind='soldier', team='Team1', position=[4.0, 5.0, 6.0],
                                                    alternates=[[7.0, 8.0, 9.0], 'unresolved-guid'])]),
        dict(name='', location='CQ_FLAG', index='1', role='flag', initial_owner='TeamNeutral', capture_radius=None,
             position=[-1.5, 0.25, 10.0], spawns=[])])
    lines = [l for l in spawnmap.flags_text(m).splitlines() if not l.startswith('#')]
    check(lines == ['flag 0 base 1 1.00 2.00 3.00 20.00 Base_One', 'flag 1 flag 0 -1.50 0.25 10.00 0.00 CQ_FLAG',
                    'spawn 1 0 4.00 5.00 6.00', 'spawn 1 0 7.00 8.00 9.00'], f'--navflags lines: {lines}')


if __name__ == '__main__':
    navflags()
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
