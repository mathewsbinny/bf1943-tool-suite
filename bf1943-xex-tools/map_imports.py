"""Map original XEX import records without patching the decoded memory image.

  python -B map_imports.py          (from the research workspace, or with BF1943_RESEARCH set)

Reads work/extracted/default.xex, the checked image and XEXLoaderWV's ImportRenamer.java (the ordinal name tables)
under work/tools; writes research/evidence/xex-imports.json and work/xex-analysis/imports.tsv.
"""
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
from typing import Any

from workspace import research_root  # noqa: E402

LOADER = 'work/tools/XEXLoaderWV/XEXLoaderWV/src/main/java/xexloaderwv'     # in the research workspace


def require(condition: object, message: str) -> None:
    """ValueError(message) unless condition holds."""
    if not condition:
        raise ValueError(message)


def read(data: bytes, offset: int, fmt: str) -> tuple[Any, ...]:
    """struct.unpack_from(fmt, data, offset); ValueError when it lies outside the data."""
    require(0 <= offset <= len(data) - struct.calcsize(fmt), f'Out of bounds: {offset:#x} {fmt}')
    return struct.unpack_from(fmt, data, offset)


def provenance(path: Path, root: Path) -> dict[str, str]:
    """A workspace file's record: its path relative to the research workspace, and its SHA-256."""
    return dict(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def tool_provenance() -> dict[str, str]:
    """This script's record, by its path in the suite (as verify_xex_image.py and export_callgraph_strings.py name
    themselves): the script lies in the suite, not in the workspace, so provenance() cannot name it (2026-10-31: it
    raised ValueError there, and the script always exited 1 without writing imports.tsv)."""
    return dict(path='bf1943-xex-tools/map_imports.py', sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def names_from_java(text: str) -> dict[str, dict[int, str]]:
    """{library: {ordinal: name}} from ImportRenamer.java's `<library>_replacer` tables."""
    tables: dict[str, dict[int, str]] = {}
    for match in re.finditer(r'public static ArrayList<Replacer> (\w+)_replacer\s*=.*?\n\s*};', text, re.S):
        entries = re.findall(r'new Replacer\((0x[0-9A-Fa-f]+),\s*"([^"]+)"\)', match[0])
        table: dict[int, str] = {}
        for ordinal_text, name in entries:
            ordinal = int(ordinal_text, 16)
            require(ordinal not in table, f'Duplicate name-table ordinal: {match[1]} {ordinal}')
            table[ordinal] = name
        tables[match[1]] = table
    require(tables, 'No reference name tables parsed')
    return tables


def parse(xex: bytes, image: bytes, tables: dict[str, dict[int, str]]) -> dict[str, Any]:
    """The import directory of an XEX2 file: its libraries, each record, and the imports with their names (from
    tables) and thunks (in the decoded image). ValueError for anything out of bounds or inconsistent."""
    require(xex[:4] == b'XEX2', 'Not XEX2')
    pe_offset, = read(xex, 8, '>I')
    count, = read(xex, 20, '>I')
    require(24 + count * 8 <= pe_offset <= len(xex), 'Optional header bounds')
    headers: dict[int, int] = {}
    for i in range(count):
        key, value = read(xex, 24 + i * 8, '>II')
        require(key not in headers, 'Duplicate optional header')
        headers[key] = value
    # 2026-10-31 fuzzing: a KeyError before
    require(0x10201 in headers and 0x103ff in headers, 'No image base or import directory optional header')
    base = headers[0x10201]
    start = headers[0x103ff]
    size, string_size, library_count = read(xex, start, '>III')
    require(size >= 12 and start + size <= pe_offset, 'Import directory bounds')
    directory = xex[start:start + size]
    require(string_size <= size - 12, 'String table bounds')
    strings = directory[12:12 + string_size]
    require(not strings or strings[-1] == 0, 'Unterminated string table')
    names = [name.decode('ascii') for name in strings.split(b'\0') if name]
    pos = 12 + string_size
    libraries: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    addresses: set[int] = set()
    for index in range(library_count):
        lib_size, = read(directory, pos, '>I')
        require(lib_size >= 40 and pos + lib_size <= size, 'Library bounds')
        name_index, record_count = read(directory, pos + 36, '>HH')
        require(name_index < len(names), 'Library name index')
        require(lib_size == 40 + record_count * 4, 'Library record size mismatch')
        name = names[name_index]
        lib: dict[str, Any] = dict(library=name, index=index, directory_offset=hex(start + pos), size=lib_size,
                                   record_count=record_count, records=[], import_count=0, thunk_count=0)
        current: dict[str, Any] | None = None
        for record_index in range(record_count):
            address, = read(directory, pos + 40 + record_index * 4, '>I')
            require(address % 4 == 0 and address not in addresses, 'Unaligned or duplicate import address')
            addresses.add(address)
            value, = read(image, address - base, '>I')
            kind, ordinal, library_byte = value >> 24, value & 0xffff, (value >> 16) & 255
            require(kind in (0, 1), f'Unknown import record type {kind}')
            require(library_byte == index, 'Record library index mismatch')
            lib['records'].append(dict(address=hex(address), word=hex(value), type=kind, ordinal=ordinal))
            if kind == 0:
                resolved = tables.get(name.lower().split('.')[0], {}).get(ordinal)
                current = dict(library=name, ordinal=ordinal, name=resolved or f'{name}_ord_{ordinal}',
                               name_resolved=resolved is not None, slot=hex(address), thunk=None)
                imports.append(current)
                lib['import_count'] += 1
            else:
                if current is None or current['thunk'] is not None:
                    raise ValueError('Orphan or duplicate thunk')
                require(current['ordinal'] == ordinal, 'Thunk ordinal mismatch')
                require(address - base + 16 <= len(image), 'Thunk extent bounds')
                current['thunk'] = hex(address)
                current['thunk_bytes'] = image[address - base:address - base + 16].hex()
                lib['thunk_count'] += 1
        libraries.append(lib)
        pos += lib_size
    require(pos == size, 'Unconsumed import directory bytes')
    return dict(image_base=hex(base), directory_offset=hex(start), directory_size=size,
                string_table=names, libraries=libraries, imports=imports)


def main() -> None:
    root = research_root()              # the research workspace (workspace.py)
    loader = root / LOADER
    source = root / 'work/extracted/default.xex'
    image = root / 'work/xex-analysis/default.checked.memory.bin'
    reference = loader / 'ImportRenamer.java'
    before = [provenance(path, root) for path in (source, image)]
    report = parse(source.read_bytes(), image.read_bytes(), names_from_java(reference.read_text()))
    report['provenance'] = dict(
        inputs=before, parser=tool_provenance(),
        references=[provenance(loader / name, root) for name in ('XEXHeader.java', 'ImportRenamer.java')],
        reference_repository='https://github.com/zeroKilo/XEXLoaderWV',
        reference_commit='4bcf58da4caa5260644fc19f5eef5ccde3b7ff46')
    report['summary'] = dict(
        libraries=len(report['libraries']), imports=len(report['imports']),
        thunks=sum(entry['thunk'] is not None for entry in report['imports']),
        unresolved_names=sum(not entry['name_resolved'] for entry in report['imports']))
    report['limitations'] = ['Addresses and ordinals are observed in the original header and decoded image.',
        'Names are inferred from the third-party ordinal table; they are not embedded debug symbols.',
        'Slots without thunks are not automatically classified as data exports.',
        'Import thunk bytes retain loader fixup words and are not patched or treated as executable PowerPC here.',
        'No signatures, calling conventions, runtime resolution, or API behavior are established.']
    require(before == [provenance(path, root) for path in (source, image)], 'Input changed during parsing')
    output = root / 'research/evidence/xex-imports.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    with (root / 'work/xex-analysis/imports.tsv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['library', 'ordinal', 'name', 'slot', 'thunk'],
                                delimiter='\t', extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(report['imports'])
    print(json.dumps(report['summary']))


if __name__ == '__main__':
    main()
