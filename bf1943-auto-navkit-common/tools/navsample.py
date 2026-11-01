"""NavKit: reader and checks for collision samples taken in game by OPFOR's [dev] navsample option.

Usage:
  navsample.py info    <samples.bin>
  navsample.py compare <samples.bin> <pathdata.hpd> [out.png]   agreement with an existing pathdata.hpd

File format: see src/dll/navsample.cpp. Columns are j-major (z), then i (x); sample (i, j) is at
(minX + i * pitch, minZ + j * pitch), minX/minZ being whole multiples of the pitch (as in the original,
whose outlines run halfway between samples).
"""
import math
import struct
import sys
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

HEADER = struct.Struct('<4sI3f2i6fI')
BLOCKED, FAULT = 1, 2


@dataclass
class Samples:
    pitch: float
    min_x: float
    min_z: float
    nx: int
    nz: int
    y_top: float
    y_bottom: float
    radius: float
    cap_bottom: float
    cap_top: float
    ray_radius: float
    start: npt.NDArray[np.int64]    # per column: index of its first surface (length nx*nz + 1)
    y: npt.NDArray[np.float32]        # per surface
    normal: npt.NDArray[np.float32]   # per surface, (n, 3) floats
    flags: npt.NDArray[np.uint8]    # per surface
    # per surface (version 2): shape vtable, shape word +0x0C, collidable word +0x1C
    hit: npt.NDArray[np.uint32] | None = None

    def centre(self, i: float, j: float) -> tuple[float, float]:
        return self.min_x + i * self.pitch, self.min_z + j * self.pitch

    def column(self, i: int, j: int) -> range:
        col = j * self.nx + i
        return range(self.start[col], self.start[col + 1])


MAX_COLUMNS = 1 << 27      # nx * nz at most: 1.8 x the largest run so far (Coral Sea at 0.2 m, 73,023,000 columns);
                           # Samples.start holds 8 bytes per column, whether or not the run finished them


def load(path: str) -> tuple[Samples, int]:
    """(Samples, columns read). Versions 1 and 2 keep an interrupted run's finished columns (the rest empty); version
    3, written whole at the end of a run, is read exactly as OPFOR's navsample.cpp writes it. ValueError for anything
    else (2026-10-31 fuzzing: struct.error on a cut header, a 2^31-column header that hung making its column table,
    and version-3 counts or records outside their blocks)."""
    data = open(path, 'rb').read()
    if len(data) < HEADER.size:
        raise ValueError(f'{path}: not a navsample file ({len(data)} bytes, shorter than its header)')
    magic, version, pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_bottom, cap_top, ray_radius, _ = \
        HEADER.unpack_from(data, 0)
    if magic != b'ONS1' or version not in (1, 2, 3):
        raise ValueError(f'{path}: not a navsample file')
    if nx < 1 or nz < 1 or nx * nz > MAX_COLUMNS:
        raise ValueError(f'{path}: a grid of {nx} x {nz} columns (at least 1 x 1, at most {MAX_COLUMNS} columns)')
    if version == 3:
        # records, then the column counts in one block, then { u64 offset of the counts, "CNT3" } - read at once
        if len(data) < HEADER.size + 12 or data[-4:] != b'CNT3':
            raise ValueError(f'{path}: sampling run not finished (no column counts)')
        counts_at = struct.unpack_from('<Q', data, len(data) - 12)[0]
        if counts_at + nx * nz + 12 != len(data) or (counts_at - HEADER.size) % 20:
            raise ValueError(f'{path}: the column counts at {counts_at} are not the {nx * nz} bytes before the trailer, '
                             f'after whole 20-byte records')
        counts = np.frombuffer(data, np.uint8, nx * nz, counts_at).astype(np.int64)
        start = np.zeros(nx * nz + 1, dtype=np.int64)
        np.cumsum(counts, out=start[1:])
        if HEADER.size + 20 * int(start[-1]) != counts_at:
            raise ValueError(f'{path}: the column counts add up to {int(start[-1])} surfaces, and the records before '
                             f'them hold {(counts_at - HEADER.size) // 20}')
        # per surface: height, normal (signed bytes, / 127), flags, hit details
        records = np.frombuffer(data, np.dtype([('y', '<f4'), ('n', 'i1', 3), ('f', 'u1'), ('h', '<u4', 3)]),
                                int(start[-1]), HEADER.size)
        return Samples(pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_bottom, cap_top, ray_radius, start,
                       records['y'].astype(np.float32), (records['n'] / 127.0).astype(np.float32),
                       records['f'].astype(np.uint8), records['h'].astype(np.uint32)), nx * nz
    pos = HEADER.size
    cols = nx * nz
    start = np.zeros(cols + 1, dtype=np.int64)
    ys, normals, flags, hits = [], [], [], []
    record_size = 8 if version == 1 else 20
    col = 0
    while col < cols and pos < len(data):
        count = data[pos]
        if pos + 1 + count * record_size > len(data):
            break                     # the run stopped inside this column's records: keep the finished columns
        pos += 1
        for _ in range(count):
            y, normal_x, normal_y, normal_z, flag = struct.unpack_from('<f3bB', data, pos)
            if version == 2:
                hits.append(struct.unpack_from('<3I', data, pos + 8))
            pos += record_size
            ys.append(y)
            normals.append((normal_x / 127.0, normal_y / 127.0, normal_z / 127.0))
            flags.append(flag)
        col += 1
        start[col] = len(ys)
    start[col:] = len(ys)   # an interrupted run leaves the rest empty
    hit_details = np.array(hits, dtype=np.uint32).reshape(-1, 3) if version == 2 else None    # version 1: none
    return Samples(pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_bottom, cap_top, ray_radius, start,
                   np.array(ys, dtype=np.float32), np.array(normals, dtype=np.float32).reshape(-1, 3),
                   np.array(flags, dtype=np.uint8), hit_details), col


def column_sectors(s: Samples, cols: npt.ArrayLike) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """(X, Z) arrays: the 100 m grid squares (floor(x / 100), floor(z / 100), before the sector offsets) of columns,
    from whole centimetres - min + i * pitch in floats puts a column on a sector line on either side of it, by the
    float32 rounding of the header's minimum and pitch (navbuild.stage and detailed.py then disagreed)."""
    cols = np.asarray(cols, dtype=np.int64)
    step = int(round(float(s.pitch) * 100))
    x = int(round(float(s.min_x) * 100)) + (cols % s.nx) * step
    z = int(round(float(s.min_z) * 100)) + (cols // s.nx) * step
    return x // 10000, z // 10000


def walkable(s: Samples, slope_max_deg: float = 40.0) -> npt.NDArray[np.bool]:
    """Per surface: the soldier fits (no capsule overlap) and the ground is not too steep."""
    min_ny = math.cos(math.radians(slope_max_deg)) - 0.01
    return ((s.flags & (BLOCKED | FAULT)) == 0) & (s.normal[:, 1] >= min_ny)


def reference_cells(s: Samples, hpd_path: str) -> dict[int, list[tuple[float, float, float]]]:
    """Rasterises the areas of an existing pathdata.hpd onto the sample grid.
    Returns {column: [(min_y, max_y, coverage), ...]}. Outlines run on cell corners ((k + 0.5) * pitch)
    with axis-aligned and 45-degree segments; a 45-degree segment passes through a sample and cuts its
    cell in half. Coverage is measured at four points inside the cell, off both diagonals:
    1 = whole cell inside, 0.5 = cut by a diagonal, 0.25/0.75 = rarer cases. Layers of overlapping
    areas (sector margins) are merged, keeping the largest coverage."""
    hpd = hpdtool.parse(open(hpd_path, 'rb').read())
    cells: dict[int, list[tuple[float, float, float]]] = {}
    for sector in hpd.sectors:
        mesh = kymesh.parse_mesh(sector.mesh)
        for area in mesh.areas:
            # The boundary of an area is its outline polylines (open) closed by its portal segments
            # (B to neighbouring areas, C on the sector border).
            segs = [(line[k], line[k + 1]) for line in area.outline for k in range(len(line) - 1)]
            segs += ([(portal.p0, portal.p1) for portal in area.portals]
                     + [(border.p0, border.p1) for border in area.border_portals])
            points = [point for seg in segs for point in seg]
            if not points:
                continue
            xs = [point[0] for point in points]
            zs = [point[2] for point in points]
            i0 = max(0, int(math.ceil((min(xs) - s.min_x) / s.pitch)))
            i1 = min(s.nx - 1, int(math.floor((max(xs) - s.min_x) / s.pitch)))
            j0 = max(0, int(math.ceil((min(zs) - s.min_z) / s.pitch)))
            j1 = min(s.nz - 1, int(math.floor((max(zs) - s.min_z) / s.pitch)))
            if i0 > i1 or j0 > j1:
                continue
            grid_i, grid_j = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
            px = s.min_x + grid_i * s.pitch          # the samples' x and z
            pz = s.min_z + grid_j * s.pitch
            count = np.zeros(px.shape, dtype=np.int8)
            # the four probe points of each cell, as fractions of the pitch from its sample
            for off_x, off_z in ((0.25, 0.1), (-0.25, -0.1), (0.1, -0.25), (-0.1, 0.25)):
                probe_x, probe_z = px + off_x * s.pitch, pz + off_z * s.pitch
                inside = np.zeros(px.shape, dtype=bool)
                for (x1, _, z1), (x2, _, z2) in segs:    # even-odd rule
                    if z1 == z2:
                        continue
                    cross = (probe_z >= min(z1, z2)) & (probe_z < max(z1, z2))
                    x_cross = x1 + (probe_z - z1) * (x2 - x1) / (z2 - z1)
                    inside ^= cross & (probe_x < x_cross)
                count += inside
            ys = [point[1] for point in points]
            lo, hi = min(ys), max(ys)
            for i, j, inside_count in zip(grid_i[count > 0], grid_j[count > 0], count[count > 0]):
                layers = cells.setdefault(int(j) * s.nx + int(i), [])
                coverage = inside_count / 4.0
                for k, (layer_lo, layer_hi, layer_coverage) in enumerate(layers):
                    if lo <= layer_hi + 0.6 and layer_lo <= hi + 0.6:      # same surface (overlapping sectors)
                        layers[k] = (min(lo, layer_lo), max(hi, layer_hi), max(coverage, layer_coverage))
                        break
                else:
                    layers.append((lo, hi, coverage))
    return cells


def flood(s: Samples, ok: npt.NDArray[np.bool], seeds: Iterable[int], max_dy: float) -> npt.NDArray[np.bool]:
    """Surfaces reachable from the seed surfaces over walkable neighbours (4-connected, |dy| <= max_dy)."""
    reach = np.zeros(len(s.y), dtype=bool)
    queue: deque[int] = deque()
    for k in seeds:
        if ok[k] and not reach[k]:
            reach[k] = True
            queue.append(k)
    column_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    while queue:
        k = queue.popleft()
        col = column_of[k]
        i, j = col % s.nx, col // s.nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if not (0 <= ni < s.nx and 0 <= nj < s.nz):
                continue
            for n in s.column(ni, nj):
                if ok[n] and not reach[n] and abs(float(s.y[n]) - float(s.y[k])) <= max_dy:
                    reach[n] = True
                    queue.append(n)
    return reach


def compare(s: Samples, hpd_path: str, png: str | None = None, slope: float = 40.0, step: float = 0.28,
            tol: float = 0.6) -> None:
    """Prints how well the samples' walkable surfaces agree with an existing pathdata.hpd, and draws it to png."""
    reference = reference_cells(s, hpd_path)
    ok = walkable(s, slope)
    tp = fn = fp_raw = 0        # reference cells found, reference cells missed; walkable surfaces not in the reference
    part_yes = part_no = 0
    matched = np.zeros(len(s.y), dtype=bool)
    status = np.zeros((s.nz, s.nx), dtype=np.uint8)   # 0 none, 1 both, 2 only reference, 3 only ours, 4 cut cell
    for col in range(s.nx * s.nz):
        surfaces = range(s.start[col], s.start[col + 1])
        layers = reference.get(col, [])
        j, i = divmod(col, s.nx)
        for lo, hi, coverage in layers:
            hit = [k for k in surfaces if ok[k] and lo - tol <= s.y[k] <= hi + tol]
            matched[hit] = True
            if coverage < 1:        # cut by a diagonal: either answer is consistent with the reference
                part_yes += bool(hit)
                part_no += not hit
                status[j, i] = max(status[j, i], 4)
            elif hit:
                tp += 1
                status[j, i] = 1
            else:
                fn += 1
                status[j, i] = 2
    # Our walkable surfaces connected to the matched ones; the rest is unreachable (the original drops it).
    max_dy = max(step, s.pitch * math.tan(math.radians(slope))) + 0.05
    reach = flood(s, ok, np.nonzero(matched)[0], max_dy)
    fp = reach & ~matched
    fp_raw = int((ok & ~matched).sum())
    for k in np.nonzero(fp)[0]:
        col = int(np.searchsorted(s.start, k, side='right') - 1)
        j, i = divmod(col, s.nx)
        if status[j, i] == 0:
            status[j, i] = 3
    total_ref = tp + fn
    print(f'reference cells (layers) fully inside: {total_ref}; cut by a diagonal: {part_yes + part_no} '
          f'(we mark {part_yes} walkable)')
    print(f'  found walkable by sampling: {tp} ({tp / max(total_ref, 1) * 100:.1f}%)')
    print(f'  missed:                     {fn} ({fn / max(total_ref, 1) * 100:.1f}%)')
    print(f'extra walkable, connected to the reference: {int(fp.sum())} '
          f'({fp.sum() / max(total_ref, 1) * 100:.1f}% of reference); unconnected walkable surfaces: '
          f'{fp_raw - int(fp.sum())}')
    if png:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap
        cmap = ListedColormap(['#202020', '#3cb44b', '#e6194b', '#4363d8', '#7a9a7a'])
        half = s.pitch / 2
        extent = (s.min_x - half, s.min_x + s.nx * s.pitch - half, s.min_z - half, s.min_z + s.nz * s.pitch - half)
        fig, ax = plt.subplots(figsize=(12, 12 * s.nz / max(s.nx, 1)))
        ax.imshow(status, origin='lower', extent=extent, cmap=cmap, vmin=0, vmax=4, interpolation='nearest')
        ax.set_title('green = both, red = only reference, blue = only NavKit (connected), '
                     'grey-green = cell cut by a diagonal')
        fig.savefig(png, dpi=110, bbox_inches='tight')
        print('wrote', png)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    s, cols = load(argv[2])
    if argv[1] == 'info':
        per_column = np.diff(s.start)
        ok = walkable(s)
        blocked = int((s.flags & BLOCKED).astype(bool).sum())
        faults = int((s.flags & FAULT).astype(bool).sum())
        print(f'{s.nx} x {s.nz} columns at {s.pitch} m from ({s.min_x}, {s.min_z}); {cols} sampled')
        print(f'surfaces {len(s.y)}, per column max {per_column.max() if len(per_column) else 0}, '
              f'blocked {blocked}, faults {faults}, walkable {int(ok.sum())}')
        print(f'agent capsule r {s.radius} from {s.cap_bottom} to {s.cap_top} m, ray radius {s.ray_radius}')
    elif argv[1] == 'compare':
        compare(s, argv[3], argv[4] if len(argv) > 4 else None)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navsample', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
