"""navaudit1943_checks: geometry helpers (polygons, distances, labelling, clusters).

Inputs are numpy arrays of world positions on the horizontal plane (x, z); outputs are masks, distances, labels and
the cluster records the report lists.
"""
from typing import Any

import numpy as np
import numpy.typing as npt

from .config import CLUSTER_CELL

type FloatArray = npt.NDArray[np.float64]
type Cluster = dict[str, Any]       # {'centre': [x, z], 'points', 'cells', 'bbox': [x0, z0, x1, z1]}


# ------------------------------------------------------------------------------------------------ geometry helpers

def in_poly(px: npt.ArrayLike, pz: npt.ArrayLike, poly: npt.NDArray[np.floating]) -> npt.NDArray[np.bool]:
    """Even-odd point-in-polygon on xz for arrays of points; poly (n, >=2) with x, z in columns 0 and -1."""
    px, pz = np.asarray(px, float), np.asarray(pz, float)
    x, z = poly[:, 0], poly[:, -1]
    x_next, z_next = np.roll(x, -1), np.roll(z, -1)
    out = np.zeros(px.shape, bool)
    # each edge (xa, za) -> (xb, zb); a horizontal edge crosses no ray
    for xa, za, xb, zb in zip(x, z, x_next, z_next):
        if za == zb:
            continue
        straddles = (za > pz) != (zb > pz)
        x_cross = xa + (pz - za) * (xb - xa) / (zb - za)
        out ^= straddles & (px < x_cross)
    return out


def poly_area(poly: npt.NDArray[np.floating]) -> float:
    """The polygon's area on xz (the shoelace formula); x, z in columns 0 and -1."""
    x, z = poly[:, 0], poly[:, -1]
    return 0.5 * abs(float(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))))


def seg_dist(px: float, pz: float, a: FloatArray, b: FloatArray) -> FloatArray:
    """Distances from (px, pz) to segments a -> b ((n, 2) arrays on xz)."""
    d = b - a                              # each segment's direction
    length_sq = (d ** 2).sum(1)
    # t: the nearest point's position along each segment, 0 at a and 1 at b
    t = np.clip(((px - a[:, 0]) * d[:, 0] + (pz - a[:, 1]) * d[:, 1]) / np.maximum(length_sq, 1e-12), 0, 1)
    return np.hypot(a[:, 0] + t * d[:, 0] - px, a[:, 1] + t * d[:, 1] - pz)


def label(mask: npt.NDArray[np.bool]) -> tuple[npt.NDArray[np.int_], int]:
    """8-connected components of a 2-D bool array: (labels with -1 off the mask, count). Label propagation with
    pointer jumping; no scipy here."""
    h, w = mask.shape
    labels = np.where(mask, np.arange(h * w).reshape(h, w), h * w)
    big = h * w                            # the label of cells off the mask
    for _ in range(100000):
        lowest = labels.copy()
        padded = np.pad(labels, 1, constant_values=big)
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                if di or dj:
                    np.minimum(lowest, padded[1 + dj:1 + dj + h, 1 + di:1 + di + w], out=lowest)
        lowest = np.where(mask, lowest, big)
        flat = lowest.ravel()
        # pointer jumping: follow each label to its own label's label
        for _ in range(4):
            ok = flat < big
            flat[ok] = flat[flat[ok]]
        lowest = flat.reshape(h, w)
        if np.array_equal(lowest, labels):
            break
        labels = lowest
    roots, inverse = np.unique(labels[mask], return_inverse=True)
    out = np.full((h, w), -1, int)
    out[mask] = inverse
    return out, len(roots)


def clusters_xz(x: npt.ArrayLike, z: npt.ArrayLike, weight: object = None, cell: float = CLUSTER_CELL,
                top: int | None = None) -> list[Cluster]:
    """Points grouped into 8-connected clusters on a cell grid: [{'centre', 'cells', 'points', 'bbox'}], largest
    first. (weight is not used.)"""
    x, z = np.asarray(x, float), np.asarray(z, float)
    if not len(x):
        return []
    i = np.floor(x / cell).astype(int)
    j = np.floor(z / cell).astype(int)
    i0, j0 = i.min(), j.min()
    width, height = i.max() - i0 + 1, j.max() - j0 + 1
    mask = np.zeros((height, width), bool)
    mask[j - j0, i - i0] = True
    labels, count = label(mask)
    point_label = labels[j - j0, i - i0]
    points = np.bincount(point_label, minlength=count)
    sum_x, sum_z = np.bincount(point_label, x, count), np.bincount(point_label, z, count)
    cells = np.bincount(labels[labels >= 0], minlength=count)
    lo_x, lo_z = np.full(count, np.inf), np.full(count, np.inf)
    hi_x, hi_z = np.full(count, -np.inf), np.full(count, -np.inf)
    np.minimum.at(lo_x, point_label, x)
    np.minimum.at(lo_z, point_label, z)
    np.maximum.at(hi_x, point_label, x)
    np.maximum.at(hi_z, point_label, z)
    order = np.argsort(-points, kind='stable')
    if top:
        order = order[:top]
    return [{'centre': [round(float(sum_x[k] / points[k]), 1), round(float(sum_z[k] / points[k]), 1)],
             'points': int(points[k]), 'cells': int(cells[k]),
             'bbox': [round(float(lo_x[k]), 1), round(float(lo_z[k]), 1),
                      round(float(hi_x[k]), 1), round(float(hi_z[k]), 1)]} for k in order]
