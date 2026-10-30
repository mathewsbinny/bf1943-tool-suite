"""Read and write BF1943 Xenon FbRB bundles (directory version 2).

Layout, as observed in 82 of this build's 85 bundles (the reader refuses the other three, whose payload
size or entry layout differs: async/ondemand_awards-00, async/ondemand_sounds-00, build_overlay-00): "FbRB", u32 size of the gzip directory, the gzip
directory, then the payload (gzip when the directory's tail flag is 1). The directory is: u32 version (2),
u32 string-table size, the strings, u32 entry count, entries of six u32 (name offset, flags, payload offset,
size, size again, type-name offset), then a flag byte (payload compressed) and u32 payload size. Entries
are contiguous in directory order, without alignment. gzip members carry mtime 0 and OS byte 0x0b.

`Bundle.build` rewrites only offsets and sizes; with no replaced entries it reproduces the original directory and
(decompressed) payload byte-for-byte. A compressed bundle's gzip bytes differ, since zlib is not the encoder DICE's
tools used; the game reads the decompressed content (checked on all 82 bundles by tests/test_bf1943_data.py bundles).

Command line:

  python fbrb1943.py list <bundle.fbrb> [filter]          entries: size, type, name (filter: part of the name)
  python fbrb1943.py info <bundle.fbrb>                    counts, sizes, compression
  python fbrb1943.py extract <bundle.fbrb> <out dir> [filter]
                                                           the entries as files under their names, and
                                                           fbrb.manifest.json (name, type, flags, size, sha256);
                                                           never into the game folder, and never through a link
                                                           or junction to outside <out dir> (each destination
                                                           is resolved and checked before it is written)
  python fbrb1943.py replace <bundle.fbrb> <out.fbrb> <entry name>=<file> [...]
                                                           a copy with entries replaced (names as listed);
                                                           never written into the game folder or over the input

The game folder (core game1943.find_game) is only read: a changed bundle goes elsewhere, for the Xenia kit or a
mod folder to use. Every file is written beside its destination and moved into place (write_file), so a hard link
already at a destination is replaced, never written through.
"""
import gzip
import hashlib
import json
import os
import struct
import sys
import tempfile
import zlib

sys.dont_write_bytecode = True


def gzip_member(data, template_header):
    """gzip bytes with the template's 10-byte header (mtime, flags and OS byte as the game's tools wrote)."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    return template_header + c.compress(data) + c.flush() + struct.pack('<II', zlib.crc32(data), len(data) & 0xFFFFFFFF)


class Bundle:
    def __init__(self, raw):
        if raw[:4] != b'FbRB':
            raise ValueError('not an FbRB bundle')
        cut = struct.unpack_from('>I', raw, 4)[0]
        self.raw, self.dir_header = raw, raw[8:18]
        self.directory = gzip.decompress(raw[8:8 + cut])
        d = self.directory
        if struct.unpack_from('>I', d, 0)[0] != 2:
            raise ValueError('unsupported directory version')
        strlen = struct.unpack_from('>I', d, 4)[0]
        self.strings_at, count = 8, struct.unpack_from('>I', d, 8 + strlen)[0]
        self.entries_at = 12 + strlen
        self.tail_at = self.entries_at + 24 * count
        if self.tail_at + 5 != len(d):
            raise ValueError('unexpected directory length')
        self.compressed = bool(d[self.tail_at])
        body = raw[8 + cut:]
        self.payload_header = body[:10] if self.compressed else None
        self.payload = gzip.decompress(body) if self.compressed else body
        if struct.unpack_from('>I', d, self.tail_at + 1)[0] != len(self.payload):
            raise ValueError('payload size differs from the directory')
        self.entries = []
        for k in range(count):
            name, flags, off, size, size2, kind = struct.unpack_from('>6I', d, self.entries_at + 24 * k)
            self.entries.append(dict(name=self.cstr(name), type=self.cstr(kind), flags=flags, offset=off, size=size, size2=size2))
        pos = 0
        for e in self.entries:
            if e['offset'] != pos or e['size'] != e['size2']:
                raise ValueError(f'entry layout differs from the observed convention: {e["name"]}')
            pos += e['size']
        if pos != len(self.payload):
            raise ValueError('entries do not cover the payload')

    def cstr(self, o):
        d = self.directory
        return d[8 + o:d.index(0, 8 + o)].decode('ascii')

    def data(self, name):
        e = next(e for e in self.entries if e['name'] == name)
        return self.payload[e['offset']:e['offset'] + e['size']]

    def build(self, replace=None):
        """Bundle bytes with entries replaced by name ({name: bytes}); every other byte layout is kept."""
        replace = dict(replace or {})
        missing = set(replace) - {e['name'] for e in self.entries}
        if missing:
            raise KeyError(f'not in the bundle: {sorted(missing)}')
        d = bytearray(self.directory)
        payload, pos = bytearray(), 0
        for k, e in enumerate(self.entries):
            data = replace.get(e['name'], self.payload[e['offset']:e['offset'] + e['size']])
            payload += data
            struct.pack_into('>III', d, self.entries_at + 24 * k + 8, pos, len(data), len(data))
            pos += len(data)
        struct.pack_into('>I', d, self.tail_at + 1, len(payload))
        directory = gzip_member(bytes(d), self.dir_header)
        body = gzip_member(bytes(payload), self.payload_header) if self.compressed else bytes(payload)
        return b'FbRB' + struct.pack('>I', len(directory)) + directory + body, bytes(d), bytes(payload)


def safe_path(out_dir, name):
    """Where entry <name> goes under out_dir; refuses names that would leave it."""
    p = os.path.normpath(os.path.join(out_dir, *name.replace('\\', '/').split('/')))
    if os.path.commonpath([os.path.abspath(p), os.path.abspath(out_dir)]) != os.path.abspath(out_dir):
        raise ValueError(f'entry name leaves the output folder: {name}')
    return p


def canonical(path):
    """path as Windows resolves it: links followed, case folded (C:\\Game and c:\\game are one folder)."""
    return os.path.normcase(os.path.realpath(path))


def contains(folder, path):
    """True when path is folder or lies in it; False on another drive (where os.path.commonpath raises)."""
    f, p = canonical(folder), canonical(path)
    try:
        return os.path.commonpath([f, p]) == f
    except ValueError:
        return False


def game_folder():
    """The BF1943 game folder (game1943.find_game), which the tools only read; None when there is none."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core'))
    try:
        from bfbc2core import game1943
        return game1943.find_game()
    except (ImportError, FileNotFoundError):
        return None


def inside_game(path):
    """True when path lies in the BF1943 game folder (game1943.find_game), which the tools only read."""
    game = game_folder()
    return game is not None and contains(game, path)


def own_game_root(bundle):
    """The game copy the input bundle belongs to, or None: the nearest folder above it that has a levels folder
    holding bundles (levels/*.fbrb or levels/<level>/*.fbrb), as a game copy does (2026-10-30: only find_game's
    copy was protected, so a bundle of another copy could be extracted, or replaced, into that copy)."""
    d = os.path.dirname(canonical(bundle))
    while True:
        levels = os.path.join(d, 'levels')
        if os.path.isdir(levels):
            try:
                names = os.listdir(levels)
                if any(n.lower().endswith('.fbrb') for n in names) or any(
                        f.lower().endswith('.fbrb') for n in names if os.path.isdir(os.path.join(levels, n))
                        for f in os.listdir(os.path.join(levels, n))):
                    return d
            except OSError:
                pass
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def protected(bundle):
    """The game folders the tool only reads: find_game's copy and the input bundle's own (own_game_root)."""
    return [g for g in (game_folder(), own_game_root(bundle)) if g is not None]


def in_protected(game, path):
    """The game folder (or one of the list of them) that holds path, or None."""
    for g in ([game] if isinstance(game, str) else game or ()):
        if contains(g, path):
            return g
    return None


def checked(out, path, game, name):
    """path, when it resolves (links and junctions followed) inside out and outside the game folder (game: one folder,
    a list of them, or None); ValueError otherwise. A link already inside out would send a write elsewhere although
    the name stays inside it."""
    in_game = in_protected(game, path) is not None
    if in_game or not contains(out, path):
        where = 'in the game folder, which the tools only read' if in_game else f'outside {out}'
        raise ValueError(f'{name} would be written at {canonical(path)}, {where} (through a link or junction); '
                         f'not written')
    return path


def write_file(dst, data):
    """dst holding data, or as it was: written to a temporary file beside it, then put in its place (os.replace).
    An existing dst is never opened for writing, so a hard link there (which realpath cannot see) to a file
    elsewhere, the game folder included, only loses its name in dst; the file it linked to is left as it was."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dst)), prefix='.fbrb-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def extract_target(out, name, game):
    """Where entry <name> is written under out (an existing folder), its folders made one at a time: each folder,
    and then the file, is resolved and checked (checked()) before anything is made in or written through it."""
    p = safe_path(out, name)
    cur = out
    for part in os.path.relpath(os.path.dirname(p), out).split(os.sep):
        if part in ('', '.'):
            continue
        cur = checked(out, os.path.join(cur, part), game, name)
        if not os.path.isdir(cur):
            os.mkdir(cur)
            checked(out, cur, game, name)
    return checked(out, p, game, name)


MANIFEST = 'fbrb.manifest.json'


def extract_plan(b, out, flt):
    """The entries extract writes (filtered); ValueError, before anything is written, when two would be one file on
    this system (names equal, or equal but for case: the second replaced the first, 2026-10-30), when a file would
    stand where another entry's folder goes, or when an entry is named as the manifest."""
    root = os.path.normcase(os.path.abspath(out))
    owner, folders, plan = {}, {}, []
    for e in b.entries:
        if flt not in e['name'].lower():
            continue
        key = os.path.normcase(os.path.abspath(safe_path(out, e['name'])))
        if key == os.path.join(root, MANIFEST):
            raise ValueError(f'entry {e["name"]!r} has the manifest\'s name ({MANIFEST}); nothing was extracted')
        if key in owner:
            raise ValueError(f'entries {owner[key]!r} and {e["name"]!r} would be one file on this system; nothing '
                             f'was extracted')
        owner[key] = e['name']
        d = os.path.dirname(key)
        while len(d) > len(root):
            folders.setdefault(d, e['name'])
            d = os.path.dirname(d)
        plan.append(e)
    for key, name in owner.items():
        if key in folders or key == root:
            raise ValueError(f'entry {name!r} would be a file where {folders.get(key, "the output")!r} needs a folder; '
                             f'nothing was extracted')
    return plan


def main(argv):
    if any(a in ('-h', '--help') for a in argv[1:]):
        print(__doc__)
        return 0
    if len(argv) < 3 or argv[1] not in ('list', 'info', 'extract', 'replace'):
        print(__doc__)
        return 1
    cmd, path = argv[1], argv[2]
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    b = Bundle(open(path, 'rb').read())
    if cmd == 'list':
        flt = argv[3].lower() if len(argv) > 3 else ''
        for e in b.entries:
            if flt in e['name'].lower():
                print(f"{e['size']:10d}  {e['type']:24s}  {e['name']}")
    elif cmd == 'info':
        types = {}
        for e in b.entries:
            types[e['type']] = types.get(e['type'], 0) + 1
        print(f'{os.path.basename(path)}: {len(b.entries)} entries, payload {len(b.payload):,} bytes '
              f'({"gzip" if b.compressed else "stored"}), file {len(b.raw):,} bytes')
        for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
            print(f'  {n:5d}  {t}')
    elif cmd == 'extract':
        if len(argv) < 4:
            print(__doc__)
            return 1
        out, flt = argv[3], (argv[4].lower() if len(argv) > 4 else '')
        game = protected(path)
        if in_protected(game, out):
            raise ValueError(f'{out} is in the game folder {in_protected(game, out)}, which the tools only read; '
                             f'extract elsewhere')
        plan = extract_plan(b, out, flt)                    # collisions refused before anything is written
        os.makedirs(out, exist_ok=True)
        manifest = []
        for e in plan:
            data = b.payload[e['offset']:e['offset'] + e['size']]
            p = extract_target(out, e['name'], game)       # every destination resolved and checked before writing
            write_file(p, data)                             # a new file moved into place: a hard link is not written through
            manifest.append(dict(name=e['name'], type=e['type'], flags=e['flags'], size=len(data),
                                 sha256=hashlib.sha256(data).hexdigest()))
        text = json.dumps(dict(bundle=os.path.abspath(path), sha256=hashlib.sha256(b.raw).hexdigest(), entries=manifest),
                          indent=1)
        write_file(checked(out, os.path.join(out, MANIFEST), game, MANIFEST),
                   text.encode('utf-8'))                    # UTF-8 with \n newlines on every system
        print(f'extracted {len(manifest)} entries into {out}')
    elif cmd == 'replace':
        if len(argv) < 5:
            print(__doc__)
            return 1
        out = argv[3]
        if canonical(out) == canonical(path):
            raise ValueError('the output would overwrite the input bundle; write a copy elsewhere')
        if in_protected(protected(path), out):
            raise ValueError(f'{out} is in the game folder {in_protected(protected(path), out)}, which the tools only '
                             f'read; write the bundle elsewhere')
        rep = {}
        for spec in argv[4:]:
            name, _, file = spec.partition('=')
            if not file:
                raise ValueError(f'expected <entry name>=<file>, got {spec!r}')
            rep[name] = open(file, 'rb').read()
        raw, _, _ = b.build(rep)
        write_file(out, raw)                                # never written through a hard link at out
        print(f'wrote {out}: {len(rep)} entries replaced')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core'))
    from bfbc2core import errors
    errors.setup('fbrb1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
