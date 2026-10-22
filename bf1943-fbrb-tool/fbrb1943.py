"""Read and write BF1943 Xenon FbRB bundles (directory version 2).

Layout, as observed in 82 of this build's 85 bundles (the reader refuses the other three, whose payload
size or entry layout differs: async/ondemand_awards-00, async/ondemand_sounds-00, build_overlay-00): "FbRB", u32 size of the gzip directory, the gzip
directory, then the payload (gzip when the directory's tail flag is 1). The directory is: u32 version (2),
u32 string-table size, the strings, u32 entry count, entries of six u32 (name offset, flags, payload offset,
size, size again, type-name offset), then a flag byte (payload compressed) and u32 payload size. Entries
are contiguous in directory order, without alignment. gzip members carry mtime 0 and OS byte 0x0b.

`Bundle.build` rewrites only offsets and sizes; with no replaced entries it reproduces the original
directory and payload byte-for-byte (checked by tests/test_fbrb.py).
"""
import gzip
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
