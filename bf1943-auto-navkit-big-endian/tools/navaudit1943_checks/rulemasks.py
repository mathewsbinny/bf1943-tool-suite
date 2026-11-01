"""navaudit1943_checks, check 7: where each rule changed the kept ground (the rule-off masks), near-misses, ladders."""
import json
import math
import os

import numpy as np

import navaudit1943 as A  # noqa: E402
import soldier1943  # noqa: E402

from .config import NEAR
from .geometry import clusters_xz
from .loading import sector_of


# ------------------------------------------------------------------------------------------------ 7: rules

def rules(C, positions):
    out = {'objective floor': 'no such rule in the BF1943 pipeline: navbuild.ORDER_NODES adds nodes at AI order '
                              'positions and BF1943 levels have none (AreaOrders absent); the spawn seeds '
                              '(soldier_spawns.json) only seed the flood'}
    req = np.array([r['position'] for r in positions], float) if positions else np.zeros((0, 3))
    for _which, G in C['grids'].items():
        s = G.s
        col = G.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        for v, kv in G.variants.items():
            diff = np.nonzero(kv != G.keep)[0]
            removed = diff[kv[diff] & ~G.keep[diff]]          # walkable without the rule: the rule removed them
            added = diff[~kv[diff] & G.keep[diff]]            # walkable only with the rule: the rule added them
            rec = {'removed': int(len(removed)), 'added': int(len(added))}
            for name, sel in (('removed', removed), ('added', added)):
                cl = clusters_xz(cx[sel], cz[sel], top=12)
                for c_ in cl:
                    x, z = c_['centre']
                    if len(req):
                        d = np.hypot(req[:, 0] - x, req[:, 2] - z)
                        k = int(np.argmin(d))
                        c_['nearest_required'] = f"{positions[k]['kind']}: {positions[k]['name']} ({d[k]:.0f} m)"
                rec[name + ' clusters'] = cl
                if len(req) and len(sel):
                    near = []
                    for r in positions:
                        x, y, z = r['position']
                        m_ = (np.abs(cx[sel] - x) <= 20) & (np.abs(cz[sel] - z) <= 20) & (np.abs(s.y[sel] - y) <= 5)
                        if m_.any():
                            near.append(f"{r['kind']}: {r['name']}: {int(m_.sum())} surfaces within 20 m")
                    rec[name + ' near required positions'] = near
            out[f'{v} ({G.label})'] = rec
    # near-misses: within 20 % of each threshold, on surfaces that pass every other surface test
    nm = {}
    for _which, G in C['grids'].items():
        s = G.s
        col = G.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        y = s.y.astype(float)
        clear = (s.flags & 1) == 0
        ang = np.degrees(np.arccos(np.clip(s.normal[:, 1], -1, 1)))
        # clearance: the next surface up in the column (stored top down)
        above = np.full(len(y), np.inf)
        first = np.zeros(len(y), bool)
        first[s.start[:-1][np.diff(s.start) > 0]] = True
        above[1:] = np.where(first[1:], np.inf, y[:-1])
        clr = above - y
        depth = np.where(np.isfinite(G.top[col]), G.top[col] - y, -np.inf)
        rec = {}
        lo_, hi_ = soldier1943.SLOPE_MAX * (1 - NEAR), soldier1943.SLOPE_MAX * (1 + NEAR)
        sel = np.nonzero(clear & (ang >= lo_) & (ang <= hi_))[0]
        rec[f'slope {lo_:.0f}-{hi_:.0f} deg (limit {soldier1943.SLOPE_MAX:g})'] = _near(G, sel, ang[sel] <= soldier1943.SLOPE_MAX + 0.6, cx, cz)
        H = soldier1943.HEIGHT
        sel = np.nonzero(clear & (clr >= H * (1 - NEAR)) & (clr <= H * (1 + NEAR)))[0]
        rec[f'headroom {H * (1 - NEAR):.2f}-{H * (1 + NEAR):.2f} m (limit {H:g})'] = _near(G, sel, clr[sel] >= H, cx, cz)
        W = soldier1943.WATER_DEPTH
        sel = np.nonzero(clear & (depth >= W * (1 - NEAR)) & (depth <= W * (1 + NEAR)))[0]
        rec[f'water depth {W * (1 - NEAR):.2f}-{W * (1 + NEAR):.2f} m (limit {W:g})'] = _near(G, sel, depth[sel] <= W, cx, cz)
        t = G.hf.height(cx, cz)
        und = t - y
        sel = np.nonzero(clear & (und >= 0.5 * (1 - NEAR)) & (und <= 0.5 * (1 + NEAR)))[0]
        rec['under the terrain 0.40-0.60 m (limit 0.5)'] = _near(G, sel, und[sel] <= 0.5, cx, cz)
        rec['step (sills, stairs)'] = step_near(G)
        nm[G.label] = rec
    out['near-misses'] = nm
    out['ladders'] = ladder_report(C)
    return out


def _near(G, sel, inside_limit, cx, cz):
    k = sel[inside_limit]
    o = sel[~inside_limit]
    return {'inside the limit': int(len(k)), 'kept of those': int(G.keep[k].sum()),
            'just past the limit': int(len(o)), 'past the limit, clusters': clusters_xz(cx[o], cz[o], top=8)}


def step_near(G):
    """Neighbouring surfaces (8 directions) on one floor whose height difference is within 20 % of the step: kept
    ground beside ground the step rule left out (dy just over STEP) and kept pairs just under it."""
    s = G.s
    nx, nz = s.nx, s.nz
    STEP = soldier1943.STEP
    lo, hi = STEP * (1 - NEAR), STEP * (1 + NEAR)
    col = G.col_of()
    kept = np.nonzero(G.keep)[0]
    clear = (s.flags & 1) == 0
    slope_ok = s.normal[:, 1] >= math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01
    over, under = [], 0
    ky = s.y[kept]
    kc = col[kept]
    for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1), (-1, 0), (0, -1), (-1, -1), (-1, 1)):
        i = kc % nx + di
        j = kc // nx + dj
        ok = (i >= 0) & (i < nx) & (j >= 0) & (j < nz)
        nc = np.where(ok, j * nx + i, 0)
        st, en = s.start[nc], s.start[nc + 1]
        for q in range(int((en - st).max()) if len(st) else 0):
            n = st + q
            v = ok & (n < en)
            n = np.where(v, n, 0)
            dy = s.y[n] - ky
            m_ = v & (np.abs(dy) >= lo) & (np.abs(dy) <= hi)
            past = m_ & (np.abs(dy) > STEP) & ~G.keep[n] & clear[n] & slope_ok[n]
            over.append(n[past])
            under += int((m_ & (np.abs(dy) <= STEP) & G.keep[n]).sum())
    over = np.unique(np.concatenate(over)) if over else np.zeros(0, int)
    cx = s.min_x + (col[over] % nx) * s.pitch
    cz = s.min_z + (col[over] // nx) * s.pitch
    return {'kept pairs stepping %.2f-%.2f m' % (lo, STEP): under // 2,
            'left out, %.2f-%.2f m above or below kept ground' % (STEP, hi): int(len(over)),
            'left out, clusters': clusters_xz(cx, cz, top=12)}


def ladder_report(C):
    nv = C['nav']
    nav = nv.nav
    out = []
    lad = json.load(open(os.path.join(C['p']['objects'], 'ladders.json'), encoding='utf-8'))
    le = nav.ladder_edge if nav.ladder_edge is not None else np.zeros(len(nav.edges), bool)
    E = nav.edges[le]
    for L in lad:
        lo, hi = L['lo'], L['hi']
        cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        foot, top = lo[1], hi[1]
        sf, af = nv.locate(cx, cz, foot, r=3.0, dy=1.0)
        st_, at_ = nv.locate(cx, cz, top, r=3.0, dy=1.5)
        links = 0
        for a, b in E:
            pa, pb = nav.node_pos[a], nav.node_pos[b]
            if min(math.hypot(pa[0] - cx, pa[2] - cz), math.hypot(pb[0] - cx, pb[2] - cz)) <= 4.0:
                links += 1
        sec = {sector_of(C, lo[0], lo[2]), sector_of(C, hi[0], hi[2])}
        out.append(dict(ladder=L['bp'].split('/')[-1], centre=[round(cx, 1), round(foot, 1), round(cz, 1)], top=round(top, 1),
                        foot_area=nv.area_info(sf, foot), top_area=nv.area_info(st_, top), ladder_edges_near=links,
                        ends_in_two_sectors=len(sec) > 1,
                        near_miss=bool(sf and A.OBJECTIVE_REACH * 0.6 <= sf[0] <= 1.8) or bool(st_ and 1.2 <= st_[0] <= 1.8)))
    return out
