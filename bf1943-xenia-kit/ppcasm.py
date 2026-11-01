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

Operand names in the encodings follow the PowerPC manual (rd, rs, sh, mb, me, bo, disp, simm, uimm).
"""
import re
import struct
from collections.abc import Mapping
from typing import cast

D_FORM = {'addi': 14, 'addis': 15, 'ori': 24, 'oris': 25, 'andi.': 28, 'lwz': 32, 'stw': 36, 'stwu': 37,
          'lbz': 34, 'stb': 38, 'lhz': 40, 'sth': 44}
MEMORY = {'lwz', 'stw', 'stwu', 'lbz', 'stb', 'lhz', 'sth'}
UNSIGNED = {'ori', 'oris', 'andi.'}              # rA, rS, uimm
X_ARITH = {'add': 266, 'subf': 40, 'mullw': 235}  # rD, rA, rB (op 31)
BRANCH_COND = {'beq': (12, 2), 'bne': (4, 2), 'blt': (12, 0), 'bge': (4, 0), 'bgt': (12, 1), 'ble': (4, 1)}
FIXED = {'blr': 0x4E800020, 'bctr': 0x4E800420, 'bctrl': 0x4E800421, 'nop': 0x60000000}
SPR = {'lr': 8, 'ctr': 9}

type Statement = tuple[int, str | None, str | None, str]   # (line number, label, mnemonic, argument text)
type Encoded = list[int] | bytes                            # instruction words, or a .string's bytes


class AsmError(ValueError):
    pass


def reg(text: str) -> int:
    match = re.fullmatch(r'r(\d+)', text.strip())
    if not match or int(match.group(1)) > 31:
        raise AsmError(f'not a register: {text!r}')
    return int(match.group(1))


def base(text: str) -> int:
    """A D-form base register: r0 there reads as 0 (an absolute address), never what the source meant."""
    register = reg(text)
    if register == 0:
        raise AsmError('a base of r0 reads 0, not r0 (an absolute address): use another register')
    return register


def freg(text: str) -> int:
    match = re.fullmatch(r'f(\d+)', text.strip())
    if not match or int(match.group(1)) > 31:
        raise AsmError(f'not a floating-point register: {text!r}')
    return int(match.group(1))


def crf(text: str) -> int:
    match = re.fullmatch(r'cr([0-7])', text.strip())
    if not match:
        raise AsmError(f'not a condition register field: {text!r}')
    return int(match.group(1))


def number(text: str) -> int:
    try:
        return int(text.strip(), 0)
    except ValueError as e:
        raise AsmError(f'not a number: {text!r}') from e


def simm(value: int) -> int:
    if not -0x8000 <= value <= 0x7FFF:
        raise AsmError(f'signed 16-bit immediate out of range: {value:#x}')
    return value & 0xFFFF


def uimm(value: int) -> int:
    if not 0 <= value <= 0xFFFF:
        raise AsmError(f'unsigned 16-bit immediate out of range: {value:#x}')
    return value


def split_args(text: str) -> list[str]:
    if not text.strip():
        return []
    return [arg.strip() for arg in text.split(',')]


def statements(source: str) -> list[Statement]:
    """[(line number, label or None, mnemonic or None, argument text)]"""
    out: list[Statement] = []
    for n, line in enumerate(source.splitlines(), 1):
        code = line.split('#', 1)[0].strip() if '.string' not in line else line.strip()
        while True:
            label = re.match(r'([A-Za-z_][\w.]*):\s*(.*)', code)
            if not label:
                break
            out.append((n, label.group(1), None, ''))
            code = label.group(2)
        if code:
            parts = code.split(None, 1)
            out.append((n, None, parts[0].lower(), parts[1] if len(parts) > 1 else ''))
    return out


def size_of(mnemonic: str, args: str) -> int:
    if mnemonic in ('la', 'li32'):
        return 8
    if mnemonic == '.string':
        text = string_literal(args)
        return (len(text) + 1 + 3) // 4 * 4
    return 4


def string_literal(args: str) -> bytes:
    match = re.fullmatch(r'\s*"([^"\\]*)"\s*(#.*)?', args)
    if not match:
        raise AsmError(f'.string needs one plain "..." literal: {args!r}')
    return match.group(1).encode('ascii')


def assemble(source: str, origin: int, symbols: Mapping[str, int] | None = None) -> tuple[bytes, dict[str, int]]:
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
            at += size_of(cast(str, mnemonic), args)     # a statement without a label has a mnemonic
    out = bytearray()
    at = origin
    for n, label, mnemonic, args in parsed:
        if label:
            continue
        mnemonic = cast(str, mnemonic)
        try:
            words = encode(mnemonic, split_args(args) if mnemonic != '.string' else args, at, names)
        except AsmError as e:
            raise AsmError(f'line {n}: {mnemonic} {args}: {e}') from None
        if isinstance(words, bytes):
            out += words
            at += len(words)
        else:
            for word in words:
                out += struct.pack('>I', word)
            at += 4 * len(words)
    return bytes(out), names


def value(text: str, names: Mapping[str, int]) -> int:
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


def displacement(operand: str, shape: str) -> tuple[str, str]:
    """A memory operand "offset(rY)": (offset text, base register text); AsmError "expected <shape>" otherwise."""
    match = re.fullmatch(r'(-?\w+)\((r\d+)\)', operand.replace(' ', ''))
    if not match:
        raise AsmError(f'expected {shape}')
    return match.group(1), match.group(2)


def encode(mnemonic: str, operands: list[str] | str, at: int, names: Mapping[str, int]) -> Encoded:
    """One statement at address at: its words, or bytes for .string (whose operands are its argument text)."""
    if mnemonic != '.string' and mnemonic in ARITY and len(operands) not in ARITY[mnemonic]:
        counts = ' or '.join(map(str, ARITY[mnemonic]))
        raise AsmError(f'{mnemonic} takes {counts} operand(s), not {len(operands)}')
    if mnemonic in FIXED and not operands:
        return [FIXED[mnemonic]]
    if mnemonic == '.long':
        return [value(operands[0], names) & 0xFFFFFFFF]
    if mnemonic == '.string':
        text = string_literal(cast(str, operands))
        return text + b'\0' * (size_of(mnemonic, cast(str, operands)) - len(text))
    args = cast(list[str], operands)
    if mnemonic in MEMORY:
        offset, base_reg = displacement(args[1], 'rX, offset(rY)')
        return [D_FORM[mnemonic] << 26 | reg(args[0]) << 21 | base(base_reg) << 16 | simm(value(offset, names))]
    if mnemonic in ('std', 'ld'):   # DS-form: the game keeps 64-bit values in saved registers
        offset_text, base_reg = displacement(args[1], 'rX, offset(rY)')
        offset = value(offset_text, names)
        if offset & 3:
            raise AsmError('std/ld offset must be a multiple of 4')
        primary = 62 if mnemonic == 'std' else 58
        return [primary << 26 | reg(args[0]) << 21 | base(base_reg) << 16 | simm(offset)]
    if mnemonic in ('lfs', 'stfs'):   # D-form, a floating-point register
        offset, base_reg = displacement(args[1], 'fX, offset(rY)')
        primary = 48 if mnemonic == 'lfs' else 52
        return [primary << 26 | freg(args[0]) << 21 | base(base_reg) << 16 | simm(value(offset, names))]
    if mnemonic in ('addi', 'addis'):
        if reg(args[1]) == 0:     # the instruction reads 0 for a base of r0: what was meant is not what it does
            plain = 'li' if mnemonic == 'addi' else 'lis'
            raise AsmError(f'{mnemonic} with base r0 adds to 0, not to r0: write {plain} for that')
        return [D_FORM[mnemonic] << 26 | reg(args[0]) << 21 | reg(args[1]) << 16 | simm(value(args[2], names))]
    if mnemonic in UNSIGNED:
        return [D_FORM[mnemonic] << 26 | reg(args[1]) << 21 | reg(args[0]) << 16 | uimm(value(args[2], names))]
    if mnemonic == 'li':
        return [14 << 26 | reg(args[0]) << 21 | simm(value(args[1], names))]
    if mnemonic == 'lis':
        immediate = value(args[1], names)
        return [15 << 26 | reg(args[0]) << 21 | (simm(immediate) if immediate < 0x8000 else uimm(immediate))]
    if mnemonic == 'la':      # address: lis rD, ha; addi rD, rD, lo
        address = value(args[1], names) & 0xFFFFFFFF
        lo = address & 0xFFFF
        hi = ((address + 0x8000) >> 16) & 0xFFFF
        rd = reg(args[0])
        # addi with base r0 reads 0, not r0: lis r0, hi; ori r0, r0, lo (unsigned, so hi unadjusted), as li32
        if rd == 0:
            return [15 << 26 | address >> 16, 24 << 26 | lo]
        return [15 << 26 | rd << 21 | hi, 14 << 26 | rd << 21 | rd << 16 | lo]
    if mnemonic == 'li32':
        word = value(args[1], names) & 0xFFFFFFFF
        rd = reg(args[0])
        return [15 << 26 | rd << 21 | word >> 16, 24 << 26 | rd << 21 | rd << 16 | (word & 0xFFFF)]
    if mnemonic == 'mr':
        rs = reg(args[1])
        return [31 << 26 | rs << 21 | reg(args[0]) << 16 | rs << 11 | 444 << 1]
    if mnemonic in X_ARITH:
        return [31 << 26 | reg(args[0]) << 21 | reg(args[1]) << 16 | reg(args[2]) << 11 | X_ARITH[mnemonic] << 1]
    if mnemonic in ('cmpwi', 'cmplwi', 'cmpw', 'cmplw'):
        if len(args) == 2:
            args = ['cr0'] + args
        field = crf(args[0]) << 23 | reg(args[1]) << 16
        if mnemonic == 'cmpwi':
            return [11 << 26 | field | simm(value(args[2], names))]
        if mnemonic == 'cmplwi':
            return [10 << 26 | field | uimm(value(args[2], names))]
        return [31 << 26 | field | reg(args[2]) << 11 | (0 if mnemonic == 'cmpw' else 32) << 1]
    if mnemonic in ('slwi', 'srwi'):
        n = value(args[2], names)
        if not 0 < n < 32:
            raise AsmError('shift out of range')
        sh, mb, me = (n, 0, 31 - n) if mnemonic == 'slwi' else (32 - n, n, 31)
        return [21 << 26 | reg(args[1]) << 21 | reg(args[0]) << 16 | sh << 11 | mb << 6 | me << 1]
    if mnemonic in ('mflr', 'mfctr', 'mtlr', 'mtctr'):
        spr = SPR[mnemonic[2:]]
        field = ((spr & 0x1F) << 5 | spr >> 5) << 11
        return [31 << 26 | reg(args[0]) << 21 | field | (339 if mnemonic.startswith('mf') else 467) << 1]
    if mnemonic in ('b', 'bl'):
        disp = value(args[0], names) - at
        if disp & 3 or not -0x2000000 <= disp < 0x2000000:
            raise AsmError(f'branch target out of reach: {disp:#x}')
        return [18 << 26 | (disp & 0x3FFFFFC) | (mnemonic == 'bl')]
    if mnemonic in BRANCH_COND:
        if len(args) == 2:
            field, target = crf(args[0]), args[1]
        else:
            field, target = 0, args[0]
        bo, bit = BRANCH_COND[mnemonic]
        disp = value(target, names) - at
        if disp & 3 or not -0x8000 <= disp < 0x8000:
            raise AsmError(f'conditional branch target out of reach: {disp:#x}')
        return [16 << 26 | bo << 21 | (4 * field + bit) << 16 | (disp & 0xFFFC)]
    raise AsmError('unsupported instruction (add it, with a capstone test)')
