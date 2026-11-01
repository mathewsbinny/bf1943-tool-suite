"""NavKit (big-endian): a BF1943 build checked against its level's spawns and flags.

  routecheck1943.py <file.hpd> <level> [--game <dist\\xenon folder>] [--json out.json]

Every soldier spawn of each base and flag, and each alternate, is joined to the route graph (core navpath.join:
the nearest node within 15 m horizontally and 4 m in height); a spawn on the largest network passes, one on a
small island or off the graph is listed. A base's spawns on a carrier form the carrier's own network, which is
expected (the sea divides it) and reported apart. Then a route between the first spawns of every pair of flags,
which must exist. Exit code 0 when every flag spawn is on the main network and every flag pair is routed, and there
was something to check (evaluate: no spawn, a flag without spawns or no flag pair fails).
"""
import json
import os
import sys
from typing import TYPE_CHECKING, Any

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import soldier1943  # noqa: E402
from bfbc2core import game1943, navdata, navpath, terrain  # noqa: E402

if TYPE_CHECKING:
    import argparse

    from bfbc2core.navdata import NavData

type Report = dict[str, Any]        # check()'s result: {'level', 'hpd', 'summary', 'spawns', 'routes', ...}


def check(hpd: str, level: str, game: str | None = None) -> Report:
    """{'summary', 'spawns': [...], 'routes': [...], 'problems': [...], 'notes': [...], 'ok'} for a build of a level."""
    import spawnmap
    game = game1943.find_game(game)
    level_game = game1943.Game(game, level)
    hf_data = level_game.data(f'terrains/{level}/{level}.heightfield-000.res')
    # BF1943 builds store nodes half the soldier's height up
    nav = navdata.load(hpd, terrain.read(hf_data) if hf_data else None, y_offset=soldier1943.SETTINGS.y_offset)
    return evaluate(nav, spawnmap.spawn_map(game, level)['objectives'], level, hpd)


def evaluate(nav: 'NavData', objectives: list[dict[str, Any]], level: str, hpd: str) -> Report:
    """check() on a loaded build (navdata.NavData) and the level's objectives (spawnmap's). Passing needs something
    checked (2026-10-30: an empty graph and a flag without soldier spawns passed, with no spawn and no route checked):
    soldier spawns on the level, each joined to the graph; every flag with soldier spawns on the main network and
    routed to every other; and when any flag has soldier spawns, every flag has them and there are at least two. A
    level whose flags have no soldier spawns at all (Coral Sea's, an air level: its soldiers start on the carriers)
    has no route to check, and the report says so; an objective without soldier spawns is a note then (the
    tutorial's Axis carrier has none)."""
    main = max(range(len(nav.islands)), key=lambda i: nav.islands[i].nodes) if nav.islands else -1
    spawns: list[dict[str, Any]] = []
    firsts = {}                                     # flag name -> its first soldier spawn's position
    for objective in objectives:
        for spawn in objective['spawns']:
            if spawn.get('kind') != 'soldier':
                continue
            alternates = [a for a in spawn.get('alternates', []) if isinstance(a, list)]
            for k, point in enumerate([spawn['position']] + alternates):
                node, distance = navpath.join(nav, point)
                island = None if node is None else int(nav.node_island[node])
                spawns.append(dict(objective=objective['name'], role=objective['role'], spawn=spawn['name'], point=k,
                                   position=point, island_nodes=None if island is None else nav.islands[island].nodes,
                                   main=island == main, join_m=round(distance, 1) if node is not None else None))
            if objective['role'] == 'flag':
                firsts.setdefault(objective['name'], spawn['position'])
    routes = []
    names = list(firsts)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            route = navpath.route(nav, firsts[first], firsts[second])
            routes.append(dict(a=first, b=second, found=bool(route.found),
                               cost=round(float(route.cost), 1) if route.found else None,
                               reason=None if route.found else str(route.reason)))
    flag_off = [spawn for spawn in spawns if spawn['role'] == 'flag' and not spawn['main']]
    unrouted = [route for route in routes if not route['found']]
    base_off = sum(1 for spawn in spawns if spawn['role'] == 'base' and not spawn['main'])
    summary = dict(navdata.summary(nav), main_network_nodes=nav.islands[main].nodes if main >= 0 else 0,
                   spawn_points=len(spawns), flag_points_off_main=len(flag_off),
                   base_points_off_main=base_off,
                   flag_pairs=len(routes), flag_pairs_routed=len(routes) - len(unrouted))
    problems, notes = [], []
    bare = [objective for objective in objectives if not any(s.get('kind') == 'soldier' for s in objective['spawns'])]
    flags = [objective for objective in objectives if objective['role'] == 'flag']
    if not spawns:
        problems.append('no soldier spawn on the level: nothing checked')
    # flags' spawns off the graph are in flag_off
    unjoined = [spawn for spawn in spawns if spawn['island_nodes'] is None and spawn['role'] != 'flag']
    if unjoined:
        problems.append(f'{len(unjoined)} base spawn point(s) with no route node within reach')
    if firsts:
        for objective in bare:
            message = f"{objective['role']} {objective['name'] or '(no name)'}: no soldier spawn"
            (problems if objective['role'] == 'flag' else notes).append(message)
        if len(firsts) < 2:
            problems.append(f'{len(firsts)} flag with soldier spawns: no flag pair to route')
    else:
        notes += [f"{objective['role']} {objective['name'] or '(no name)'}: no soldier spawn" for objective in bare]
        notes.append(f'no route checked: none of the {len(flags)} flags has soldier spawns')
    return dict(level=level, hpd=os.path.abspath(hpd), summary=summary, spawns=spawns, routes=routes,
                problems=problems, notes=notes, ok=not flag_off and not unrouted and not problems)


def report(result: Report) -> str:
    """check()'s result as text: the summary, the spawns off the main network, each flag pair's route, the
    problems and notes, and 'ok' or 'FAILED'."""
    summary = result['summary']
    lines = [f"{result['level']}: {summary['nodes']} nodes, {summary['edges']} edges, {summary['islands']} islands, "
             f"main network {summary['main_network_nodes']} nodes, {summary['ladder links']} ladder links",
             f"spawn points: {summary['spawn_points']}; flag points off the main network "
             f"{summary['flag_points_off_main']}; base points off it {summary['base_points_off_main']} "
             f"(a carrier base is its own network)",
             f"flag pairs routed: {summary['flag_pairs_routed']} of {summary['flag_pairs']}"]
    off = {}                                            # (role, spawn, island size) -> points
    for spawn in result['spawns']:
        if not spawn['main']:
            off.setdefault((spawn['role'], spawn['spawn'], spawn['island_nodes']), []).append(spawn)
    for (role, spawn_name, nodes), points in off.items():
        where = 'no node within reach' if nodes is None else f'an island of {nodes} nodes'
        at = f"point {points[0]['point']} at {points[0]['position']}" if len(points) == 1 else f'{len(points)} points'
        lines.append(f'  {role:4s} {spawn_name}, {at}: {where}')
    for route in result['routes']:
        outcome = f"{route['cost']} m" if route['found'] else f"no route ({route['reason']})"
        lines.append(f"  {route['a']} -> {route['b']}: " + outcome)
    lines += [f'  problem: {p}' for p in result.get('problems', ())]
    lines += [f'  note: {n}' for n in result.get('notes', ())]
    lines.append('ok' if result['ok'] else 'FAILED')
    return '\n'.join(lines)


def parse(argv: list[str]) -> 'argparse.Namespace | None':
    """argparse over the arguments (2026-10-30 review: option values were taken as positionals, so options before the
    .hpd checked the game folder as the build); None for -h / --help."""
    import argparse
    if any(arg in ('-h', '--help') for arg in argv[1:]):
        return None
    parser = argparse.ArgumentParser(prog='routecheck1943.py', add_help=False)
    parser.add_argument('hpd')
    parser.add_argument('level')
    parser.add_argument('--game')
    parser.add_argument('--json')
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    args = parse(argv)
    if args is None:
        print(__doc__)
        return 0
    result = check(args.hpd, args.level, args.game)
    print(report(result))
    if args.json:
        with open(args.json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(result, f, indent=1)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('routecheck1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
