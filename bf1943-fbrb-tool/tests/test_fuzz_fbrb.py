r"""fbrb1943.Bundle fuzzed: damaged bundles refused with ValueError

Mutation fuzzing (core/fuzzcheck.py) of the bundle reader on made-up bundles built by test_fbrb_guards.py's builder
(no game data): the whole file (stored and gzip payloads) and, under its gzip, the directory itself. On any input
Bundle returns or raises ValueError (never struct.error, gzip's BadGzipFile, EOFError or zlib.error), within its
time and memory limits, and a bundle it accepts rebuilds (Bundle.build) to one that reads back the same. Fixed seeds;
BF1943_FUZZ=long for more (README "Tests").
"""
import gzip
import struct

import fbrb1943
import fuzzcheck

GUARDS = fuzzcheck.fixtures('bf1943-fbrb-tool/tests/test_fbrb_guards.py')
ENTRIES = (('a/one.bin', b'first entry'), ('two.bin', b'second'), ('levels/x.dbx', bytes(range(40))))


def compressed(entries=ENTRIES):
    """made_up()'s bundle with its payload gzip, as most of the game's are (the directory's flag byte 1)."""
    raw = GUARDS.made_up(entries)
    cut = struct.unpack_from('>I', raw, 4)[0]
    d = bytearray(gzip.decompress(raw[8:8 + cut]))
    d[-5] = 1
    directory = fbrb1943.gzip_member(bytes(d), GUARDS.HEADER)
    payload = fbrb1943.gzip_member(b''.join(x for _, x in entries), GUARDS.HEADER)
    return b'FbRB' + struct.pack('>I', len(directory)) + directory + payload


def read(raw):
    b = fbrb1943.Bundle(raw)
    for e in b.entries:
        b.data(e['name'])
    return b


def same_after_build(b, _raw):
    """A bundle the reader accepts rebuilds to one that reads back with the same entries and bytes, or build() refuses
    it in words (a gzip header with a flag set, which it does not copy: README of the bundle tool)."""
    try:
        rebuilt = b.build()[0]
    except ValueError:
        return None
    again = fbrb1943.Bundle(rebuilt)
    if [(e['name'], e['type'], e['size']) for e in again.entries] != [(e['name'], e['type'], e['size'])
                                                                      for e in b.entries]:
        return 'the rebuilt bundle lists other entries'
    if again.payload != b.payload:
        return 'the rebuilt bundle holds other bytes'
    return None


def test_bundle_bytes():
    seeds = [GUARDS.made_up(ENTRIES), compressed()]
    bounds = []
    for s in seeds:
        cut = struct.unpack_from('>I', s, 4)[0]
        bounds.append([0, 4, 8, 18, 8 + cut - 8, 8 + cut, 8 + cut + 10, len(s) - 8, len(s) - 1])
    report = fuzzcheck.run('fbrb1943.Bundle', read, seeds, iterations=1500, boundaries=bounds,
                           fields=[[(4, '>I')], [(4, '>I')]], check=same_after_build)
    assert report.ok, report.text()


def test_bundle_directory():
    """The directory's own fields (counts, offsets, sizes, the string table), mutated under a fresh gzip member."""
    raw = GUARDS.made_up(ENTRIES)
    cut = struct.unpack_from('>I', raw, 4)[0]
    d = gzip.decompress(raw[8:8 + cut])
    payload = raw[8 + cut:]
    strlen = struct.unpack_from('>I', d, 4)[0]
    count = struct.unpack_from('>I', d, 8 + strlen)[0]
    at = 12 + strlen
    fields = [(0, '>I'), (4, '>I'), (8 + strlen, '>I'), (len(d) - 4, '>I')] + \
        [(at + 24 * k + 4 * w, '>I') for k in range(count) for w in range(6)]
    bounds = [4, 8, 8 + strlen, at, at + 24, len(d) - 5, len(d) - 4]

    def bundle_of(directory):
        z = fbrb1943.gzip_member(directory, GUARDS.HEADER)
        return read(b'FbRB' + struct.pack('>I', len(z)) + z + payload)
    report = fuzzcheck.run('fbrb1943 directory', bundle_of, [d], iterations=1500, boundaries=[bounds],
                           fields=[fields], check=lambda b, _d: same_after_build(b, None))
    assert report.ok, report.text()


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_cut_and_damaged_gzip():
    """A header cut inside its 8 bytes or a directory cut short was a struct.error; a directory or payload that is not
    whole gzip data passed gzip's BadGzipFile and EOFError through."""
    raw = GUARDS.made_up(ENTRIES)
    for data in (b'FbRB', b'FbRB' + bytes(4), raw[:0x12], b'FbRB' + struct.pack('>I', 1) + b'\x1f\0', compressed()[:-8]):
        assert fuzzcheck.outcome(fbrb1943.Bundle, data).startswith('ValueError'), fuzzcheck.outcome(fbrb1943.Bundle, data)


def test_regression_directory_fields():
    """A directory of 4 or 8 bytes was a struct.error; a name offset past the string table read a name out of the
    entries after it."""
    raw = GUARDS.made_up(ENTRIES)
    cut = struct.unpack_from('>I', raw, 4)[0]
    d = gzip.decompress(raw[8:8 + cut])

    def bundle_of(directory):
        z = fbrb1943.gzip_member(directory, GUARDS.HEADER)
        return fbrb1943.Bundle(b'FbRB' + struct.pack('>I', len(z)) + z + raw[8 + cut:])
    assert fuzzcheck.refused(bundle_of, d[:4]) and fuzzcheck.refused(bundle_of, d[:8])
    strlen = struct.unpack_from('>I', d, 4)[0]
    past = bytearray(d)
    struct.pack_into('>I', past, 12 + strlen, strlen + 4)       # entry 0's name: just past the string table
    assert fuzzcheck.refused(bundle_of, bytes(past))
