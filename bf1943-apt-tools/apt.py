"""Reader, disassembler and block rewriter for BF1943's EA APT menu movies (big-endian, Xbox 360).

Reads a menu .res (Apt): its characters, frames, action blocks and their instructions (walk, listing, validate).
Rewriter edits one movie (in-place patches, relocated blocks) and writes the .res again (Rewriter.res). The command
line validates a movie and prints its problems.

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
                                    (exit 0 = no problem, 1 = problems found, 2 = the movie could not be read:
                                    not a menu .res, cut short, or a count or pointer past its .apt)

A damaged movie raises ValueError (never IndexError or struct.error, 2026-10-30 second review): every count is at
most what the file could hold, and the frame items read over all characters at most one per word of the .apt (so a
crafted movie cannot make validation read for hours).
"""
import struct
import sys
from collections.abc import Sequence
from typing import Any, cast

sys.dont_write_bytecode = True
SIGNATURE = 0x09876543
FUNCTION_MARKERS = (0x98765432, 0x12345678)

type Buffer = bytes | bytearray
# a .const entry: a string, or (kind, value) for the others (kind 4: a register)
type Constant = str | tuple[int, int]


def be32(data: Buffer, offset: int) -> int:
    """The big-endian u32 at offset; ValueError past the end."""
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f'damaged movie: a word at 0x{offset:x} lies past the end ({len(data)} bytes)')
    return struct.unpack_from('>I', data, offset)[0]


def bigf(raw: bytes) -> list[tuple[str, bytes]]:
    """[(name, bytes)] from a BIGF archive (total size little-endian, the rest big-endian), in order. ValueError for
    a header or file past the archive's end."""
    if len(raw) < 16 or raw[:4] != b'BIGF' or struct.unpack_from('<I', raw, 4)[0] != len(raw):
        raise ValueError('not a BIGF archive of this size')
    count = be32(raw, 8)
    if count > (len(raw) - 16) // 10:
        raise ValueError(f'damaged BIGF archive: {count} files in {len(raw)} bytes')
    out: list[tuple[str, bytes]] = []
    pos = 16
    for _ in range(count):
        end = raw.find(0, pos + 8)
        if pos + 8 > len(raw) or end < 0:
            raise ValueError('damaged BIGF archive: its file list runs past the end')
        offset, size = struct.unpack_from('>II', raw, pos)
        if offset + size > len(raw):
            raise ValueError(f'damaged BIGF archive: a file at 0x{offset:x} of {size} bytes runs past the end')
        out.append((raw[pos + 8:end].decode('ascii', 'replace'), raw[offset:offset + size]))
        pos = end + 1
    return out


def write_bigf(files: Sequence[tuple[str, bytes]], template: bytes) -> bytes:
    """A BIGF archive with the given (name, bytes) in order, laid out as `template` is: the same header
    words and header padding, each file 4-byte aligned. Rebuilding the template's own files reproduces it. The
    names must be the template's, in its order (only the offsets and sizes are written)."""
    names = [name for name, _ in bigf(template)]
    if [name for name, _ in files] != names:
        raise ValueError(f'file names differ from the template ({[name for name, _ in files]} against {names})')
    head = 16 + sum(8 + len(name) + 1 for name in names)
    # each file's (offset, size) row: after the 16-byte header and the rows (8 bytes and a NUL-ended name) before it
    rows = [16 + sum(8 + len(name) + 1 for name in names[:k]) for k in range(len(names))]
    first = min((be32(template, row) for row in rows), default=head)
    if first < head:
        raise ValueError('damaged BIGF template: a file starts inside its header')
    out = bytearray(template[:first])
    pos = 16
    offsets: list[tuple[int, int]] = []
    for _name, data in files:
        while len(out) % 4:
            out.append(0)
        offsets.append((len(out), len(data)))
        out += data
    for (offset, size), (name, _) in zip(offsets, files):
        struct.pack_into('>II', out, pos, offset, size)
        pos += 8 + len(name) + 1
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

    def __init__(self, op: int, args: dict[str, Any] | None = None, pc: int | None = None) -> None:
        self.op, self.args, self.pc = op, dict(args or {}), pc

    @property
    def label(self) -> str:
        return OPS[self.op][0]

    def __repr__(self) -> str:
        return f'Ins({self.label}, {self.args})'


def op(label: str, **args: Any) -> Ins:
    """A new instruction by its label (OPS), with its operands."""
    return Ins(BY_LABEL[label], args)


class Apt:
    """One menu movie: its archive's files, the .apt and .const bytes, the movie character and the constants."""

    def __init__(self, raw_res: bytes) -> None:
        self.files = bigf(raw_res)
        names = [name for name, _ in self.files]
        name = next((name[:-4] for name in names if name.endswith('.apt')), None)
        if name is None or name + '.const' not in names:
            raise ValueError(f'not a menu movie: the archive holds {names}, no <name>.apt and <name>.const')
        self.name: str = name
        self.apt = dict(self.files)[self.name + '.apt']
        self.const = dict(self.files)[self.name + '.const']
        const = self.const
        if const[:17] != b'Apt constant file':
            raise ValueError('bad .const header')
        self.movie, count, first = be32(const, 20), be32(const, 24), be32(const, 28)
        self.constants: list[Constant] = []
        for k in range(self.counted(count, 8, 'constants', const)):
            kind, value = be32(const, first + 8 * k), be32(const, first + 8 * k + 4)
            self.constants.append(self.cstr(value, const) if kind == 1 else (kind, value))
        movie = self.movie
        if be32(self.apt, movie) != 9 or be32(self.apt, movie + 4) != SIGNATURE:
            raise ValueError('movie character not found')

    def u(self, offset: int) -> int:
        """The .apt's big-endian u32 at offset."""
        return be32(self.apt, offset)

    def counted(self, n: int, size: int, what: str, data: bytes | None = None) -> int:
        """n, when n records of size bytes could fit in the file (the .apt unless data); ValueError otherwise."""
        limit = len(self.apt if data is None else data) // size
        if n > limit:
            raise ValueError(f'damaged movie: {n} {what}, more than the {limit} its file could hold')
        return n

    @staticmethod
    def cstr(offset: int, data: Buffer) -> str:
        """The NUL-ended Latin-1 string at offset of data."""
        end = data.find(0, offset)
        if offset >= len(data) or end < 0:
            raise ValueError(f'damaged movie: a string at 0x{offset:x} does not end inside its file')
        return data[offset:end].decode('latin1')

    def s(self, offset: int) -> str | None:
        """The .apt's string at offset; None for offset 0 (no string)."""
        return self.cstr(offset, self.apt) if offset else None

    def characters(self) -> list[int]:
        """Each character's address (0: an empty slot)."""
        movie = self.movie
        count = self.counted(self.u(movie + 20), 4, 'characters')
        return [self.u(self.u(movie + 24) + 4 * i) for i in range(count)]

    def imports(self) -> list[tuple[str | None, str | None, int]]:
        """(movie, symbol, character id) for each import (count at movie +40, table at +44, 16-byte rows)."""
        movie = self.movie
        out: list[tuple[str | None, str | None, int]] = []
        for i in range(self.counted(self.u(movie + 40), 16, 'imports')):
            row = self.u(movie + 44) + 16 * i
            out.append((self.s(self.u(row)), self.s(self.u(row + 4)), self.u(row + 8)))
        return out

    def exports(self) -> list[tuple[str | None, int]]:
        """(symbol, character id) for each export (count at movie +48, table at +52, 8-byte rows)."""
        movie = self.movie
        rows = [self.u(movie + 52) + 8 * i for i in range(self.counted(self.u(movie + 48), 8, 'exports'))]
        return [(self.s(self.u(row)), self.u(row + 4)) for row in rows]

    def frames(self, char: int, budget: list[int] | None = None) -> list[tuple[int, list[tuple[int, int]]]]:
        """[(frame index, [(item address, item type)])] of a Movie or Sprite character. budget: a one-item list of the
        frame items still allowed (action_items shares one over all characters: at most one item per word of the .apt;
        2026-10-30 second review: a crafted movie made validation read count x items words, hours for 1 MB)."""
        budget = budget if budget is not None else [len(self.apt) // 4]
        count, table = self.counted(self.u(char + 8), 8, 'frames'), self.u(char + 12)
        out: list[tuple[int, list[tuple[int, int]]]] = []
        for f in range(count):
            n, items_at = self.counted(self.u(table + 8 * f), 4, 'frame items'), self.u(table + 8 * f + 4)
            budget[0] -= n
            if budget[0] < 0:
                raise ValueError(f'damaged movie: more frame items than words in the .apt ({len(self.apt) // 4})')
            items = []
            for k in range(n):
                item = self.u(items_at + 4 * k)
                items.append((item, self.u(item)))
            out.append((f, items))
        return out

    def action_items(self) -> list[tuple[str, int, int, int]]:
        """(owner description, frame item address, offset of its bytecode pointer, bytecode address)."""
        out: list[tuple[str, int, int, int]] = []
        budget = [len(self.apt) // 4]
        sprites = [(i, char) for i, char in enumerate(self.characters()) if char and self.u(char) == 5]
        owners: list[tuple[int | None, int]] = [(None, self.movie), *sprites]
        for char_index, char in owners:
            who = 'movie' if char_index is None else char_index
            for f, items in self.frames(char, budget):
                for item, kind in items:
                    if kind == 1:
                        out.append((f'char {who} frame {f} action', item, 4, self.u(item + 4)))
                    elif kind == 8:
                        owner = f'char {who} frame {f} initaction sprite {self.u(item + 4)}'
                        out.append((owner, item, 8, self.u(item + 8)))
        return out

    def action_blocks(self) -> list[tuple[str, int]]:
        """(owner description, bytecode address) of each action block a frame item names."""
        return [(owner, start) for owner, _, _, start in self.action_items()]

    def frame_action(self, char: int, frame: int) -> tuple[int, int, int]:
        """(frame item, pointer field offset, bytecode address) of the single Action on a sprite frame."""
        address = self.characters()[char]
        hits = [(item, 4, self.u(item + 4))
                for f, items in self.frames(address) if f == frame
                for item, kind in items if kind == 1]
        if len(hits) != 1:
            raise ValueError(f'char {char} frame {frame} has {len(hits)} actions')
        return hits[0]

    def decode(self, pc: int) -> tuple[Ins, int]:
        """The instruction at pc and the pc after it."""
        if not 0 <= pc < len(self.apt):
            raise ValueError(f'damaged movie: an instruction at 0x{pc:x}, past the end of the .apt')
        apt, code = self.apt, self.apt[pc]
        if code not in OPS:
            raise ValueError(f'unknown opcode 0x{code:02x} at 0x{pc:x}')
        layout = OPS[code][1]
        pos = pc + 1                    # the operands' address
        args: dict[str, Any] = {}
        if layout.startswith('A'):
            pos = (pos + 3) & ~3
            layout = layout[1:]
        if pos + OPERAND_BYTES[layout] > len(apt):
            raise ValueError(f'damaged movie: the operands of 0x{code:02x} at 0x{pc:x} run past the end of the .apt')
        if layout == 'I':
            args['value'] = struct.unpack_from('>i', apt, pos)[0]
        elif layout == 'J':
            args['offset'] = struct.unpack_from('>i', apt, pos)[0]
            args['target'] = pos + 4 + args['offset']
        elif layout == 'P':
            args['ptr'] = self.u(pos)
            args['string'] = self.s(args['ptr'])
        elif layout == 'PP':
            args['ptrs'] = (self.u(pos), self.u(pos + 4))
            args['strings'] = tuple(self.s(ptr) for ptr in args['ptrs'])
        elif layout == 'F':
            args['value'] = struct.unpack_from('>f', apt, pos)[0]
            args['raw'] = apt[pos:pos + 4]
        elif layout == 'B':
            args['value'] = apt[pos]
        elif layout == 'H':
            args['value'] = struct.unpack_from('>H', apt, pos)[0]
        elif layout == 'L':
            n, items_at = self.u(pos), self.u(pos + 4)
            args['items'] = [self.u(items_at + 4 * k) for k in range(self.counted(n, 4, 'list items'))]
        elif layout in ('F1', 'F2'):
            name_ptr, nparams, third = self.u(pos), self.u(pos + 4), self.u(pos + 8)
            if layout == 'F2':
                # name, parameter count, register count and flags (one word), parameter list, body size
                params, size = self.u(pos + 12), self.u(pos + 16)
                args['params'] = [(self.u(params + 8 * k), self.u(params + 8 * k + 4))
                                  for k in range(self.counted(nparams, 8, 'parameters'))]
                args['regflags'] = third
                markers = pos + 20
            else:
                params, size = third, self.u(pos + 12)
                args['params'] = [self.u(params + 4 * k) for k in range(self.counted(nparams, 4, 'parameters'))]
                markers = pos + 16
            if (self.u(markers), self.u(markers + 4)) != FUNCTION_MARKERS:
                raise ValueError(f'function header markers missing at 0x{pc:x}')
            args.update(name_ptr=name_ptr, name=self.s(name_ptr), size=size, body=markers + 8)
        elif layout == 'TRY':
            args['raw'] = apt[pos:pos + 20]
        return Ins(code, args, pc), pos + OPERAND_BYTES[layout]

    def walk(self, start: int, budget: list[int] | None = None) -> list[Ins]:
        """Instructions of one action block, linearly, to its End (the fixup walker's extent). budget: a one-item list
        of the instructions still allowed (validate shares one over all blocks); ValueError when it runs out."""
        pc = start
        out: list[Ins] = []
        while True:
            ins, nxt = self.decode(pc)
            out.append(ins)
            if budget is not None:
                budget[0] -= 1
                if budget[0] < 0:
                    raise ValueError(f'damaged movie: the action blocks hold more instructions than the .apt has bytes '
                                     f'({len(self.apt)}): blocks overlap or are shared')
            if ins.op == 0x00:
                return out
            pc = nxt

    def validate(self) -> tuple[int, int, list[str]]:
        """Every block decodes to an End with no End inside a function; branches and With bodies land on the
        block's instruction starts (2026-10-30 second review: the byte after the End passed, and Rewriter.block then
        failed on it); function ends on an instruction start up to the End. ValueError when the movie's characters or
        frames cannot be read (the command line's exit 2). The blocks together decode at most one instruction per byte
        of the .apt, as blocks that neither overlap nor are shared do (every block of the game's movies; the fixup walker
        would fix a shared block's pointers twice); past that each block is a problem (2026-10-31: a 0.9 MB movie of
        60,000 frame items naming one long block took hours)."""
        problems: list[str] = []
        blocks, count = self.action_blocks(), 0
        budget = [len(self.apt)]
        for owner, start in blocks:
            try:
                block = self.walk(start, budget)
            except Exception as e:
                problems.append(f'{owner} @0x{start:x}: {e}')
                continue
            count += len(block)
            starts = {cast(int, i.pc) for i in block}     # decoded: every instruction has its pc
            for i in block:
                if 'body' in i.args:
                    end = i.args['body'] + i.args['size']
                    if end not in starts or end > cast(int, block[-1].pc):
                        problems.append(f'{owner}: {i.label} {i.args["name"]} at 0x{i.pc:x} body end 0x{end:x} '
                                        f'not inside the block')
                if 'target' in i.args and i.args['target'] not in starts:
                    problems.append(f'{owner}: {i.label} at 0x{i.pc:x} -> 0x{i.args["target"]:x} not an instruction')
        return len(blocks), count, problems

    # ---- listing

    def operand_text(self, i: Ins, pool: list[int] | None) -> str:
        """The operands of instruction i as listing text; pool: the ConstantPool in force (its .const indices)."""
        args = i.args
        if i.op == 0x96:  # PushData lists index the .const table directly
            return ', '.join(repr(self.constant(k)) for k in args['items'])
        if i.op == 0x88:
            return f'{len(args["items"])} constants'
        if i.op in POOL_INDEX_OPS or i.op == 0xA3:
            index = pool[args['value']] if pool is not None and args['value'] < len(pool) else None
            return repr(self.constant(index)) if index is not None else f'<pool {args["value"]}>'
        if 'name' in args:
            names = [self.s(param[1]) if isinstance(param, tuple) else self.s(param) for param in args['params']]
            return f'{args["name"]!r} params={names} size={args["size"]}'
        if 'target' in args:
            return f'-> 0x{args["target"]:x}'
        if 'string' in args:
            return repr(args['string'])
        if 'strings' in args:
            return repr(args['strings'])
        if 'value' in args:
            return repr(args['value'])
        return ''

    def constant(self, k: int) -> Constant:
        """Constant k for a listing: its string, 'reg<n>' for a register, (kind, value) for the others."""
        if k >= len(self.constants):
            return f'<const {k}>'
        value = self.constants[k]
        return value if isinstance(value, str) else {4: f'reg{value[1]}'}.get(value[0], value)

    def listing(self, start: int) -> list[str]:
        """The block at start as text lines: address, indentation per enclosing function, label and operands."""
        pool: list[int] | None = None
        ends: list[int] = []
        lines: list[str] = []
        for i in self.walk(start):
            ends = [e for e in ends if e > cast(int, i.pc)]     # decoded: it has its pc
            if i.op == 0x88:
                pool = i.args['items']
            lines.append(f'{i.pc:06x} {"  " * len(ends)}{i.label} {self.operand_text(i, pool)}')
            if 'body' in i.args:
                ends.append(i.args['body'] + i.args['size'])
        return lines


class Rewriter:
    """Edits one movie: same-size patches in place, and relocated blocks appended after the original .apt."""

    def __init__(self, apt: Apt) -> None:
        self.src = apt
        self.apt = bytearray(apt.apt)
        self.base = (len(self.apt) + 15) & ~15
        self.tail = bytearray()
        self.strings: dict[str, int] = {}
        self.log: list[dict[str, Any]] = []

    # -- data area

    def alloc(self, data: bytes, align: int = 4) -> int:
        """data appended to the tail (aligned); its .apt offset."""
        while (self.base + len(self.tail)) % align:
            self.tail.append(0)
        offset = self.base + len(self.tail)
        self.tail += data
        return offset

    def string(self, text: str) -> int:
        """The .apt offset of text in the tail, appended the first time it is asked for."""
        if text not in self.strings:
            self.strings[text] = self.alloc(text.encode('latin1') + b'\0', 1)
        return self.strings[text]

    def words(self, values: Sequence[int]) -> int:
        """The .apt offset of the values as big-endian u32s in the tail (0 for none)."""
        return self.alloc(b''.join(struct.pack('>I', value) for value in values)) if values else 0

    # -- in-place patch

    def patch(self, pc: int, expected: bytes, replacement: bytes, why: str) -> None:
        if len(expected) != len(replacement):
            raise ValueError('in-place patches must keep the size')
        found = bytes(self.apt[pc:pc + len(expected)])
        if found != expected:
            raise ValueError(f'0x{pc:x}: expected {expected.hex()}, found {found.hex()}')
        self.apt[pc:pc + len(expected)] = replacement
        self.log.append(dict(kind='patch', at=pc, before=expected.hex(), after=replacement.hex(), why=why))

    # -- block relocation

    def relink(self, ins: list[Ins]) -> list[Ins]:
        """Turn decoded pc-based targets and function ends into references to Ins objects."""
        by_pc = {i.pc: i for i in ins}
        for i in ins:
            if 'target' in i.args and isinstance(i.args['target'], int):
                if i.args['target'] not in by_pc:
                    raise ValueError(f'{i.label} at 0x{i.pc:x} -> 0x{i.args["target"]:x}, not an instruction of the '
                                     f'block')
                i.args['target'] = by_pc[i.args['target']]
            if 'body' in i.args:
                end = i.args.pop('body') + i.args.pop('size')
                if end not in by_pc:
                    raise ValueError(f'{i.label} at 0x{i.pc:x}: body end 0x{end:x} is not an instruction of the block')
                i.args['end'] = by_pc[end]
        return ins

    def block(self, start: int) -> list[Ins]:
        """The block at start as editable, relinked instructions."""
        return self.relink(self.src.walk(start))

    def assemble(self, ins: Sequence[Ins]) -> int:
        """Append the instruction list as a new block; returns its offset. Lists are always emitted fresh."""
        if not ins or ins[-1].op != 0x00 or any(i.op == 0x00 for i in ins[:-1]):
            raise ValueError('a block ends with its only End')
        if any(i.op == 0x8F for i in ins):
            raise ValueError('Try blocks are not rewritten')
        if len({id(i) for i in ins}) != len(ins):
            raise ValueError('the block holds one instruction object more than once (build each use fresh)')
        # pass 1: positions (alignment depends on the absolute offset)
        start = self.alloc(b'', 4)
        pc = start
        address_of: dict[int, int] = {}     # id(instruction) -> its address
        for i in ins:
            address_of[id(i)] = pc
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
            if start + len(out) != address_of[id(i)]:
                raise AssertionError(f'{i.label}: emitted position differs from the layout pass')
            out.append(i.op)
            layout = OPS[i.op][1]
            if layout.startswith('A'):
                while (start + len(out)) % 4:
                    out.append(0)
                layout = layout[1:]
            args = i.args
            if layout == 'I':
                out += struct.pack('>i', args['value'])
            elif layout == 'J':
                after = start + len(out) + 4
                out += struct.pack('>i', address_of[id(args['target'])] - after)
            elif layout == 'P':
                out += struct.pack('>I', self.string(args['string']) if 'ptr' not in args else args['ptr'])
            elif layout == 'PP':
                ptrs = args['ptrs'] if 'ptrs' in args else [self.string(text) for text in args['strings']]
                out += struct.pack('>II', *ptrs)
            elif layout == 'F':
                out += args['raw'] if 'raw' in args else struct.pack('>f', args['value'])
            elif layout == 'B':
                out.append(args['value'])
            elif layout == 'H':
                out += struct.pack('>H', args['value'])
            elif layout == 'L':
                out += struct.pack('>II', len(args['items']), self.words(args['items']))
            elif layout in ('F1', 'F2'):
                name = args['name_ptr'] if 'name_ptr' in args else self.string(args['name'])
                header_end = start + len(out) + (20 if layout == 'F2' else 16) + 8
                size = address_of[id(args['end'])] - header_end
                if layout == 'F2':
                    params = self.words([word for pair in args['params'] for word in pair])
                    out += struct.pack('>5I', name, len(args['params']), args['regflags'], params, size)
                else:
                    out += struct.pack('>4I', name, len(args['params']), self.words(args['params']), size)
                out += struct.pack('>2I', *FUNCTION_MARKERS)
        if len(out) != code_len:
            raise AssertionError('assembled size differs from the layout pass')
        self.tail[start - self.base:start - self.base + code_len] = out
        return start

    def repoint(self, item: int, field: int, old: int, new: int, why: str) -> None:
        if be32(self.apt, item + field) != old:
            raise ValueError(f'frame item 0x{item:x} does not point at 0x{old:x}')
        struct.pack_into('>I', self.apt, item + field, new)
        self.log.append(dict(kind='relocate', item=item, old_block=old, new_block=new, why=why))

    def result(self) -> bytes:
        """The edited .apt: the original bytes (patched), then the tail from base."""
        if not self.tail:
            return bytes(self.apt)
        return bytes(self.apt) + bytes(self.base - len(self.apt)) + bytes(self.tail)

    def res(self, template: bytes) -> bytes:
        """The menu .res with the edited .apt, laid out as template (write_bigf)."""
        apt_name = self.src.name + '.apt'
        files = [(name, self.result() if name == apt_name else data) for name, data in self.src.files]
        return write_bigf(files, template)


def main(argv: list[str]) -> int:
    """0: no problem, 1: problems found, 2: the movie could not be read (2026-10-30 second review: a damaged movie
    raised a traceback, exit 1 as if problems were found)."""
    if len(argv) < 2 or argv[1] in ('-h', '--help'):
        print(__doc__)
        return 0 if len(argv) > 1 else 1
    try:
        with open(argv[1], 'rb') as f:
            apt = Apt(f.read())
        blocks, instructions, problems = apt.validate()
    except (OSError, ValueError) as e:
        print(f'error: {argv[1]} could not be read as a menu movie: {e}', file=sys.stderr)
        try:
            from bfbc2core import errors
            errors.logger().error(f'{argv[1]}: not read as a menu movie: {e}')
        except ImportError:
            pass
        return 2
    print(f'{apt.name}: {blocks} action blocks, {instructions} instructions, {len(problems)} problems')
    for problem in problems[:40]:
        print('  ', problem)
    return 1 if problems else 0                     # a script can tell an invalid movie from a sound one


if __name__ == '__main__':
    # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
    from bfbc2core import errors
    errors.setup('apt', gui=False)                  # a log; an unexpected error explained in words (exit 1)
    with errors.guard():
        sys.exit(main(sys.argv))
