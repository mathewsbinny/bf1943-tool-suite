"""NavKit (big-endian): the route check (routecheck1943) on made-up builds and objectives (no game data; the game, the
build's reader and the spawn map are stood in for). It passes only when something was checked (2026-10-30: an empty
graph and a flag without soldier spawns passed with zero spawns and zero routes): an empty graph fails; a flag
without soldier spawns fails while other flags have them; one flag alone has no pair and fails; two routed flags
pass; a level whose flags have no soldier spawns (Coral Sea's) passes on its carriers with a note, but not when the
carriers' spawns join no node.

  python -B test_routecheck1943.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), 'tools'), os.path.join(SUITE, 'core')]
import routecheck1943 as rc  # noqa: E402
from bfbc2core import navdata  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def graph(xs):
    """Route nodes at ground y 10 along x (stored 0.9 m up), each joined both ways to the next; one island."""
    n = len(xs)
    pos = np.array([(x, 10.9, 0.0) for x in xs], np.float64).reshape(-1, 3)
    edges = np.array([(a, a + 1) for a in range(n - 1)] + [(a + 1, a) for a in range(n - 1)], np.int64).reshape(-1, 2)
    cost = np.array([abs(xs[b] - xs[a]) for a, b in edges], np.float64)
    islands = [navdata.Island(0, n, 0, pos.mean(axis=0))] if n else []
    return navdata.NavData('made-up.hpd', [], np.zeros((0, 2)), np.zeros(0), np.zeros(0, np.int32), pos, np.zeros(n, int),
                           edges, cost, np.zeros(n, np.int32), islands, np.zeros(len(edges), bool))


def objective(role, name, at=None, kind='soldier'):
    return dict(role=role, name=name, spawns=[] if at is None else [dict(kind=kind, name=name + ' spawn', position=list(at))])


def run(nav, objectives):
    """routecheck1943.check with the game, the reader and the spawn map stood in for."""
    sys.modules['spawnmap'] = SimpleNamespace(spawn_map=lambda game, level: {'objectives': objectives})
    rc.game1943.find_game = lambda game=None: 'made-up game'
    rc.game1943.Game = lambda game, level: SimpleNamespace(data=lambda name: None)
    rc.navdata.load = lambda hpd, hf=None, cache_dir=None: nav
    return rc.check('made-up.hpd', 'made_up', None)


if __name__ == '__main__':
    line = graph([0.0, 10.0, 20.0, 30.0])
    r = run(graph([]), [objective('flag', 'A')])
    check(not r['ok'], f'an empty graph and a flag without soldier spawns fails ({r["summary"]["spawn_points"]} spawns, '
                       f'{len(r["routes"])} routes; {r.get("problems")})')
    r = run(line, [objective('flag', 'A', (0, 10, 0)), objective('flag', 'B', (30, 10, 0))])
    check(r['ok'] and len(r['routes']) == 1 and r['routes'][0]['found'], f'two flags on the graph, routed: ok ({r.get("problems")})')
    r = run(line, [objective('flag', 'A', (0, 10, 0)), objective('flag', 'B', (30, 10, 0)),
                   objective('flag', 'C', (20, 10, 0), kind='vehicle')])
    check(not r['ok'] and any('flag C' in p for p in r.get('problems', ())),
          f'a flag with only a vehicle spawn while the others have soldiers fails ({r.get("problems")})')
    r = run(line, [objective('flag', 'A', (0, 10, 0)), objective('base', 'Carrier', (10, 10, 0))])
    check(not r['ok'] and not r['routes'], f'one flag alone: no pair routed, fails ({r.get("problems")})')
    air = [objective('base', 'Allies', (0, 10, 0)), objective('base', 'Axis', (30, 10, 0)), objective('flag', ''),
           objective('flag', 'South')]
    r = run(line, air)
    check(r['ok'] and not r['routes'] and any('no route checked' in n for n in r.get('notes', ())),
          f'flags without soldier spawns (an air level): its carriers pass, with a note ({r.get("notes")})')
    r = run(graph([]), air)
    check(not r['ok'], f'the air level with an empty graph fails: its carriers\' spawns join no node ({r.get("problems")})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
