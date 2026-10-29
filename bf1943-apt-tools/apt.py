"""Reader, disassembler and block rewriter for BF1943's EA APT menu movies (big-endian, Xbox 360).

A menu .res is a BIGF archive of <name>.apt (movie, characters, bytecode), <name>.const (constant table)
and <name>.geo. Pointers inside .apt are offsets from its start.

Observed in this build:
- Movie/Sprite characters carry signature 0x09876543; frame items are Action (1), FrameLabel (2),
  PlaceObject (3), RemoveObject (4), Background (5) and InitAction (8).
- Bytecode is AS2 plus EA push/call opcodes. Operand sizes and alignment are those of the game's own
  pointer fixup, `829748f8`, whose jump table (8297498c) was checked against OPS during the research
  (operation-rising-sun research/AGENT_PROGRESS.md; that test is not kept here).
- That fixup walks each action block linearly to its first End byte, adding the movie base to pointer
  operands, and converts PushData and ConstantPool lists in place from .const indices to runtime values.
  So a rewritten block must be reachable from a frame item (not only by a branch), must not contain an End
  inside a function body, and must not share any list with another block.
Opcode names for EA-specific opcodes are descriptive labels, not recovered originals.

Command line:

  python -B apt.py <menu .res>      validate one movie: its action blocks, instructions and problems
"""
import struct
import sys

sys.dont_write_bytecode = True
SIGNATURE = 0x09876543
FUNCTION_MARKERS = (0x98765432, 0x12345678)


def be32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def bigf(raw):
    """[(name, bytes)] from a BIGF archive (total size little-endian, the rest big-endian), in order."""
    if raw[:4] != b'BIGF' or struct.unpack_from('<I', raw, 4)[0] != len(raw):
        raise ValueError('not a BIGF archive of this size')
    out, p = [], 16
    for _ in range(be32(raw, 8)):
        off, size = struct.unpack_from('>II', raw, p)
        end = raw.index(0, p + 8)
        out.append((raw[p + 8:end].decode('ascii'), raw[off:off + size]))
        p = end + 1
    return out


def write_bigf(files, template):
    """A BIGF archive with the given (name, bytes) in order, laid out as `template` is: the same header
    words and header padding, each file 4-byte aligned. Rebuilding the template's own files reproduces it."""
    head = 16 + sum(8 + len(n) + 1 for n, _ in files)
    if head != 16 + sum(8 + len(n) + 1 for n, _ in bigf(template)):
        raise ValueError('file names differ from the template')
    first = be32(template, 16)
    out = bytearray(template[:first])
    p, offsets = 16, []
    for name, data in files:
        while len(out) % 4:
            out.append(0)
        offsets.append((len(out), len(data)))
        out += data
    for (off, size), (name, _) in zip(offsets, files):
        struct.pack_into('>II', out, p, off, size)
        p += 8 + len(name) + 1
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out)


# opcode -> (label, operand layout). 'A' first: operands start at the next 4-byte boundary of the .apt.
# I int32, J int32 offset from the end of the operand (branches, With), P string pointer, PP two string
# pointers, F float32, B byte, H uint16, L count + pointer to a list of uint32 .const indices,
# F1/F2 function headers, TRY 20 bytes. Sizes match the fixup walker's groups in 829748f8.
OPS = {
    0x00: ('End', ''), 0x04: ('NextFrame', ''), 0x05: ('PrevFrame', ''), 0x06: ('Play', ''), 0x07: ('Stop', ''),
    0x08: ('ToggleQuality', ''), 0x09: ('StopSounds', ''), 0x0A: ('Add', ''), 0x0B: ('Subtract', ''),
    0x0C: ('Multiply', ''), 0x0D: ('Divide', ''), 0x0E: ('Equals', ''), 0x0F: ('Less', ''), 0x10: ('And', ''),
    0x11: ('Or', ''), 0x12: ('Not', ''), 0x13: ('StringEquals', ''), 0x14: ('StringLength', ''),
    0x15: ('StringExtract', ''), 0x17: ('Pop', ''), 0x18: ('ToInteger', ''), 0x1C: ('GetVariable', ''),
    0x1D: ('SetVariable', ''), 0x20: ('SetTarget2', ''), 0x21: ('StringAdd', ''), 0x22: ('GetProperty', ''),
    0x23: ('SetProperty', ''), 0x24: ('CloneSprite', ''), 0x25: ('RemoveSprite', ''), 0x26: ('Trace', ''),
    0x27: ('StartDrag', ''), 0x28: ('EndDrag', ''), 0x29: ('StringLess', ''), 0x2A: ('Throw', ''),
    0x2B: ('CastOp', ''), 0x2C: ('ImplementsOp', ''), 0x30: ('RandomNumber', ''), 0x31: ('MBStringLength', ''),
    0x32: ('CharToAscii', ''), 0x33: ('AsciiToChar', ''), 0x34: ('GetTime', ''), 0x35: ('MBStringExtract', ''),
    0x36: ('MBCharToAscii', ''), 0x37: ('MBAsciiToChar', ''), 0x3A: ('Delete', ''), 0x3B: ('Delete2', ''),
    0x3C: ('DefineLocal', ''), 0x3D: ('CallFunction', ''), 0x3E: ('Return', ''), 0x3F: ('Modulo', ''),
    0x40: ('NewObject', ''), 0x41: ('DefineLocal2', ''), 0x42: ('InitArray', ''), 0x43: ('InitObject', ''),
    0x44: ('TypeOf', ''), 0x45: ('TargetPath', ''), 0x46: ('Enumerate', ''), 0x47: ('Add2', ''),
    0x48: ('Less2', ''), 0x49: ('Equals2', ''), 0x4A: ('ToNumber', ''), 0x4B: ('ToString', ''),
    0x4C: ('PushDuplicate', ''), 0x4D: ('StackSwap', ''), 0x4E: ('GetMember', ''), 0x4F: ('SetMember', ''),
    0x50: ('Increment', ''), 0x51: ('Decrement', ''), 0x52: ('CallMethod', ''), 0x53: ('NewMethod', ''),
    0x54: ('InstanceOf', ''), 0x55: ('Enumerate2', ''), 0x56: ('Op56?', ''), 0x59: ('PushZero', ''),
    0x5A: ('PushOne', ''), 0x5B: ('CallFunctionPop', ''), 0x5C: ('CallFunction?', ''), 0x5D: ('CallMethodPop', ''),
    0x5E: ('CallMethod?', ''), 0x60: ('BitAnd', ''), 0x61: ('BitOr', ''), 0x62: ('BitXor', ''),
    0x63: ('ShiftLeft', ''), 0x64: ('ShiftRight', ''), 0x65: ('ShiftRight2', ''), 0x66: ('StrictEquals', ''),
    0x67: ('Greater', ''), 0x68: ('StringGreater', ''), 0x69: ('Extends', ''), 0x70: ('PushThis', ''),
    0x71: ('PushGlobal', ''), 0x72: ('ZeroVar', ''), 0x73: ('PushTrue', ''), 0x74: ('PushFalse', ''),
    0x75: ('PushNull', ''), 0x76: ('PushUndefined', ''), 0x77: ('Op77?', 'I'),
    0x81: ('GotoFrame', 'AI'), 0x83: ('GetURL', 'APP'), 0x87: ('StoreRegister', 'AI'),
    0x88: ('ConstantPool', 'AL'), 0x8A: ('WaitForFrame', ''), 0x8B: ('SetTarget', 'AP'),
    0x8C: ('GotoLabel', 'AP'), 0x8D: ('WaitForFrame2', ''), 0x8E: ('DefineFunction2', 'AF2'),
    0x8F: ('Try', 'ATRY'), 0x94: ('With', 'AJ'), 0x96: ('PushData', 'AL'), 0x99: ('BranchAlways', 'AJ'),
    0x9A: ('GetURL2', ''), 0x9B: ('DefineFunction', 'AF1'), 0x9D: ('BranchIfTrue', 'AJ'),
    0x9E: ('CallFrame', ''), 0x9F: ('GotoFrame2', 'AI'),
    0xA1: ('PushString', 'AP'), 0xA2: ('PushConstant', 'B'), 0xA3: ('PushWordConstant', 'H'),
    0xA4: ('GetStringVar', 'AP'), 0xA5: ('GetStringMember', 'AP'), 0xA6: ('SetStringVar', 'AP'),
    0xA7: ('SetStringMember', 'AP'), 0xAE: ('PushValueOfVar', 'B'), 0xAF: ('GetNamedMember', 'B'),
    0xB0: ('CallNamedFuncPop', 'B'), 0xB1: ('CallNamedFunc', 'B'), 0xB2: ('CallNamedMethodPop', 'B'),
    0xB3: ('CallNamedMethod', 'B'), 0xB4: ('PushFloat', 'F'), 0xB5: ('PushByte', 'B'), 0xB6: ('PushShort', 'H'),
    0xB7: ('PushLong', 'I'), 0xB8: ('BranchIfFalse', 'AJ'),
}
BY_LABEL = {label: op for op, (label, _) in OPS.items()}
# Operand bytes after the opcode (and after alignment, for 'A' layouts), as the fixup walker advances.
OPERAND_BYTES = {'': 0, 'I': 4, 'J': 4, 'P': 4, 'PP': 8, 'F': 4, 'B': 1, 'H': 2, 'L': 8, 'F1': 24, 'F2': 28, 'TRY': 20}
POOL_INDEX_OPS = {0xA2, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


class Ins:
    """One instruction. `args` holds decoded operands; for rewriting, `target`/`end` may name other Ins."""
    __slots__ = ('op', 'args', 'pc')

    def __init__(self, op, args=None, pc=None):
        self.op, self.args, self.pc = op, dict(args or {}), pc

    @property
    def label(self):
        return OPS[self.op][0]

    def __repr__(self):
        return f'Ins({self.label}, {self.args})'


def op(label, **args):
    return Ins(BY_LABEL[label], args)


class Apt:
    def __init__(self, raw_res):
        self.files = bigf(raw_res)
        names = [n for n, _ in self.files]
        self.name = next(n[:-4] for n in names if n.endswith('.apt'))
        self.apt = dict(self.files)[self.name + '.apt']
        self.const = dict(self.files)[self.name + '.const']
        c = self.const
        if c[:17] != b'Apt constant file':
            raise ValueError('bad .const header')
        self.movie, count, first = be32(c, 20), be32(c, 24), be32(c, 28)
        self.constants = []
        for k in range(count):
            kind, value = struct.unpack_from('>II', c, first + 8 * k)
            self.constants.append(self.cstr(value, c) if kind == 1 else (kind, value))
        m = self.movie
        if be32(self.apt, m) != 9 or be32(self.apt, m + 4) != SIGNATURE:
            raise ValueError('movie character not found')

    def u(self, o):
        return be32(self.apt, o)

    @staticmethod
    def cstr(o, b):
        return b[o:b.index(0, o)].decode('latin1')

    def s(self, o):
        return self.cstr(o, self.apt) if o else None

    def characters(self):
        m = self.movie
        return [self.u(self.u(m + 24) + 4 * i) for i in range(self.u(m + 20))]

    def imports(self):
        """(movie, symbol, character id) for each import (count at movie +40, table at +44, 16-byte rows)."""
        m, out = self.movie, []
        for i in range(self.u(m + 40)):
            row = self.u(m + 44) + 16 * i
            out.append((self.s(self.u(row)), self.s(self.u(row + 4)), self.u(row + 8)))
        return out

    def exports(self):
        """(symbol, character id) for each export (count at movie +48, table at +52, 8-byte rows)."""
        m = self.movie
        return [(self.s(self.u(self.u(m + 52) + 8 * i)), self.u(self.u(m + 52) + 8 * i + 4)) for i in range(self.u(m + 48))]

    def frames(self, char):
        """[(frame index, [(item address, item type)])] of a Movie or Sprite character."""
        count, table = self.u(char + 8), self.u(char + 12)
        return [(f, [(self.u(self.u(table + 8 * f + 4) + 4 * k), self.u(self.u(self.u(table + 8 * f + 4) + 4 * k)))
                     for k in range(self.u(table + 8 * f))]) for f in range(count)]

    def action_items(self):
        """(owner description, frame item address, offset of its bytecode pointer, bytecode address)."""
        out = []
        for ci, c in [(None, self.movie)] + [(i, c) for i, c in enumerate(self.characters()) if c and self.u(c) == 5]:
            who = 'movie' if ci is None else ci
            for f, items in self.frames(c):
                for item, kind in items:
                    if kind == 1:
                        out.append((f'char {who} frame {f} action', item, 4, self.u(item + 4)))
                    elif kind == 8:
                        out.append((f'char {who} frame {f} initaction sprite {self.u(item + 4)}', item, 8, self.u(item + 8)))
        return out

    def action_blocks(self):
        return [(owner, start) for owner, _, _, start in self.action_items()]

    def frame_action(self, char, frame):
        """(frame item, pointer field offset, bytecode address) of the single Action on a sprite frame."""
        c = self.characters()[char]
        hits = [(it, 4, self.u(it + 4)) for f, items in self.frames(c) if f == frame for it, k in items if k == 1]
        if len(hits) != 1:
            raise ValueError(f'char {char} frame {frame} has {len(hits)} actions')
        return hits[0]

    def decode(self, pc):
        """The instruction at pc and the pc after it."""
        a, code = self.apt, self.apt[pc]
        if code not in OPS:
            raise ValueError(f'unknown opcode 0x{code:02x} at 0x{pc:x}')
        layout = OPS[code][1]
        p, args = pc + 1, {}
        if layout.startswith('A'):
            p = (p + 3) & ~3
            layout = layout[1:]
        if layout == 'I':
            args['value'] = struct.unpack_from('>i', a, p)[0]
        elif layout == 'J':
            args['offset'] = struct.unpack_from('>i', a, p)[0]
            args['target'] = p + 4 + args['offset']
        elif layout == 'P':
            args['ptr'] = self.u(p)
            args['string'] = self.s(args['ptr'])
        elif layout == 'PP':
            args['ptrs'] = (self.u(p), self.u(p + 4))
            args['strings'] = tuple(self.s(x) for x in args['ptrs'])
        elif layout == 'F':
            args['value'] = struct.unpack_from('>f', a, p)[0]
            args['raw'] = a[p:p + 4]
        elif layout == 'B':
            args['value'] = a[p]
        elif layout == 'H':
            args['value'] = struct.unpack_from('>H', a, p)[0]
        elif layout == 'L':
            n, lst = self.u(p), self.u(p + 4)
            args['items'] = [self.u(lst + 4 * k) for k in range(n)]
        elif layout in ('F1', 'F2'):
            name_ptr, nparams, third = self.u(p), self.u(p + 4), self.u(p + 8)
            if layout == 'F2':
                # name, parameter count, register count and flags (one word), parameter list, body size
                params, size = self.u(p + 12), self.u(p + 16)
                args['params'] = [(self.u(params + 8 * k), self.u(params + 8 * k + 4)) for k in range(nparams)]
                args['regflags'] = third
                markers = p + 20
            else:
                params, size = third, self.u(p + 12)
                args['params'] = [self.u(params + 4 * k) for k in range(nparams)]
                markers = p + 16
            if (self.u(markers), self.u(markers + 4)) != FUNCTION_MARKERS:
                raise ValueError(f'function header markers missing at 0x{pc:x}')
            args.update(name_ptr=name_ptr, name=self.s(name_ptr), size=size, body=markers + 8)
        elif layout == 'TRY':
            args['raw'] = a[p:p + 20]
        return Ins(code, args, pc), p + OPERAND_BYTES[layout]

    def walk(self, start):
        """Instructions of one action block, linearly, to its End (the fixup walker's extent)."""
        pc, out = start, []
        while True:
            ins, nxt = self.decode(pc)
            out.append(ins)
            if ins.op == 0x00:
                return out
            pc = nxt

    def validate(self):
        """Every block decodes to an End with no End inside a function; branches, With bodies and function
        ends land on instruction starts."""
        problems, blocks, count = [], self.action_blocks(), 0
        for owner, start in blocks:
            try:
                ins = self.walk(start)
            except Exception as e:
                problems.append(f'{owner} @0x{start:x}: {e}')
                continue
            count += len(ins)
            starts = {i.pc for i in ins} | {ins[-1].pc + 1}
            for i in ins:
                if 'body' in i.args:
                    end = i.args['body'] + i.args['size']
                    if end not in starts or end > ins[-1].pc:
                        problems.append(f'{owner}: {i.label} {i.args["name"]} at 0x{i.pc:x} body end 0x{end:x} not inside the block')
                if 'target' in i.args and i.args['target'] not in starts:
                    problems.append(f'{owner}: {i.label} at 0x{i.pc:x} -> 0x{i.args["target"]:x} not an instruction')
        return len(blocks), count, problems

    # ---- listing

    def operand_text(self, i, pool):
        a = i.args
        if i.op == 0x96:  # PushData lists index the .const table directly
            return ', '.join(repr(self.constant(k)) for k in a['items'])
        if i.op == 0x88:
            return f'{len(a["items"])} constants'
        if i.op in POOL_INDEX_OPS or i.op == 0xA3:
            v = pool[a['value']] if pool is not None and a['value'] < len(pool) else None
            return repr(self.constant(v)) if v is not None else f'<pool {a["value"]}>'
        if 'name' in a:
            names = [self.s(x[1]) if isinstance(x, tuple) else self.s(x) for x in a['params']]
            return f'{a["name"]!r} params={names} size={a["size"]}'
        if 'target' in a:
            return f'-> 0x{a["target"]:x}'
        if 'string' in a:
            return repr(a['string'])
        if 'strings' in a:
            return repr(a['strings'])
        if 'value' in a:
            return repr(a['value'])
        return ''

    def constant(self, k):
        if k >= len(self.constants):
            return f'<const {k}>'
        c = self.constants[k]
        return c if isinstance(c, str) else {4: f'reg{c[1]}'}.get(c[0], c)

    def listing(self, start):
        pool, ends, lines = None, [], []
        for i in self.walk(start):
            ends = [e for e in ends if e > i.pc]
            if i.op == 0x88:
                pool = i.args['items']
            lines.append(f'{i.pc:06x} {"  " * len(ends)}{i.label} {self.operand_text(i, pool)}')
            if 'body' in i.args:
                ends.append(i.args['body'] + i.args['size'])
        return lines


class Rewriter:
    """Edits one movie: same-size patches in place, and relocated blocks appended after the original .apt."""

    def __init__(self, apt):
        self.src = apt
        self.apt = bytearray(apt.apt)
        self.base = (len(self.apt) + 15) & ~15
        self.tail = bytearray()
        self.strings = {}
        self.log = []

    # -- data area

    def alloc(self, data, align=4):
        while (self.base + len(self.tail)) % align:
            self.tail.append(0)
        at = self.base + len(self.tail)
        self.tail += data
        return at

    def string(self, s):
        if s not in self.strings:
            self.strings[s] = self.alloc(s.encode('latin1') + b'\0', 1)
        return self.strings[s]

    def words(self, values):
        return self.alloc(b''.join(struct.pack('>I', v) for v in values)) if values else 0

    # -- in-place patch

    def patch(self, pc, expected, replacement, why):
        if len(expected) != len(replacement):
            raise ValueError('in-place patches must keep the size')
        if bytes(self.apt[pc:pc + len(expected)]) != expected:
            raise ValueError(f'0x{pc:x}: expected {expected.hex()}, found {bytes(self.apt[pc:pc + len(expected)]).hex()}')
        self.apt[pc:pc + len(expected)] = replacement
        self.log.append(dict(kind='patch', at=pc, before=expected.hex(), after=replacement.hex(), why=why))

    # -- block relocation

    def relink(self, ins):
        """Turn decoded pc-based targets and function ends into references to Ins objects."""
        by_pc = {i.pc: i for i in ins}
        for i in ins:
            if 'target' in i.args and isinstance(i.args['target'], int):
                i.args['target'] = by_pc[i.args['target']]
            if 'body' in i.args:
                i.args['end'] = by_pc[i.args.pop('body') + i.args.pop('size')]
        return ins

    def block(self, start):
        """The block at start as editable, relinked instructions."""
        return self.relink(self.src.walk(start))

    def assemble(self, ins):
        """Append the instruction list as a new block; returns its offset. Lists are always emitted fresh."""
        if not ins or ins[-1].op != 0x00 or any(i.op == 0x00 for i in ins[:-1]):
            raise ValueError('a block ends with its only End')
        if any(i.op == 0x8F for i in ins):
            raise ValueError('Try blocks are not rewritten')
        if len({id(i) for i in ins}) != len(ins):
            raise ValueError('the block holds one instruction object more than once (build each use fresh)')
        # pass 1: positions (alignment depends on the absolute offset)
        start = self.alloc(b'', 4)
        pc, at = start, {}
        for i in ins:
            at[id(i)] = pc
            layout = OPS[i.op][1]
            pc += 1
            if layout.startswith('A'):
                pc = (pc + 3) & ~3
                layout = layout[1:]
            pc += OPERAND_BYTES[layout]
        # data the operands point to, allocated after the code
        code_len = pc - start
        self.tail += bytes(code_len)
        out = bytearray()
        for i in ins:
            if start + len(out) != at[id(i)]:
                raise AssertionError(f'{i.label}: emitted position differs from the layout pass')
            out.append(i.op)
            layout = OPS[i.op][1]
            if layout.startswith('A'):
                while (start + len(out)) % 4:
                    out.append(0)
                layout = layout[1:]
            a = i.args
            if layout == 'I':
                out += struct.pack('>i', a['value'])
            elif layout == 'J':
                after = start + len(out) + 4
                out += struct.pack('>i', at[id(a['target'])] - after)
            elif layout == 'P':
                out += struct.pack('>I', self.string(a['string']) if 'ptr' not in a else a['ptr'])
            elif layout == 'PP':
                out += struct.pack('>II', *(a['ptrs'] if 'ptrs' in a else [self.string(s) for s in a['strings']]))
            elif layout == 'F':
                out += a['raw'] if 'raw' in a else struct.pack('>f', a['value'])
            elif layout == 'B':
                out.append(a['value'])
            elif layout == 'H':
                out += struct.pack('>H', a['value'])
            elif layout == 'L':
                out += struct.pack('>II', len(a['items']), self.words(a['items']))
            elif layout in ('F1', 'F2'):
                name = a['name_ptr'] if 'name_ptr' in a else self.string(a['name'])
                header_end = start + len(out) + (20 if layout == 'F2' else 16) + 8
                size = at[id(a['end'])] - header_end
                if layout == 'F2':
                    params = self.words([w for pair in a['params'] for w in pair])
                    out += struct.pack('>5I', name, len(a['params']), a['regflags'], params, size)
                else:
                    out += struct.pack('>4I', name, len(a['params']), self.words(a['params']), size)
                out += struct.pack('>2I', *FUNCTION_MARKERS)
        if len(out) != code_len:
            raise AssertionError('assembled size differs from the layout pass')
        self.tail[start - self.base:start - self.base + code_len] = out
        return start

    def repoint(self, item, field, old, new, why):
        if be32(self.apt, item + field) != old:
            raise ValueError(f'frame item 0x{item:x} does not point at 0x{old:x}')
        struct.pack_into('>I', self.apt, item + field, new)
        self.log.append(dict(kind='relocate', item=item, old_block=old, new_block=new, why=why))

    def result(self):
        apt = bytes(self.apt) + bytes(self.base - len(self.apt)) + bytes(self.tail) if self.tail else bytes(self.apt)
        return apt

    def res(self, template):
        files = [(n, self.result() if n == self.src.name + '.apt' else d) for n, d in self.src.files]
        return write_bigf(files, template)


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 else 1)
    apt = Apt(open(sys.argv[1], 'rb').read())
    blocks, n, problems = apt.validate()
    print(f'{apt.name}: {blocks} action blocks, {n} instructions, {len(problems)} problems')
    for p in problems[:40]:
        print('  ', p)
