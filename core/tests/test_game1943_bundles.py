"""game1943.Game on a made-up game folder (no game data): a level bundle's file overrides the shared bundle's; a
damaged level bundle (its payload size wrong, cut short, not gzip) raises BundleError naming it, and never lets the
shared bundle's copy of a file stand in for the level's own (2026-10-30: every ValueError of the reader was taken for
"another layout" and the bundle skipped).

  python -B test_game1943_bundles.py
"""
import gzip
import os
import struct
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import game1943  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def bundle(files):
    """An uncompressed FbRB v2 bundle of {name: bytes} (fbrb1943's layout: entries contiguous in order)."""
    strings, entries, payload = b'', [], b''
    for name, data in files.items():
        n = len(strings)
        strings += name.encode('ascii') + b'\0'
        t = len(strings)
        strings += b'res\0'
        entries.append(struct.pack('>6I', n, 0, len(payload), len(data), len(data), t))
        payload += data
    d = struct.pack('>II', 2, len(strings)) + strings + struct.pack('>I', len(files)) + b''.join(entries) + \
        b'\0' + struct.pack('>I', len(payload))
    z = gzip.compress(d, mtime=0)
    return b'FbRB' + struct.pack('>I', len(z)) + z + payload


def game_folder(root, level_bundle):
    os.makedirs(os.path.join(root, 'levels', 'lv'))
    open(os.path.join(root, 'levels', game1943.COMMON), 'wb').write(bundle({'a/x.res': b'shared', 'a/y.res': b'only shared'}))
    open(os.path.join(root, 'levels', 'lv', 'level-00.fbrb'), 'wb').write(level_bundle)
    return root


if __name__ == '__main__':
    good = bundle({'a/x.res': b'level'})
    with tempfile.TemporaryDirectory() as d:
        g = game1943.Game(game_folder(os.path.join(d, 'good'), good), 'lv')
        check(g.data('a/x.res') == b'level' and g.data('a/y.res') == b'only shared',
              'the level bundle\'s file overrides the shared one; the shared bundle fills the rest')
        # damaged copies of the level bundle: the payload one byte short of the directory's size (the reader's
        # "payload size differs" - before, skipped as another layout), cut inside its directory, not gzip
        cut = struct.unpack_from('>I', good, 4)[0]
        for what, raw in (('payload one byte short', good[:-1]), ('cut inside its directory', good[:8 + cut // 2]),
                          ('directory not gzip', good[:8] + b'\0' * cut + good[8 + cut:])):
            root = game_folder(os.path.join(d, what.replace(' ', '_')), raw)
            try:
                g = game1943.Game(root, 'lv')
                got = f'loaded; a/x.res = {g.data("a/x.res")!r}'
            except game1943.BundleError as e:
                got = str(e)
            check(got.startswith(os.path.join(root, 'levels', 'lv', 'level-00.fbrb')) and 'damaged' in got,
                  f'{what}: BundleError naming the bundle, not the shared copy ({got[-90:]})')
        check(issubclass(game1943.BundleError, ValueError), 'BundleError is a ValueError (callers catching that still do)')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
