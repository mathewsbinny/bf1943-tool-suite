"""stfs.py - read-only lister/extractor for Xbox 360 content packages (STFS: LIVE / PIRS / CON).

Reads a package file: `info` and `list` print its header and file table, `extract` writes the chosen files under an
output folder. Package(path) is the library form: .entries (the file table, one dict per entry) and .read(entry).

Usage: python stfs.py info <package>
       python stfs.py list <package> [filter]
       python stfs.py extract <package> <outdir> [filter]      (filter = case-insensitive substring)
       extract refuses (nothing written) a package whose names would leave <outdir> ('..', a drive, ...).
       A damaged file (a block chain that loops, fewer bytes than its size) stops extract with an error and leaves
       an earlier extracted copy of that file as it was. Two files that would be one file here (names equal but
       for case) or a file where a folder goes also stop extract before anything is written.
       An unknown command or a missing argument prints this and exits 1. Refused as damaged: a name longer than
       its 40-byte field, a parent that is no folder entry, a header or file table cut short. Refused as no STFS
       package: no LIVE / PIRS / CON signature, a volume descriptor size other than 0x24, a header size outside
       0x39D-0x100000, a file table of no blocks. Refused unread: a
       package whose block separation has bit 1 set (its second hash-table copies are current; this reader reads
       the first copies only), and a package with two copies of each hash table and more than 0xAA allocated
       blocks (there each level-1 entry chooses its level-0 table's current copy, which this reader does not follow).

Format (public STFS documentation, Free60): header with display name (UTF-16BE at 0x411), header size
(u32 BE at 0x340) and the volume descriptor at 0x379 (block separation u8 at 0x37B, file-table block
count u16 LE at 0x37C, file-table first block u24 LE at 0x37E, allocated block count u32 BE at 0x395). Data blocks are 0x1000 bytes from the
first 4 KiB boundary after the header, with a hash table interleaved before every 0xAA data blocks (and
a level-1 table every 0x70E4); hash entries (0x18 bytes: SHA-1, status, next block u24 BE) chain the
blocks of fragmented files. File table entries are 0x40 bytes: name (0x28), flags (low 6 bits = name
length, 0x40 = blocks consecutive, 0x80 = directory), block count u24 LE at 0x29, first block u24 LE at
0x2F, parent index u16 BE at 0x32 (0xFFFF = root), size u32 BE at 0x34.
"""
import os
import struct
import sys
import tempfile
from typing import Any

BLOCK = 0x1000
HEADER_MIN = 0x39D          # through the volume descriptor's allocated and unallocated block counts

# a file table entry: name, dir, consecutive, count, start, parent and size as read, and path (from its parents)
type Entry = dict[str, Any]


class Package:
    """An open package (its file stays open in .f until closed): its header fields and file table (.entries)."""

    def __init__(self, path: str) -> None:
        self.f = open(path, 'rb')
        header = self.f.read(0x1000)
        if len(header) < HEADER_MIN:                # 2026-10-30 review: an IndexError before
            self.f.close()
            raise ValueError(f'damaged package: {len(header)} bytes, shorter than an STFS header '
                             f'({HEADER_MIN:#x} bytes); nothing was read')
        self.magic = header[:4]
        self.name = header[0x411:0x411 + 0x80].decode('utf-16-be', 'ignore').split('\0')[0]
        self.header_size: int = struct.unpack_from('>I', header, 0x340)[0]
        self.separation = header[0x37B]
        self.ft_blocks: int = struct.unpack_from('<H', header, 0x37C)[0]
        self.ft_start = int.from_bytes(header[0x37E:0x381], 'little')
        self.allocated: int = struct.unpack_from('>I', header, 0x395)[0]
        self.base = (self.header_size + 0xFFF) & ~0xFFF
        # read-only packages (separation bit 0 set) keep one hash table per level; others keep two
        self.shift = 0 if (self.separation & 1) else 1
        # an STFS package: its signature, a volume descriptor of 0x24 bytes, a header that ends past the descriptor,
        # and a file table of at least one block (2026-10-30 second review: 4,096 zero bytes read as an empty package)
        why = ('no LIVE, PIRS or CON signature' if self.magic not in (b'LIVE', b'PIRS', b'CON ') else
               f'volume descriptor size {header[0x379]:#x}, not 0x24' if header[0x379] != 0x24 else
               f'header size {self.header_size:#x}' if not HEADER_MIN <= self.header_size <= 0x100000 else
               'a file table of no blocks' if not self.ft_blocks else None)
        if why:
            self.f.close()
            raise ValueError(f'not an STFS package ({why}); nothing was read')
        try:
            if self.shift == 1 and self.allocated > 0xAA:
                # two copies of each table and more than one level: each level-1 entry's status bit 0x40 says which
                # copy of its level-0 table is current (Velocity, GetHashAddressOfBlock). This reader reads the first
                # copies only, so it could follow stale block chains (2026-10-30 review: refused, not read wrong)
                raise ValueError(f'package not read: it keeps two copies of each hash table and has {self.allocated} '
                                 f'blocks (over 0xAA, so level-1 entries choose each level-0 copy), which this reader '
                                 f'does not follow')
            if self.separation & 2:
                # bit 1: the package was last written to the second copy of its hash tables. This reader follows
                # block chains through the first copy only, which may then be stale (2026-10-30: refused rather
                # than read wrong; the active-copy rules are not implemented)
                raise ValueError(f'package not read: its volume descriptor (block separation {self.separation:#x}, '
                                 f'bit 1 set) says its hash tables\' second copies are the current ones, and this '
                                 f'reader reads only the first copies')
            self.entries = self._file_table()
        except BaseException:
            self.f.close()
            raise

    def _fix(self, block: int) -> int:
        """Data block number -> raw block number: a level-0 hash table precedes every 0xAA data blocks
        (the first one included), a level-1 table every 0x70E4, a level-2 table after that."""
        with_level0 = (((block + 0xAA) // 0xAA) << self.shift) + block
        if block < 0xAA:
            return with_level0
        level1_tables = ((block + 0x70E4) // 0x70E4) << self.shift
        if block < 0x70E4:
            return with_level0 + level1_tables
        return (1 << self.shift) + with_level0 + level1_tables

    def _addr(self, block: int) -> int:
        return self.base + self._fix(block) * BLOCK

    def _hash_entry(self, block: int) -> bytes:
        """Level-0 hash entry of a data block (first copy of the table)."""
        if block < 0xAA:
            raw = 0
        else:
            raw = (block // 0xAA) * (0xAA + (1 << self.shift)) + (((block // 0x70E4) + 1) << self.shift)
            if block // 0x70E4:
                raw += 1 << self.shift
        self.f.seek(self.base + raw * BLOCK + (block % 0xAA) * 0x18)
        return self.f.read(0x18)

    def _next(self, block: int) -> int:
        entry = self._hash_entry(block)
        return int.from_bytes(entry[0x15:0x18], 'big')

    def _blocks(self, start: int, count: int, consecutive: bool) -> list[int]:
        """A file's data block numbers: a run from start, or the chain its hash entries name."""
        if consecutive:
            return list(range(start, start + count))
        out: list[int] = []
        block = start
        seen: set[int] = set()
        for _ in range(count):
            if block in seen:                       # a damaged chain: it loops back to a block already read
                raise ValueError(f'damaged package: the block chain from block {start} comes back to block {block} '
                                 f'after {len(out)} block(s); nothing was read')
            seen.add(block)
            out.append(block)
            block = self._next(block)
        return out

    def _file_table(self) -> list[Entry]:
        joined = bytearray()                        # joined in place (up to 65,535 blocks: bytes += copied them all)
        for block in self._blocks(self.ft_start, self.ft_blocks, False):
            self.f.seek(self._addr(block))
            chunk = self.f.read(BLOCK)
            if len(chunk) < BLOCK:                  # 2026-10-30 review: a cut table was read past its end
                raise ValueError(f'damaged package: the file table\'s block {block} holds {len(chunk)} of {BLOCK} '
                                 f'bytes (the package is cut short); nothing was read')
            joined += chunk
        raw = bytes(joined)
        entries: list[Entry] = []
        for i in range(0, len(raw), 0x40):
            e = raw[i:i + 0x40]
            flags = e[0x28]
            if not flags & 0x3F:
                break
            # the name field is 0x28 bytes: a longer name reads the entry's other fields
            if flags & 0x3F > 0x28:
                raise ValueError(f'damaged package: entry {i // 0x40} has a name of {flags & 0x3F} bytes in a 40-byte '
                                 f'field; nothing was read')
            entries.append({'name': e[:flags & 0x3F].decode('latin-1'), 'dir': bool(flags & 0x80),
                            'consecutive': bool(flags & 0x40), 'count': int.from_bytes(e[0x29:0x2C], 'little'),
                            'start': int.from_bytes(e[0x2F:0x32], 'little'),
                            'parent': struct.unpack_from('>H', e, 0x32)[0],
                            'size': struct.unpack_from('>I', e, 0x34)[0]})
        for i, e in enumerate(entries):
            parent = e['parent']
            if parent != 0xFFFF and (parent >= len(entries) or not entries[parent]['dir']):  # before: taken as the root
                raise ValueError(f'damaged package: entry {i} ({e["name"]!r}) names entry {parent} as its parent '
                                 f'folder, and '
                                 f'{"there is no such entry" if parent >= len(entries) else "that entry is a file"}; '
                                 f'nothing was read')
        for i, e in enumerate(entries):
            parts, parent, seen = [e['name']], e['parent'], {i}
            while parent != 0xFFFF and parent < len(entries):
                if parent in seen:                  # a damaged table: the parent chain loops
                    raise ValueError(f'damaged package: entry {i} ({e["name"]!r}) has itself among its parent '
                                     f'folders (entry {parent}); nothing was read')
                seen.add(parent)
                parts.append(entries[parent]['name'])
                parent = entries[parent]['parent']
            e['path'] = '/'.join(reversed(parts))
        return entries

    def read(self, e: Entry) -> bytes:
        """The file's bytes; ValueError when its blocks hold fewer than its declared size (a damaged or cut package)."""
        if e['size'] > e['count'] * BLOCK:
            raise ValueError(f'damaged package: {e["path"]} declares {e["size"]} bytes in {e["count"]} block(s) '
                             f'of {BLOCK} bytes')
        out = bytearray()
        # only the blocks that hold its size, and none past the end of the package (2026-10-31 review, while fuzzing:
        # a count of 2^24 blocks was read block by block, millions of reads past the end, before the size check
        # refused it)
        need = min(e['count'], -(-e['size'] // BLOCK))
        for block in self._blocks(e['start'], need, e['consecutive']):
            self.f.seek(self._addr(block))
            chunk = self.f.read(BLOCK)
            out += chunk
            if len(chunk) < BLOCK:
                break
        if len(out) < e['size']:
            raise ValueError(f'damaged package: {e["path"]} declares {e["size"]} bytes, the package holds {len(out)} '
                             f'(it ends early)')
        return bytes(out[:e['size']])


def safe_target(dest: str, path: str) -> str:
    """Where an archive path is extracted under dest; ValueError for a path that would leave it (a '..', '.' or
    empty part, a drive or root, a backslash or colon in a name - package names are untrusted input)."""
    parts = path.split('/')
    for part in parts:
        if part in ('', '.', '..') or '\\' in part or ':' in part or part != part.strip():
            raise ValueError(f'unsafe path in the package: {path!r} (part {part!r}); nothing was extracted')
    root = os.path.realpath(dest)
    target = os.path.realpath(os.path.join(root, *parts))
    if os.path.commonpath([root, target]) != root or target == root:
        raise ValueError(f'unsafe path in the package: {path!r} leaves {dest}; nothing was extracted')
    return target


def write_file(dst: str, data: bytes) -> None:
    """dst holding data, or as it was: written to a temporary file beside it, then put in its place."""
    folder = os.path.dirname(dst)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.stfs-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


COMMANDS = {'info': (3, 3), 'list': (3, 4), 'extract': (4, 5)}      # command -> (fewest, most) arguments with argv[0]


def extract_plan(pkg: Package, dest: str, flt: str) -> list[tuple[Entry, str]]:
    """[(entry, target)] of the files extract writes, every package path checked first (safe_target); ValueError when
    two files would be one file on this system (names equal but for case, here: the second would replace the first)
    or a file would stand where another file's folder goes. Nothing is written before this passes."""
    for e in pkg.entries:
        safe_target(dest, e['path'])
    root = os.path.normcase(os.path.realpath(dest))
    plan: list[tuple[Entry, str]] = []
    owner: dict[str, str] = {}          # target (normcased) -> package path
    folders: dict[str, str] = {}        # folder (normcased) -> the first package path under it
    for e in pkg.entries:
        if e['dir'] or flt not in e['path'].lower():
            continue
        target = safe_target(dest, e['path'])
        key = os.path.normcase(target)
        if key in owner:
            raise ValueError(f'damaged package: {owner[key]!r} and {e["path"]!r} would be one file on this system; '
                             f'nothing was extracted')
        owner[key] = e['path']
        folder = os.path.dirname(key)
        while len(folder) > len(root):
            folders.setdefault(folder, e['path'])
            folder = os.path.dirname(folder)
        plan.append((e, target))
    for key, path in owner.items():
        if key in folders:
            raise ValueError(f'damaged package: the file {path!r} would stand where {folders[key]!r} needs a folder; '
                             f'nothing was extracted')
    return plan


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS or not COMMANDS[argv[1]][0] <= len(argv) <= COMMANDS[argv[1]][1]:
        print(__doc__)                              # an unknown command or a missing argument does nothing: exit 1
        return 1
    cmd = argv[1]
    pkg = Package(argv[2])
    try:
        if cmd == 'info':
            print(f'{pkg.magic!r} "{pkg.name}" header {pkg.header_size:#x} separation {pkg.separation} '
                  f'file table {pkg.ft_blocks} blocks at {pkg.ft_start}; {len(pkg.entries)} entries')
        elif cmd == 'list':
            flt = argv[3].lower() if len(argv) > 3 else ''
            for e in pkg.entries:
                if not e['dir'] and flt in e['path'].lower():
                    print(f'{e["size"]:>12}  {e["path"]}')
        else:
            flt = argv[4].lower() if len(argv) > 4 else ''
            for e, dst in extract_plan(pkg, argv[3], flt):      # every path checked before anything is written
                write_file(dst, pkg.read(e))            # read (and checked) first: a damaged entry leaves dst as it was
                print(f'{e["size"]:>12}  {dst}')
    finally:
        pkg.f.close()
    return 0


if __name__ == '__main__':
    # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
    from bfbc2core import errors
    errors.setup('stfs', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
