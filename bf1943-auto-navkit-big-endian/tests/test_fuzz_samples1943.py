r"""NavKit (big-endian): the audit's sample reader fuzzed (navaudit1943.load_samples)

Mutation fuzzing (core/fuzzcheck.py) of navaudit1943.load_samples, the numpy reader of navsample1943's version-1
samples that the audit and jump links use, on a made-up file (no game data). On any input it returns or raises
ValueError within its time and memory limits, and what it returns is what navsample.load reads from the same file
(an interrupted run keeps its finished columns in both). Fixed seeds; BF1943_FUZZ=long for more (README "Tests").
"""
import struct

import numpy as np

import fuzzcheck
import navaudit1943
import navsample


def samples(columns=4):
    head = navsample.HEADER.pack(b'ONS1', 1, 0.8, 0.0, 0.0, columns, 1, 100, -100, 0.4, 0.3, 1.8, 0.1, 0)
    out = bytearray(head)
    for c in range(columns):
        out.append(c % 3)
        for k in range(c % 3):
            out += struct.pack('<f3bB', float(k), 0, 127, 0, 0)
    return bytes(out)


def test_load_samples(tmp_path):
    seed = samples()
    path = str(tmp_path / 'samples.bin')

    def load(d):
        with open(path, 'wb') as f:
            f.write(d)
        return navaudit1943.load_samples(path)

    def as_navsample(s, d):
        """The same columns and surfaces as navsample.load."""
        ref, _ = navsample.load(path)
        if not (np.array_equal(s.start, ref.start) and np.array_equal(s.y, ref.y, equal_nan=True)
                and np.array_equal(s.flags, ref.flags) and np.allclose(s.normal, ref.normal)):
            return 'load_samples and navsample.load read the file differently'
        return None
    h = navsample.HEADER.size
    # an interrupted run keeps its finished columns: a cut copy is a shorter run (prefix_ok), read as navsample does
    # memory: a header may ask for up to navsample.MAX_COLUMNS columns (three 8-byte tables here)
    report = fuzzcheck.run('navaudit1943.load_samples', load, [seed], iterations=800, prefix_ok=True,
                           memory=4 << 30, limit=10.0,
                           boundaries=[[h, h + 1, len(seed) - 1]], fields=[[(4, '<I'), (20, '<i'), (24, '<i')]],
                           check=as_navsample)
    assert report.ok, report.text()


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_interrupted_column(tmp_path):
    """A run that stopped inside a column's records: navsample.load keeps the finished columns, load_samples indexed
    past the end of the file (IndexError). A cut header was a struct.error."""
    path = str(tmp_path / 'samples.bin')
    seed = samples()
    with open(path, 'wb') as f:
        f.write(seed[:-3])                                        # inside column 2's second record
    s = navaudit1943.load_samples(path)
    ref, cols = navsample.load(path)
    assert cols == 2 and np.array_equal(s.start, ref.start) and np.array_equal(s.y, ref.y)
    with open(path, 'wb') as f:
        f.write(seed[:30])
    assert fuzzcheck.refused(navaudit1943.load_samples, path)
