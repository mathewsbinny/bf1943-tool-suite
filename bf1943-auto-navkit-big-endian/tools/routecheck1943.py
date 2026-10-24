"""NavKit (big-endian): a BF1943 build checked against its level's spawns and flags.

  routecheck1943.py <file.hpd> <level> [--game <dist\\xenon folder>] [--json out.json]

Every soldier spawn of each base and flag, and each alternate, is joined to the route graph (core navpath.join:
the nearest node within 15 m horizontally and 4 m in height); a spawn on the largest network passes, one on a
small island or off the graph is listed. A base's spawns on a carrier form the carrier's own network, which is
expected (the sea divides it) and reported apart. Then a route between the first spawns of every pair of flags,
which must exist. Exit code 0 when every flag spawn is on the main network and every flag pair is routed.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-spawn-map'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
import soldier1943  # noqa: E402
from bfbc2core import game1943, navdata, navpath, terrain  # noqa: E402


def check(hpd, level, game=None):
    """{'summary', 'spawns': [...], 'routes': [...], 'ok'} for a build of a level."""
    import spawnmap
    game = game1943.find_game(game)
    g = game1943.Game(game, level)
    hf_data = g.data(f'terrains/{level}/{level}.heightfield-000.res')
    navdata.Y_OFFSET = soldier1943.HEIGHT / 2          # BF1943 builds store nodes half the soldier's height up
    nav = navdata.load(hpd, terrain.read(hf_data) if hf_data else None)
    main = max(range(len(nav.islands)), key=lambda i: nav.islands[i].nodes) if nav.islands else -1
    m = spawnmap.spawn_map(game, level)
    spawns, firsts = [], {}
    for o in m['objectives']:
        for s in o['spawns']:
            if s.get('kind') != 'soldier':
                continue
            for k, p in enumerate([s['position']] + [a for a in s.get('alternates', []) if isinstance(a, list)]):
                n, d = navpath.join(nav, p)
                isl = None if n is None else int(nav.node_island[n])
                spawns.append(dict(objective=o['name'], role=o['role'], spawn=s['name'], point=k, position=p,
                                   island_nodes=None if isl is None else nav.islands[isl].nodes, main=isl == main,
                                   join_m=round(d, 1) if n is not None else None))
            if o['role'] == 'flag':
                firsts.setdefault(o['name'], s['position'])
    routes = []
    names = list(firsts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = navpath.route(nav, firsts[a], firsts[b])
            routes.append(dict(a=a, b=b, found=bool(r.found), cost=round(float(r.cost), 1) if r.found else None,
                               reason=None if r.found else str(r.reason)))
    flag_off = [s for s in spawns if s['role'] == 'flag' and not s['main']]
    unrouted = [r for r in routes if not r['found']]
    summary = dict(navdata.summary(nav), main_network_nodes=nav.islands[main].nodes if main >= 0 else 0,
                   spawn_points=len(spawns), flag_points_off_main=len(flag_off),
                   base_points_off_main=sum(1 for s in spawns if s['role'] == 'base' and not s['main']),
                   flag_pairs=len(routes), flag_pairs_routed=len(routes) - len(unrouted))
    return dict(level=level, hpd=os.path.abspath(hpd), summary=summary, spawns=spawns, routes=routes,
                ok=not flag_off and not unrouted)


def report(r):
    s = r['summary']
    lines = [f"{r['level']}: {s['nodes']} nodes, {s['edges']} edges, {s['islands']} islands, main network "
             f"{s['main_network_nodes']} nodes, {s['ladder links']} ladder links",
             f"spawn points: {s['spawn_points']}; flag points off the main network {s['flag_points_off_main']}; base points "
             f"off it {s['base_points_off_main']} (a carrier base is its own network)",
             f"flag pairs routed: {s['flag_pairs_routed']} of {s['flag_pairs']}"]
    off = {}                                            # (role, spawn, island size) -> points
    for sp in r['spawns']:
        if not sp['main']:
            off.setdefault((sp['role'], sp['spawn'], sp['island_nodes']), []).append(sp)
    for (role, spawn, nodes), pts in off.items():
        where = 'no node within reach' if nodes is None else f'an island of {nodes} nodes'
        at = f"point {pts[0]['point']} at {pts[0]['position']}" if len(pts) == 1 else f'{len(pts)} points'
        lines.append(f'  {role:4s} {spawn}, {at}: {where}')
    for rt in r['routes']:
        lines.append(f"  {rt['a']} -> {rt['b']}: " + (f"{rt['cost']} m" if rt['found'] else f"no route ({rt['reason']})"))
    lines.append('ok' if r['ok'] else 'FAILED')
    return '\n'.join(lines)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    opts = dict(zip(argv[1:], argv[2:]))
    if len(args) < 2:
        print(__doc__)
        return 1
    r = check(args[0], args[1], opts.get('--game'))
    print(report(r))
    if '--json' in opts:
        with open(opts['--json'], 'w', encoding='utf-8') as f:
            json.dump(r, f, indent=1)
    return 0 if r['ok'] else 1


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('routecheck1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
