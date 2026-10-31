"""NavKit's builder (navbuild), detailed zones and merging: the second stage at pitch 0.2 over the detailed sectors,
merged with the normal build (merge_detailed, which runs the seam passes), and nodes at one position folded into one
(merge_coincident_border)."""
from collections import defaultdict

import numpy as np

from .config import DETAILED_PITCH, grid_at
from .inputs import column_sectors
from .areas import cross_grid_resolver, same_grid_resolver, sector_links
from .stages import stage
from .seams import seam_far_links, seam_fine_links, seam_gap_nodes


def merge_detailed(a, detailed, xoff, zoff, seeds, common, log):
    """The second pass: DICE's detailed-zone sectors built at pitch 0.2 from their own sampling run (stage b),
    merged with the normal build a. Stage b is seeded from a's walkable ground in those sectors (ground joined to the
    rest of the level only through normal sectors would otherwise be lost) and continues a's node ids. Shared nodes
    on the seams between a detailed and a normal sector come from a and are attached to b's ground (dropped from
    both sides where b has none there); neighbour links across those seams are found by position on the other grid.
    common: stage()'s arguments, the build's Settings and BuildState among them.
    Returns the merged graphs (normal sectors from a, detailed from b)."""
    settings, state = common['settings'], common['state']
    dset = set(detailed['sectors']) & a['sectors']
    if not dset:
        log('detailed pass: none of its sectors has ground in the normal build - nothing built at pitch 0.2')
        return a['graphs']
    alv = a['level']
    sa = alv.s
    owners = defaultdict(set)
    for sec, nodes in a['border_nodes'].items():
        for nid, _pos, _k in nodes:
            owners[nid].add(sec)
    grid_min = (min(X for X, _ in a['sectors']), min(Y for _, Y in a['sectors']))
    kept = np.nonzero(alv.keep)[0]
    kept_sec = list(zip(*column_sectors(sa, alv.col_of[kept], xoff, zoff, settings, sa.pitch)))
    # a discarded attempt's order nodes and ladder counts must not stay: the retry reuses its node ids
    # (id_start), and an ordinary node with a stale order id was pruned as an order node (2026-10-31)
    order_ids, ladder_stats = set(state.order_ids), dict(state.ladder_stats)
    while True:
        state.order_ids, state.ladder_stats = set(order_ids), dict(ladder_stats)
        # seeds: a's kept ground in the detailed sectors
        pts = []
        for k, sec in zip(kept, kept_sec):
            if sec in dset:
                c = alv.col_of[k]
                x, z = sa.min_x + (c % sa.nx) * sa.pitch, sa.min_z + (c // sa.nx) * sa.pitch
                pts.append((x, float(alv.y[k]), z))
        # a's shared nodes on the seams between a detailed and a normal sector, per detailed sector
        carried = defaultdict(list)
        for sec in dset:
            for nid, pos, _k in a['border_nodes'].get(sec, ()):
                if owners[nid] - dset:
                    carried[sec].append((nid, pos))
        log(f'detailed pass: {len(dset)} sectors at pitch {DETAILED_PITCH}, {len(pts)} seed points from the normal '
            f'build, {sum(len(v) for v in carried.values())} shared nodes on mixed seams')
        b = stage(detailed['samples'], detailed.get('high'), xoff, zoff, seeds=seeds, **common,
                  grid=grid_at(DETAILED_PITCH, settings), sector_filter=dset, grid_min=grid_min, id_start=a['next_id'],
                  carried=carried, seed_points=pts)
        missing = dset - b['sectors']
        if not missing:
            break
        # a sector without ground at 0.2 stays at the normal pitch: its seams are then normal seams (with their
        # nodes and edges from the normal build) or mixed seams with the detailed sectors - so the pass is made
        # again without it, which carries those seams' nodes too (dropping them unlinked the sector, 2026-10-30)
        log(f'detailed pass: {len(missing)} sectors without ground at pitch {DETAILED_PITCH} kept at the normal pitch: '
            + ', '.join(f'{(X & 0xFF) << 8 | (Y & 0xFF):04x}' for X, Y in sorted(missing)) + ' - pass made again')
        dset -= missing
        if not dset:
            log('detailed pass: no sector left at pitch 0.2 - the normal build is kept')
            return a['graphs']
    dset = b['sectors']
    drop = b['dropped']
    log(f'detailed pass: {len(drop)} mixed-seam shared nodes without ground at pitch {DETAILED_PITCH} dropped')
    # links across mixed seams: b's sectors to a's normal neighbours, a's normal sectors to b's detailed ones
    a_norm = {sec: g for sec, g in zip(sorted(a['sectors']), a['graphs']) if sec not in dset}
    b_det = {sec: g for sec, g in zip(sorted(b['sectors']), b['graphs'])}
    a_view = dict(a, info={s_: i for s_, i in a['info'].items() if s_ not in dset})
    # a's links between its own normal sectors are found again here with the detailed grid's pitch in the step rule
    # (a's surfaces, b's grid): until 2026-10-31 this ran while set_pitch(DETAILED_PITCH) was in force, and the
    # refactor kept the builds byte-identical. The 0.8 m grid (alv itself) is likely what was meant: to check
    a_links_level = alv.on_grid(b['level'].grid)
    for here, there, mine in ((b, a_view, b_det), (a, b, a_norm)):
        for (X, Y), g in mine.items():
            areas, mesh = here['info'][(X, Y)]['areas'], g[2]
            same = same_grid_resolver(b['level'] if here is b else a_links_level,
                                      {s_: i for s_, i in here['info'].items() if s_ in mine}, X, Y)
            cross = cross_grid_resolver(here, there, X, Y)
            mesh.links = sector_links(X, Y, areas, mesh.areas,
                                      lambda other, ar, bd, s=same, c=cross: s(other, ar, bd) if other in mine else c(other, ar, bd))
    merged = []
    for sec in sorted(set(a_norm) | set(b_det)):
        sid, bnd, mesh, pts_, pairs, origin = a_norm[sec] if sec in a_norm else b_det[sec]
        if drop and sec in a_norm:
            # a's normal sector: drop the mixed-seam shared nodes stage b had no ground for
            gone = {q for q, p in enumerate(pts_) if p[0] in drop}
            pairs = [p for p in pairs if p[0] not in gone and p[1] not in gone]
        merged.append((sid, bnd, mesh, pts_, pairs, origin))
    if settings.seam_gap_nodes:
        by_sec = dict(zip(sorted(set(a_norm) | set(b_det)), merged))
        added, runs, one_side = seam_gap_nodes(a, b, by_sec, set(b_det), xoff, zoff, state)
        log(f'detailed pass: {added} seam gap nodes on mixed seams ({runs} stretches of shared ground with no linked '
            f'shared node; {one_side} joined on one side only)')
    if settings.seam_fine_links:
        by_sec = dict(zip(sorted(set(a_norm) | set(b_det)), merged))
        n_links = seam_fine_links(a, b, by_sec, set(b_det), xoff, zoff, state)
        log(f'detailed pass: {n_links} fine-resolved link(s) in normal sectors beside mixed seams')
        for p, q in (state.fine_links[-n_links:] if n_links else []):
            log(f'  fine link {p} - {q}')
    if settings.seam_far_links:
        by_sec = dict(zip(sorted(set(a_norm) | set(b_det)), merged))
        n_far = seam_far_links(a, b, by_sec, set(b_det), xoff, zoff, state)
        log(f'detailed pass: {n_far} fine-resolved link(s) two 0.8 m columns apart beside mixed seams')
        for p, q in (state.far_links[-n_far:] if n_far else []):
            log(f'  far link {p} - {q}')
    log(f'merged: {len(a_norm)} normal + {len(b_det)} detailed sectors')
    return merged


def merge_coincident_border(graphs, log=print):
    """Nodes at one position in one sector (with BORDER_SEAM_LINE, a seam-line node and a node carried across a
    mixed-pitch seam onto a nearby surface: zero-length edges, which hpdcheck refuses): border nodes there become one
    id, the lowest, in every sector, and an ordinary node there becomes that border node. Returns the graphs."""
    parent = {}
    key = lambda p: tuple(round(float(v), 3) for v in p[1])

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for _sid, _b, _m, pts, _pairs, _o in graphs:
        at = {}
        for p in pts:
            if p[0] & 0x80000000:
                if key(p) in at and find(at[key(p)]) != find(p[0]):
                    lo, hi = sorted((find(at[key(p)]), find(p[0])))
                    parent[hi] = lo
                at.setdefault(key(p), p[0])
    out, folded = [], 0
    for sid, bnd, mesh, pts, pairs, origin in graphs:
        border_at = {}
        for q, p in enumerate(pts):
            if p[0] & 0x80000000:
                border_at.setdefault(key(p), q)
        ids = []
        for _q, p in enumerate(pts):
            if p[0] & 0x80000000:
                ids.append(find(p[0]))
            elif key(p) in border_at:
                ids.append(find(pts[border_at[key(p)]][0]))
                folded += 1
            else:
                ids.append(p[0])
        rep = {}                                    # id -> the entry kept (a border entry where there is one)
        for q, nid in enumerate(ids):
            if nid not in rep or (pts[q][0] & 0x80000000 and not pts[rep[nid]][0] & 0x80000000):
                rep[nid] = q
        order = list(dict.fromkeys(ids))
        index = {nid: n for n, nid in enumerate(order)}
        new_pts = [(nid,) + tuple(pts[rep[nid]][1:]) for nid in order]
        seen, new_pairs = set(), []
        for a_, b_, d in pairs:
            u, v = index[ids[a_]], index[ids[b_]]
            if u != v and (min(u, v), max(u, v)) not in seen:
                seen.add((min(u, v), max(u, v)))
                new_pairs.append((u, v, d))
        out.append((sid, bnd, mesh, new_pts, new_pairs, origin))
    if parent or folded:
        log(f'nodes at one position merged: {len(parent)} border id(s), {folded} ordinary node(s)')
    return out
