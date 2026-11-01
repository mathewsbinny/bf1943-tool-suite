"""NavKit's builder (navbuild), detailed zones and merging: the second stage at pitch 0.2 over the detailed sectors,
merged with the normal build (merge_detailed, which runs the seam passes), and nodes at one position folded into one
(merge_coincident_border)."""
from collections import defaultdict
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from .config import DETAILED_PITCH, grid_at
from .inputs import column_sectors
from .areas import cross_grid_resolver, same_grid_resolver, sector_links
from .stages import stage
from .seams import seam_far_links, seam_fine_links, seam_gap_nodes

if TYPE_CHECKING:
    from .config import GraphPoint, Log, Points, SectorGraph, StageResult


def merge_detailed(normal: 'StageResult', detailed: dict[str, Any], xoff: int, zoff: int, seeds: 'Points | None',
                   common: dict[str, Any], log: 'Log') -> 'list[SectorGraph]':
    """The second pass: DICE's detailed-zone sectors built at pitch 0.2 from their own sampling run (the fine stage,
    "stage b"), merged with the normal build (normal, "stage a"). The fine stage is seeded from the normal stage's
    walkable ground in those sectors (ground joined to the rest of the level only through normal sectors would
    otherwise be lost) and continues its node ids. Shared nodes on the seams between a detailed and a normal sector
    come from the normal stage and are attached to the fine stage's ground (dropped from both sides where it has none
    there); neighbour links across those seams are found by position on the other grid. detailed: build()'s
    {'samples', 'high', 'sectors'}; common: stage()'s arguments, the build's Settings and BuildState among them.
    Returns the merged graphs (normal sectors from the normal stage, detailed ones from the fine stage)."""
    settings, state = common['settings'], common['state']
    detailed_set = set(detailed['sectors']) & normal['sectors']
    if not detailed_set:
        log('detailed pass: none of its sectors has ground in the normal build - nothing built at pitch 0.2')
        return normal['graphs']
    normal_level = normal['level']
    samples = normal_level.s
    owners = defaultdict(set)
    for sec, nodes in normal['border_nodes'].items():
        for nid, _pos, _k in nodes:
            owners[nid].add(sec)
    grid_min = (min(X for X, _ in normal['sectors']), min(Y for _, Y in normal['sectors']))
    kept = np.nonzero(normal_level.keep)[0]
    kept_sec = list(zip(*column_sectors(samples, normal_level.col_of[kept], xoff, zoff, settings, samples.pitch)))
    # a discarded attempt's order nodes and ladder counts must not stay: the retry reuses its node ids
    # (id_start), and an ordinary node with a stale order id was pruned as an order node (2026-10-31)
    order_ids, ladder_stats = set(state.order_ids), dict(state.ladder_stats)
    while True:
        state.order_ids, state.ladder_stats = set(order_ids), dict(ladder_stats)
        # seeds: the normal stage's kept ground in the detailed sectors
        seed_pts = []
        for k, sec in zip(kept, kept_sec):
            if sec in detailed_set:
                col = normal_level.col_of[k]
                x = samples.min_x + (col % samples.nx) * samples.pitch
                z = samples.min_z + (col // samples.nx) * samples.pitch
                seed_pts.append((x, float(normal_level.y[k]), z))
        # the normal stage's shared nodes on the seams between a detailed and a normal sector, per detailed sector
        carried = defaultdict(list)
        for sec in detailed_set:
            for nid, pos, _k in normal['border_nodes'].get(sec, ()):
                if owners[nid] - detailed_set:
                    carried[sec].append((nid, pos))
        n_carried = sum(len(sector_nodes) for sector_nodes in carried.values())
        log(f'detailed pass: {len(detailed_set)} sectors at pitch {DETAILED_PITCH}, {len(seed_pts)} seed points from '
            f'the normal build, {n_carried} shared nodes on mixed seams')
        fine_stage = stage(detailed['samples'], detailed.get('high'), xoff, zoff, seeds=seeds, **common,
                           grid=grid_at(DETAILED_PITCH, settings), sector_filter=detailed_set, grid_min=grid_min,
                           id_start=normal['next_id'], carried=carried, seed_points=seed_pts)
        missing = detailed_set - fine_stage['sectors']
        if not missing:
            break
        # a sector without ground at 0.2 stays at the normal pitch: its seams are then normal seams (with their
        # nodes and edges from the normal build) or mixed seams with the detailed sectors - so the pass is made
        # again without it, which carries those seams' nodes too (dropping them unlinked the sector, 2026-10-30)
        log(f'detailed pass: {len(missing)} sectors without ground at pitch {DETAILED_PITCH} kept at the normal pitch: '
            + ', '.join(f'{(X & 0xFF) << 8 | (Y & 0xFF):04x}' for X, Y in sorted(missing)) + ' - pass made again')
        detailed_set -= missing
        if not detailed_set:
            log('detailed pass: no sector left at pitch 0.2 - the normal build is kept')
            return normal['graphs']
    detailed_set = fine_stage['sectors']
    drop = fine_stage['dropped']
    log(f'detailed pass: {len(drop)} mixed-seam shared nodes without ground at pitch {DETAILED_PITCH} dropped')
    # links across mixed seams: the fine stage's sectors to their normal neighbours, the normal sectors to their
    # detailed ones
    normal_graphs = {sec: graph for sec, graph in zip(sorted(normal['sectors']), normal['graphs'])
                     if sec not in detailed_set}
    detailed_graphs = {sec: graph for sec, graph in zip(sorted(fine_stage['sectors']), fine_stage['graphs'])}
    normal_view = dict(normal, info={sector: sector_info for sector, sector_info in normal['info'].items()
                                     if sector not in detailed_set})
    # the normal stage's links between its own normal sectors are found again here with the detailed grid's pitch in
    # the step rule (the normal stage's surfaces, the fine stage's grid): until 2026-10-31 this ran while
    # set_pitch(DETAILED_PITCH) was in force, and the refactor kept the builds byte-identical. The 0.8 m grid
    # (normal_level itself) is likely what was meant: to check
    normal_links_level = normal_level.on_grid(fine_stage['level'].grid)
    for here, there, mine in ((fine_stage, normal_view, detailed_graphs), (normal, fine_stage, normal_graphs)):
        for (X, Y), graph in mine.items():
            areas, mesh = here['info'][(X, Y)]['areas'], graph[2]
            mine_info = {sector: sector_info for sector, sector_info in here['info'].items() if sector in mine}
            same_resolve = same_grid_resolver(fine_stage['level'] if here is fine_stage else normal_links_level,
                                              mine_info, X, Y)
            cross_resolve = cross_grid_resolver(here, there, X, Y)
            # a neighbour on this sector's own grid (mine), else across the seam on the other grid
            mesh.links = sector_links(X, Y, areas, mesh.areas,
                                      lambda other, area, border, same=same_resolve, cross=cross_resolve:
                                      same(other, area, border) if other in mine else cross(other, area, border))
    merged = []
    for sec in sorted(set(normal_graphs) | set(detailed_graphs)):
        sid, bounds, mesh, pts, pairs, origin = normal_graphs[sec] if sec in normal_graphs else detailed_graphs[sec]
        if drop and sec in normal_graphs:
            # a normal sector: drop the mixed-seam shared nodes the fine stage had no ground for
            gone = {index for index, point in enumerate(pts) if point[0] in drop}
            pairs = [pair for pair in pairs if pair[0] not in gone and pair[1] not in gone]
        merged.append((sid, bounds, mesh, pts, pairs, origin))
    if settings.seam_gap_nodes:
        by_sec = dict(zip(sorted(set(normal_graphs) | set(detailed_graphs)), merged))
        added, runs, one_side = seam_gap_nodes(normal, fine_stage, by_sec, set(detailed_graphs), xoff, zoff, state)
        log(f'detailed pass: {added} seam gap nodes on mixed seams ({runs} stretches of shared ground with no linked '
            f'shared node; {one_side} joined on one side only)')
    if settings.seam_fine_links:
        by_sec = dict(zip(sorted(set(normal_graphs) | set(detailed_graphs)), merged))
        n_links = seam_fine_links(normal, fine_stage, by_sec, set(detailed_graphs), xoff, zoff, state)
        log(f'detailed pass: {n_links} fine-resolved link(s) in normal sectors beside mixed seams')
        for end_a, end_b in (state.fine_links[-n_links:] if n_links else []):
            log(f'  fine link {end_a} - {end_b}')
    if settings.seam_far_links:
        by_sec = dict(zip(sorted(set(normal_graphs) | set(detailed_graphs)), merged))
        n_far = seam_far_links(normal, fine_stage, by_sec, set(detailed_graphs), xoff, zoff, state)
        log(f'detailed pass: {n_far} fine-resolved link(s) two 0.8 m columns apart beside mixed seams')
        for end_a, end_b in (state.far_links[-n_far:] if n_far else []):
            log(f'  far link {end_a} - {end_b}')
    log(f'merged: {len(normal_graphs)} normal + {len(detailed_graphs)} detailed sectors')
    return merged


def merge_coincident_border(graphs: 'list[SectorGraph]', log: 'Log' = print) -> 'list[SectorGraph]':
    """Nodes at one position in one sector (with BORDER_SEAM_LINE, a seam-line node and a node carried across a
    mixed-pitch seam onto a nearby surface: zero-length edges, which hpdcheck refuses): border nodes there become one
    id, the lowest, in every sector, and an ordinary node there becomes that border node. Returns the graphs."""
    parent: dict[int, int] = {}
    spot_of = lambda point: tuple(round(float(coord), 3) for coord in point[1])

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for _sid, _b, _m, pts, _pairs, _o in graphs:
        first_at = {}
        for point in pts:
            if point[0] & 0x80000000:
                if spot_of(point) in first_at and find(first_at[spot_of(point)]) != find(point[0]):
                    lo, hi = sorted((find(first_at[spot_of(point)]), find(point[0])))
                    parent[hi] = lo
                first_at.setdefault(spot_of(point), point[0])
    out, folded = [], 0
    for sid, bounds, mesh, pts, pairs, origin in graphs:
        border_at = {}
        for entry, point in enumerate(pts):
            if point[0] & 0x80000000:
                border_at.setdefault(spot_of(point), entry)
        ids = []
        for _entry, point in enumerate(pts):
            if point[0] & 0x80000000:
                ids.append(find(point[0]))
            elif spot_of(point) in border_at:
                ids.append(find(pts[border_at[spot_of(point)]][0]))
                folded += 1
            else:
                ids.append(point[0])
        kept = {}                                    # id -> the entry kept (a border entry where there is one)
        for entry, nid in enumerate(ids):
            if nid not in kept or (pts[entry][0] & 0x80000000 and not pts[kept[nid]][0] & 0x80000000):
                kept[nid] = entry
        order = list(dict.fromkeys(ids))
        index = {nid: position for position, nid in enumerate(order)}
        new_pts = [cast('GraphPoint', (nid,) + tuple(pts[kept[nid]][1:])) for nid in order]
        seen, new_pairs = set(), []
        for node_a, node_b, dist in pairs:
            u, coord = index[ids[node_a]], index[ids[node_b]]
            if u != coord and (min(u, coord), max(u, coord)) not in seen:
                seen.add((min(u, coord), max(u, coord)))
                new_pairs.append((u, coord, dist))
        out.append((sid, bounds, mesh, new_pts, new_pairs, origin))
    if parent or folded:
        log(f'nodes at one position merged: {len(parent)} border id(s), {folded} ordinary node(s)')
    return out
