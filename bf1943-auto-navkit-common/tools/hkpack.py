"""NavKit: reader for Havok binary packfiles (32-bit), as in BC2 *_HavokAsset.res and BF1943 *_Havok.res.

Generic: objects are found through the packfile's virtual fixups, and their fields are decoded with the
hkClass reflection data stored in the file's __types__ section, so no class layouts are hard-coded.
The byte order comes from the file's layout rules (little-endian: BFBC2 PC; big-endian: BF1943 Xbox 360),
and the class-member record size from its Havok version (24 bytes from Havok 5; 20 in Havok 4.x).
BF1943's Havok 4.6.1 packfiles have an empty __types__ section: their objects are listed, and fields are
decoded only for classes whose layout is given (Packfile(..., classes=...)).

Usage:
  hkpack.py classes <file>              object counts per class
  hkpack.py dump <file> <class> [n]     decoded fields of the first n objects of a class
"""
import struct
import sys
from collections import Counter
from typing import Any, cast

MAGIC = bytes.fromhex('57e0e05710c0c010')

# hkClassMember::Type (Havok 6.x)
T_VOID, T_BOOL, T_CHAR, T_INT8, T_UINT8, T_INT16, T_UINT16, T_INT32, T_UINT32, T_INT64, T_UINT64, \
    T_REAL, T_VECTOR4, T_QUATERNION, T_MATRIX3, T_ROTATION, T_QSTRANSFORM, T_MATRIX4, T_TRANSFORM, \
    T_ZERO, T_POINTER, T_FUNCTIONPOINTER, T_ARRAY, T_INPLACEARRAY, T_ENUM, T_STRUCT, T_SIMPLEARRAY, \
    T_HOMOGENEOUSARRAY, T_VARIANT, T_CSTRING, T_ULONG, T_FLAGS = range(32)
SIMPLE = {T_BOOL: ('?', 1), T_CHAR: ('b', 1), T_INT8: ('b', 1), T_UINT8: ('B', 1), T_INT16: ('h', 2),
          T_UINT16: ('H', 2), T_INT32: ('i', 4), T_UINT32: ('I', 4), T_INT64: ('q', 8), T_UINT64: ('Q', 8),
          T_REAL: ('f', 4), T_ULONG: ('I', 4)}
VEC = {T_VECTOR4: 4, T_QUATERNION: 4, T_MATRIX3: 12, T_ROTATION: 12, T_QSTRANSFORM: 12, T_MATRIX4: 16,
       T_TRANSFORM: 16}

# a class description's key: its hkClass object's offset, or ('given', name) for a layout given to Packfile
type ClassKey = int | tuple[str, str]


class Packfile:
    """One packfile: its sections, pointer fixups, objects (offset, class name) and class descriptions. d: the file's
    bytes, e: its struct byte-order prefix ('<' or '>'); offsets are absolute in d."""

    def __init__(self, data: bytes, base: int | None = None, classes: dict[str, dict[str, Any]] | None = None) -> None:
        """classes: {name: {'size': n, 'parent': name or None, 'members': [...]}} for files without __types__."""
        if base is None:
            base = data.find(MAGIC)
            if base < 0:
                raise ValueError('no Havok packfile')
        self.d = data
        self.base = base
        if base < 0 or base + 0x40 > len(data):     # 2026-10-31 fuzzing: IndexError / struct.error before
            raise ValueError(f'damaged Havok packfile: its header at {base:#x} runs past the end ({len(data)} bytes)')
        rules = data[base + 16:base + 20]       # layout rules: bytes in a pointer, little-endian, ...
        self.ptr_size, self.little_endian = rules[0], bool(rules[1])
        self.e = endian = '<' if self.little_endian else '>'
        header = struct.unpack_from(endian + '8I', data, base)
        (_m0, _m1, _tag, self.version, _layout, self.nsections, self.contents_sec, self.contents_off) = header
        self.contents_version = data[base + 0x28:base + 0x38].split(b'\0')[0].decode('ascii', 'replace')
        self.member_size = 20 if self.contents_version.startswith('Havok-4.') else 24
        if base + 0x40 + 0x30 * self.nsections > len(data):
            raise ValueError(f'damaged Havok packfile: {self.nsections} sections run past the end '
                             f'({len(data)} bytes)')
        self.sections: list[dict[str, Any]] = []
        for i in range(self.nsections):
            header_at = base + 0x40 + i * 0x30
            name = data[header_at:header_at + 20].split(b'\0')[0].decode('ascii', 'replace')
            # the section's start, then its fixup tables' and its end's offsets (from the start)
            start, local, global_, virtual, exports, imports, end = struct.unpack_from(endian + '7I', data,
                                                                                       header_at + 20)
            self.sections.append({'name': name, 'start': base + start, 'local': local, 'global': global_,
                                  'virtual': virtual, 'exports': exports, 'imports': imports, 'end': end})
        self.ptr: dict[int, int] = {}  # absolute file offset of a pointer slot -> absolute file offset of its target
        for section in self.sections:
            section_start = section['start']
            for k in range(section['local'], min(section['global'], len(data) - section_start - 7), 8):
                src, dst = struct.unpack_from(endian + '2I', data, section_start + k)
                if src == 0xFFFFFFFF:
                    continue
                self.ptr[section_start + src] = section_start + dst
            for k in range(section['global'], min(section['virtual'], len(data) - section_start - 11), 12):
                src, target_section, dst = struct.unpack_from(endian + '3I', data, section_start + k)
                if src == 0xFFFFFFFF:
                    continue
                self.ptr[section_start + src] = self._section(target_section)['start'] + dst
        self.objects: list[tuple[int, str]] = []  # (absolute offset, class name)
        for section in self.sections:
            section_start = section['start']
            for k in range(section['virtual'], min(section['exports'], len(data) - section_start - 11), 12):
                src, name_section, name_off = struct.unpack_from(endian + '3I', data, section_start + k)
                if src == 0xFFFFFFFF:
                    continue
                cname = self.cstr(self._section(name_section)['start'] + name_off)
                self.objects.append((section_start + src, cname))
        self.by_offset = {off: cname for off, cname in self.objects}
        self.classes: dict[ClassKey, dict[str, Any]] = {}
        self._read_classes()
        for name, layout in (classes or {}).items():   # given layouts, for classes the file does not describe
            if name not in self.class_by_name:
                key = ('given', name)
                parent = ('given', layout['parent']) if layout.get('parent') else None
                self.classes[key] = {'name': name, 'parent': parent, 'size': layout['size'],
                                     'members': layout['members']}
                self.class_by_name[name] = key

    def _section(self, index: int) -> dict[str, Any]:
        if index >= len(self.sections):                 # 2026-10-31 fuzzing: an IndexError before
            raise ValueError(f'damaged Havok packfile: a fixup names section {index} of {len(self.sections)}')
        return self.sections[index]

    def at(self, fmt: str, off: int | None) -> tuple[Any, ...]:
        """struct.unpack_from(self.e + fmt, data, off); ValueError when it lies outside the file (2026-10-31 fuzzing:
        struct.error before, and a negative offset read from the end)."""
        size = struct.calcsize(self.e + fmt)
        if off is None or off < 0 or off + size > len(self.d):
            raise ValueError(f'damaged Havok packfile: {size} bytes at {off} lie outside the file '
                             f'({len(self.d)} bytes)')
        return struct.unpack_from(self.e + fmt, self.d, off)

    def cstr(self, off: int | None) -> str:
        """The zero-ended string at off. ValueError when there is none inside the file (2026-10-31 fuzzing: a
        TypeError for a missing pointer, and str.index's 'subsection not found')."""
        end = self.d.find(b'\0', off) if off is not None and 0 <= off < len(self.d) else -1
        if end < 0:
            raise ValueError(f'damaged Havok packfile: a string at {off} does not end inside the file')
        return self.d[off:end].decode('ascii', 'replace')

    def deref(self, slot: int) -> int | None:
        return self.ptr.get(slot)

    # --- hkClass reflection --------------------------------------------------------------------------
    def _read_classes(self) -> None:
        for off, cname in self.objects:
            if cname != 'hkClass':
                continue
            name_p = self.deref(off)
            name = self.cstr(name_p) if name_p else '?'
            parent = self.deref(off + 4)
            size, _nint = self.at('2i', off + 8)
            members_p = self.deref(off + 24)
            nmembers = self.at('i', off + 28)[0]
            if nmembers > 0 and (members_p is None or members_p + nmembers * self.member_size > len(self.d)):
                raise ValueError(f'damaged Havok packfile: the {nmembers} members of class {name!r} lie outside '
                                 f'the file')
            members: list[dict[str, Any]] = []
            for k in range(max(0, nmembers)):
                member_at = cast(int, members_p) + k * self.member_size      # not None: nmembers > 0
                mname = self.cstr(self.deref(member_at))
                mclass = self.deref(member_at + 4)
                mtype, msubtype, carray, flags, moffset = self.at('BBhHH', member_at + 12)
                members.append({'name': mname, 'class': mclass, 'type': mtype, 'subtype': msubtype,
                                'carray': carray, 'offset': moffset})
            self.classes[off] = {'name': name, 'parent': parent, 'size': size, 'members': members}
        # looked up with a member's class name too, None for a member without one
        self.class_by_name: dict[str | None, ClassKey] = {layout['name']: off for off, layout in self.classes.items()}

    def all_members(self, cname: str | None) -> list[dict[str, Any]]:
        """Members of a class including inherited ones, as (name, member)."""
        off = self.class_by_name.get(cname)
        chain, seen = [], set()
        while off is not None and off in self.classes:  # a parent that is no class description ends the chain
            if off in seen:                             # 2026-10-31 review: a class its own parent looped forever
                raise ValueError(f'damaged Havok packfile: class {cname!r} is among its own parents')
            seen.add(off)
            chain.append(self.classes[off])
            off = self.classes[off]['parent']
        out: list[dict[str, Any]] = []
        for layout in reversed(chain):
            out.extend(layout['members'])
        return out

    def member_class_name(self, member: dict[str, Any]) -> str | None:
        return self.classes[member['class']]['name'] if member['class'] in self.classes else None

    def struct_size(self, cname: str | None) -> int | None:
        off = self.class_by_name.get(cname)
        return self.classes[off]['size'] if off is not None else None

    def read_value(self, at: int, mtype: int, sub: int, mclass_name: str | None, carray: int = 0,
                   depth: int = 0) -> Any:
        if depth > 32:                              # 2026-10-31 review: a struct holding itself recursed without end
            raise ValueError(f'damaged Havok packfile: structs nested more than 32 deep at {at:#x}')
        if carray:
            size = self.elem_size(mtype, mclass_name)
            if not size or carray < 0 or at + carray * size > len(self.d):
                raise ValueError(f'damaged Havok packfile: an array of {carray} at {at:#x} lies outside the file')
            return [self.read_value(at + i * size, mtype, sub, mclass_name, depth=depth + 1) for i in range(carray)]
        if mtype in SIMPLE:
            return self.at(SIMPLE[mtype][0], at)[0]
        if mtype in VEC:
            return self.at(f'{VEC[mtype]}f', at)
        if mtype in (T_ENUM, T_FLAGS):
            return self.at({T_INT8: 'b', T_UINT8: 'B', T_INT16: 'h', T_UINT16: 'H'}.get(sub, 'i'), at)[0]
        if mtype == T_POINTER:
            target = self.deref(at)
            return ('ptr', target, self.by_offset.get(target)) if target else None
        if mtype == T_CSTRING:
            string_at = self.deref(at)
            return self.cstr(string_at) if string_at else None
        if mtype == T_STRUCT:
            return self.read_object(at, mclass_name, depth + 1)
        if mtype in (T_ARRAY, T_SIMPLEARRAY):
            data_p = self.deref(at)
            count = self.at('i', at + 4)[0]
            if not data_p or count <= 0:
                return []
            size = self.elem_size(sub, mclass_name)
            if size is None:
                return [('?', count)]
            # every element inside the file (2026-10-31 fuzzing: struct.error past the end; review: a count of 2^31
            # elements of size 0 read for ever)
            if not size or data_p + count * size > len(self.d):
                raise ValueError(f'damaged Havok packfile: an array of {count} x {size} bytes at {data_p:#x} lies '
                                 f'outside the file')
            return [self.read_value(data_p + i * size, sub, 0, mclass_name, depth=depth + 1) for i in range(count)]
        return None

    def elem_size(self, mtype: int, mclass_name: str | None) -> int | None:
        if mtype in SIMPLE:
            return SIMPLE[mtype][1]
        if mtype in VEC:
            return VEC[mtype] * 4
        if mtype in (T_POINTER, T_CSTRING, T_ULONG):
            return 4
        if mtype == T_STRUCT:
            return self.struct_size(mclass_name)
        return None

    def read_object(self, at: int, cname: str | None, depth: int = 0) -> dict[str, Any]:
        """The object's fields by its class's members. ValueError for a field outside the file."""
        out: dict[str, Any] = {}
        for member in self.all_members(cname):
            if member['type'] in (T_ZERO, T_VOID, T_FUNCTIONPOINTER):
                continue
            out[member['name']] = self.read_value(at + member['offset'], member['type'], member['subtype'],
                                                  self.member_class_name(member), member['carray'], depth)
        return out

    def objects_of(self, cname: str) -> list[int]:
        return [off for off, class_name in self.objects if class_name == cname]


def load(path: str) -> Packfile:
    return Packfile(open(path, 'rb').read())


def short(value: Any, depth: int = 0) -> str:
    """A decoded value in brief: long lists cut to their first three items, pointers as their target's class."""
    if isinstance(value, dict):
        return '{' + ', '.join(f'{key}: {short(item, depth + 1)}' for key, item in value.items()) + '}'
    if isinstance(value, list):
        if len(value) > 6:
            return f'[{len(value)} items: ' + ', '.join(short(item, depth + 1) for item in value[:3]) + ', ...]'
        return '[' + ', '.join(short(item, depth + 1) for item in value) + ']'
    if isinstance(value, tuple) and value and value[0] == 'ptr':
        return f'->{value[2] or hex(value[1])}'
    if isinstance(value, tuple):
        return '(' + ', '.join(f'{item:.3f}' if isinstance(item, float) else str(item) for item in value) + ')'
    if isinstance(value, float):
        return f'{value:.4g}'
    return str(value)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    pf = load(argv[2])
    if argv[1] == 'classes':
        print(f'Havok packfile at 0x{pf.base:X}, version {pf.version}, {pf.contents_version}, '
              f'{"little" if pf.little_endian else "big"}-endian, '
              f'{len(pf.sections)} sections, {len(pf.classes)} class descriptions')
        for class_name, count in Counter(cname for _, cname in pf.objects).most_common():
            print(f'{count:7d}  {class_name}')
    elif argv[1] == 'dump':
        count = int(argv[4]) if len(argv) > 4 else 2
        for off in pf.objects_of(argv[3])[:count]:
            print(f'@0x{off:X}', short(pf.read_object(off, argv[3])))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
    from bfbc2core import errors
    errors.setup('hkpack', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
