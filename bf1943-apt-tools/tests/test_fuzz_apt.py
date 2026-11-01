r"""apt.Apt fuzzed: damaged menu movies refused with ValueError

Mutation fuzzing (core/fuzzcheck.py) of the menu movie reader on test_apt_cli.py's made-up movie (no game data): the
BIGF archive as a whole, and the .apt movie inside it (rebuilt into the archive), read and validated as the command
line does (Apt, validate, then a listing of every block). On any input it returns or raises ValueError, within its
time and memory limits. Fixed seeds; BF1943_FUZZ=long for more (README "Tests").
"""
import struct

import apt
import fuzzcheck

CLI = fuzzcheck.fixtures('bf1943-apt-tools/tests/test_apt_cli.py')


def validated(raw):
    a = apt.Apt(raw)
    blocks, count, problems = a.validate()
    for _owner, start in a.action_blocks():
        try:
            a.listing(start)
        except ValueError:
            pass                    # validate() reported it
    return blocks, count, problems


def test_archive():
    seed = CLI.made_up(0)
    count = struct.unpack_from('>I', seed, 8)[0]
    fields = [(4, '<I'), (8, '>I')]
    p = 16
    for _ in range(count):
        fields += [(p, '>I'), (p + 4, '>I')]
        p = seed.index(b'\0', p + 8) + 1
    report = fuzzcheck.run('apt archive', validated, [seed], iterations=1500, boundaries=[[4, 8, 16, p, len(seed) - 1]],
                           fields=[fields])
    assert report.ok, report.text()


def test_movie():
    good = CLI.made_up(0)
    movie = apt.bigf(good)[0][1]
    m = 0x10
    fields = [(m + k, '>I') for k in range(0, 56, 4)] + [(0x50, '>I'), (0x54, '>I'), (0x58, '>I'), (0x60, '>I'),
                                                        (0x64, '>I'), (0x74, '>i')]
    report = fuzzcheck.run('apt movie', lambda d: validated(CLI.res_of(d)), [movie], iterations=1500,
                           boundaries=[[0x10, 0x50, 0x58, 0x60, 0x70, 0x78]], fields=[fields], prefix_ok=True)
    assert report.ok, report.text()


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_shared_block():
    """A movie whose frame items all name one long block was walked once per item: 3,000 items of a 20,000-instruction
    block took about a minute (60,000 items of 200,000, a 0.9 MB movie, hours). Now the blocks together may decode one
    instruction per byte of the .apt (no game movie shares or overlaps a block), and past that each is a problem."""
    import time
    n, m = 3000, 20000
    a = bytearray(0x100 + 12 * n + m + 16)
    lst, items = 0x100, 0x100 + 4 * n
    code = items + 8 * n
    struct.pack_into('>4I', a, 0x10, 9, apt.SIGNATURE, 1, 0x50)
    struct.pack_into('>2I', a, 0x50, n, lst)
    for k in range(n):
        struct.pack_into('>I', a, lst + 4 * k, items + 8 * k)
        struct.pack_into('>2I', a, items + 8 * k, 1, code)
    a[code:code + m] = b'\x73' * m                      # PushTrue ... End
    t = time.perf_counter()
    blocks, count, problems = apt.Apt(CLI.res_of(bytes(a))).validate()
    assert time.perf_counter() - t < 5.0 and blocks == n and problems and 'overlap' in problems[-1], problems[-1:]
