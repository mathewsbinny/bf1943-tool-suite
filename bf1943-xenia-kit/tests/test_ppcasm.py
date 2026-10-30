"""Tests for ppcasm: every supported instruction, decoded back by capstone (an independent decoder).

  python -B test_ppcasm.py      (skips the capstone checks, with a note, if capstone is not installed)
"""
import os
import struct
import sys

sys.dont_write_bytecode = True
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
import ppcasm

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


ORIGIN = 0x82F00000
# (source line, capstone text); capstone prints simplified mnemonics
CASES = [
    ('stwu r1, -0x60(r1)', 'stwu r1, -0x60(r1)'),
    ('stw r31, 0x5c(r1)', 'stw r31, 0x5c(r1)'),
    ('lwz r3, 0x28(r3)', 'lwz r3, 0x28(r3)'),
    ('lbz r4, 0x214(r30)', 'lbz r4, 0x214(r30)'),
    ('std r31, -0x10(r1)', 'std r31, -0x10(r1)'),
    ('stfs f1, 0x184(r1)', 'stfs f1, 0x184(r1)'),
    ('lfs f31, -8(r3)', 'lfs f31, -8(r3)'),
    ('ld r29, -0x20(r1)', 'ld r29, -0x20(r1)'),
    ('stb r4, 0x214(r3)', 'stb r4, 0x214(r3)'),
    ('lhz r5, 2(r6)', 'lhz r5, 2(r6)'),
    ('sth r5, -2(r6)', 'sth r5, -2(r6)'),
    ('addi r3, r31, 0x10', 'addi r3, r31, 0x10'),
    ('addis r3, r3, -1', 'addis r3, r3, -1'),
    ('li r4, 1', 'li r4, 1'),
    ('li r4, -1', 'li r4, -1'),
    ('lis r3, 0x8236', 'lis r3, -0x7dca'),
    ('ori r3, r3, 0xbc98', 'ori r3, r3, 0xbc98'),
    ('oris r3, r3, 0x1234', 'oris r3, r3, 0x1234'),
    ('andi. r0, r3, 0xff', 'andi. r0, r3, 0xff'),
    ('mr r31, r3', 'mr r31, r3'),
    ('add r3, r4, r5', 'add r3, r4, r5'),
    ('subf r3, r4, r5', 'subf r3, r4, r5'),
    ('mullw r3, r4, r5', 'mullw r3, r4, r5'),
    ('cmpwi r3, 0', 'cmpwi r3, 0'),
    ('cmpwi cr6, r3, -5', 'cmpwi cr6, r3, -5'),
    ('cmplwi r3, 8', 'cmplwi r3, 8'),
    ('cmpw r3, r4', 'cmpw r3, r4'),
    ('cmplw cr7, r3, r4', 'cmplw cr7, r3, r4'),
    ('slwi r3, r4, 2', 'slwi r3, r4, 2'),
    ('srwi r3, r4, 3', 'srwi r3, r4, 3'),
    ('mflr r12', 'mflr r12'),
    ('mtlr r12', 'mtlr r12'),
    ('mtctr r11', 'mtctr r11'),
    ('mfctr r11', 'mfctr r11'),
    ('blr', 'blr'),
    ('bctr', 'bctr'),
    ('bctrl', 'bctrl'),
    ('nop', 'nop'),
]


def capstone_text(words, origin):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)
    return [f'{i.mnemonic} {i.op_str}'.strip() for i in md.disasm(words, origin)]


def encodings():
    try:
        import capstone  # noqa: F401
    except ImportError:
        print('skip capstone checks: capstone is not installed')
        return
    for line, want in CASES:
        code, _ = ppcasm.assemble(line, ORIGIN)
        got = capstone_text(code, ORIGIN)
        check(got == [want], f'{line} -> {got}')
    # branches: absolute targets and labels, forward and back
    src = '''
    start:
        bl 0x8236bc98
        b done
        beq start
        bne cr7, done
        blt done
        bge done
        bgt done
        ble start
    done:
        blr
    '''
    code, names = ppcasm.assemble(src, ORIGIN)
    got = capstone_text(code, ORIGIN)
    done = names['done']
    check(got == ['bl 0x8236bc98', f'b {done:#x}', f'beq {ORIGIN:#x}', f'bne cr7, {done:#x}', f'blt {done:#x}',
                  f'bge {done:#x}', f'bgt {done:#x}', f'ble {ORIGIN:#x}', 'blr'], f'branches decode as written: {got}')
    code, _ = ppcasm.assemble('la r3, 0x82f08000\nli32 r4, 0x8236bc98', ORIGIN)
    got = capstone_text(code, ORIGIN)
    check(got == ['lis r3, -0x7d0f', 'addi r3, r3, -0x8000', 'lis r4, -0x7dca', 'ori r4, r4, 0xbc98'],
          f'la and li32 build the full value: {got}')
    # la into r0: addi's base r0 reads 0, so lis + addi would leave only the low half (capstone: lis r0, ...; li r0,
    # 0x1234). Each pair is run as capstone decodes it, for the value it leaves in the register.
    for line in ('la r0, 0x82f01234', 'la r0, 0x82f08000', 'la r3, 0x82f01234', 'la r3, 0x82f08000', 'li32 r0, 0x82f08000'):
        v = int(line.split(', ')[1], 0)
        got = capstone_text(ppcasm.assemble(line, ORIGIN)[0], ORIGIN)
        check(loaded(got) == v, f'{line} leaves {v:#x} in the register: {got}')


def loaded(text):
    """The value a lis/li/addi/ori sequence (capstone's text) leaves in its register, rA = r0 in addi read as 0."""
    regs = {}
    for line in text:
        m, ops = line.split(None, 1)
        o = [x.strip() for x in ops.split(',')]
        if m == 'lis':
            regs[o[0]] = int(o[1], 0) << 16 & 0xFFFFFFFF
        elif m == 'li':
            regs[o[0]] = int(o[1], 0) & 0xFFFFFFFF
        elif m == 'addi':
            regs[o[0]] = ((0 if o[1] == 'r0' else regs[o[1]]) + int(o[2], 0)) & 0xFFFFFFFF
        elif m == 'ori':
            regs[o[0]] = regs[o[1]] | int(o[2], 0)
        else:
            raise AssertionError(f'loaded() does not know {line}')
    return regs[o[0]]


def data_and_errors():
    code, names = ppcasm.assemble('a: .long 0x12345678\nb: .string "Bot 1"\nc: .long a', ORIGIN)
    check(code == bytes.fromhex('12345678') + b'Bot 1\0\0\0' + struct.pack('>I', ORIGIN)
          and names['c'] == ORIGIN + 12, '.long and .string lay out words, padded, with labels')
    code, _ = ppcasm.assemble('stwu r1, -FRAME(r1)\nlwz r3, OFF(r4)', ORIGIN, {'FRAME': 0x1C0, 'OFF': 0x98})
    check(code == ppcasm.assemble('stwu r1, -0x1c0(r1)\nlwz r3, 0x98(r4)', ORIGIN)[0], 'symbols (and negated symbols) as offsets')
    # a base of r0 reads 0 (addi, addis and every D-form access): refused rather than assembled as something else
    for bad in ('li r3, 0x8000','std r31, -0x12(r1)','lwz r3, 0x28', 'foo r3', 'bl 0x80000000', 'beq far', 'mr r32, r3', 'x:\nx:',
                'addi r3, r0, 4', 'addis r3, r0, 1', 'lwz r3, 8(r0)', 'stw r3, 0(r0)', 'std r3, 8(r0)', 'lfs f1, 4(r0)'):
        try:
            ppcasm.assemble(bad, ORIGIN, {'far': ORIGIN + 0x10000})
            check(False, f'refuses {bad!r}')
        except ppcasm.AsmError:
            check(True, f'refuses {bad!r}')
    # operand counts: an extra operand was dropped and a missing one raised IndexError (no line number) before
    # 2026-10-30; both are an AsmError naming the line now
    for bad in ('li r3, 1, 2', 'mr r3, r4, r5', 'mtctr r11, r12', 'b 0x1000, 0x2000', 'subf r5, r6, r5, r7', 'blr r3',
                'lwz r3, 0(r4), 8', '.long 1, 2', 'beq cr0, x, y', 'cmpwi cr0, r3, 0, 1',
                'addi r3, r4', 'slwi r3, r4', 'mr r3', 'lis r3', 'la r3', 'std r3', 'cmpwi r3', 'beq', 'bl'):
        try:
            ppcasm.assemble(f'nop\n{bad}', ORIGIN, {'x': ORIGIN, 'y': ORIGIN})
            check(False, f'refuses {bad!r} (operand count)')
        except ppcasm.AsmError as e:
            check(str(e).startswith('line 2:'), f'refuses {bad!r} (operand count), naming the line: {e}')
        except Exception as e:      # noqa: BLE001 - anything else is the defect
            check(False, f'refuses {bad!r} (operand count) with an AsmError, not {type(e).__name__}')


if __name__ == '__main__':
    encodings()
    data_and_errors()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
