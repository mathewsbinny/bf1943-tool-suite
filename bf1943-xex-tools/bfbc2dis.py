"""Two-image helpers (BF1943 checked image and BFBC2 Xbox 360 image): .pdata functions, normalized fingerprints,
direct call edges, capstone disassembly. Read only; research reading only (capstone has no VMX128; those print .long).

  python -B <suite>/bf1943-xex-tools/bfbc2dis.py {bf1943|bfbc2} <hex address> [count]

Importing this module looks up the research workspace (workspace.py: BF1943_RESEARCH, else the current folder) for
IMAGES; set BF1943_RESEARCH before the import. The investigations import IMAGES, ROOT and Image from it.
"""
import sys
sys.dont_write_bytecode = True
import bisect  # noqa: E402
import collections  # noqa: E402
import hashlib  # noqa: E402
import struct  # noqa: E402
from collections.abc import Iterator  # noqa: E402

import capstone  # noqa: E402

from workspace import research_root  # noqa: E402

ROOT = research_root()                  # the research workspace (workspace.py)
IMAGES = {'bf1943': (ROOT / 'work/xex-analysis/default.checked.memory.bin',
                     'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'),
          'bfbc2': (ROOT / 'work/bfbc2-xenon/game.memory.bin',
                    '094e53261d2ffdf2f0c7069b7d6762d46a5bb93ee09b01c1189ffae2dc28c4ae')}
_disassembler = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)
# D-form loads and stores by primary opcode, for disp_accesses
D_FORM_NAMES = {32: 'lwz', 34: 'lbz', 36: 'stw', 38: 'stb', 40: 'lhz', 44: 'sth', 48: 'lfs', 52: 'stfs', 50: 'lfd',
                54: 'stfd'}


type Normalized = tuple[int] | tuple[int, int]     # (primary opcode, extended opcode or form bits)


def normalize(instruction: int) -> Normalized:
    """Same normalization as compare_bfbc2_code.py: primary + extended opcode, operands masked."""
    op = instruction >> 26
    if op in (19, 31, 63):
        return (op, (instruction >> 1) & 0x3ff)
    if op == 59:
        return (op, (instruction >> 1) & 0x1f)
    if op == 30:
        return (op, (instruction >> 1) & 0xf)
    if op in (58, 62):
        return (op, instruction & 3)
    if op in (4, 5, 6):
        return (op, instruction & 0x7f3)
    if op in (16, 18):
        return (op, instruction & 3)
    return (op,)


class Image:
    """One image ('bf1943' or 'bfbc2', IMAGES), checked against its hash: its .pdata functions and readers over it."""

    def __init__(self, name: str) -> None:
        path, expected = IMAGES[name]
        self.name = name
        self.path = path
        self.data = path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        assert self.sha256 == expected, path
        pe = struct.unpack_from('<I', self.data, 60)[0]
        self.base = struct.unpack_from('<I', self.data, pe + 24 + 28)[0]
        rva, size = struct.unpack_from('<II', self.data, pe + 24 + 96 + 3 * 8)   # the exception directory (.pdata)
        functions: list[tuple[int, int]] = []
        for offset in range(rva, rva + size, 8):
            start, packed = struct.unpack_from('>II', self.data, offset)
            length = ((packed >> 8) & 0x3fffff) * 4
            if length and start % 4 == 0 and self.base <= start and start + length <= self.base + len(self.data):
                functions.append((start, start + length))
        self.functions = sorted(set(functions))
        self.starts = [start for start, _ in self.functions]
        self.end_of = dict(self.functions)
        self._fingerprints: dict[int, str] | None = None
        self._callers: dict[int, list[tuple[int, int]]] | None = None

    def word(self, address: int) -> int:
        return struct.unpack_from('>I', self.data, address - self.base)[0]

    def valid(self, address: int) -> bool:
        """Whether address lies in the image."""
        return self.base <= address < self.base + len(self.data)

    def containing(self, address: int) -> tuple[int, int] | None:
        """The (start, end) of the .pdata function holding address, or None."""
        i = bisect.bisect_right(self.starts, address) - 1
        return self.functions[i] if i >= 0 and address < self.functions[i][1] else None

    def norm_seq(self, start: int, end: int | None = None) -> list[Normalized]:
        """The normalized instruction words of the function at start (to end, else its .pdata end)."""
        end = end or self.end_of[start]
        return [normalize(self.word(address)) for address in range(start, end, 4)]

    def fingerprint(self, start: int, end: int | None = None) -> str:
        """SHA-1 of the function's normalized instructions (norm_seq), comparable across the two images."""
        return hashlib.sha1(repr(self.norm_seq(start, end)).encode()).hexdigest()

    def fingerprints(self) -> dict[int, str]:
        """{function start: fingerprint} of every .pdata function (computed once)."""
        if self._fingerprints is None:
            self._fingerprints = {start: self.fingerprint(start, end) for start, end in self.functions}
        return self._fingerprints

    def calls(self, start: int, end: int | None = None) -> list[tuple[int, int]]:
        """Direct bl targets in order, [(site, target)]."""
        end = end or self.end_of[start]
        out: list[tuple[int, int]] = []
        for site in range(start, end, 4):
            instruction = self.word(site)
            # bl / bla
            if instruction >> 26 == 18 and instruction & 1:
                displacement = instruction & 0x3fffffc
                if displacement & 0x2000000:
                    displacement -= 0x4000000
                absolute = instruction & 2
                out.append((site, (displacement if absolute else site + displacement) & 0xffffffff))
        return out

    def callers(self) -> dict[int, list[tuple[int, int]]]:
        """{target: [(site, calling function start)]} over every function's direct calls."""
        if self._callers is None:
            callers: collections.defaultdict[int, list[tuple[int, int]]] = collections.defaultdict(list)
            for start, end in self.functions:
                for site, target in self.calls(start, end):
                    callers[target].append((site, start))
            self._callers = callers
        return self._callers

    def insn(self, address: int) -> tuple[str, str]:
        """(mnemonic, operands) of the instruction at address; '.long' for a word capstone cannot decode."""
        offset = address - self.base
        for instruction in _disassembler.disasm(self.data[offset:offset + 4], address):
            return instruction.mnemonic, instruction.op_str
        return '.long', f'0x{self.word(address):08x}'

    def lines(self, address: int, count: int) -> Iterator[tuple[int, int, str, str]]:
        """(address, word, mnemonic, operands) of count instructions from address."""
        for k in range(count):
            at = address + 4 * k
            mnemonic, operands = self.insn(at)
            yield at, self.word(at), mnemonic, operands

    def cstring(self, address: int) -> str | None:
        """The printable ASCII NUL-terminated string at address (1 to 159 characters), else None."""
        if not self.valid(address):
            return None
        offset = address - self.base
        text = self.data[offset:self.data.find(b'\0', offset)]
        if 0 < len(text) < 160 and all(32 <= c < 127 for c in text):
            return text.decode('latin1')
        return None

    def disp_accesses(self, start: int, end: int | None = None) -> list[tuple[int, str, int, int]]:
        """D-form load/store accesses [(addr, mnemonic, base reg, displacement)]."""
        end = end or self.end_of[start]
        out: list[tuple[int, str, int, int]] = []
        for address in range(start, end, 4):
            instruction = self.word(address)
            op = instruction >> 26
            if op in D_FORM_NAMES:
                displacement = instruction & 0xffff
                if displacement & 0x8000:
                    displacement -= 0x10000
                out.append((address, D_FORM_NAMES[op], (instruction >> 16) & 31, displacement))
        return out


if __name__ == '__main__':
    image = Image(sys.argv[1])
    address = int(sys.argv[2], 16)
    count = int(sys.argv[3]) if len(sys.argv) > 3 else ((image.end_of.get(address, address + 160) - address) // 4)
    for at, instruction, mnemonic, operands in image.lines(address, count):
        print(f'{at:08x}  {instruction:08x}  {mnemonic:8s} {operands}')
