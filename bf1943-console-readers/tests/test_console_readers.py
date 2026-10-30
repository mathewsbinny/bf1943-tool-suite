r"""The STFS reader (stfs.py) on packages built here: both kinds (read-only: one hash table per level; others:
two), with a consecutive file, a fragmented file (hash-entry chain), a folder; info, list and extract, with a
filter; damaged packages (parent loops, short files, block chains that loop) refused, and a damaged file leaves
an earlier extracted copy intact. Your own BF1943 package is read by the Xenia kit's full build test (run_tests.py bf1943).

  python test_console_readers.py
"""
import contextlib
import io
import os
import shutil
import struct
import sys
import tempfile

sys.dont_write_bytecode = True                        # run_tests.py starts tests without -B
HERE =os.path.dirname(os.path.abspath(__file__))
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
        # a fragmented file's block chain that loops (b.bin: 5 -> 3 -> 4): refused, not the same block read twice
        hashes = table - 0x1000                                         # the level-0 hash table of a read-only package
        for label, links in (('loops on its first block', {5: 5}), ('comes back to its first block', {3: 5})):
            path = os.path.join(work, 'loop')
            build_stfs(path, True)
            with open(path, 'r+b') as f:
                for b, nxt in links.items():
                    f.seek(hashes + b * 0x18 + 0x15)
                    f.write(nxt.to_bytes(3, 'big'))
            p = stfs.Package(path)
            try:
                got = p.read(next(e for e in p.entries if e['path'] == 'Content/b.bin'))
                why = f'{len(got)} bytes returned'
            except ValueError as e:
                why = str(e)
            p.f.close()
            c.check('chain' in why and 'returned' not in why, f'stfs: a block chain that {label} is refused', why)
        # a damaged file met by extract leaves the copy extracted earlier as it was (read before anything is written)
        path = os.path.join(work, 'again')
        build_stfs(path, True)
        dest = os.path.join(work, 'again_out')
        stfs_cli('extract', path, dest, 'top.txt')
        with open(path, 'r+b') as f:
            f.seek(table + 3 * 0x40 + 0x34)                             # top.txt: a size larger than its one block
            f.write(struct.pack('>I', 0x1001))
        try:
            stfs_cli('extract', path, dest, 'top.txt')
            why = 'extracted without an error'
        except ValueError as e:
            why = str(e)
        kept = open(os.path.join(dest, 'top.txt'), 'rb').read()
        c.check('damaged' in why and kept == b'top level' and os.listdir(dest) == ['top.txt'],
                'stfs: a damaged file leaves its earlier extracted copy intact (no temporary file left)',
                f'{why}; top.txt holds {kept!r}; folder {os.listdir(dest)}')
        # 2026-10-30 review: the command line's exit codes; extract's collisions; damaged file tables; second copies
        path = os.path.join(work, 'cli')
        build_stfs(path, True)
        code, out = stfs_cli('lsit', path)
        c.check(code == 1 and 'Usage' in out, f'stfs: an unknown command prints the usage and exits 1 (exit {code})')
        code, out = stfs_cli('extract', path)
        c.check(code == 1 and 'Usage' in out, f'stfs: extract without a folder prints the usage and exits 1 (exit {code})')

        def renamed(name, index, parent=None):
            """The package with entry <index> renamed (and moved to <parent>)."""
            build_stfs(path, True)
            with open(path, 'r+b') as f:
                f.seek(table + index * 0x40)
                e = bytearray(f.read(0x40))
                e[:0x28] = name.encode('latin-1').ljust(0x28, b'\0')
                e[0x28] = (e[0x28] & 0xC0) | len(name)
                if parent is not None:
                    struct.pack_into('>H', e, 0x32, parent)
                f.seek(table + index * 0x40)
                f.write(e)
        for label, args in (('two files whose names differ only in case', ('TOP.TXT', 1, 0xFFFF)),
                            ('a file named as other files\' folder', ('content', 3))):
            renamed(*args)
            dest = os.path.join(work, 'collide_out')
            shutil.rmtree(dest, ignore_errors=True)
            try:
                stfs_cli('extract', path, dest)
                why = 'extracted without an error'
            except ValueError as e:
                why = str(e)
            c.check('damaged' in why and not os.path.exists(dest), f'stfs: extract refuses {label}, nothing written', why)
        for label, edit in (('a parent past the file table', lambda e: struct.pack_into('>H', e, 0x32, 0x0123)),
                            ('a parent that is a file', lambda e: struct.pack_into('>H', e, 0x32, 1)),
                            ('a name longer than its 40-byte field', lambda e: e.__setitem__(0x28, 0x40 | 0x30))):
            build_stfs(path, True)
            with open(path, 'r+b') as f:
                f.seek(table + 2 * 0x40)                                # Content/b.bin
                e = bytearray(f.read(0x40))
                edit(e)
                f.seek(table + 2 * 0x40)
                f.write(e)
            try:
                stfs.Package(path).f.close()
                why = 'read without an error'
            except ValueError as e:
                why = str(e)
            c.check('damaged' in why, f'stfs: {label} is refused', why)
        build_stfs(path, False)
        with open(path, 'r+b') as f:
            f.seek(0x37B)
            f.write(bytes([2]))                                         # its hash tables' second copies are current
        try:
            stfs.Package(path).f.close()
            why = 'read without an error'
        except ValueError as e:
            why = str(e)
        c.check('second copies' in why, 'stfs: a package whose second hash-table copies are current is refused', why)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
