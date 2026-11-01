r"""stfs.Package fuzzed: damaged packages refused with ValueError

Mutation fuzzing (core/fuzzcheck.py) of the STFS reader on the packages test_console_readers.py builds (no game data;
both kinds: one hash table per level, and two): the header and volume descriptor, the file table, the hash entries
that chain fragmented files, and the data. On any input the package opens and every file in it reads, or the reader
raises ValueError, within its time and memory limits; a package cut short is never read as whole. Fixed seeds;
BF1943_FUZZ=long for more (README "Tests").
"""
import io
import os

import fuzzcheck
import stfs

READERS = fuzzcheck.fixtures('bf1943-console-readers/tests/test_console_readers.py')


def seeds(folder):
    out = []
    for read_only in (True, False):
        p = os.path.join(folder, f'seed{int(read_only)}.stfs')
        READERS.build_stfs(p, read_only)
        data = open(p, 'rb').read()
        tables = 1 if read_only else 2
        base = 0xA000
        table = base + tables * 0x1000                  # data block 0: the file table
        fields = [(0x340, '>I'), (0x37B, 'B'), (0x37C, '<H'), (0x37E, '<H'), (0x395, '>I'), (0x379, 'B')]
        for k in range(4):
            e = table + 0x40 * k
            fields += [(e + 0x28, 'B'), (e + 0x29, '<H'), (e + 0x2F, '<H'), (e + 0x32, '>H'), (e + 0x34, '>I')]
        fields += [(base + b * 0x18 + 0x16, '>H') for b in (0, 3, 4, 5)]
        bounds = [0x340, 0x37C, 0x39D, 0x1000, base] + [base + 0x1000 * k for k in range(1, tables + 8)]
        out.append((data, bounds, fields))
    return out


def test_package(tmp_path):
    made = seeds(str(tmp_path))

    def read(data):
        stfs.open = lambda path, mode: io.BytesIO(data)     # the case in memory: Package opens no file
        try:
            pkg = stfs.Package('case.stfs')
        finally:
            del stfs.open
        return [(e['path'], pkg.read(e)) for e in pkg.entries if not e['dir']]
    # a package cut inside the padding of its last block is read the same: it lost no data (same=)
    report = fuzzcheck.run('stfs.Package', read, [d for d, _, _ in made], iterations=1500,
                           boundaries=[b for _, b, _ in made], fields=[f for _, _, f in made],
                           same=lambda a, b: a == b)
    assert report.ok, report.text()


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_block_count(tmp_path):
    """A file entry of 2^24 - 1 blocks holding 6,000 bytes was read block by block (millions of reads past the end,
    and a list of every block number) before its size was checked; now only its size's blocks are read."""
    import time
    data, _, _ = seeds(str(tmp_path))[0]
    p = str(tmp_path / 'count.stfs')
    damaged = bytearray(data)
    e = 0xA000 + 0x1000 + 0x40                          # the read-only seed's file table, entry 1 (Content/a.bin)
    damaged[e + 0x29:e + 0x2C] = (0xFFFFFF).to_bytes(3, 'little')
    with open(p, 'wb') as f:
        f.write(bytes(damaged))
    pkg = stfs.Package(p)
    try:
        t = time.perf_counter()
        entry = next(x for x in pkg.entries if x['path'] == 'Content/a.bin')
        assert len(pkg.read(entry)) == 6000 and time.perf_counter() - t < 2.0
    finally:
        pkg.f.close()
