r"""The STFS reader (stfs.py) on packages built here: both kinds (read-only: one hash table per level; others:
two), with a consecutive file, a fragmented file (hash-entry chain), a folder; info, list and extract, with a
filter. Your own BF1943 package is read by the Xenia kit's full build test (run_tests.py bf1943).

  python test_console_readers.py
"""
import contextlib
import io
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(ROOT, 'stfs'))
import stfs  # noqa: E402


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''))


# ------------------------------------------------------------------------------------------------ STFS

def build_stfs(path, read_only, top_name='top.txt'):
    """Data blocks: 0 file table; 1-2 Content/a.bin (consecutive); 5 -> 3 -> 4 Content/b.bin (fragmented);
    6 top.txt (or top_name, for the path-safety cases)."""
    files = {'Content/a.bin': os.urandom(6000), 'Content/b.bin': os.urandom(9000), top_name: b'top level'}
    h = bytearray(0x1000)
    h[0:4] = b'LIVE'
    header_size = 0x971A
    struct.pack_into('>I', h, 0x340, header_size)
    h[0x37B] = 1 if read_only else 0
    struct.pack_into('<H', h, 0x37C, 1)
    h[0x37E:0x381] = (0).to_bytes(3, 'little')
    name = 'Test Package'.encode('utf-16-be')
    h[0x411:0x411 + len(name)] = name
    base = (header_size + 0xFFF) & ~0xFFF
    tables = 1 if read_only else 2

    def entry(name, flags, start, count, parent, size):
        e = bytearray(0x40)
        e[:len(name)] = name.encode('latin-1')
        e[0x28] = len(name) | flags
        e[0x29:0x2C] = count.to_bytes(3, 'little')
        e[0x2C:0x2F] = count.to_bytes(3, 'little')
        e[0x2F:0x32] = start.to_bytes(3, 'little')
        struct.pack_into('>H', e, 0x32, parent)
        struct.pack_into('>I', e, 0x34, size)
        return bytes(e)

    table = (entry('Content', 0x80, 0, 0, 0xFFFF, 0) + entry('a.bin', 0x40, 1, 2, 0, 6000) +
             entry('b.bin', 0x00, 5, 3, 0, 9000) + entry(top_name, 0x40, 6, 1, 0xFFFF, 9))
    blocks = {0: table, 1: files['Content/a.bin'][:0x1000], 2: files['Content/a.bin'][0x1000:],
              5: files['Content/b.bin'][:0x1000], 3: files['Content/b.bin'][0x1000:0x2000], 4: files['Content/b.bin'][0x2000:],
              6: files[top_name]}
    hashes = bytearray(0x1000)
    for b, nxt in ((0, 0xFFFFFF), (5, 3), (3, 4), (4, 0xFFFFFF)):
        hashes[b * 0x18 + 0x15:b * 0x18 + 0x18] = nxt.to_bytes(3, 'big')
    with open(path, 'wb') as f:
        f.write(h)
        for k in range(tables):                       # the level-0 hash table(s) before the first data block
            f.seek(base + k * 0x1000)
            f.write(hashes)
        for b, blob in blocks.items():
            f.seek(base + (tables + b) * 0x1000)
            f.write(blob.ljust(0x1000, b'\0'))
    return files


def stfs_cli(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = stfs.main(['stfs.py', *args])
    return code, buf.getvalue()


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='console_test_')
    try:
        for read_only in (True, False):
            label = 'read-only package' if read_only else 'package with two hash tables'
            path = os.path.join(work, f'pkg_{int(read_only)}')
            files = build_stfs(path, read_only)
            p = stfs.Package(path)
            c.check(p.magic == b'LIVE' and p.name == 'Test Package' and p.shift == (0 if read_only else 1), f'stfs: header ({label})')
            paths = {e['path']: e for e in p.entries}
            c.check(set(paths) == {'Content', 'Content/a.bin', 'Content/b.bin', 'top.txt'} and paths['Content']['dir'],
                    f'stfs: file table with folder paths ({label})', str(sorted(paths)))
            c.check(all(p.read(paths[k]) == v for k, v in files.items()), f'stfs: consecutive and fragmented files read exactly ({label})')
            p.f.close()
            code, out = stfs_cli('list', path, 'content/')
            c.check(code == 0 and 'Content/a.bin' in out and 'top.txt' not in out and 'Content\n' not in out, f'stfs: list with a filter ({label})')
            dest = os.path.join(work, f's_{int(read_only)}')
            code, out = stfs_cli('extract', path, dest)
            c.check(all(open(os.path.join(dest, *k.split('/')), 'rb').read() == v for k, v in files.items()), f'stfs: extract ({label})')
            code, out = stfs_cli('info', path)
            c.check(code == 0 and '4 entries' in out, f'stfs: info ({label})')
        # crafted names must not leave the destination: refused before anything is written
        for bad in ('../outside.bin', '..', '.', 'a\\..\\..\\x.bin', 'C:x.bin', ' top.txt'):
            path = os.path.join(work, 'evil')
            build_stfs(path, True, top_name=bad)
            dest = os.path.join(work, 'evil_out')
            shutil.rmtree(dest, ignore_errors=True)
            try:
                stfs_cli('extract', path, dest)
                refused = False
            except ValueError as e:
                refused = 'unsafe path' in str(e)
            outside = os.path.exists(os.path.join(work, 'outside.bin')) or os.path.exists(os.path.join(work, 'x.bin'))
            c.check(refused and not outside and not os.path.exists(dest), f'stfs: a package name {bad!r} is refused, nothing written')
        # damaged packages: a folder that is its own parent (or two that are each other's), a file shorter than its size
        table = (0x971A + 0xFFF & ~0xFFF) + 0x1000                     # data block 0 of a read-only package
        for label, parents in (('a folder that is its own parent', {0: 0}), ('two entries that are each other\'s parent', {0: 1, 1: 0})):
            path = os.path.join(work, 'cycle')
            build_stfs(path, True)
            with open(path, 'r+b') as f:
                for i, parent in parents.items():
                    f.seek(table + i * 0x40 + 0x32)
                    f.write(struct.pack('>H', parent))
            try:
                stfs.Package(path).f.close()
                why = 'read without an error'
            except ValueError as e:
                why = str(e)
            c.check('parent' in why, f'stfs: {label} is refused', why)
        for label, size, cut in (('a file cut short by the end of the package', 9, 3), ('a size larger than its blocks', 0x1001, None)):
            path = os.path.join(work, 'short')
            build_stfs(path, True)
            with open(path, 'r+b') as f:
                f.seek(table + 3 * 0x40 + 0x34)                         # top.txt, data block 6
                f.write(struct.pack('>I', size))
                if cut is not None:
                    f.truncate(table + 6 * 0x1000 + cut)
            p = stfs.Package(path)
            try:
                got = p.read(next(e for e in p.entries if e['path'] == 'top.txt'))
                why = f'{len(got)} bytes returned'
            except ValueError as e:
                why = str(e)
            p.f.close()
            c.check('bytes' in why and 'returned' not in why, f'stfs: {label} is refused', why)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
