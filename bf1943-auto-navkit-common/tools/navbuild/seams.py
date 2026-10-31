"""NavKit's builder (navbuild), links across mixed-pitch seams (a 0.8 m sector beside a 0.2 m one), made after the
two stages are merged: gap nodes where both grids' ground meets the seam with no shared node (seam_gap_nodes), and
walk edges in the 0.8 m sector where the 0.2 m samples join its pieces (seam_fine_links, seam_far_links). Which run is
the build's Settings' (seam_gap_nodes, seam_fine_links, seam_far_links)."""
import math
from collections import defaultdict, deque

from .config import CARRY_RADIUS, DETAILED_PITCH, DIST_EDGE_MAX, JoinScope
from .graph import join_groups, line_walkable


SEAM_FINE_BAND = 2.0            # m: seam_fine_links looks this far into the normal sector from a mixed seam


def fine_columns(sa, sb, gi, gj, pa, pb):
    """The columns of the fine grid sb (pitch pb) whose centres lie in the coarse grid sa's cell (gi, gj) (pitch pa):
    [c - pa/2, c + pa/2) on each axis, (pa / pb)^2 of them, each once. Both seam passes use it (2026-10-30). Before,
    seam_fine_links rounded the points c + (u + 0.5) x pb to the fine grid; those fall on halves, and round-half-even
    took some columns twice and left others out (seam_far_links found it on Iwo Jima's slope, 2026-10-29)."""
    cx, cz = sa.min_x + gi * pa, sa.min_z + gj * pa
    fi0 = int(math.ceil((cx - pa / 2 - sb.min_x) / pb - 1e-6))
    fj0 = int(math.ceil((cz - pa / 2 - sb.min_z) / pb - 1e-6))
    n = int(round(pa / pb))
    return [(fi0 + u, fj0 + v) for u in range(n) for v in range(n)]


def seam_fine_links(a, b, by_sec, b_secs, xoff, zoff, state):
    """SEAM_FINE_LINKS: in a normal (0.8 m) sector beside a detailed one, ground the 0.2 m samples join but the 0.8 m
    step rule does not - a lip the coarse grid cannot resolve (Coral Sea 0b0b: a strip at 138.11 and the ground at
    137.58, 0.53 m apart at 0.8 m, joined at 0.2 m by a slope 0.6-1.4 m past the seam; the strip's 0.2 m side was
    closed, the last seam break, 2026-10-26). Within SEAM_FINE_BAND of the seam, the 0.8 m kept surfaces fall into
    pieces under the 0.8 m step rule and the 0.2 m kept surfaces into pieces under the 0.2 m one; where one 0.2 m piece
    stands on two 0.8 m pieces in side-by-side columns, the two 0.8 m surfaces get a node each and a walk edge between
    them in the normal sector's graph (once per pair of pieces). by_sec: {sector: merged graph tuple}, changed in place.
    Each level's step rule is its own grid's (la 0.8 m, lb 0.2 m). Returns the links made (also in state.fine_links)."""
    la, lb = a['level'], b['level']
    sa, sb = la.s, lb.s
    pa, pb = a['pitch'], DETAILED_PITCH
    yoff = la.settings.y_offset
    made = 0
    next_id = [b['next_id'] + 500000]

    def new_id():
        next_id[0] += 1
        return next_id[0]
    for S in sorted(b_secs):
        for dX, dY in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            T = (S[0] + dX, S[1] + dY)
            if T not in by_sec or T in b_secs or T not in a['info']:
                continue
            info = a['info'][T]
            own = info['area_of']
            gi0, gj0, ox, oz = info['origin']
            x0, z0 = (S[0] + xoff) * 100.0, (S[1] + zoff) * 100.0
            seam = (x0 + (100.0 if dX > 0 else 0.0)) if dX else (z0 + (100.0 if dY > 0 else 0.0))
            # the 0.8 m surfaces of T in the band, in pieces under the 0.8 m step rule
            cols = set()
            for t in range(int(100 / pa) + 1):
                for q in range(int(SEAM_FINE_BAND / pa) + 1):
                    x = (seam + dX * q * pa) if dX else (x0 + t * pa)
                    z = (z0 + t * pa) if dX else (seam + dY * q * pa)
                    cols.add((int(round((x - sa.min_x) / pa)), int(round((z - sa.min_z) / pa))))
            coarse = {k: (gi, gj) for gi, gj in cols for k in la.surfaces(gi, gj) if k in own}
            piece = {}
            for k0 in coarse:
                if k0 in piece:
                    continue
                piece[k0] = k0
                q_ = deque([k0])
                while q_:
                    k = q_.popleft()
                    gi, gj = coarse[k]
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            if di or dj:
                                n = la.neighbour(k, gi, gj, di, dj)
                                if n in coarse and n not in piece:
                                    piece[n] = k0
                                    q_.append(n)
            if len(set(piece.values())) < 2:
                continue
            # the 0.2 m surfaces under them, in pieces under the 0.2 m step rule; each mapped to the 0.8 m surface
            # of its column within 0.4 m of its height
            fine = {}
            for k, (gi, gj) in coarse.items():
                for fi, fj in fine_columns(sa, sb, gi, gj, pa, pb):
                    for f in lb.surfaces(fi, fj):
                        if abs(float(lb.y[f]) - float(la.y[k])) <= 0.4:
                            fine.setdefault(f, (fi, fj, k))
            seen = {}
            for f0 in fine:
                if f0 in seen:
                    continue
                seen[f0] = f0
                q_ = deque([f0])
                while q_:
                    f = q_.popleft()
                    fi, fj, _k = fine[f]
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            if di or dj:
                                n = lb.neighbour(f, fi, fj, di, dj)
                                if n in fine and n not in seen:
                                    seen[n] = f0
                                    q_.append(n)
            # a fine piece on two coarse pieces: link a side-by-side pair of their surfaces, once per pair
            touched = defaultdict(set)
            for f, root in seen.items():
                touched[root].add(fine[f][2])
            done = set()
            pts, pairs = by_sec[T][3], by_sec[T][4]
            for _root, ks in sorted(touched.items()):
                for k1 in sorted(ks):
                    for k2 in sorted(ks):
                        p1, p2 = piece[k1], piece[k2]
                        if p1 >= p2 or (p1, p2) in done:
                            continue
                        (g1, h1), (g2, h2) = coarse[k1], coarse[k2]
                        if max(abs(g1 - g2), abs(h1 - h2)) != 1:
                            continue
                        done.add((p1, p2))
                        ends = []
                        for k, (gi, gj) in ((k1, coarse[k1]), (k2, coarse[k2])):
                            idx = next((q for q, p in enumerate(pts) if p[2] == k), None)
                            if idx is None:
                                i, j = gi - gi0, gj - gj0
                                pos = (ox + i * pa, float(la.y[k]) + yoff, oz + j * pa)
                                pts.append((new_id(), pos, k, i, j))
                                idx = len(pts) - 1
                                for q, p in enumerate(pts[:-1]):
                                    d = math.dist(pos, p[1])
                                    if d <= DIST_EDGE_MAX and line_walkable(la, k, (i, j), (p[3], p[4]), gi0, gj0, p[2]):
                                        pairs.append((idx, q, d))
                            ends.append(idx)
                        pairs.append((ends[0], ends[1], math.dist(pts[ends[0]][1], pts[ends[1]][1])))
                        state.fine_links.append(tuple(tuple(round(v, 2) for v in pts[e][1]) for e in ends))
                        made += 1
    return made


def seam_far_links(a, b, by_sec, b_secs, xoff, zoff, state):
    """SEAM_FAR_LINKS: SEAM_FINE_LINKS for 0.8 m pieces two columns apart (2026-10-29). Iwo Jima 0b0d: a strip of four
    0.8 m surfaces 0.8-2.4 m past the seam and the main network's area beside it, joined by a 30-degree slope of 0.2 m
    kept ground (steps of 0.20 m at most), with no 0.8 m surface kept in the columns between (their one 0.8 m sample
    lies on a steep facet), so no two side-by-side columns ever met and the strip stayed an island of 3 nodes.
    Within SEAM_FINE_BAND of the seam and one column past it, the 0.8 m kept surfaces fall into pieces under the 0.8 m
    step rule; the 0.2 m kept surfaces under those columns (within 0.4 m of the 0.8 m surface) and under the columns
    with no 0.8 m surface (all of them) fall into pieces under the 0.2 m step rule. Two 0.8 m surfaces of different
    pieces, two columns apart (one of them in the band), that one 0.2 m piece stands on get a walk edge when the build's
    own line rule (line_walkable) walks the 0.2 m samples from one to the other; once per pair of pieces. The side-by-
    side pairs stay SEAM_FINE_LINKS' (it runs first, unchanged). by_sec: {sector: merged graph tuple}, changed in place.
    Each level's step rule is its own grid's. Returns the links made (also in state.far_links)."""
    la, lb = a['level'], b['level']
    sa, sb = la.s, lb.s
    pa, pb = a['pitch'], DETAILED_PITCH
    yoff = la.settings.y_offset
    made = 0
    next_id = [b['next_id'] + 600000]

    def new_id():
        next_id[0] += 1
        return next_id[0]

    def under(gi, gj):
        return fine_columns(sa, sb, gi, gj, pa, pb)
    for S in sorted(b_secs):
        for dX, dY in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            T = (S[0] + dX, S[1] + dY)
            if T not in by_sec or T in b_secs or T not in a['info']:
                continue
            info = a['info'][T]
            own = info['area_of']
            gi0, gj0, ox, oz = info['origin']
            x0, z0 = (S[0] + xoff) * 100.0, (S[1] + zoff) * 100.0
            seam = (x0 + (100.0 if dX > 0 else 0.0)) if dX else (z0 + (100.0 if dY > 0 else 0.0))
            nb = int(SEAM_FINE_BAND / pa)
            cols, band = set(), set()
            for t in range(int(100 / pa) + 1):
                for q in range(nb + 2):
                    x = (seam + dX * q * pa) if dX else (x0 + t * pa)
                    z = (z0 + t * pa) if dX else (seam + dY * q * pa)
                    c_ = (int(round((x - sa.min_x) / pa)), int(round((z - sa.min_z) / pa)))
                    cols.add(c_)
                    if q <= nb:
                        band.add(c_)
            coarse = {k: (gi, gj) for gi, gj in cols for k in la.surfaces(gi, gj) if k in own}
            piece = {}
            for k0 in coarse:
                if k0 in piece:
                    continue
                piece[k0] = k0
                q_ = deque([k0])
                while q_:
                    k = q_.popleft()
                    gi, gj = coarse[k]
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            if di or dj:
                                n = la.neighbour(k, gi, gj, di, dj)
                                if n in coarse and n not in piece:
                                    piece[n] = k0
                                    q_.append(n)
            if len(set(piece.values())) < 2:
                continue
            fine = {}
            for k, (gi, gj) in coarse.items():
                for fi, fj in under(gi, gj):
                    for f in lb.surfaces(fi, fj):
                        if abs(float(lb.y[f]) - float(la.y[k])) <= 0.4:
                            fine.setdefault(f, (fi, fj, k))
            # every other 0.2 m kept surface under the columns carries the flood but no 0.8 m surface: those under
            # a column with no 0.8 m surface, and those further than 0.4 m from it (on a 30-degree slope a 0.8 m
            # column spans 0.46 m of rise, so the 0.4 m match alone breaks the slope into pieces)
            for gi, gj in sorted(cols):
                for fi, fj in under(gi, gj):
                    for f in lb.surfaces(fi, fj):
                        fine.setdefault(f, (fi, fj, None))
            seen = {}
            for f0 in fine:
                if f0 in seen:
                    continue
                seen[f0] = f0
                q_ = deque([f0])
                while q_:
                    f = q_.popleft()
                    fi, fj, _k = fine[f]
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            if di or dj:
                                n = lb.neighbour(f, fi, fj, di, dj)
                                if n in fine and n not in seen:
                                    seen[n] = f0
                                    q_.append(n)
            # per fine piece, per coarse surface it stands on: the fine surface nearest that column's centre
            touched = defaultdict(dict)
            for f, root in seen.items():
                fi, fj, k = fine[f]
                if k is None:
                    continue
                gi, gj = coarse[k]
                d = (sb.min_x + fi * pb - sa.min_x - gi * pa) ** 2 + (sb.min_z + fj * pb - sa.min_z - gj * pa) ** 2
                if k not in touched[root] or d < touched[root][k][1]:
                    touched[root][k] = (f, d)
            done = set()
            pts, pairs = by_sec[T][3], by_sec[T][4]
            for _root, ks in sorted(touched.items()):
                for k1 in sorted(ks):
                    for k2 in sorted(ks):
                        p1, p2 = piece[k1], piece[k2]
                        if p1 >= p2 or (p1, p2) in done:
                            continue
                        (g1, h1), (g2, h2) = coarse[k1], coarse[k2]
                        if max(abs(g1 - g2), abs(h1 - h2)) != 2 or (coarse[k1] not in band and coarse[k2] not in band):
                            continue
                        f1, f2 = ks[k1][0], ks[k2][0]
                        ok = line_walkable(lb, f1, fine[f1][:2], fine[f2][:2], 0, 0, f2)
                        if not ok:
                            continue
                        done.add((p1, p2))
                        ends = []
                        for k, (gi, gj) in ((k1, coarse[k1]), (k2, coarse[k2])):
                            idx = next((q for q, p in enumerate(pts) if p[2] == k), None)
                            if idx is None:
                                i, j = gi - gi0, gj - gj0
                                pos = (ox + i * pa, float(la.y[k]) + yoff, oz + j * pa)
                                pts.append((new_id(), pos, k, i, j))
                                idx = len(pts) - 1
                                for q, p in enumerate(pts[:-1]):
                                    d = math.dist(pos, p[1])
                                    if d <= DIST_EDGE_MAX and line_walkable(la, k, (i, j), (p[3], p[4]), gi0, gj0, p[2]):
                                        pairs.append((idx, q, d))
                            ends.append(idx)
                        pairs.append((ends[0], ends[1], math.dist(pts[ends[0]][1], pts[ends[1]][1])))
                        state.far_links.append(tuple(tuple(round(v, 2) for v in pts[e][1]) for e in ends))
                        made += 1
    return made


SEAM_REACH = 1.0                # seam_gap_nodes: each grid's ground for a gap node lies within this of the seam (m)


def seam_gap_nodes(a, b, by_sec, b_secs, xoff, zoff, state):
    """SEAM_GAP_NODES: the shared nodes of a mixed-pitch seam come from the normal build (one per BORDER_NODE_SPACING
    of its ground along the seam) and are carried to the detailed one; detailed ground at the seam that no carried node
    reaches - a 0.2 m opening the 0.8 m run places no node near, a carried node dropped for want of ground within
    CARRY_RADIUS, or a copy that joined no node of its sector - was not joined across the seam at all (Wake Island's
    Japanese carrier, Coral Sea's islets: 2026-10-24). Along every mixed seam, each stretch where both grids have
    kept ground of their own sector within SEAM_REACH of the seam, within 0.6 m of each other's height, and no shared
    node with edges in both sectors lies within CARRY_RADIUS + 0.8 m of it (and 0.6 m of its height), gets one shared
    node on the seam (the stretch's middle first), joined into both
    sector graphs by walkable lines (join_groups when none reaches). by_sec: {sector: merged graph tuple}, changed in
    place. Returns (nodes added, stretches found, nodes that joined only the detailed side)."""
    la, lb = a['level'], b['level']
    yoff, fine_step = lb.settings.y_offset, lb.settings.detailed_link_step
    next_id = [b['next_id']]

    def new_id():
        next_id[0] += 1
        return next_id[0]

    def linked_shared(sec):
        pts, pairs = by_sec[sec][3], by_sec[sec][4]
        has = set()
        for u, v, _d in pairs:
            has.update((u, v))
        return {pts[q][0]: pts[q][1] for q in has if pts[q][0] & 0x80000000}

    def ground(level, info, sec, x, z, inward, step):
        """Kept surfaces of the sector's own areas at the column nearest the seam (within SEAM_REACH inward)."""
        s = level.s
        own = info[sec]['area_of']
        for q in range(int(SEAM_REACH / step + 1e-6) + 1):
            px, pz = x + inward[0] * q * step, z + inward[1] * q * step
            gi, gj = int(round((px - s.min_x) / step)), int(round((pz - s.min_z) / step))
            ks = [k for k in level.surfaces(gi, gj) if k in own]
            if ks:
                return [(k, gi, gj) for k in ks]
        return []

    def attach(level, info, sec, nid, pos, k, gi, gj):
        """The shared node on surface k of the sector's graph, linked by walkable lines (on the level's own grid)."""
        gi0, gj0, ox, oz = info[sec]['origin']
        pts, pairs = by_sec[sec][3], by_sec[sec][4]
        if any(p[2] == k for p in pts):                     # one node per surface
            return False
        i, j = gi - gi0, gj - gj0
        pts.append((nid, pos, k, i, j))
        me = len(pts) - 1
        for q, p in enumerate(pts[:-1]):
            d = math.dist(pos, p[1])
            if d <= DIST_EDGE_MAX and line_walkable(level, k, (i, j), (p[3], p[4]), gi0, gj0, p[2]):
                pairs.append((me, q, d))
        if not any(me in (u, v) for u, v, _d in pairs):
            join = JoinScope(sec, set(info), info[sec].get('win'), state.detailed_sectors)
            join_groups(level, info[sec]['areas'], pts, pairs, gi0, gj0, ox, oz, new_id, join)
        return any(me in (u, v) for u, v, _d in pairs)

    added = runs_found = one_side = 0
    pa, pb = a['pitch'], DETAILED_PITCH
    for S in sorted(b_secs):
        for dX, dY in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            T = (S[0] + dX, S[1] + dY)
            if T not in by_sec or T in b_secs or T not in a['info']:
                continue
            x0, z0 = (S[0] + xoff) * 100.0, (S[1] + zoff) * 100.0
            # the seam line, the direction along it, and inward (into S, into T)
            if dX:
                xs = x0 + (100.0 if dX > 0 else 0.0)
                along = [(xs, z0 + t * pb) for t in range(int(100 / pb))]
            else:
                zs = z0 + (100.0 if dY > 0 else 0.0)
                along = [(x0 + t * pb, zs) for t in range(int(100 / pb))]
            into_s, into_t = (-dX, -dY), (dX, dY)
            shared = {nid: pos for nid, pos in linked_shared(S).items() if nid in linked_shared(T)}
            runs, active = [], []
            for t, (x, z) in enumerate(along):
                gb = ground(lb, b['info'], S, x, z, into_s, pb)
                ga = ground(la, a['info'], T, x, z, into_t, pa)
                nxt = []
                for k, gi, gj in gb:
                    yb = float(lb.y[k])
                    ka = [m for m in ga if abs(float(la.y[m[0]]) - yb) <= 0.6]
                    if not ka:
                        continue
                    m = min(ka, key=lambda m: abs(float(la.y[m[0]]) - yb))
                    item = (t, x, z, yb, (k, gi, gj), m)
                    for r in active:
                        if r[-1][0] == t - 1 and abs(r[-1][3] - yb) <= fine_step:
                            r.append(item)
                            nxt.append(r)
                            active.remove(r)
                            break
                    else:
                        r = [item]
                        runs.append(r)
                        nxt.append(r)
                active = nxt
            for r in runs:
                covered = any(min(math.hypot(p[0] - it[1], p[2] - it[2]) for it in r) <= CARRY_RADIUS + 0.8 and
                              min(abs(p[1] - yoff - it[3]) for it in r) <= 0.6 for p in shared.values())
                if covered:
                    continue
                runs_found += 1
                mid = len(r) // 2
                for it in sorted(r, key=lambda it: abs(it[0] - r[mid][0])):
                    _t, x, z, yb, (kb, gib, gjb), (ka, gia, gja) = it
                    nid = 0x80000000 | new_id()
                    pos = (x, yb + yoff, z)
                    if not attach(lb, b['info'], S, nid, pos, kb, gib, gjb):
                        continue
                    if attach(la, a['info'], T, nid, pos, ka, gia, gja):
                        added += 1
                    else:
                        one_side += 1
                    break
    return added, runs_found, one_side
