"""hpdview.py - draw one sector of a pathdata.hpd top-down (x right, z up) to a PNG.

Usage: python hpdview.py <pathdata.hpd> <sector index | 0xID> <out.png>

Optional 4th to 7th arguments: minX maxX minZ maxZ zoom window (all four or none).

Colours: grey = tile grid, black = E lines, purple = D lines, cyan = F cells (arrow = row direction),
green = B segments, red = C segments with a horizontal direction (arrow), magenta = C segments with
direction "up", orange = graph nodes, light orange = graph edges.
"""
import struct
import sys

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import hpdtool


def u32(mesh: bytes, offset: int) -> int:
    return struct.unpack_from('<I', mesh, offset)[0]


def f32(mesh: bytes, offset: int) -> float:
    return struct.unpack_from('<f', mesh, offset)[0]


def points_between(mesh: bytes, start: int, end: int) -> list[tuple[float, ...]]:
    """The 3-D points (12 bytes each) from start up to end."""
    return [struct.unpack_from('<3f', mesh, at) for at in range(start, end - 11, 12)]


def segs_from_lists(mesh: bytes, obj: int, count_off: int, table_off: int) -> list[list[tuple[float, ...]]]:
    """D/E lists of the area object at obj: index table of count+1 offsets (relative to the object), data = 3-D
    points."""
    count = u32(mesh, obj + count_off)
    out: list[list[tuple[float, ...]]] = []
    if not count:
        return out
    table = obj + u32(mesh, obj + table_off)
    offsets = [u32(mesh, table + j * 4) for j in range(count + 1)]
    for j in range(count):
        out.append(points_between(mesh, obj + offsets[j], obj + offsets[j + 1]))
    return out


def main() -> int | None:
    if len(sys.argv) < 4 or any(arg in ('-h', '--help') for arg in sys.argv[1:]):
        print(__doc__)
        return 0 if len(sys.argv) > 1 else 1
    path, which, out = sys.argv[1], sys.argv[2], sys.argv[3]
    hpd = hpdtool.parse(open(path, 'rb').read())
    if which.startswith('0x'):
        sector = next(candidate for candidate in hpd.sectors if candidate.id == int(which, 16))
    else:
        sector = hpd.sectors[int(which)]
    mesh = sector.mesh
    _, _tiles, objects, _, _ = hpdtool.mesh_layout(mesh)
    fig, ax = plt.subplots(figsize=(12, 12))
    # the tile grid: the sector's bounds and tile counts from the mesh header
    x0, x1, z0, z1 = (f32(mesh, 0x28 + i * 4) for i in range(4))
    nx, nz = u32(mesh, 0x38), u32(mesh, 0x3C)
    for i in range(nx + 1):
        x = x0 + (x1 - x0) * i / nx
        ax.plot([x, x], [z0, z1], color='0.85', lw=0.8)
    for i in range(nz + 1):
        z = z0 + (z1 - z0) * i / nz
        ax.plot([x0, x1], [z, z], color='0.85', lw=0.8)
    for obj in objects:                                  # each area object
        for points in segs_from_lists(mesh, obj, 0x40, 0x44):
            ax.plot([point[0] for point in points], [point[2] for point in points], color='black', lw=0.6)
        for points in segs_from_lists(mesh, obj, 0x38, 0x3C):
            ax.plot([point[0] for point in points], [point[2] for point in points], color='tab:purple', lw=1.6)
        # F cells: 16-byte table entries (point offset, row direction), the points up to the next entry's offset
        cell_count = u32(mesh, obj + 0x48)
        if cell_count:
            table = obj + u32(mesh, obj + 0x4C)
            for j in range(cell_count):
                start = obj + u32(mesh, table + j * 16)
                end = obj + u32(mesh, table + (j + 1) * 16)
                points = points_between(mesh, start, end)
                ax.fill([point[0] for point in points], [point[2] for point in points], color='tab:cyan', alpha=0.6,
                        lw=0)
                direction = struct.unpack_from('<3f', mesh, table + j * 16 + 4)
                centre_x = sum(point[0] for point in points) / len(points)
                centre_z = sum(point[2] for point in points) / len(points)
                ax.arrow(centre_x, centre_z, direction[0] * 0.6, direction[2] * 0.6, head_width=0.2, color='teal',
                         lw=0.5)
        # B segments (0x20 bytes each), then C segments (0x2C bytes each): ends (x, z) at +0, +8 and +12, +20
        b_count = u32(mesh, obj + 0x28)
        records = obj + 0x50
        for k in range(b_count):
            rec = records + k * 0x20
            ax.plot([f32(mesh, rec), f32(mesh, rec + 12)], [f32(mesh, rec + 8), f32(mesh, rec + 20)],
                    color='tab:green', lw=2)
        records += b_count * 0x20
        for k in range(u32(mesh, obj + 0x30)):
            rec = records + k * 0x2C
            up = f32(mesh, rec + 0x24) > 0.5
            ax.plot([f32(mesh, rec), f32(mesh, rec + 12)], [f32(mesh, rec + 8), f32(mesh, rec + 20)],
                    color='tab:red' if not up else 'magenta', lw=2)
            if not up:
                mid_x = (f32(mesh, rec) + f32(mesh, rec + 12)) / 2
                mid_z = (f32(mesh, rec + 8) + f32(mesh, rec + 20)) / 2
                ax.arrow(mid_x, mid_z, f32(mesh, rec + 0x20) * 0.8, f32(mesh, rec + 0x28) * 0.8, head_width=0.25,
                         color='tab:red', lw=0.5)
    for edge in sector.edges:
        src, dst = sector.nodes[edge.src].pos, sector.nodes[edge.dst].pos
        ax.plot([src[0], dst[0]], [src[2], dst[2]], color='moccasin', lw=0.8, zorder=1)
    ax.scatter([node.pos[0] for node in sector.nodes], [node.pos[2] for node in sector.nodes], s=6,
               color='tab:orange', zorder=3)
    if len(sys.argv) >= 8:
        ax.set_xlim(float(sys.argv[4]), float(sys.argv[5]))
        ax.set_ylim(float(sys.argv[6]), float(sys.argv[7]))
    ax.set_aspect('equal')
    ax.set_title(f'sector {sector.id:#06x}: {len(objects)} areas, {len(sector.nodes)} nodes, '
                 f'{len(sector.edges)} edges')
    fig.savefig(out, dpi=110, bbox_inches='tight')
    print(f'wrote {out}')


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('hpdview', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main())
