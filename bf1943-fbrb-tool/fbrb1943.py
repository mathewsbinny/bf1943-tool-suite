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
                                                           fbrb.manifest.json (name, type, flags, size, sha256)
  python fbrb1943.py replace <bundle.fbrb> <out.fbrb> <entry name>=<file> [...]
                                                           a copy with entries replaced (names as listed);
                                                           never written into the game folder or over the input

The game folder (core game1943.find_game) is only read: a changed bundle goes elsewhere, for the Xenia kit or a
mod folder to use.
"""
import gzip
import hashlib
import json
import os
import struct
import sys
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


def inside_game(path):
    """True when path lies in the BF1943 game folder (game1943.find_game), which the tools only read."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core'))
    try:
        from bfbc2core import game1943
        game = game1943.find_game()
    except (ImportError, FileNotFoundError):
        return False
    a, g = os.path.abspath(path), os.path.abspath(game)
    return os.path.commonpath([a, g]) == g


def main(argv):
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
        manifest = []
        for e in b.entries:
            if flt not in e['name'].lower():
                continue
            data = b.data(e['name'])
            p = safe_path(out, e['name'])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').write(data)
            manifest.append(dict(name=e['name'], type=e['type'], flags=e['flags'], size=len(data),
                                 sha256=hashlib.sha256(data).hexdigest()))
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, 'fbrb.manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(dict(bundle=os.path.abspath(path), sha256=hashlib.sha256(b.raw).hexdigest(), entries=manifest), f, indent=1)
        print(f'extracted {len(manifest)} entries into {out}')
    elif cmd == 'replace':
        if len(argv) < 5:
            print(__doc__)
            return 1
        out = argv[3]
        if os.path.abspath(out) == os.path.abspath(path):
            raise ValueError('the output would overwrite the input bundle; write a copy elsewhere')
        if inside_game(out):
            raise ValueError(f'{out} is in the game folder, which the tools only read; write the bundle elsewhere')
        rep = {}
        for spec in argv[4:]:
            name, _, file = spec.partition('=')
            if not file:
                raise ValueError(f'expected <entry name>=<file>, got {spec!r}')
            rep[name] = open(file, 'rb').read()
        raw, _, _ = b.build(rep)
        open(out, 'wb').write(raw)
        print(f'wrote {out}: {len(rep)} entries replaced')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core'))
    from bfbc2core import errors
    errors.setup('fbrb1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
