"""Binary DBX reader for the BFBC2 suite, written from bf1943-dbx-tools/FORMAT.md.

    f = read('Levels/MP_005.dbx')            # or parse(bytes)
    f.instances                              # list of Element (tag 'instance')
    f.get(element, 'guid')                   # first attribute of that name, or None
    value(element)                           # a value as the XML shows it (text, 'a/b/c', true/false)
    to_xml(data)                             # FORMAT.md section 3, identical to `DbxEdit xml`

Elements keep their kind and raw value bytes, so numbers can be read exactly (numbers(element)).
"""
import math
import struct
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CONTAINER, TEXT, NUMBERS = 0xA, 2, 7
MAGIC = b'{binary}'
WHOLE_NUMBER_FIELDS = {'OriginalHashedWaveName', 'HashedName', 'HashedWaveName', 'OnRoadMaterialNameHashes', 'Hash', 'Id',
                       'CompositeMeshPartNames'}


@dataclass
class Element:
    tag: str
    kind: int
    attributes: List[Tuple[str, str]] = field(default_factory=list)
    text: str = ''                         # TEXT
    raw: bytes = b''                       # NUMBERS: count/size varints + values; other kinds: the two bytes
    children: Optional[List['Element']] = None

    def attr(self, name, default=None):
        for k, v in self.attributes:
            if k == name:
                return v
        return default

    def child(self, name):
        """The first direct child with attribute name == name."""
        for c in self.children or ():
            if c.attr('name') == name:
                return c
        return None


@dataclass
class DbxFile:
    strings: List[str]
    roots: List[Element]

    @property
    def partition(self):
        return self.roots[0]

    @property
    def instances(self):
        return [e for e in self.roots[0].children if e.tag == 'instance']

    def instance(self, guid):
        g = guid.lower()
        for e in self.instances:
            if (e.attr('guid') or '').lower() == g:
                return e
        return None

    @staticmethod
    def get(e, name):
        return e.attr(name)


def is_binary(data):
    return len(data) > 24 and data[:8] == MAGIC


def _varint(data, pos):
    value, shift = 0, 0
    while True:
        if pos >= len(data):
            raise ValueError(f'truncated .dbx: a number runs past the end of the file ({len(data)} bytes)')
        if shift > 63:
            raise ValueError(f'damaged .dbx: a number longer than 64 bits at {pos:#x}')
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, pos
        shift += 7


def _need(data, pos, size, what):
    if pos + size > len(data):
        raise ValueError(f'truncated .dbx: {what} at {pos:#x} needs {size} bytes, the file ends at {len(data):#x}')


def _string(strings, index, pos):
    if index >= len(strings):
        raise ValueError(f'damaged .dbx: string {index} at {pos:#x}, the table has {len(strings)}')
    return strings[index]


def _strings(data):
    if len(data) < 24:
        raise ValueError('truncated .dbx: no string table header')
    area, count = struct.unpack_from('>II', data, 16)
    _need(data, 24, 4 * count, f'a table of {count} string offsets')
    stop = 24 + area                                # the string area: the offset table, then the strings
    if stop > len(data):
        raise ValueError(f'truncated .dbx: the string area ends at {stop:#x}, past the file ({len(data):#x})')
    offsets = struct.unpack_from(f'>{count}I', data, 24)
    base = 24 + 4 * count
    if base > stop:
        raise ValueError(f'damaged .dbx: {count} string offsets do not fit in the string area of {area} bytes')
    out = []
    for i, off in enumerate(offsets):
        start = base + off
        end = data.find(b'\0', start, stop)
        if end < 0:
            raise ValueError(f'damaged .dbx: string {i} at {start:#x} does not end inside the string area '
                             f'(which ends at {stop:#x})')
        out.append(data[start:end].decode('utf-8', errors='replace'))
    return out, stop


class DeletedEntry(ValueError):
    """The file is a bundle's zero-byte *deleted* marker: there is nothing to read (it is not a broken file)."""


def parse(data):
    """A binary .dbx file as an element tree (FORMAT.md sections 1-2)."""
    if not data:
        raise DeletedEntry('empty file: a *deleted* bundle entry (older FbrbTool unpacks wrote them as files)')
    if not is_binary(data):
        raise ValueError('not a binary .dbx file')
    strings, pos = _strings(data)
    roots = []
    stack = []
    n = len(data)
    while pos < n:
        tag, pos = _varint(data, pos)
        if tag == 0:
            if not stack:
                break
            stack.pop()
            continue
        _need(data, pos, 1, 'an element header')
        head = data[pos]
        pos += 1
        e = Element(_string(strings, tag, pos), head >> 4)
        for _ in range(head & 0xF):
            k, pos = _varint(data, pos)
            v, pos = _varint(data, pos)
            e.attributes.append((_string(strings, k, pos), _string(strings, v, pos)))
        if e.kind == CONTAINER:
            _need(data, pos, 1, 'a container')
            pos += 1
            e.children = []
            (stack[-1].children if stack else roots).append(e)
            stack.append(e)
            continue
        if e.kind == TEXT:
            idx, pos = _varint(data, pos)
            e.text = _string(strings, idx, pos)
        elif e.kind == NUMBERS:
            start = pos
            count, pos = _varint(data, pos)
            size, pos = _varint(data, pos)
            if count and size < 2:                  # every value reads at least two bytes (FORMAT.md section 3)
                raise ValueError(f'damaged .dbx: {count} numbers of size {size} at {start:#x} (the smallest is 2 bytes)')
            _need(data, pos, count * size, f'{count} numbers of {size} bytes')
            pos += count * size
            e.raw = data[start:pos]
        else:
            _need(data, pos, 2, 'a value')
            e.raw = data[pos:pos + 2]
            pos += 2
        (stack[-1].children if stack else roots).append(e)
    if not roots:
        raise ValueError('damaged .dbx: no element after the string table (a partition has at least its root)')
    if roots[0].kind != CONTAINER:
        # DbxFile.instances reads the first root's children (2026-10-30: a value there passed, then raised TypeError)
        raise ValueError(f'damaged .dbx: the first element ({roots[0].tag!r}) is a value, not the container a '
                         f'partition starts with')
    if stack:
        raise ValueError(f'truncated .dbx: {len(stack)} container(s) not closed at the end of the file '
                         f'(innermost {stack[-1].tag!r})')
    return DbxFile(strings, roots)


def read(path):
    with open(path, 'rb') as f:
        return parse(f.read())


# ------------------------------------------------------------------------------------------------ values

def numbers(e):
    """(size, [raw big-endian bytes per value]) of a NUMBERS element."""
    count, p = _varint(e.raw, 0)
    size, p = _varint(e.raw, p)
    return size, [e.raw[p + i * size:p + (i + 1) * size] for i in range(count)]


def floats(e):
    """The values of a 4-byte NUMBERS element read as decimals (e.g. a Transform)."""
    size, vals = numbers(e)
    if size != 4:
        raise ValueError('not 4-byte numbers')
    return [struct.unpack('>f', v)[0] for v in vals]


def _net_g(value, precision):
    """.NET's "G<precision>" formatting of a finite double (what "R" builds on): the exact binary value rounded
    to <precision> significant digits, a tie rounded away from zero; scientific notation (E+XX / E-XX) for an
    exponent below -4 or from <precision> up; trailing zeros dropped."""
    if value == 0:
        return '0'   # .NET Framework prints negative zero as 0 too
    d = Decimal(value)                     # exact
    exp = d.adjusted()
    unit = Decimal(1).scaleb(exp - precision + 1)
    r = d.quantize(unit, rounding=ROUND_HALF_UP)
    if r.adjusted() != exp:                # 9.99... rounded up to 10.0...
        exp = r.adjusted()
        r = d.quantize(Decimal(1).scaleb(exp - precision + 1), rounding=ROUND_HALF_UP)
    digits = str(abs(r.scaleb(-r.as_tuple().exponent))).rstrip('0') or '0'
    digits = digits[:precision]
    sign = '-' if r < 0 else ''
    if exp < -4 or exp >= precision:
        m = digits[0] + ('.' + digits[1:] if len(digits) > 1 else '')
        return f"{sign}{m}E{'+' if exp >= 0 else '-'}{abs(exp):02d}"
    if exp >= 0:
        whole = digits[:exp + 1].ljust(exp + 1, '0')
        frac = digits[exp + 1:]
        return sign + whole + ('.' + frac if frac else '')
    return sign + '0.' + '0' * (-exp - 1) + digits


def _net_r_single(bits):
    v = struct.unpack('>f', bits)[0]
    s = _net_g(v, 7)
    if struct.unpack('>f', struct.pack('>f', float(s)))[0] != v:
        s = _net_g(v, 9)
    return s


def _net_r_double(raw):
    v = struct.unpack('>d', raw)[0]
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    s = _net_g(v, 15)
    if float(s) != v:
        s = _net_g(v, 17)
    return s


def _number_text(raw, size, field_name):
    if size == 4:
        whole = struct.unpack('>i', raw)[0]
        if field_name in WHOLE_NUMBER_FIELDS or (whole >> 24) == 0 or ((whole >> 23) & 0xFF) == 0xFF:
            return str(whole)
        return _net_r_single(raw)
    if size == 8:
        return _net_r_double(raw)
    return str(raw[0] << 8 | raw[1])


def value(e):
    """A non-container element's value as the XML shows it (unescaped)."""
    if e.kind == TEXT:
        return e.text
    if e.kind == NUMBERS:
        size, vals = numbers(e)
        first = e.attributes[0][1] if e.attributes else ''
        return '/'.join(_number_text(v, size, first) for v in vals)
    v = e.raw[1]
    return 'false' if v == 0 else 'true' if v == 1 else str(v)


# ------------------------------------------------------------------------------------------------ XML

def _escape(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def to_xml(data):
    """FORMAT.md section 3: the readable XML, identical to `DbxEdit xml` on every valid file. The file is read
    with parse(), so a damaged one raises its ValueError here too (DbxEdit's export reads a string with no zero
    byte to the end of the file; this does not). Written with a stack, not recursion, so any nesting parse() accepts
    is exported (parse() reads iteratively too)."""
    out = ['<?xml version="1.0"?>\r\n']
    todo = [(e, 0) for e in reversed(parse(data).roots)]    # (element, depth); (tag string, depth) closes a container
    while todo:
        e, depth = todo.pop()
        if isinstance(e, str):
            out.append('\t' * depth + '</' + e + '>\r\n')
            continue
        line = '\t' * depth + '<' + e.tag + ''.join(' ' + k + '="' + _escape(v) + '"' for k, v in e.attributes)
        if e.kind == CONTAINER:
            out.append(line + '>\r\n')
            todo.append((e.tag, depth))
            todo.extend((c, depth + 1) for c in reversed(e.children))
            continue
        content = _escape(value(e))
        if e.kind == TEXT and not content:
            out.append(line + ' />\r\n')
        else:
            out.append(line + '>' + content + '</' + e.tag + '>\r\n')
    return ''.join(out)
