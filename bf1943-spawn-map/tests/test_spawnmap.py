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


def placeholders():
    """Coral Sea's placeholder capture points (Index 0 at the map origin, no spawns), objectives without an index or a
    position, and a spawn without a position are not written; a duplicate index among real objectives is an error."""
    def obj(name, index, position, role='flag', spawns=()):
        return dict(name=name, location='', index=index, role=role, initial_owner='TeamNeutral', capture_radius=None,
                    position=position, spawns=list(spawns))
    base = obj('Carrier', '10', [5.0, 1.0, 5.0], 'base', [dict(kind='soldier', team='Team1', position=None,
                                                                alternates=[[1.0, 1.0, 1.0]])])
    m = dict(level='made_up', objectives=[base, obj('Spawn', '0', [0.0, 0.0, 0.0]), obj('Spawn', '0', [0.0, 0.0, 0.0]),
                                          obj('NoIndex', None, [1.0, 2.0, 3.0]), obj('NoPos', '7', None),
                                          obj('Real', '5', [4.0, 5.0, 6.0])])
    try:
        text = spawnmap.flags_text(m)
    except Exception as e:                            # noqa: BLE001 - the old code: TypeError on a missing position
        check(False, f'placeholders: flags_text raised {type(e).__name__}: {e}')
        return
    lines = [l for l in text.splitlines() if not l.startswith('#')]
    check(lines == ['flag 10 base 0 5.00 1.00 5.00 0.00 Carrier', 'flag 5 flag 0 4.00 5.00 6.00 0.00 Real',
                    'spawn 1 10 1.00 1.00 1.00'], f'placeholders and objectives without index or position left out: {lines}')
    check('# left out, not flags: Spawn' in text, 'the left-out objectives are named in a comment')
    dup = dict(level='made_up', objectives=[obj('A', '3', [1.0, 0.0, 1.0]), obj('B', '3', [2.0, 0.0, 2.0])])
    try:
        spawnmap.flags_text(dup)
        check(False, 'a duplicate index is an error')
    except ValueError:
        check(True, 'a duplicate index is an error')


if __name__ == '__main__':
    navflags()
    placeholders()
    if '--made-up' in sys.argv:                       # the quick tier: no game data
        print('all passed' if not failures else f'{len(failures)} failed')
        sys.exit(1 if failures else 0)
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
        flags = [l.split() for l in spawnmap.flags_text(m).splitlines() if l.startswith('flag ')]
        idx = [f[1] for f in flags]
        check(len(idx) == len(set(idx)) and not any(f[4:7] == ['0.00', '0.00', '0.00'] for f in flags),
              f'{level}: --navflags has {len(flags)} flags, each index once, none at the map origin')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
