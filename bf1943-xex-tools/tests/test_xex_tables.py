r"""XEX tools: section map, helper resolution, VX128 classes on a made-up image

map_functions.py, resolve_helpers.py, classify_vmx.py and xenonrecomp_reference.py on a made-up research workspace (no
game data, nothing read from or written to operation-rising-sun): an 8 MB stand-in image with a PE header, four
sections, .pdata records of each kind (a plain function, one exactly covering a register helper block, and records
with each error) and the six register helper blocks as resolve_helpers lays them out, and a stand-in XenonRecomp
reference (folder and ZIP). The modules' pinned hashes (image, reference archive and members) are replaced by the
made-up ones for the test and restored afterwards. Checked: the helper words against known PowerPC encodings, branch
targets, the tables and evidence each main() writes, and every refusal (another image, a helper that differs, a
reference that differs or is missing).
"""
import contextlib
import hashlib
import io
import json
import os
import struct
import zipfile

import classify_vmx
import map_functions
import resolve_helpers
import xenonrecomp_reference as xr

BASE = 0x82000000
SIZE = 0x7C0000
FUNC = 0x82010000


def sha(b):
    return hashlib.sha256(b).hexdigest()


def pdata_word(length, prolog, bit30=True, exception=False):
    return (length // 4) << 8 | (prolog // 4) | (0x40000000 if bit30 else 0) | (0x80000000 if exception else 0)


def image():
    """The stand-in image: PE header (base 0x82000000; .rdata, .pdata, .text, and .bss past the image's end), a
    function at FUNC calling three helper entries and holding VX128 words, and the register helper blocks."""
    d = bytearray(SIZE)
    d[:2] = b'MZ'
    pe = 0x80
    struct.pack_into('<I', d, 60, pe)
    opt_size = 0xE0
    struct.pack_into('<4sHH', d, pe, b'PE\0\0', 0x1F2, 4)
    struct.pack_into('<H', d, pe + 20, opt_size)
    opt = pe + 24
    struct.pack_into('<I', d, opt + 28, BASE)
    sections = [(b'.rdata', 0x1000, 0x1000, 0x40000000), (b'.pdata', 0x2000, 8 * 7, 0x40000000),
                (b'.text', 0x10000, SIZE - 0x10000, 0x60000000), (b'.bss', SIZE, 0x1000, 0xC0000000)]
    for k, (name, rva, size, flags) in enumerate(sections):
        at = opt + opt_size + 40 * k
        struct.pack_into('<8sIIII', d, at, name, size, rva, size, rva)
        struct.pack_into('<I', d, at + 36, flags)
    struct.pack_into('<II', d, opt + 96 + 3 * 8, 0x2000, 8 * 7)
    for start, kind, restore in resolve_helpers.HELPER_BLOCKS:
        for first, last in ([(14, 31), (64, 127)] if kind == 'vmx' else [(14, 31)]):
            words = resolve_helpers.helper_words(kind, restore, first, last)
            struct.pack_into(f'>{len(words)}I', d, start - BASE, *words)
            start += 4 * len(words)
    save = resolve_helpers.HELPER_BLOCKS[0][0]
    gprlr = 4 * len(resolve_helpers.helper_words('gprlr', False, 14, 31))
    code = [bl(FUNC, save + 4 * 4), bl(FUNC + 4, save + 4 * 17), 0x48000000 | ((save - FUNC - 8) & 0x3FFFFFC),  # b
            0x100001C3 | 1 << 21 | 3 << 16,              # stvx128 (one table entry)
            0x10000003,                                  # matches both made-up masks (ambiguous)
            0x13FFFFFF,                                  # opcode 4, no entry
            0x38600000, 0x4E800020]                      # li r3, 0; blr
    struct.pack_into(f'>{len(code)}I', d, FUNC - BASE, *code)
    records = [(FUNC, pdata_word(4 * len(code), 8, exception=True)),
               (save, pdata_word(gprlr, gprlr, bit30=False)),    # exactly the gprlr save helper: resolved
               (FUNC + 0x40, pdata_word(16, 0, bit30=False)),    # bit 30 clear, no helper: unresolved
               (FUNC + 0x82, pdata_word(16, 0)),                 # misaligned
               (FUNC + 0x100, pdata_word(8, 16)),                # prologue past the end
               (BASE + SIZE, pdata_word(8, 0)),                  # outside the image's executable bytes
               (FUNC + 0x200, 0x40000000)]                       # zero length
    for k, (start, word) in enumerate(records):
        struct.pack_into('>II', d, 0x2000 + 8 * k, start, word)
    return bytes(d)


def bl(at, target):
    return 0x48000001 | ((target - at) & 0x3FFFFFC)


PPC_DIS = (b'/* made-up ppc-dis.c table rows */\n'
           b'{ "stvx128", VX128_1(4, 451), VX128_1_MASK, PPCVEC128, { VS128, RA, RB } },\n'
           b'{ "made_a", VX128(4, 0), VX128_MASK, PPCVEC128, { 0 } },\n'
           b'{ "made_b", VX128_3(4, 0), VX128_3_MASK, PPCVEC128, { 0 } },\n'
           b'{ "notvx", X(31, 0), X_MASK, PPC, { 0 } },\n')
MEMBERS = {'XenonRecomp-main/thirdparty/disasm/ppc-dis.c': PPC_DIS,
           'XenonRecomp-main/XenonRecomp/recompiler.cpp': b'// recompiler\n',
           'XenonRecomp-main/XenonUtils/ppc_context.h': b'// context\n',
           'XenonRecomp-main/XenonUtils/xbox.h': b'// xbox\n',
           'XenonRecomp-main/README.md': b'# XenonRecomp\n'}


@contextlib.contextmanager
def workspace(tmp, data=None, archive=False):
    """A made-up research workspace with the image and the reference; the modules' pins point at them."""
    data = image() if data is None else data
    for p in ('work/xex-analysis', 'work/vmx128', 'research/evidence'):
        (tmp / p).mkdir(parents=True)
    (tmp / 'work/xex-analysis/default.checked.memory.bin').write_bytes(data)
    if archive:
        (tmp / 'Tools').mkdir()
        with zipfile.ZipFile(tmp / 'Tools/XenonRecomp.zip', 'w') as z:
            for name, content in MEMBERS.items():
                z.writestr(name, content)
    else:
        for name, content in MEMBERS.items():
            path = tmp / 'Tools/XenonRecomp' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    saved = (os.environ.get('BF1943_RESEARCH'), resolve_helpers.IMAGE_HASH, map_functions.IMAGE_SHA256,
             classify_vmx.IMAGE_SHA256, xr.ARCHIVE_SHA256, xr.MEMBERS)
    os.environ['BF1943_RESEARCH'] = str(tmp)
    resolve_helpers.IMAGE_HASH = map_functions.IMAGE_SHA256 = classify_vmx.IMAGE_SHA256 = sha(data)
    xr.ARCHIVE_SHA256 = sha((tmp / 'Tools/XenonRecomp.zip').read_bytes()) if archive else '0' * 64
    xr.MEMBERS = {name: sha(content) for name, content in MEMBERS.items()}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield data
    finally:
        if saved[0] is None:
            os.environ.pop('BF1943_RESEARCH', None)
        else:
            os.environ['BF1943_RESEARCH'] = saved[0]
        (resolve_helpers.IMAGE_HASH, map_functions.IMAGE_SHA256, classify_vmx.IMAGE_SHA256, xr.ARCHIVE_SHA256,
         xr.MEMBERS) = saved[1:]


def test_helper_words_encodings():
    """The first words of each block against the PowerPC encodings (std/ld r14,-152(r1); stfd/lfd f14,-144(r12);
    li r11,-288 and stvx/lvx v14,r11,r12; li r11,-1024 and stvx128 v64,r11,r12), and each block ends in blr."""
    w = resolve_helpers.helper_words
    assert w('gprlr', False, 14, 31)[:1] == [0xF9C1FF68] and w('gprlr', False, 14, 31)[-2:] == [0x9181FFF8, 0x4E800020]
    assert w('gprlr', True, 14, 31)[:1] == [0xE9C1FF68] and w('gprlr', True, 14, 31)[-3:] == [0x8181FFF8, 0x7D8803A6,
                                                                                               0x4E800020]
    assert w('fpr', False, 14, 31)[0] == 0xD9CCFF70 and w('fpr', True, 14, 31)[0] == 0xC9CCFF70
    assert w('vmx', False, 14, 31)[:2] == [0x3960FEE0, 0x7DCB61CE] and w('vmx', True, 14, 31)[1] == 0x7DCB60CE
    assert w('vmx', False, 64, 127)[:2] == [0x3960FC00, 0x100B61CB] and len(w('vmx', True, 64, 127)) == 129
    assert resolve_helpers.branch_target(bl(0x82000100, 0x82000000), 0x82000100) == 0x82000000
    assert resolve_helpers.branch_target(0x48000102, 0x82000100) == 0x100, 'ba: absolute'
    assert resolve_helpers.branch_target(0x4BFFFFFD, 0x82000100) == 0x820000FC, 'bl -4'


def test_verified_blocks_refusals():
    data = image()
    saved = resolve_helpers.IMAGE_HASH
    try:
        resolve_helpers.IMAGE_HASH = sha(data)
        blocks = resolve_helpers.verified_blocks(data)
        assert [b['name'] for b in blocks] == ['__savegprlr_14', '__restgprlr_14', '__savefpr_14', '__restfpr_14',
                                               '__savevmx_14', '__savevmx_64', '__restvmx_14', '__restvmx_64']
        vmx64 = blocks[5]
        assert vmx64['start'] == blocks[4]['end'] and vmx64['entries'][1]['address'] - vmx64['entries'][0]['address'] == 8
        damaged = bytearray(data)
        damaged[resolve_helpers.HELPER_BLOCKS[2][0] - BASE] ^= 1
        resolve_helpers.IMAGE_HASH = sha(bytes(damaged))
        try:
            resolve_helpers.verified_blocks(bytes(damaged))
            why = None
        except ValueError as e:
            why = str(e)
        assert why == f'Full helper mismatch {resolve_helpers.HELPER_BLOCKS[2][0]:x}', why
        try:
            resolve_helpers.verified_blocks(data)
            why = None
        except ValueError as e:
            why = str(e)
        assert why == 'Unexpected image'
    finally:
        resolve_helpers.IMAGE_HASH = saved


def test_map_then_resolve(tmp_path):
    with workspace(tmp_path):
        map_functions.main()
        report = json.loads((tmp_path / 'research/evidence/xex-functions.json').read_text())
        rows = report['functions']
        errors = [r['errors'] for r in rows]
        assert errors[0] == [] and errors[1] == [] and rows[1]['helper_blocks'][0]['name'] == '__savegprlr_14'
        assert errors[2] == ['unresolved bit30-clear record']
        assert 'alignment/length' in errors[3] and 'prologue exceeds function' in errors[4]
        assert 'outside available executable section' in errors[5] and 'alignment/length' in errors[6]
        assert rows[0]['exception_flag'] and report['summary']['valid'] == 2 and not report['summary']['entry_in_pdata']
        assert report['reference']['source'] == 'folder'
        assert [s['available_size'] for s in report['sections']][-1] == 0, '.bss lies past the image'
        tsv = (tmp_path / 'work/xex-analysis/functions.tsv').read_text().splitlines()
        assert tsv[1].startswith(f'{FUNC:08x}\t') and tsv[2].endswith('__savegprlr_14') and len(tsv) == 3
        sections = (tmp_path / 'work/xex-analysis/sections.tsv').read_text().splitlines()
        assert [line.split('\t')[0] for line in sections[1:]] == ['.rdata', '.pdata', '.text']

        resolve_helpers.main()
        res = json.loads((tmp_path / 'research/evidence/xex-helper-resolution.json').read_bytes())
        assert res['summary']['helper_blocks'] == 8 and res['summary']['direct_branch_candidates'] == 3
        assert res['reference_source'] == 'folder' and sorted(res['reference_members']) == sorted(
            m for m in MEMBERS if not m.endswith('ppc_context.h'))
        branches = (tmp_path / 'work/xex-analysis/helper-branches.tsv').read_bytes().split(b'\r\n')
        assert branches[0] == b'address\ttarget\tlink\tname' and len(branches) == 5
        assert b'\t0\t__savegprlr_14' in branches[3], 'the plain branch is a candidate without link'
        entries = (tmp_path / 'work/xex-analysis/helper-entries.tsv').read_bytes()
        assert entries.count(b'\r\n') == 1 + 18 * 4 + (18 + 64) * 2

        classify_vmx.main()
        cls = json.loads((tmp_path / 'research/evidence/vmx128-classification.json').read_text(encoding='utf-8'))
        assert cls['definitions'] == 3 and cls['counts'] == {'stvx128': 1} and cls['ambiguous'] == 1
        assert cls['unmatched_primary_4_5_6'] == 1 and cls['functions'] == 1
        lines = (tmp_path / 'work/vmx128/classified.tsv').read_bytes().split(b'\r\n')
        assert lines[0] == b'address\tfunction\tword\tmatches' and lines[2].endswith(b'made_a,made_b')


def test_vx128_table():
    table = classify_vmx.vx128_table(PPC_DIS)
    assert [name for name, _, _ in table] == ['stvx128', 'made_a', 'made_b']
    assert table[0] == ('stvx128', 0xFC0007F3, 4 << 26 | (451 & 0x7F3))


def test_reference_archive(tmp_path):
    with workspace(tmp_path, archive=True):
        assert xr.source() == 'archive' and xr.archive_path(tmp_path) == tmp_path / 'Tools/XenonRecomp.zip'
        name, content = xr.read('/xbox.h')
        assert name == 'XenonRecomp-main/XenonUtils/xbox.h' and content == MEMBERS[name]
        assert xr.provenance(tmp_path) == dict(source='archive', archive_sha256=xr.ARCHIVE_SHA256,
                                               members_verified=sorted(MEMBERS))
        xr.MEMBERS = dict(xr.MEMBERS, **{name: '0' * 64})
        try:
            xr.read('/xbox.h', tmp_path)
            why = None
        except ValueError as e:
            why = str(e)
        assert why == f'{name} differs from recorded hash'
        xr.ARCHIVE_SHA256 = '0' * 64
        try:
            xr.source(tmp_path)
            why = None
        except ValueError as e:
            why = str(e)
        assert why == 'XenonRecomp.zip differs from recorded hash'
    empty = tmp_path / 'empty'
    empty.mkdir()
    try:
        xr.source(empty)
        why = None
    except FileNotFoundError as e:
        why = str(e)
    assert why and 'neither archive nor extracted folder' in why
    assert xr.folder_path(empty) == empty / 'Tools/XenonRecomp'
