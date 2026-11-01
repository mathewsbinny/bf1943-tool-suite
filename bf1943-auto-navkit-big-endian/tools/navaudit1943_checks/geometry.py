"""navaudit1943_checks: geometry helpers (polygons, distances, labelling, clusters)."""
import numpy as np

from .config import CLUSTER_CELL


# ------------------------------------------------------------------------------------------------ geometry helpers

def in_poly(px, pz, poly):
    """Even-odd point-in-polygon on xz for arrays of points; poly (n, >=2) with x, z in columns 0 and -1."""
    px, pz = np.asarray(px, float), np.asarray(pz, float)
    x, z = poly[:, 0], poly[:, -1]
    x2, z2 = np.roll(x, -1), np.roll(z, -1)
    out = np.zeros(px.shape, bool)
    for a, b, c, d in zip(x, z, x2, z2):
        if b == d:
            continue
        cond = (b > pz) != (d > pz)
        xi = a + (pz - b) * (c - a) / (d - b)
        out ^= cond & (px < xi)
    return out


def poly_area(poly):
    x, z = poly[:, 0], poly[:, -1]
    return 0.5 * abs(float(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))))


def seg_dist(px, pz, a, b):
    """Distances from (px, pz) to segments a -> b ((n, 2) arrays on xz)."""
    d = b - a
    L = (d ** 2).sum(1)
    t = np.clip(((px - a[:, 0]) * d[:, 0] + (pz - a[:, 1]) * d[:, 1]) / np.maximum(L, 1e-12), 0, 1)
    return np.hypot(a[:, 0] + t * d[:, 0] - px, a[:, 1] + t * d[:, 1] - pz)


def label(mask):
    """8-connected components of a 2-D bool array: (labels with -1 off the mask, count). Label propagation with
    pointer jumping; no scipy here."""
    h, w = mask.shape
    lab = np.where(mask, np.arange(h * w).reshape(h, w), h * w)
    big = h * w
    for _ in range(100000):
        m = lab.copy()
        p = np.pad(lab, 1, constant_values=big)
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                if di or dj:
                    np.minimum(m, p[1 + dj:1 + dj + h, 1 + di:1 + di + w], out=m)
        m = np.where(mask, m, big)
        flat = m.ravel()
        # pointer jumping: follow each label to its own label's label
        for _ in range(4):
            ok = flat < big
            flat[ok] = flat[flat[ok]]
        m = flat.reshape(h, w)
        if np.array_equal(m, lab):
            break
        lab = m
    roots, inv = np.unique(lab[mask], return_inverse=True)
    out = np.full((h, w), -1, int)
    out[mask] = inv
    return out, len(roots)


def clusters_xz(x, z, weight=None, cell=CLUSTER_CELL, top=None):
    """Points grouped into 8-connected clusters on a cell grid: [{'centre', 'cells', 'points', 'bbox'}], largest
    first."""
    x, z = np.asarray(x, float), np.asarray(z, float)
    if not len(x):
        return []
    i = np.floor(x / cell).astype(int)
    j = np.floor(z / cell).astype(int)
    i0, j0 = i.min(), j.min()
    W, H = i.max() - i0 + 1, j.max() - j0 + 1
    mask = np.zeros((H, W), bool)
    mask[j - j0, i - i0] = True
    lab, n = label(mask)
    pl = lab[j - j0, i - i0]
    cnt = np.bincount(pl, minlength=n)
    sx, sz = np.bincount(pl, x, n), np.bincount(pl, z, n)
    cells = np.bincount(lab[lab >= 0], minlength=n)
    lo_x, lo_z = np.full(n, np.inf), np.full(n, np.inf)
    hi_x, hi_z = np.full(n, -np.inf), np.full(n, -np.inf)
    np.minimum.at(lo_x, pl, x)
    np.minimum.at(lo_z, pl, z)
    np.maximum.at(hi_x, pl, x)
    np.maximum.at(hi_z, pl, z)
    order = np.argsort(-cnt, kind='stable')
    if top:
        order = order[:top]
    return [{'centre': [round(float(sx[k] / cnt[k]), 1), round(float(sz[k] / cnt[k]), 1)], 'points': int(cnt[k]),
             'cells': int(cells[k]), 'bbox': [round(float(lo_x[k]), 1), round(float(lo_z[k]), 1),
                                              round(float(hi_x[k]), 1), round(float(hi_z[k]), 1)]} for k in order]
