"""Two-image helpers (BF1943 checked image and BFBC2 Xbox 360 image): .pdata functions, normalized fingerprints,
direct call edges, capstone disassembly. Read only; research reading only (capstone has no VMX128; those print .long).

  python -B <suite>/bf1943-xex-tools/bfbc2dis.py {bf1943|bfbc2} <hex address> [count]
"""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import bisect, collections, hashlib, struct
import capstone

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
IMAGES = {'bf1943': (ROOT / 'work/xex-analysis/default.checked.memory.bin',
                     'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'),
          'bfbc2': (ROOT / 'work/bfbc2-xenon/game.memory.bin',
                    '094e53261d2ffdf2f0c7069b7d6762d46a5bb93ee09b01c1189ffae2dc28c4ae')}
_md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)


def normalize(w):
    """Same normalization as compare_bfbc2_code.py: primary + extended opcode, operands masked."""
    op = w >> 26
    if op in (19, 31, 63): return (op, (w >> 1) & 0x3ff)
    if op == 59: return (op, (w >> 1) & 0x1f)
    if op == 30: return (op, (w >> 1) & 0xf)
    if op in (58, 62): return (op, w & 3)
    if op in (4, 5, 6): return (op, w & 0x7f3)
    if op in (16, 18): return (op, w & 3)
    return (op,)


class Image:
    def __init__(self, name):
        path, expected = IMAGES[name]
        self.name = name
        self.path = path
        self.data = path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        assert self.sha256 == expected, path
        pe = struct.unpack_from('<I', self.data, 60)[0]
        self.base = struct.unpack_from('<I', self.data, pe + 24 + 28)[0]
        rva, size = struct.unpack_from('<II', self.data, pe + 24 + 96 + 3 * 8)
        fs = []
        for off in range(rva, rva + size, 8):
            start, packed = struct.unpack_from('>II', self.data, off)
            length = ((packed >> 8) & 0x3fffff) * 4
            if length and start % 4 == 0 and self.base <= start and start + length <= self.base + len(self.data):
                fs.append((start, start + length))
        self.functions = sorted(set(fs))
        self.starts = [s for s, _ in self.functions]
        self.end_of = dict(self.functions)
        self._fp = None
        self._callers = None

    def word(self, a):
        return struct.unpack_from('>I', self.data, a - self.base)[0]

    def valid(self, a):
        return self.base <= a < self.base + len(self.data)

    def containing(self, a):
        i = bisect.bisect_right(self.starts, a) - 1
        return self.functions[i] if i >= 0 and a < self.functions[i][1] else None

    def norm_seq(self, s, e=None):
        e = e or self.end_of[s]
        return [normalize(self.word(a)) for a in range(s, e, 4)]

    def fingerprint(self, s, e=None):
        return hashlib.sha1(repr(self.norm_seq(s, e)).encode()).hexdigest()

    def fingerprints(self):
        if self._fp is None:
            self._fp = {s: self.fingerprint(s, e) for s, e in self.functions}
        return self._fp

    def calls(self, s, e=None):
        """Direct bl targets in order, [(site, target)]."""
        e = e or self.end_of[s]
        out = []
        for a in range(s, e, 4):
            w = self.word(a)
            if w >> 26 == 18 and w & 1:
                li = w & 0x3fffffc
                if li & 0x2000000: li -= 0x4000000
                out.append((a, (li if w & 2 else a + li) & 0xffffffff))
        return out

    def callers(self):
        if self._callers is None:
            c = collections.defaultdict(list)
            for s, e in self.functions:
                for site, t in self.calls(s, e):
                    c[t].append((site, s))
            self._callers = c
        return self._callers

    def insn(self, a):
        for i in _md.disasm(self.data[a - self.base:a - self.base + 4], a):
            return i.mnemonic, i.op_str
        return '.long', f'0x{self.word(a):08x}'

    def lines(self, a, n):
        for k in range(n):
            m, o = self.insn(a + 4 * k)
            yield a + 4 * k, self.word(a + 4 * k), m, o

    def cstring(self, a):
        if not self.valid(a): return None
        o = a - self.base
        s = self.data[o:self.data.find(b'\0', o)]
        return s.decode('latin1') if 0 < len(s) < 160 and all(32 <= c < 127 for c in s) else None

    def disp_accesses(self, s, e=None):
        """D-form load/store accesses [(addr, mnemonic, base reg, displacement)]."""
        e = e or self.end_of[s]
        names = {32: 'lwz', 34: 'lbz', 36: 'stw', 38: 'stb', 40: 'lhz', 44: 'sth', 48: 'lfs', 52: 'stfs', 50: 'lfd', 54: 'stfd'}
        out = []
        for a in range(s, e, 4):
            w = self.word(a); op = w >> 26
            if op in names:
                d = w & 0xffff
                out.append((a, names[op], (w >> 16) & 31, d - 0x10000 if d & 0x8000 else d))
        return out


if __name__ == '__main__':
    img = Image(sys.argv[1])
    a = int(sys.argv[2], 16)
    n = int(sys.argv[3]) if len(sys.argv) > 3 else ((img.end_of.get(a, a + 160) - a) // 4)
    for at, w, m, o in img.lines(a, n):
        print(f'{at:08x}  {w:08x}  {m:8s} {o}')
