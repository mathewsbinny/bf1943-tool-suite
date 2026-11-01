r"""XEX tools: call graph and string export on a made-up image

export_callgraph_strings.py's main() on a made-up research workspace (no game data; nothing read from or written to
operation-rising-sun): a 16 KB stand-in image with .rdata (strings in each encoding, a function pointer table, a
vtable), .text (functions written instruction by instruction to reach each kind of call edge, each tracked and
untracked instruction class, a merge, an unreached block, two code gaps found through a call and a data word), .data
(pointer cells) and an import table, plus a made-up Ghidra call reference table. The module's pinned image hash is
replaced by the made-up one for the test and restored afterwards. Checked: the edge kinds, the inferred functions, the
strings and their references, the vtable candidate, the cross-check, the tables and the evidence it writes.
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import struct
import sys

import export_callgraph_strings as E

BASE = 0x82000000
SIZE = 0x4000
F_A, F_B, F_C, THUNK, HELPER = 0x82001000, 0x82001800, 0x82001840, 0x82001880, 0x82001900
GAP1, GAP2, F_D, F_E = 0x82001A00, 0x82001B00, 0x82001C00, 0x82001D00
CTR, LR, XER = 9, 8, 1


class Asm:
    def __init__(self, start):
        self.start, self.words = start, []

    @property
    def pc(self):
        return self.start + 4 * len(self.words)

    def w(self, *words):
        self.words += words

    def d(self, op, rt, ra, imm):
        self.w(op << 26 | rt << 21 | ra << 16 | (imm & 0xFFFF))

    def x(self, xo, rs, ra, rb):
        self.w(31 << 26 | rs << 21 | ra << 16 | rb << 11 | xo << 1)

    def mtspr(self, spr, rs):
        self.x(467, rs, spr & 31, spr >> 5)

    def mfspr(self, rt, spr):
        self.x(339, rt, spr & 31, spr >> 5)

    def li32(self, r, value):
        self.d(15, r, 0, value >> 16)
        self.d(14, r, r, value & 0xFFFF)

    def b(self, target, link=0):
        self.w(18 << 26 | ((target - self.pc) & 0x3FFFFFC) | link)

    def bc(self, bo, target, link=0):
        self.w(16 << 26 | bo << 21 | ((target - self.pc) & 0xFFFC) | link)

    def bclr(self, bo, link):
        self.w(19 << 26 | bo << 21 | 16 << 1 | link)

    def bcctr(self, bo, link):
        self.w(19 << 26 | bo << 21 | 528 << 1 | link)


def function_a():
    a = Asm(F_A)
    a.d(15, 3, 0, 0x8200)                  # lis r3
    a.d(14, 3, 3, 0x100)                   # addi: 'hello world'
    a.d(14, 4, 3, 2)                       # its interior
    a.d(24, 3, 5, 0x80)                    # ori: the short string 'ab'
    a.d(25, 3, 6, 0)                       # oris
    a.d(24, 30, 7, 0)                      # ori of a symbol, 0: a copy
    a.d(24, 30, 7, 1)                      # ori of a symbol: unknown
    a.d(15, 20, 0, 0x8200)                 # lis r20 (kept over calls)
    a.d(32, 8, 20, 0x300)                   # read-only word: F_B
    a.mtspr(CTR, 8)
    a.bcctr(20, 1)                         # indirect_resolved
    a.d(32, 8, 20, 0x304)                   # THUNK
    a.mtspr(CTR, 8)
    a.bcctr(20, 1)                         # import_call
    a.d(32, 8, 20, 0x308)                   # inside F_A, not a start
    a.mtspr(CTR, 8)
    a.bcctr(20, 1)                         # indirect_resolved_nonentry
    a.d(14, 10, 20, 0x310)                  # the vtable
    a.d(36, 10, 31, 0)                     # stw r10, 0(r31): a vtable store
    a.d(32, 11, 31, 0)                     # remembered
    a.d(32, 12, 11, 4)                     # its second slot: F_C
    a.mtspr(CTR, 12)
    a.bcctr(20, 1)
    a.w(*[32 << 26 | 11 << 21 | 30 << 16, 32 << 26 | 12 << 21 | 11 << 16 | 8])        # [[r30] + 8]
    a.mtspr(CTR, 12)
    a.bcctr(20, 1)                         # virtual
    a.d(32, 12, 29, 0x10)
    a.mtspr(CTR, 12)
    a.bcctr(20, 1)                         # [x + 0x10]
    a.d(32, 11, 28, 4)
    a.d(32, 12, 11, 8)
    a.mtspr(CTR, 12)
    a.bcctr(20, 1)                         # [[x + 4] + 8]
    a.mtspr(CTR, 27)
    a.bcctr(20, 1)                         # an incoming register: unknown
    a.li32(12, F_B)
    a.mtspr(LR, 12)
    a.bclr(20, 1)                          # blrl to F_B
    a.mfspr(0, LR)
    a.mfspr(3, CTR)
    a.mfspr(4, XER)
    a.bclr(12, 1)                          # conditional blrl (LR = the return address: inside F_A)
    a.bcctr(12, 1)                         # conditional bcctrl
    for target in (F_B, THUNK, HELPER + 4, F_A + 8, GAP1, 0x82003900):
        a.b(target, 1)
    a.bc(20, F_C, 1)                       # bcl always: a call
    a.bc(12, F_C, 1)                       # conditional call
    a.bc(12, F_C)                          # conditional tail call
    a.bc(12, THUNK)                        # conditional import tail call
    a.bc(12, HELPER + 4)                   # conditional helper tail call
    a.d(34, 3, 20, 0x100)                   # lbz
    a.d(33, 3, 31, 4)                      # lwzu
    a.d(35, 3, 31, 1)                      # lbzu
    a.d(37, 3, 1, -16)                     # stwu
    a.d(38, 3, 20, 0x3010)                  # stb to .data
    a.d(36, 20, 20, 0x3014)                  # stw a constant to .data
    a.d(32, 9, 20, 0x3014)                  # ... and read it back
    a.d(32, 9, 20, 0x3018)                  # writable: unknown
    a.d(32, 9, 20, 0x400)                   # an import slot: not folded
    a.d(32, 9, 20, 0x3800)                  # BINKBSS: not folded
    a.d(32, 9, 20, 0x7000)                  # past the image
    a.d(46, 29, 1, 0)                      # lmw
    a.d(47, 29, 1, 0)                      # stmw
    a.d(48, 1, 20, 0x100)                   # lfs
    a.d(49, 1, 31, 8)                      # lfsu
    a.d(52, 1, 31, 8)                      # stfs
    a.d(55, 1, 31, 8)                      # stfdu
    a.w(58 << 26 | 3 << 21 | 20 << 16 | 0x100)          # ld
    a.w(58 << 26 | 3 << 21 | 31 << 16 | 8 | 1)         # ldu
    a.w(62 << 26 | 3 << 21 | 31 << 16 | 8)             # std
    a.w(62 << 26 | 3 << 21 | 1 << 16 | (-32 & 0xFFFC) | 1)     # stdu
    for xo, rs, ra, rb in ((444, 3, 4, 3), (28, 3, 4, 5), (826, 3, 4, 5), (151, 3, 31, 4), (183, 3, 31, 4),
                           (0, 0, 3, 4), (55, 3, 31, 4), (23, 3, 31, 4), (266, 3, 4, 5), (19, 3, 0, 0), (1000, 3, 4, 5)):
        a.x(xo, rs, ra, rb)
    a.d(12, 3, 4, 1)                       # addic
    a.d(21, 4, 3, 0)                       # rlwinm
    a.d(30, 4, 3, 0)                       # rldicl
    a.w(0x100001C3 | 1 << 21 | 31 << 16 | 4 << 11)    # stvx128
    a.d(10, 0, 3, 0)                       # cmpli
    a.d(32, 3, 26, 0)
    for _ in range(5):
        a.d(32, 3, 3, 0)                   # nested loads past the cap
    a.mtspr(CTR, 3)
    a.bcctr(20, 1)
    a.bc(12, a.pc + 8)                     # inside, forward
    a.b(a.pc + 8)                          # inside, over one word
    a.w(0x60000000)                        # unreached (nop)
    a.d(14, 3, 0, 1)
    a.bc(20, F_E)                          # always, outside: tail call
    return a.words


def function_b():
    a = Asm(F_B)
    a.li32(12, F_C)
    a.mtspr(CTR, 12)
    a.bcctr(20, 0)                         # resolved tail call
    return a.words


def function_c():
    a = Asm(F_C)
    a.li32(12, THUNK)
    a.mtspr(CTR, 12)
    a.bcctr(20, 0)                         # import tail call
    return a.words


def function_d():
    a = Asm(F_D)
    a.li32(12, F_D + 0x10)
    a.mtspr(CTR, 12)
    a.bcctr(20, 0)                         # a branch inside: no edge
    a.mtspr(CTR, 27)                       # F_D + 0x10, reached through CTR only: seeded unreached
    a.bcctr(20, 0)                         # unresolved branch
    a.b(F_A)                               # never reached either
    return a.words


def function_e():
    a = Asm(F_E)
    top = a.pc
    a.bc(12, a.pc + 12)                    # to the merge
    a.d(14, 3, 0, 1)
    a.d(36, 3, 31, 0)                      # a remembered slot on one path only
    a.d(14, 4, 0, 0)                       # merge
    a.bc(12, top)                          # a loop back
    a.bclr(20, 0)                          # blr
    return a.words


def made_up_image():
    d = bytearray(SIZE)
    d[0x100:0x10C] = b'hello world\0'
    d[0x120:0x12F] = 'café au lait\0'.encode('utf-8')
    d[0x140:0x14A] = b'\xff\xfeABCDEFG\0'
    d[0x160:0x16C] = 'Words'.encode('utf-16-be') + b'\0\0'
    d[0x180:0x183] = b'ab\0'
    struct.pack_into('>4I', d, 0x300, F_B, THUNK, F_A + 4, 0)
    struct.pack_into('>3I', d, 0x310, F_B, F_C, 0)     # a vtable of two functions
    struct.pack_into('>I', d, 0x400, 0x12345678)       # an import slot
    struct.pack_into('>4I', d, 0x3000, 0x82000100, F_B, GAP2, 0)
    for start, words in ((F_A, function_a()), (F_B, function_b()), (F_C, function_c()), (F_D, function_d()),
                         (F_E, function_e()), (THUNK, [0x3D600000, 0x816B0000, 0x7D6903A6, 0x4E800420]),
                         (HELPER, [0xF9C1FF68, 0xF9E1FF70, 0x4E800020]),
                         (GAP1, [0x38600001, 18 << 26 | ((F_B - (GAP1 + 4)) & 0x3FFFFFC) | 1, 0x4E800020]),
                         (GAP2, [0x38600002, 0x4E800020])):
        struct.pack_into(f'>{len(words)}I', d, start - BASE, *words)
    return bytes(d), len(function_a())


def tsv(path, header, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write('\t'.join(header) + '\n' + ''.join('\t'.join(r) + '\n' for r in rows))


@contextlib.contextmanager
def workspace(tmp):
    data, words_a = made_up_image()
    work = tmp / 'work/xex-analysis'
    work.mkdir(parents=True)
    (tmp / 'research/evidence').mkdir(parents=True)
    (work / 'default.checked.memory.bin').write_bytes(data)
    tsv(work / 'sections.tsv', ['name', 'start', 'size', 'read', 'write', 'execute'],
        [['.rdata', '82000000', str(0x1000), '1', '0', '0'], ['.text', '82001000', str(0x2000), '1', '0', '1'],
         ['.data', '82003000', str(0x800), '1', '1', '0'], ['BINKBSS', '82003800', str(0x100), '1', '0', '0']])
    tsv(work / 'imports.tsv', ['slot', 'thunk', 'library', 'name'],
        [['82000400', f'{THUNK:08x}', 'xboxkrnl.exe', 'NtClose'], ['82000404', '', 'xam.xex', 'XamData']])
    ends = {F_A: F_A + 4 * words_a, F_B: F_B + 16, F_C: F_C + 16, F_D: F_D + 28, F_E: F_E + 24, HELPER: HELPER + 12}
    tsv(work / 'functions.tsv', ['start', 'end', 'prolog_bytes', 'exception_flag'],
        [[f'{s:08x}', f'{e:08x}', '0', '0'] for s, e in ends.items()])
    tsv(work / 'helper-entries.tsv', ['address', 'name'], [[f'{HELPER:08x}', '__savegprlr_14'],
                                                          [f'{HELPER + 4:08x}', '__savegprlr_15']])
    tsv(work / 'decompiled-index.tsv', ['address', 'name', 'file'],
        [[f'{F_A:08x}', 'big_function', 'big_function.c'], [f'{HELPER:08x}', '__savegprlr_14', '']])
    saved = (os.environ.get('BF1943_RESEARCH'), E.IMAGE_SHA256, sys.argv)
    os.environ['BF1943_RESEARCH'] = str(tmp)
    E.IMAGE_SHA256 = hashlib.sha256(data).hexdigest()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield work
    finally:
        if saved[0] is None:
            os.environ.pop('BF1943_RESEARCH', None)
        else:
            os.environ['BF1943_RESEARCH'] = saved[0]
        E.IMAGE_SHA256, sys.argv = saved[1:]


def read(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def test_export(tmp_path):
    with workspace(tmp_path) as work:
        ghidra = tmp_path / 'ghidra-call-refs.tsv'
        bl_b = F_A + 4 * function_a().index(next(w for k, w in enumerate(function_a())
                                                if w >> 26 == 18 and w & 1 and E.branch_target(w, F_A + 4 * k) == F_B))
        tsv(ghidra, ['instruction', 'target', 'kind'], [[f'{bl_b:08x}', f'{F_B:08x}', 'call'],
                                                        ['82001ffc', f'{F_C:08x}', 'conditional_call'],
                                                        ['82001ff8', '', 'call'], ['82001ff4', '', 'computed_call']])
        sys.argv = ['export_callgraph_strings.py', '--ghidra-call-refs', str(ghidra)]
        summary = E.main()
        out = work / 'callgraph'
        kinds = summary['call_site_kinds']
        for kind in ('indirect_resolved', 'import_call', 'indirect_resolved_nonentry', 'indirect_virtual_unresolved',
                     'indirect_pointer_unresolved', 'indirect_unknown', 'conditional_indirect_resolved_nonentry',
                     'conditional_indirect_unknown', 'direct_call', 'helper_call', 'direct_call_nonentry',
                     'conditional_call', 'conditional_tail_call', 'import_tail_call', 'helper_conditional_tail_call',
                     'tail_call', 'indirect_tail_call_resolved', 'indirect_branch_unresolved'):
            assert kind in kinds, (kind, kinds)
        assert summary['functions_inferred'] == 2 and summary['functions_inferred_from_calls'] == 1
        assert summary['functions_inferred_from_data_pointers'] == 1 and summary['discovery_rounds'][0]['new_functions'] == 2
        assert summary['pdata_only_graph']['nonentry_call_sites_outside_every_function'] == 2
        assert summary['basic_blocks_seeded_unreached'] >= 1
        assert summary['string_encodings'] == {'ascii': 3, 'utf-16be': 1, 'utf-8': 1}, summary['string_encodings']
        assert summary['strings_short_referenced'] == 1 and summary['vtable_candidates'] == 1
        assert summary['string_pointer_cells'] == 1 and summary['data_pointers_to_functions'] >= 1
        cross = summary['ghidra_cross_check']
        assert cross['both'] == 1 and cross['only_ghidra'] == 1 and cross['ghidra_computed_calls'] == 1
        strings = {r['address']: r for r in read(out / 'strings.tsv')}
        assert strings['82000142']['text'] == 'ABCDEFG' and strings['82000120']['encoding'] == 'utf-8'
        assert strings['82000160']['text'] == 'Words' and strings['82000180']['chars'] == '2'
        refs = read(out / 'string-refs.tsv')
        assert any(r['string'] == '82000100' and r['detail'] == 'interior+2' for r in refs)
        assert any(r['kind'] == 'data_pointer' and r['from'] == '82003000' for r in refs)
        vt = read(out / 'vtable-candidates.tsv')
        assert vt == [dict(address='82000310', leading_function_pointers='2', stored_at_offset_0_by=f'{F_A:08x}')]
        inferred = read(out / 'inferred-functions.tsv')
        assert [(r['start'], r['source']) for r in inferred] == [(f'{GAP1:08x}', 'inferred_call_target'),
                                                                 (f'{GAP2:08x}', 'inferred_data_pointer')]
        assert inferred[0]['end'] == f'{GAP1 + 12:08x}'
        funcs = {r['address']: r for r in read(out / 'callgraph-functions.tsv')}
        assert funcs[f'{F_A:08x}']['decompiled_file'] == 'big_function.c' and funcs[f'{HELPER:08x}']['source'] == 'helper'
        assert funcs[f'{F_B:08x}']['callers'] != '0'
        edges = read(out / 'callgraph-edges.tsv')
        assert any(e['callee_name'] == 'NtClose' for e in edges) and any(e['callee_name'] == '__savegprlr_15' for e in edges)
        evidence = json.loads((tmp_path / 'research/evidence/xex-callgraph-strings.json').read_bytes())
        assert evidence['outputs'] == summary['outputs'] and 'note' in evidence
        assert set(summary['outputs']) == {f'work/xex-analysis/callgraph/{p.name}' for p in out.glob('*.tsv')}


def test_other_image_refused(tmp_path):
    with workspace(tmp_path) as work:
        E.IMAGE_SHA256 = '0' * 64
        try:
            E.Image(work)
            why = None
        except SystemExit as e:
            why = str(e)
        assert why == 'unexpected decoded image'


def test_helpers():
    assert E.simm16(0xFFF0) == -16 and E.simm16(0x7FFF) == 0x7FFF
    assert E.conditional_target(0x4182FFF8, 0x82000010) == 0x82000008
    assert E.conditional_target(0x41820012, 0) == 0x10, 'bca: absolute'
    assert E.offset_value(0xFFFFFFFF, 2) == 1 and E.offset_value(('k', 4), -4) == ('k', 0)
    assert E.spr_field(31 << 26 | 3 << 21 | 9 << 16 | 467 << 1) == 9 and E.spr_field(0x7C0802A6) == 8
    assert E.always_taken(20) and not E.always_taken(12)
    assert E.latin_text('naïve café') and not E.latin_text('кот') and not E.latin_text('a\x07')
    assert E.esc('a\tb"') == 'a\\tb\\"'
    assert E.is_unresolved('indirect_pointer_unresolved') and E.is_unresolved('conditional_indirect_unknown')
    assert not E.is_unresolved('direct_call')
    assert E.outside_every_function('direct_call_nonentry', 'outside every function')
    assert not E.outside_every_function('direct_call', 'outside every function')
