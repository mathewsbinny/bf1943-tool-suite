"""A small PowerPC assembler for the code the kit adds to BF1943 (Xbox 360, 32-bit big-endian words).

Only the instructions the kit's code uses are supported, and everything else is refused. Each encoding is
checked against capstone by tests/test_ppcasm.py.

Source syntax, one statement per line ('#' starts a comment):
    label:
    stwu r1, -0x60(r1)
    bl 0x82387ef0          # absolute target address
    beq cr0, done          # or beq done (cr0)
    la r3, 0x82f00000      # pseudo: r3 = 32-bit address (lis + addi; lis + ori for r0)
    li32 r3, 0x12345678    # pseudo: r3 = 32-bit value (lis + ori)
    .long 0x00000000       # a data word
    .string "name"         # NUL-terminated bytes, padded to a word
"""
import re
import struct

D_FORM = {'addi': 14, 'addis': 15, 'ori': 24, 'oris': 25, 'andi.': 28, 'lwz': 32, 'stw': 36, 'stwu': 37,
          'lbz': 34, 'stb': 38, 'lhz': 40, 'sth': 44}
MEMORY = {'lwz', 'stw', 'stwu', 'lbz', 'stb', 'lhz', 'sth'}
UNSIGNED = {'ori', 'oris', 'andi.'}              # rA, rS, uimm
X_ARITH = {'add': 266, 'subf': 40, 'mullw': 235}  # rD, rA, rB (op 31)
BRANCH_COND = {'beq': (12, 2), 'bne': (4, 2), 'blt': (12, 0), 'bge': (4, 0), 'bgt': (12, 1), 'ble': (4, 1)}
FIXED = {'blr': 0x4E800020, 'bctr': 0x4E800420, 'bctrl': 0x4E800421, 'nop': 0x60000000}
SPR = {'lr': 8, 'ctr': 9}


class AsmError(ValueError):
    pass


def reg(text):
    m = re.fullmatch(r'r(\d+)', text.strip())
    if not m or int(m.group(1)) > 31:
        raise AsmError(f'not a register: {text!r}')
    return int(m.group(1))


def base(text):
    """A D-form base register: r0 there reads as 0 (an absolute address), never what the source meant."""
    r = reg(text)
    if r == 0:
        raise AsmError('a base of r0 reads 0, not r0 (an absolute address): use another register')
    return r


def freg(text):
    m = re.fullmatch(r'f(\d+)', text.strip())
    if not m or int(m.group(1)) > 31:
        raise AsmError(f'not a floating-point register: {text!r}')
    return int(m.group(1))


def crf(text):
    m = re.fullmatch(r'cr([0-7])', text.strip())
    if not m:
        raise AsmError(f'not a condition register field: {text!r}')
    return int(m.group(1))


def number(text):
    try:
        return int(text.strip(), 0)
    except ValueError:
        raise AsmError(f'not a number: {text!r}')


def simm(value):
    if not -0x8000 <= value <= 0x7FFF:
        raise AsmError(f'signed 16-bit immediate out of range: {value:#x}')
    return value & 0xFFFF


def uimm(value):
    if not 0 <= value <= 0xFFFF:
        raise AsmError(f'unsigned 16-bit immediate out of range: {value:#x}')
    return value


def split_args(text):
    return [a.strip() for a in text.split(',')] if text.strip() else []


def statements(source):
    """[(line number, label or None, mnemonic or None, argument text)]"""
    out = []
    for n, line in enumerate(source.splitlines(), 1):
        code = line.split('#', 1)[0].strip() if '.string' not in line else line.strip()
        while True:
            m = re.match(r'([A-Za-z_][\w.]*):\s*(.*)', code)
            if not m:
                break
            out.append((n, m.group(1), None, ''))
            code = m.group(2)
        if code:
            parts = code.split(None, 1)
            out.append((n, None, parts[0].lower(), parts[1] if len(parts) > 1 else ''))
    return out


def size_of(mnemonic, args):
    if mnemonic in ('la', 'li32'):
        return 8
    if mnemonic == '.string':
        text = string_literal(args)
        return (len(text) + 1 + 3) // 4 * 4
    return 4


def string_literal(args):
    m = re.fullmatch(r'\s*"([^"\\]*)"\s*(#.*)?', args)
    if not m:
        raise AsmError(f'.string needs one plain "..." literal: {args!r}')
    return m.group(1).encode('ascii')


def assemble(source, origin, symbols=None):
    """(bytes, {label: address}) for source placed at origin. symbols: extra names for addresses or values."""
    names = dict(symbols or {})
    at = origin
    parsed = statements(source)
    for n, label, mnemonic, args in parsed:
        if label:
            if label in names:
                raise AsmError(f'line {n}: {label} defined twice')
            names[label] = at
        else:
            at += size_of(mnemonic, args)
    out = bytearray()
    at = origin
    for n, label, mnemonic, args in parsed:
        if label:
            continue
        try:
            words = encode(mnemonic, split_args(args) if mnemonic != '.string' else args, at, names)
        except AsmError as e:
            raise AsmError(f'line {n}: {mnemonic} {args}: {e}') from None
        if isinstance(words, bytes):
            out += words
            at += len(words)
        else:
            for w in words:
                out += struct.pack('>I', w)
            at += 4 * len(words)
    return bytes(out), names


def value(text, names):
    text = text.strip()
    if text.startswith('-') and text[1:] in names:
        return -names[text[1:]]
    return names[text] if text in names else number(text)


# operand counts per mnemonic: a typo with an extra operand would otherwise assemble as something else, and a missing
# operand raised IndexError with no line number (before 2026-10-30)
ARITY = {**{k: (2,) for k in MEMORY | {'std', 'ld', 'lfs', 'stfs', 'li', 'lis', 'la', 'li32', 'mr'}},
         **{k: (3,) for k in {'addi', 'addis', 'slwi', 'srwi'} | UNSIGNED | set(X_ARITH)},
         **{k: (2, 3) for k in ('cmpwi', 'cmplwi', 'cmpw', 'cmplw')}, **{k: (1, 2) for k in BRANCH_COND},
         **{k: (1,) for k in ('mflr', 'mfctr', 'mtlr', 'mtctr', 'b', 'bl', '.long')}, **{k: (0,) for k in FIXED}}


def encode(m, a, at, names):
    if m != '.string' and m in ARITY and len(a) not in ARITY[m]:
        raise AsmError(f'{m} takes {" or ".join(map(str, ARITY[m]))} operand(s), not {len(a)}')
    if m in FIXED and not a:
        return [FIXED[m]]
    if m == '.long':
        return [value(a[0], names) & 0xFFFFFFFF]
    if m == '.string':
        text = string_literal(a)
        return text + b'\0' * (size_of(m, a) - len(text))
    if m in MEMORY:
        mm = re.fullmatch(r'(-?\w+)\((r\d+)\)', a[1].replace(' ', ''))
        if len(a) != 2 or not mm:
            raise AsmError('expected rX, offset(rY)')
        return [D_FORM[m] << 26 | reg(a[0]) << 21 | base(mm.group(2)) << 16 | simm(value(mm.group(1), names))]
    if m in ('std', 'ld'):   # DS-form: the game keeps 64-bit values in saved registers
        mm = re.fullmatch(r'(-?\w+)\((r\d+)\)', a[1].replace(' ', ''))
        if len(a) != 2 or not mm:
            raise AsmError('expected rX, offset(rY)')
        offset = value(mm.group(1), names)
        if offset & 3:
            raise AsmError('std/ld offset must be a multiple of 4')
        return [(62 if m == 'std' else 58) << 26 | reg(a[0]) << 21 | base(mm.group(2)) << 16 | simm(offset)]
    if m in ('lfs', 'stfs'):   # D-form, a floating-point register
        mm = re.fullmatch(r'(-?\w+)\((r\d+)\)', a[1].replace(' ', ''))
        if len(a) != 2 or not mm:
            raise AsmError('expected fX, offset(rY)')
        return [(48 if m == 'lfs' else 52) << 26 | freg(a[0]) << 21 | base(mm.group(2)) << 16 | simm(value(mm.group(1), names))]
    if m in ('addi', 'addis'):
        if reg(a[1]) == 0:     # the instruction reads 0 for a base of r0: what was meant is not what it does
            raise AsmError(f'{m} with base r0 adds to 0, not to r0: write {"li" if m == "addi" else "lis"} for that')
        return [D_FORM[m] << 26 | reg(a[0]) << 21 | reg(a[1]) << 16 | simm(value(a[2], names))]
    if m in UNSIGNED:
        return [D_FORM[m] << 26 | reg(a[1]) << 21 | reg(a[0]) << 16 | uimm(value(a[2], names))]
    if m == 'li':
        return [14 << 26 | reg(a[0]) << 21 | simm(value(a[1], names))]
    if m == 'lis':
        v = value(a[1], names)
        return [15 << 26 | reg(a[0]) << 21 | (simm(v) if v < 0x8000 else uimm(v))]
    if m == 'la':      # address: lis rD, ha; addi rD, rD, lo
        v = value(a[1], names) & 0xFFFFFFFF
        lo = v & 0xFFFF
        hi = ((v + 0x8000) >> 16) & 0xFFFF
        r = reg(a[0])
        if r == 0:     # addi with base r0 reads 0, not r0: lis r0, hi; ori r0, r0, lo (unsigned, so hi unadjusted), as li32
            return [15 << 26 | v >> 16, 24 << 26 | lo]
        return [15 << 26 | r << 21 | hi, 14 << 26 | r << 21 | r << 16 | lo]
    if m == 'li32':
        v = value(a[1], names) & 0xFFFFFFFF
        r = reg(a[0])
        return [15 << 26 | r << 21 | v >> 16, 24 << 26 | r << 21 | r << 16 | (v & 0xFFFF)]
    if m == 'mr':
        s = reg(a[1])
        return [31 << 26 | s << 21 | reg(a[0]) << 16 | s << 11 | 444 << 1]
    if m in X_ARITH:
        return [31 << 26 | reg(a[0]) << 21 | reg(a[1]) << 16 | reg(a[2]) << 11 | X_ARITH[m] << 1]
    if m in ('cmpwi', 'cmplwi', 'cmpw', 'cmplw'):
        if len(a) == 2:
            a = ['cr0'] + a
        field = crf(a[0]) << 23 | reg(a[1]) << 16
        if m == 'cmpwi':
            return [11 << 26 | field | simm(value(a[2], names))]
        if m == 'cmplwi':
            return [10 << 26 | field | uimm(value(a[2], names))]
        return [31 << 26 | field | reg(a[2]) << 11 | (0 if m == 'cmpw' else 32) << 1]
    if m in ('slwi', 'srwi'):
        n = value(a[2], names)
        if not 0 < n < 32:
            raise AsmError('shift out of range')
        sh, mb, me = (n, 0, 31 - n) if m == 'slwi' else (32 - n, n, 31)
        return [21 << 26 | reg(a[1]) << 21 | reg(a[0]) << 16 | sh << 11 | mb << 6 | me << 1]
    if m in ('mflr', 'mfctr', 'mtlr', 'mtctr'):
        spr = SPR[m[2:]]
        field = ((spr & 0x1F) << 5 | spr >> 5) << 11
        return [31 << 26 | reg(a[0]) << 21 | field | (339 if m.startswith('mf') else 467) << 1]
    if m in ('b', 'bl'):
        disp = value(a[0], names) - at
        if disp & 3 or not -0x2000000 <= disp < 0x2000000:
            raise AsmError(f'branch target out of reach: {disp:#x}')
        return [18 << 26 | (disp & 0x3FFFFFC) | (m == 'bl')]
    if m in BRANCH_COND:
        if len(a) == 2:
            field, target = crf(a[0]), a[1]
        else:
            field, target = 0, a[0]
        bo, bit = BRANCH_COND[m]
        disp = value(target, names) - at
        if disp & 3 or not -0x8000 <= disp < 0x8000:
            raise AsmError(f'conditional branch target out of reach: {disp:#x}')
        return [16 << 26 | bo << 21 | (4 * field + bit) << 16 | (disp & 0xFFFC)]
    raise AsmError('unsupported instruction (add it, with a capstone test)')
