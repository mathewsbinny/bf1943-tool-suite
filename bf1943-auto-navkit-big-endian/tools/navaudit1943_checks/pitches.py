"""navaudit1943_checks, check 4: resolution (0.8 m against 0.2 m walkable ground in the detailed sectors)."""
import collections
import math

import numpy as np

from .geometry import label
from .loading import objects_at


# ------------------------------------------------------------------------------------------------ 4: resolution

def ground_heights(G, cols):
    """Per column (of cols): the kept surface nearest the terrain within 1 m (NaN when none) and the number of
    kept surfaces."""
    s = G.s
    st, en = s.start[cols], s.start[cols + 1]
    n = en - st
    rep = np.repeat(np.arange(len(cols)), n)
    idx = (np.repeat(st, n) + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))).astype(np.int64)
    x = s.min_x + (cols % s.nx) * s.pitch
    z = s.min_z + (cols // s.nx) * s.pitch
    t = G.hf.height(x, z)
    y = s.y[idx]
    kept = G.keep[idx]
    d = np.abs(y - t[rep])
    h = np.full(len(cols), np.nan)
    ok = np.nonzero(kept & (d <= 1.0))[0]
    if len(ok):
        o = ok[np.lexsort((d[ok], rep[ok]))]           # by column, nearest the terrain first
        firsts = o[np.r_[True, rep[o][1:] != rep[o][:-1]]]
        h[rep[firsts]] = y[firsts]
    cnt = np.bincount(rep[kept], minlength=len(cols))
    return h, cnt, t


def resolution(C):
    if 'detailed' not in C['grids']:
        return {'skipped': 'no detailed mask'}
    G8, G2 = C['grids']['normal'], C['grids']['detailed']
    s8, s2 = G8.s, G2.s
    out = {'sectors': []}
    diffs8, diffs2, conn = [], [], []
    for (X, Z) in sorted(C['dset_built']):
        bx = (X + C['xoff']) * 100.0
        bz = (Z + C['zoff']) * 100.0
        i0 = int(math.ceil((bx - s8.min_x) / s8.pitch - 1e-6))
        i1 = int(math.floor((bx + 100 - s8.min_x) / s8.pitch - 1e-6))
        j0 = int(math.ceil((bz - s8.min_z) / s8.pitch - 1e-6))
        j1 = int(math.floor((bz + 100 - s8.min_z) / s8.pitch - 1e-6))
        # a sector at the sampled box's edge reaches past it (the box is the land plus a margin)
        i0, i1, j0, j1 = max(i0, 0), min(i1, s8.nx - 1), max(j0, 0), min(j1, s8.nz - 1)
        if i0 > i1 or j0 > j1:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        c8 = (jj * s8.nx + ii).ravel()
        h8, n8, t8 = ground_heights(G8, c8)
        x8 = s8.min_x + ii.ravel() * s8.pitch
        z8 = s8.min_z + jj.ravel() * s8.pitch
        frac = np.zeros(len(c8))
        any2 = np.zeros(len(c8))
        for di in (-2, -1, 0, 1):
            for dj in (-2, -1, 0, 1):
                i2 = np.round((x8 - s2.min_x) / s2.pitch).astype(int) + di
                j2 = np.round((z8 - s2.min_z) / s2.pitch).astype(int) + dj
                ok = (i2 >= 0) & (i2 < s2.nx) & (j2 >= 0) & (j2 < s2.nz)
                c2 = np.where(ok, j2 * s2.nx + i2, 0)
                h2, _n2, _ = ground_heights(G2, c2)
                h2 = np.where(ok, h2, np.nan)
                any2 += ~np.isnan(h2)
                frac += ~np.isnan(h2) & (np.isnan(h8) | (np.abs(h2 - np.nan_to_num(h8)) <= 0.3))
        frac /= 16.0
        any2 /= 16.0
        only8 = ~np.isnan(h8) & (frac < 0.25)
        only2 = np.isnan(h8) & (any2 >= 0.75)
        H, W = jj.shape
        for _name, mask, store in (('0.8 m only', only8, diffs8), ('0.2 m only', only2, diffs2)):
            M = mask.reshape(H, W)
            # more than a cell row: what survives removing one row from every side
            er = M.copy()
            er[1:, :] &= M[:-1, :]
            er[:-1, :] &= M[1:, :]
            er[:, 1:] &= M[:, :-1]
            er[:, :-1] &= M[:, 1:]
            if not er.any():
                continue
            lab, n = label(er)
            for k in range(n):
                sel = (lab == k).ravel()
                cx, cz = float(x8[sel].mean()), float(z8[sel].mean())
                store.append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', centre=[round(cx, 1), round(cz, 1)],
                                  core_cells=int(sel.sum()), core_m2=round(float(sel.sum()) * 0.64, 1),
                                  terrain_y=round(float(t8[sel].mean()), 1)))
        # connectivity of the ground layer: pieces one run joins and the other separates
        m8 = (~np.isnan(h8)).reshape(H, W)
        m2 = (any2 >= 0.5).reshape(H, W)
        l8, n8c = label(m8)
        l2, n2c = label(m2)
        both = m8 & m2
        pairs = set(zip(l8[both].tolist(), l2[both].tolist()))
        by2 = collections.defaultdict(set)
        by8 = collections.defaultdict(set)
        for a_, b_ in pairs:
            by2[b_].add(a_)
            by8[a_].add(b_)
        size8 = np.bincount(l8[m8], minlength=n8c)
        size2 = np.bincount(l2[m2], minlength=n2c)
        for b_, s8set in by2.items():
            big = [a_ for a_ in s8set if size8[a_] >= 25]
            if len(big) > 1:
                sel = (l2 == b_).ravel()
                conn.append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', kind='0.2 m joins pieces 0.8 m separates',
                                 pieces=len(big), centre=[round(float(x8[sel].mean()), 1), round(float(z8[sel].mean()), 1)]))
        for a_, s2set in by8.items():
            big = [b_ for b_ in s2set if size2[b_] >= 25]
            if len(big) > 1:
                sel = (l8 == a_).ravel()
                conn.append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', kind='0.8 m joins pieces 0.2 m separates',
                                 pieces=len(big), centre=[round(float(x8[sel].mean()), 1), round(float(z8[sel].mean()), 1)]))
        out['sectors'].append(dict(sector=f'{((X & 0xFF) << 8) | (Z & 0xFF):04x}', ground_08=int(m8.sum()),
                                   ground_02=int(m2.sum()), only08=int(only8.sum()), only02=int(only2.sum())))
    for lst in (diffs8, diffs2):
        for d in lst:
            x, z = d['centre']
            d['objects'] = objects_at(C, x, d['terrain_y'], z, grow=1.0)
            c8 = G8.col(x, z)
            d['column_08'] = G8.describe(c8, d['terrain_y'] - 1.5, d['terrain_y'] + 2.5, C['mm'])['surfaces'] if c8 is not None else None
            c2 = G2.col(x, z)
            d['column_02'] = G2.describe(c2, d['terrain_y'] - 1.5, d['terrain_y'] + 2.5, C['mm'])['surfaces'] if c2 is not None else None
        lst.sort(key=lambda d: -d['core_cells'])
    out['walkable at 0.8 m, not at 0.2 m'] = diffs8
    out['walkable at 0.2 m, not at 0.8 m'] = diffs2
    out['connectivity'] = conn
    return out
