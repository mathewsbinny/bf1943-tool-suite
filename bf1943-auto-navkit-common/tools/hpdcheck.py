"""NavKit: structural checks of a pathdata.hpd - what DICE's files always satisfy and the game relies on.

  hpdcheck.py <pathdata.hpd> [...]          exit code 0 when every file passes, 1 otherwise

Checks, per file:
  closed boundaries   every area's outline, portals and border portals meet in pairs at every end point (DICE: no
                      open area; an open one misleads point-in-area tests - navbuild.BRIDGE_UNION)
  edges               every edge's two node indices exist in its sector
  node order          each sector's nodes sorted by unsigned id, border nodes (id flag 80000000h) after the others
                      (the game binary-searches these arrays - navbuild.SORT_NODES_BY_ID)
  zero-length edges   no edge between two different nodes at one position (navbuild.DEDUP_GRAPH_NODES)
Every check holds on DICE's 15 files (13 campaign levels, ONS_MP_002 / 004; 2026-10-23). Not checked: the owner bit
of border nodes (field c bit 16) - DICE's files have border nodes with no owner copy and with two or more.
"""
import os
import sys
from collections import Counter

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

BORDER = 0x80000000


def check(path: str) -> dict[str, list[str]]:
    """{check name: [problem descriptions]} (empty lists: passed)."""
    hpd = hpdtool.parse(open(path, 'rb').read())
    out: dict[str, list[str]] = {'closed boundaries': [], 'edges': [], 'node order': [], 'zero-length edges': []}
    for sector in hpd.sectors:
        mesh = kymesh.parse_mesh(sector.mesh)
        for ai, area in enumerate(mesh.areas):
            ends: Counter[tuple[float, float]] = Counter()      # (x, z) of a segment end -> segments ending there
            segs = [(line[k], line[k + 1]) for line in area.outline for k in range(len(line) - 1)]
            segs += ([(portal.p0, portal.p1) for portal in area.portals]
                     + [(border.p0, border.p1) for border in area.border_portals])
            for p0, p1 in segs:
                ends[(round(p0[0], 2), round(p0[2], 2))] += 1
                ends[(round(p1[0], 2), round(p1[2], 2))] += 1
            odd = [point for point, count in ends.items() if count % 2]
            if odd:
                out['closed boundaries'].append(f'sector {sector.id:04x} area {ai}: {len(odd)} open end(s), '
                                                f'e.g. {odd[0]}')
        node_count = len(sector.nodes)
        for edge in sector.edges:
            if not (0 <= edge.src < node_count and 0 <= edge.dst < node_count):
                out['edges'].append(f'sector {sector.id:04x}: edge {edge.src} -> {edge.dst} of {node_count} nodes')
                continue
            src, dst = sector.nodes[edge.src], sector.nodes[edge.dst]
            if src.id != dst.id and tuple(src.pos) == tuple(dst.pos):
                out['zero-length edges'].append(f'sector {sector.id:04x}: nodes {src.id:08x} and {dst.id:08x} at '
                                                f'{tuple(round(v, 2) for v in src.pos)}')
        ids = [node.id & 0xFFFFFFFF for node in sector.nodes]
        key = [((node_id & BORDER) != 0, node_id) for node_id in ids]
        if key != sorted(key):
            out['node order'].append(f'sector {sector.id:04x}: nodes not sorted by id with border nodes last')
    return out


def main(argv: list[str]) -> int:
    if any(arg in ('-h', '--help') for arg in argv[1:]):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    bad = 0
    for path in argv[1:]:
        problems = check(path)
        fails = {name: found for name, found in problems.items() if found}
        bad += bool(fails)
        print(f'{"ok  " if not fails else "FAIL"} {os.path.basename(path)}' +
              ''.join(f'\n       {name}: {len(found)} - {found[0]}' for name, found in fails.items()))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
