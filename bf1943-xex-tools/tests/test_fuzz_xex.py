r"""executable tools: XEX and image header readers fuzzed; map_imports runs on a made-up workspace

Mutation fuzzing (core/fuzzcheck.py) of the executable tools' readers of binary input, on made-up files (no game
data): verify_xex_image.security_info (the XEX2 security info and page descriptors), pe_layout (the decoded image's
PE headers), walk_relocations, and map_imports.parse (the XEX's optional headers and import directory, and the import
records in the image). On any input each returns or raises ValueError, within its time and memory limits. Then
map_imports.py, run as a script on a made-up research workspace, writes imports.tsv and its evidence (2026-10-31: it
always exited 1, naming its own path relative to the workspace). Fixed seeds; BF1943_FUZZ=long for more.
"""
import json
import os
import struct
import subprocess
import sys

import fuzzcheck
import map_imports
import verify_xex_image

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 0x82000000
# (address, kind, library, ordinal): kind 0 an import slot, 1 the thunk of the slot before it
RECORDS = [[(BASE + 0x100, 0, 0, 5), (BASE + 0x200, 1, 0, 5), (BASE + 0x104, 0, 0, 9)],
           [(BASE + 0x110, 0, 1, 7), (BASE + 0x220, 1, 1, 7)]]
JAVA = ('public static ArrayList<Replacer> xam_replacer = new ArrayList<Replacer>() {{\n'
        '    new Replacer(0x5, "XamFive"), new Replacer(0x9, "XamNine")\n    };\n'
        'public static ArrayList<Replacer> xboxkrnl_replacer = new ArrayList<Replacer>() {{\n'
        '    new Replacer(0x7, "KeSeven")\n    };\n')


def xex_and_image():
    """(xex, image): an XEX2 with two optional headers (image base, import directory) and an import directory of two
    libraries; the image holds the records' words and 16-byte thunks."""
    image = bytearray(0x400)
    for lib in RECORDS:
        for address, kind, index, ordinal in lib:
            struct.pack_into('>I', image, address - BASE, kind << 24 | index << 16 | ordinal)
    strings = b'xam.xex\0xboxkrnl.exe\0\0\0'
    libs = b''
    for k, lib in enumerate(RECORDS):
        libs += struct.pack('>I', 40 + 4 * len(lib)) + bytes(32) + struct.pack('>HH', k, len(lib))
        libs += b''.join(struct.pack('>I', a) for a, _, _, _ in lib)
    start = 24 + 16
    directory = struct.pack('>III', 12 + len(strings) + len(libs), len(strings), len(RECORDS)) + strings + libs
    pe_offset = start + len(directory)
    head = b'XEX2' + struct.pack('>5I', 0, pe_offset, 0, 0, 2) + struct.pack('>4I', 0x10201, BASE, 0x103FF, start)
    return head + directory, bytes(image)


def security_xex(descriptors=3):
    """An XEX2 whose security info (at 0x100) has the two sizes, a 256-byte signature, the image info and digest, and
    `descriptors` page descriptors."""
    sec = 0x100
    out = bytearray(sec + 0x184 + 24 * descriptors)
    out[0:4] = b'XEX2'
    struct.pack_into('>5I', out, 4, 0, 0x2000, 0, sec, 0)
    struct.pack_into('>II', out, sec, 0x1000, descriptors * 0x10000)
    struct.pack_into('>III', out, sec + 8 + 256, 0x174, 0, BASE)
    struct.pack_into('>I', out, sec + 0x180, descriptors)
    for k in range(descriptors):
        struct.pack_into('>I', out, sec + 0x184 + 24 * k, 1 << 4 | 1)
    return bytes(out)


def pe_image(sections=('.text', '.data', '.reloc')):
    """A PE header block: e_lfanew 0x80, the file and optional headers, 16 data directories, the section headers, then
    one relocation block of two entries and an empty one."""
    out = bytearray(0x400)
    pe, optional_size = 0x80, 0xE0
    struct.pack_into('<I', out, 60, pe)
    out[pe:pe + 4] = b'PE\0\0'
    struct.pack_into('<HH', out, pe + 4, 0x1F2, len(sections))
    struct.pack_into('<H', out, pe + 20, optional_size)
    opt = pe + 24
    struct.pack_into('<I', out, opt + 28, BASE)
    struct.pack_into('<II', out, opt + 56, 0x30000, 0x1000)
    struct.pack_into('<I', out, opt + 92, 16)
    for k, name in enumerate(sections):
        struct.pack_into('<8sIIII', out, opt + optional_size + 40 * k, name.encode('ascii'), 0x1000, 0x1000 * (k + 1),
                         0x1000, 0x1000 * (k + 1))
    struct.pack_into('<II4H', out, 0x300, 0x1000, 16, 1, 2, 3, 4)
    return bytes(out)


def test_security_info():
    seed = security_xex()
    fields = [(8, '>I'), (16, '>I'), (0x100, '>I'), (0x104, '>I'), (0x100 + 0x180, '>I')]
    report = fuzzcheck.run('verify_xex_image.security_info', verify_xex_image.security_info, [seed], iterations=1500,
                           fields=[fields], boundaries=[[4, 24, 0x100, 0x108, 0x208, 0x220, 0x280, 0x284, len(seed) - 24]])
    assert report.ok, report.text()


def test_pe_layout():
    seed = pe_image()
    fields = [(60, '<I'), (0x86, '<H'), (0x94, '<H'), (0x98 + 92, '<I')]
    # the PE headers are read from the start of the image: what follows them (sections' bytes) is not read
    report = fuzzcheck.run('verify_xex_image.pe_layout', verify_xex_image.pe_layout, [seed], iterations=1500,
                           fields=[fields], prefix_ok=True)
    assert report.ok, report.text()
    report = fuzzcheck.run('verify_xex_image.walk_relocations', verify_xex_image.walk_relocations, [seed[0x300:]],
                           iterations=800, fields=[[(4, '<I'), (20, '<I')]], prefix_ok=True)
    assert report.ok, report.text()


def test_map_imports_parse():
    xex, image = xex_and_image()
    tables = map_imports.names_from_java(JAVA)
    out = map_imports.parse(xex, image, tables)
    assert [i['name'] for i in out['imports']] == ['XamFive', 'XamNine', 'KeSeven'], out['imports']
    fields = [(8, '>I'), (20, '>I'), (24, '>I'), (28, '>I'), (32, '>I'), (36, '>I'), (40, '>I'), (44, '>I'), (48, '>I')]
    report = fuzzcheck.run('map_imports.parse (xex)', lambda d: map_imports.parse(d, image, tables), [xex],
                           iterations=1500, fields=[fields], prefix_ok=True)
    assert report.ok, report.text()
    report = fuzzcheck.run('map_imports.parse (image)', lambda d: map_imports.parse(xex, d, tables), [image],
                           iterations=800, fields=[[(0x100, '>I'), (0x104, '>I'), (0x110, '>I'), (0x200, '>I')]],
                           prefix_ok=True)
    assert report.ok, report.text()


def test_map_imports_script(tmp_path):
    """The script on a made-up workspace: exit 0, imports.tsv with one row per import, its own path in the suite."""
    root = tmp_path / 'workspace'
    xex, image = xex_and_image()
    loader = root / map_imports.LOADER
    for folder in (root / 'work' / 'extracted', root / 'work' / 'xex-analysis', root / 'research' / 'evidence', loader):
        folder.mkdir(parents=True)
    (root / 'work' / 'extracted' / 'default.xex').write_bytes(xex)
    (root / 'work' / 'xex-analysis' / 'default.checked.memory.bin').write_bytes(image)
    (loader / 'ImportRenamer.java').write_bytes(JAVA.encode('ascii'))
    (loader / 'XEXHeader.java').write_bytes(b'// made up\n')
    r = subprocess.run([sys.executable, '-B', os.path.join(TOOLS, 'map_imports.py')], capture_output=True, text=True,
                       cwd=str(root), env=dict(os.environ, BF1943_RESEARCH=str(root)))
    assert r.returncode == 0, r.stdout + r.stderr
    tsv = (root / 'work' / 'xex-analysis' / 'imports.tsv').read_bytes()
    assert tsv == (b'library\tordinal\tname\tslot\tthunk\n'
                   b'xam.xex\t5\tXamFive\t0x82000100\t0x82000200\n'
                   b'xam.xex\t9\tXamNine\t0x82000104\t\n'
                   b'xboxkrnl.exe\t7\tKeSeven\t0x82000110\t0x82000220\n'), tsv
    evidence = json.loads((root / 'research' / 'evidence' / 'xex-imports.json').read_text(encoding='utf-8'))
    assert evidence['provenance']['parser']['path'] == 'bf1943-xex-tools/map_imports.py', evidence['provenance']
    assert [i['path'] for i in evidence['provenance']['inputs']] == ['work/extracted/default.xex',
                                                                    'work/xex-analysis/default.checked.memory.bin']


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_headers():
    """security_info and pe_layout on cut or damaged headers raised struct.error (and a file that is no XEX2 a
    SystemExit); map_imports.parse on an XEX without the image base or import directory header raised KeyError."""
    assert fuzzcheck.refused(verify_xex_image.security_info, b'XEX2')
    assert fuzzcheck.refused(verify_xex_image.security_info, b'XEX1' + bytes(20))
    assert fuzzcheck.refused(verify_xex_image.security_info, security_xex()[:-24])
    assert fuzzcheck.refused(verify_xex_image.pe_layout, bytes(10))
    assert fuzzcheck.refused(verify_xex_image.pe_layout, pe_image()[:0x1A0])        # inside the section headers
    xex, image = xex_and_image()
    no_base = bytearray(xex)
    struct.pack_into('>I', no_base, 24, 0x10202)
    assert fuzzcheck.refused(lambda d: map_imports.parse(d, image, map_imports.names_from_java(JAVA)), bytes(no_base))
