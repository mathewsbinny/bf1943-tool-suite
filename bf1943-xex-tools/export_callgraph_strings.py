"""Call graph and string export for BF1943's executable (static; no Ghidra, no execution).

  python -B export_callgraph_strings.py [--ghidra-call-refs <ghidra-call-refs.tsv>]

Run from the research workspace (or with BF1943_RESEARCH set). Reads the checked image and the tables that
map_functions.py, map_imports.py and resolve_helpers.py write; writes under work/xex-analysis/callgraph/ (these
outputs derive from EA's binary and stay in the ignored work area) and a small evidence summary to
research/evidence/xex-callgraph-strings.json (counts, hashes, paths: no strings, no code).

Method (our own decoder of the integer PowerPC subset that moves addresses; everything else is a register write
we cannot follow):
- Every .pdata function (and each register save/restore helper) is split into basic blocks; a forward data-flow
  pass tracks, per register, a constant or a symbolic value (an incoming register, a load from [symbol + offset],
  a merge point). Constants are 32-bit addresses (lis/addis + addi/ori/D-form displacement, mr copies).
- Loads from read-only sections (.rdata, .pdata, code, .edata, .XBLD; not the import slots the loader patches) fold
  to the stored word: these sections are not written at run time and the image needs no relocation
  (verify_xex_image.py), so the value is certain.
- A 4-byte store of a value to [symbol + offset] is remembered within the function until a call, an unrelated store
  that may alias, or a merge that disagrees; a later load of the same slot takes the stored value.
- Direct calls are bl/bcl targets. Indirect calls (bctrl/bcctrl/blrl) are "resolved" only when CTR/LR holds a
  constant (directly, through a read-only table word, or through a remembered store such as a vtable pointer set in
  the same function); otherwise they are "unresolved" and classified by shape: virtual (CTR = [[obj] + slot]),
  function pointer (CTR = [x + off]) or unknown. Unconditional branches leaving the function are tail calls.
- References: every constant address formed by an instruction (address, load or store) is recorded with its
  function. Data words that equal a string's address are recorded as pointer cells, and code that references such
  a cell (by its exact address) is linked to the string through it.
Strings: NUL-terminated runs in the non-code sections: ASCII/UTF-8 of at least 4 characters, UTF-16BE of at least 4
characters (Latin-script code units only), and any shorter NUL-terminated printable run that code references.
"""
from pathlib import Path
import argparse
import collections
import csv
import hashlib
import json
import struct
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402

VERSION = '1.2'   # 1.2 (2026-10-30): float stores, dcbz and VMX128 stores drop the remembered slots they may overwrite
ROOT = research_root()
WORK = ROOT / 'work/xex-analysis'
OUT = WORK / 'callgraph'
IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
BASE = 0x82000000
M32 = 0xffffffff


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tsv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


# --- inputs -----------------------------------------------------------------------------------------------------
class Image:
    def __init__(self):
        self.data = (WORK / 'default.checked.memory.bin').read_bytes()
        if hashlib.sha256(self.data).hexdigest() != IMAGE_SHA256:
            raise SystemExit('unexpected decoded image')
        self.end = BASE + len(self.data)
        self.sections = [dict(name=r['name'], start=int(r['start'], 16), size=int(r['size']), write=r['write'] == '1', execute=r['execute'] == '1')
                         for r in tsv(WORK / 'sections.tsv')]
        imports = tsv(WORK / 'imports.tsv')
        self.import_slots = {int(r['slot'], 16): r for r in imports}
        self.import_thunks = {int(r['thunk'], 16): r for r in imports if r['thunk']}
        self.word_cache = {}

    def section(self, a):
        for s in self.sections:
            if s['start'] <= a < s['start'] + s['size']:
                return s
        return None

    def readonly(self, a):
        s = self.section(a)
        return s is not None and not s['write'] and s['name'] != 'BINKBSS' and (a & ~3) not in self.import_slots

    def word(self, a):
        return struct.unpack_from('>I', self.data, a - BASE)[0]


# --- instruction decoding (the subset that moves addresses) -----------------------------------------------------
def simm16(w):
    v = w & 0xffff
    return v - 0x10000 if v & 0x8000 else v


LOGICAL31 = {28, 60, 124, 284, 316, 412, 444, 476, 24, 536, 792, 824, 27, 539, 794, 26, 58, 954, 922, 986, 413}
# stores (and dcbz / dcbz128, 1014, which zero a cache block): remembered slots are dropped
STORE31 = {151, 215, 407, 149, 662, 918, 660, 150, 214, 663, 727, 983, 231, 487, 135, 167, 199, 647, 679, 903, 935, 725, 661,
           1014}
STORE31_UPDATE = {183, 247, 439, 181, 695, 759}
NOWRITE31 = {0, 32, 4, 68, 86, 54, 278, 246, 470, 982, 598, 854, 144, 146, 178, 467, 342, 374, 822,
             535, 599, 103, 359, 7, 39, 71, 6, 38, 519, 551, 775, 807}
# VMX128 stores (opcode 4, VX128_1 under mask 0xfc0007f3: stvewx128, stvx128, stvxl128, stvlx128, stvrx128, stvlxl128,
# stvrxl128): they write memory through rA + rB, so remembered slots are dropped
VX128_STORE_MASK = 0xfc0007f3
VX128_STORES = {0x10000183, 0x100001c3, 0x100003c3, 0x10000503, 0x10000543, 0x10000703, 0x10000743}
LOAD31_UPDATE = {55, 119, 311, 375, 53, 373, 567, 631}
LOAD31_WORD = {23: 4, 87: 1, 279: 2, 343: 2, 21: 8, 341: 4, 55: 4, 119: 1, 311: 2, 375: 2, 53: 8, 373: 4}
DLOAD = {32: 4, 33: 4, 34: 1, 35: 1, 40: 2, 41: 2, 42: 2, 43: 2}
DSTORE = {36: 4, 37: 4, 38: 1, 39: 1, 44: 2, 45: 2}
FPLOADSTORE = {48, 49, 50, 51, 52, 53, 54, 55}
XO_ARITH = {266, 40, 8, 10, 138, 136, 200, 202, 232, 234, 104, 235, 233, 491, 459, 489, 457, 73, 9, 75, 11}  # XO-form: write rD only


class Function:
    __slots__ = ('start', 'end', 'name', 'label')

    def __init__(self, start, end, name, label=''):
        self.start, self.end, self.name, self.label = start, end, name, label


class Analysis:
    def __init__(self, image, functions, labels):
        self.img = image
        self.functions = functions
        self.starts = {f.start: f for f in functions}
        self.labels = labels                     # address -> label (helper entries)
        self.sorted_starts = sorted(self.starts)
        self.edges = []                          # (caller, site, callee or None, kind, detail)
        self.refs = []                           # (site, function, target, kind)
        self.vtable_stores = collections.defaultdict(set)   # vtable candidate address -> functions storing it at offset 0
        self.blocks_unreached = 0
        self.helper_targets = set()
        for f in functions:
            if f.name.startswith('__'):
                self.helper_targets.update(range(f.start, f.end, 4))
        self.source = {f.start: ('helper' if f.name.startswith('__') else 'pdata') for f in functions}

    def add_functions(self, new):
        import bisect
        for f in new:
            self.functions.append(f)
            self.starts[f.start] = f
            self.source[f.start] = f.label
        self.sorted_starts = sorted(self.starts)

    def containing(self, a):
        import bisect
        i = bisect.bisect_right(self.sorted_starts, a) - 1
        if i >= 0:
            f = self.starts[self.sorted_starts[i]]
            if f.start <= a < f.end:
                return f
        return None

    # A value is an int (32-bit constant) or a tuple (key, offset): a symbol plus a constant offset.
    def load(self, state, base, disp, size, site, fn, update_reg=None):
        if isinstance(base, int):
            a = (base + disp) & M32
            if BASE <= a < self.img.end:
                self.refs.append((site, fn.start, a, 'read'))
                mem = state['mem'].get(('abs', a))
                if mem is not None and mem[1] == size:
                    return mem[0]
                if size == 4 and a % 4 == 0 and self.img.readonly(a):
                    return self.img.word(a)
            return (('def', site, 'ld'), 0)
        key, k = base
        mem = state['mem'].get((key, k + disp))
        if mem is not None and mem[1] == size:
            return mem[0]
        depth, inner = 0, key
        while isinstance(inner, tuple) and inner[0] == 'ld':
            depth, inner = depth + 1, inner[1]
        if depth >= 4:
            return (('def', site, 'ld'), 0)      # cap symbolic depth at four nested loads
        return (('ld', key, k + disp, size), 0)

    def store(self, state, base, disp, size, value, site, fn, emit=True):
        mem = state['mem']
        if isinstance(base, int):
            a = (base + disp) & M32
            if BASE <= a < self.img.end:
                self.refs.append((site, fn.start, a, 'write'))
            slot = ('abs', a)
            key, off = 'abs', a
        else:
            key, off = base[0], base[1] + disp
            slot = (key, off)
        # Drop anything that may alias: other symbols, and overlapping slots of the same symbol.
        for s in list(mem):
            if s[0] != key or (s[1] < off + size and off < s[1] + mem[s][1]):
                del mem[s]
        if value is not None and size == 4:
            mem[slot] = (value, size)
            if emit and isinstance(value, int) and disp == 0 and not isinstance(base, int) and self.img.readonly(value) and self.img.section(value)['name'] == '.rdata':
                first = value
                if first % 4 == 0 and self.img.word(first) in self.starts:
                    self.vtable_stores[first].add(fn.start)

    def classify_indirect(self, value, via):
        """Return (callee or None, kind, detail) for an indirect call or branch through CTR/LR."""
        if isinstance(value, int):
            if value in self.starts:
                return value, 'indirect_resolved', f'{via}=constant'
            if value in self.img.import_thunks:
                return value, 'import_call', f'{via}=import thunk {self.img.import_thunks[value]["name"]}'
            return value, 'indirect_resolved_nonentry', f'{via}=constant not a function start'
        key, k = value
        if isinstance(key, tuple) and key[0] == 'ld':
            inner = key[1]
            if isinstance(inner, tuple) and inner[0] == 'ld' and inner[2] == 0:
                return None, 'indirect_virtual_unresolved', f'{via}=[[obj]+{key[2]:#x}] slot {key[2] // 4}'
            if isinstance(inner, tuple) and inner[0] == 'ld':
                return None, 'indirect_pointer_unresolved', f'{via}=[[x+{inner[2]:#x}]+{key[2]:#x}]'
            return None, 'indirect_pointer_unresolved', f'{via}=[x+{key[2]:#x}]'
        return None, 'indirect_unknown', f'{via} not tracked'

    def run_function(self, fn):
        start, end = fn.start, fn.end
        img = self.img
        words = [img.word(a) for a in range(start, end, 4)]
        n = len(words)
        leaders = {0}
        succ_of_branch = {}
        for i, w in enumerate(words):
            op = w >> 26
            pc = start + 4 * i
            if op == 18 or op == 16 or (op == 19 and ((w >> 1) & 1023) in (16, 528)):
                if i + 1 < n:
                    leaders.add(i + 1)
                if op == 18 and not w & 1:
                    t = ((w & 0x3fffffc) - (0x4000000 if w & 0x2000000 else 0) + (0 if w & 2 else pc)) & M32
                    if start <= t < end:
                        leaders.add((t - start) // 4)
                elif op == 16 and not w & 1:
                    t = ((w & 0xfffc) - (0x10000 if w & 0x8000 else 0) + (0 if w & 2 else pc)) & M32
                    if start <= t < end:
                        leaders.add((t - start) // 4)
        order = sorted(leaders)
        block_end = {b: (order[j + 1] if j + 1 < len(order) else n) for j, b in enumerate(order)}
        entry = dict(regs=[(('in', r), 0) for r in range(32)], ctr=(('in', 'ctr'), 0), lr=(('in', 'lr'), 0), mem={})
        instates = {0: entry}
        visited = set()
        work = [0]
        seeded_unreached = False
        iterations = 0
        while True:
            while work:
                b = work.pop()
                iterations += 1
                if iterations > 50 * len(order) + 1000:
                    raise RuntimeError(f'no fixpoint in {fn.name}')
                visited.add(b)
                out, succs = self.step_block(fn, words, b, block_end[b], instates[b], emit=False)
                for s in succs:
                    merged = out if s not in instates else self.merge(instates[s], out, s)
                    if s not in instates or merged != instates[s]:
                        instates[s] = merged
                        work.append(s)
            # Blocks not reached by direct flow (jump-table targets, or embedded data): seed with unknown state.
            missing = [b for b in order if b not in visited]
            if not missing:
                break
            seeded_unreached = True
            b = missing[0]
            instates[b] = dict(regs=[(('phi', start + 4 * b, r), 0) for r in range(32)], ctr=(('phi', start + 4 * b, 'ctr'), 0),
                               lr=(('phi', start + 4 * b, 'lr'), 0), mem={}, unreached=True)
            work.append(b)
        for b in order:
            self.step_block(fn, words, b, block_end[b], instates[b], emit=True)
        if seeded_unreached:
            self.blocks_unreached += sum(1 for b in order if instates[b].get('unreached'))

    def merge(self, a, b, block):
        regs = [x if x == y else (('phi', block, r), 0) for r, (x, y) in enumerate(zip(a['regs'], b['regs']))]
        mem = {k: v for k, v in a['mem'].items() if b['mem'].get(k) == v}
        out = dict(regs=regs, ctr=a['ctr'] if a['ctr'] == b['ctr'] else (('phi', block, 'ctr'), 0),
                   lr=a['lr'] if a['lr'] == b['lr'] else (('phi', block, 'lr'), 0), mem=mem)
        if a.get('unreached') and b.get('unreached'):
            out['unreached'] = True
        return out

    def step_block(self, fn, words, b, e, state, emit):
        start, end = fn.start, fn.end
        regs = list(state['regs'])
        st = dict(regs=regs, ctr=state['ctr'], lr=state['lr'], mem=dict(state['mem']))
        if state.get('unreached'):
            st['unreached'] = True
        refs_before = len(self.refs)
        vt_before = None
        succs = []
        falls = True
        for i in range(b, e):
            w = words[i]
            pc = start + 4 * i
            op = w >> 26
            rd = (w >> 21) & 31
            ra = (w >> 16) & 31
            rb = (w >> 11) & 31

            def fresh(tag='w'):
                return (('def', pc, tag), 0)

            def clobber_call(target=None):
                # Register save/restore helpers only touch r0, r11, r12 and the stack; other calls clobber the volatile set.
                helper = target is not None and target in self.helper_targets
                for r in ((0, 11, 12) if helper else (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)):
                    regs[r] = (('def', pc, r), 0)
                st['ctr'] = (('def', pc, 'ctr'), 0)
                st['lr'] = pc + 4
                st['mem'] = {}

            if op == 14 or op == 15:                                        # addi / addis (li / lis)
                imm = simm16(w) << (16 if op == 15 else 0)
                if ra == 0:
                    regs[rd] = imm & M32
                else:
                    v = regs[ra]
                    regs[rd] = (v + imm) & M32 if isinstance(v, int) else (v[0], v[1] + imm)
                if op == 14 and ra != 0 and isinstance(regs[rd], int) and BASE <= regs[rd] < self.img.end and emit:
                    self.refs.append((pc, fn.start, regs[rd], 'addr'))
            elif op in (12, 13, 7, 8):
                regs[rd] = fresh()
            elif op in (24, 25):                                            # ori / oris
                v = regs[rd]
                if isinstance(v, int):
                    regs[ra] = (v | ((w & 0xffff) << (16 if op == 25 else 0))) & M32
                    if op == 24 and (w & 0xffff) and BASE <= regs[ra] < self.img.end and emit:
                        self.refs.append((pc, fn.start, regs[ra], 'addr'))
                elif (w & 0xffff) == 0:
                    regs[ra] = v
                else:
                    regs[ra] = fresh()
            elif op in (26, 27, 28, 29, 20, 21, 23, 30):
                regs[ra] = fresh()
            elif op in DLOAD:
                size = DLOAD[op]
                base = 0 if ra == 0 else regs[ra]
                r = len(self.refs)
                v = self.load(st, base, simm16(w), size, pc, fn)
                if not emit:
                    del self.refs[r:]
                if op == 32 or op == 33:
                    regs[rd] = v
                else:
                    regs[rd] = fresh()
                if op in (33, 35, 41, 43) and ra:
                    bv = regs[ra]
                    regs[ra] = (bv + simm16(w)) & M32 if isinstance(bv, int) else (bv[0], bv[1] + simm16(w))
            elif op in DSTORE:
                size = DSTORE[op]
                base = 0 if ra == 0 else regs[ra]
                r = len(self.refs)
                self.store(st, base, simm16(w), size, regs[rd], pc, fn, emit)
                if not emit:
                    del self.refs[r:]
                if op in (37, 39, 45) and ra:
                    bv = regs[ra]
                    regs[ra] = (bv + simm16(w)) & M32 if isinstance(bv, int) else (bv[0], bv[1] + simm16(w))
            elif op == 46:                                                  # lmw
                for r in range(rd, 32):
                    regs[r] = fresh(r)
            elif op == 47:                                                  # stmw
                st['mem'] = {}
            elif op in FPLOADSTORE:
                base = 0 if ra == 0 else regs[ra]
                if op < 52:
                    if isinstance(base, int) and emit:
                        a = (base + simm16(w)) & M32
                        if BASE <= a < self.img.end:
                            self.refs.append((pc, fn.start, a, 'read'))
                else:       # a float store: no value we follow, but it overwrites (and may alias) remembered slots
                    r = len(self.refs)
                    self.store(st, base, simm16(w), 4 if op in (52, 53) else 8, None, pc, fn, emit)
                    if not emit:
                        del self.refs[r:]
                if op & 1 and ra:
                    bv = regs[ra]
                    regs[ra] = (bv + simm16(w)) & M32 if isinstance(bv, int) else (bv[0], bv[1] + simm16(w))
            elif op == 58:                                                  # ld / ldu / lwa
                ds = simm16(w) & ~3
                base = 0 if ra == 0 else regs[ra]
                if isinstance(base, int) and emit:
                    a = (base + ds) & M32
                    if BASE <= a < self.img.end:
                        self.refs.append((pc, fn.start, a, 'read'))
                regs[rd] = fresh()
                if w & 3 == 1 and ra:
                    bv = regs[ra]
                    regs[ra] = (bv + ds) & M32 if isinstance(bv, int) else (bv[0], bv[1] + ds)
            elif op == 62:                                                  # std / stdu
                ds = simm16(w) & ~3
                base = 0 if ra == 0 else regs[ra]
                r = len(self.refs)
                self.store(st, base, ds, 8, None, pc, fn)
                if not emit:
                    del self.refs[r:]
                if w & 3 == 1 and ra:
                    bv = regs[ra]
                    regs[ra] = (bv + ds) & M32 if isinstance(bv, int) else (bv[0], bv[1] + ds)
            elif op == 31:
                xo = (w >> 1) & 1023
                if xo == 444 and rd == rb:                                  # mr
                    regs[ra] = regs[rd]
                elif xo == 467:                                             # mtspr
                    spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
                    if spr == 9:
                        st['ctr'] = regs[rd]
                    elif spr == 8:
                        st['lr'] = regs[rd]
                elif xo == 339:                                             # mfspr
                    spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
                    regs[rd] = st['ctr'] if spr == 9 else st['lr'] if spr == 8 else fresh()
                elif xo in LOGICAL31 or (xo >> 1) == 413:                  # and/or/xor/shifts/extends... write rA
                    regs[ra] = fresh()
                elif xo in STORE31:
                    st['mem'] = {}
                elif xo in STORE31_UPDATE:
                    st['mem'] = {}
                    regs[ra] = fresh('u')
                elif xo in NOWRITE31:
                    pass
                elif xo in LOAD31_UPDATE:
                    regs[rd] = fresh()
                    regs[ra] = fresh('u')
                elif xo in LOAD31_WORD or (xo & 511) in XO_ARITH or xo in (19, 83, 371, 20, 84):
                    regs[rd] = fresh()
                else:                                                       # anything else: forget both fields
                    regs[rd] = fresh()
                    regs[ra] = fresh('a')
            elif op == 18:                                                  # b / bl
                t = ((w & 0x3fffffc) - (0x4000000 if w & 0x2000000 else 0) + (0 if w & 2 else pc)) & M32
                if w & 1:
                    if emit:
                        self.edges.append(self.direct(fn, pc, t, 'direct_call'))
                    clobber_call(t)
                else:
                    falls = False
                    if start <= t < end:
                        succs.append((t - start) // 4)
                    elif emit:
                        self.edges.append(self.direct(fn, pc, t, 'tail_call'))
                    break
            elif op == 16:                                                  # bc / bcl
                bo = (w >> 21) & 31
                t = ((w & 0xfffc) - (0x10000 if w & 0x8000 else 0) + (0 if w & 2 else pc)) & M32
                always = (bo & 0x14) == 0x14
                if w & 1:
                    if emit:
                        self.edges.append(self.direct(fn, pc, t, 'direct_call' if always else 'conditional_call'))
                    clobber_call(t)
                else:
                    if start <= t < end:
                        succs.append((t - start) // 4)
                    elif emit:
                        self.edges.append(self.direct(fn, pc, t, 'tail_call' if always else 'conditional_tail_call'))
                    if always:
                        falls = False
                        break
            elif op == 19:
                xo = (w >> 1) & 1023
                bo = (w >> 21) & 31
                always = (bo & 0x14) == 0x14
                if xo == 16:                                                # bclr
                    if w & 1:
                        if emit:
                            callee, kind, detail = self.classify_indirect(st['lr'], 'LR')
                            self.edges.append((fn.start, pc, callee, kind if always else 'conditional_' + kind, detail))
                        clobber_call()
                    elif always:
                        falls = False
                        break
                elif xo == 528:                                             # bcctr
                    if w & 1:
                        if emit:
                            callee, kind, detail = self.classify_indirect(st['ctr'], 'CTR')
                            self.edges.append((fn.start, pc, callee, kind if always else 'conditional_' + kind, detail))
                        clobber_call()
                    else:
                        if emit:
                            callee, kind, detail = self.classify_indirect(st['ctr'], 'CTR')
                            branch_kind = {'indirect_resolved': 'indirect_tail_call_resolved', 'import_call': 'import_tail_call'}.get(kind)
                            if branch_kind:
                                self.edges.append((fn.start, pc, callee, branch_kind, detail))
                            elif isinstance(st['ctr'], int) and start <= st['ctr'] < end:
                                pass
                            else:
                                self.edges.append((fn.start, pc, callee, 'indirect_branch_unresolved', detail + ' (jump table or tail call)'))
                        if always:
                            falls = False
                            break
            elif op == 4 and (w & VX128_STORE_MASK) in VX128_STORES:      # a VMX128 store: memory changed
                st['mem'] = {}
            # opcodes 4/5/6 (vector), 59/63 (FP), 17 (sc), 2/3 (traps), 10/11 (compares) write no GPR we follow.
        if falls and e < len(words):
            succs.append(e)
        if not emit:
            pass
        return st, succs

    def direct(self, fn, site, target, kind):
        if target in self.img.import_thunks:
            return (fn.start, site, target, 'import_call' if kind in ('direct_call', 'conditional_call') else 'import_tail_call', f'import {self.img.import_thunks[target]["library"]}:{self.img.import_thunks[target]["name"]}')
        if target in self.labels:
            return (fn.start, site, target, 'helper_call' if kind in ('direct_call', 'conditional_call') else 'helper_' + kind, self.labels[target])
        if target in self.starts:
            return (fn.start, site, target, kind, '')
        f = self.containing(target)
        return (fn.start, site, target, kind + '_nonentry', f'inside {f.name}+{target - f.start:#x}' if f else 'outside every function')


# --- strings ----------------------------------------------------------------------------------------------------
PRINTABLE = set(range(0x20, 0x7f)) | {9, 10, 13}


def latin_unit(u):
    return 0x20 <= u <= 0x7e or u in (9, 10, 13) or 0xa0 <= u <= 0x17f or 0x2010 <= u <= 0x2027 or u in (0x20ac, 0x2122)


def scan_strings(img):
    strings = {}
    data = img.data
    for s in img.sections:
        if s['execute']:
            continue
        lo, hi = s['start'] - BASE, s['start'] - BASE + s['size']
        i = lo
        while i < hi:                                   # ASCII / UTF-8 runs ending in NUL
            if data[i] in PRINTABLE or data[i] >= 0xc2:
                j = i
                while j < hi and (data[j] in PRINTABLE or data[j] >= 0x80):
                    j += 1
                if j < hi and data[j] == 0 and j - i >= 4:
                    k = i
                    try:
                        text = data[i:j].decode('utf-8')
                    except UnicodeDecodeError:
                        text = None
                    if text is None or not all((c.isprintable() or c in '\t\n\r') and (ord(c) < 0x80 or latin_unit(ord(c))) for c in text):
                        # Binary bytes that happen to precede (or form UTF-8 before) a string: keep the ASCII tail only.
                        k = max(x for x in range(i, j) if data[x] >= 0x80) + 1
                        text = data[k:j].decode('ascii')
                    if len(text) >= 4:
                        strings[BASE + k] = dict(address=BASE + k, section=s['name'], encoding='ascii' if max(data[k:j]) < 0x80 else 'utf-8', text=text, bytes=j - k + 1)
                i = j + 1
            else:
                i += 1
        i = lo + (lo & 1)
        while i + 1 < hi:                               # UTF-16BE runs (2-aligned) ending in a zero unit
            u = (data[i] << 8) | data[i + 1]
            if latin_unit(u) and u >= 0x20:
                j = i
                units = []
                while j + 1 < hi:
                    v = (data[j] << 8) | data[j + 1]
                    if not latin_unit(v):
                        break
                    units.append(v)
                    j += 2
                if j + 1 < hi and data[j] == 0 and data[j + 1] == 0 and len(units) >= 4 and sum(chr(x).isalnum() or x == 0x20 for x in units) >= 0.6 * len(units):
                    if BASE + i not in strings:
                        text = ''.join(map(chr, units))
                        strings[BASE + i] = dict(address=BASE + i, section=s['name'], encoding='utf-16be', text=text, bytes=j - i + 2)
                i = j + 2
            else:
                i += 2
    return strings


def short_string_at(img, a):
    """A NUL-terminated printable ASCII run of 1-3 characters at a (for code-referenced short strings)."""
    off = a - BASE
    if not 0 <= off < len(img.data):
        return None
    s = img.section(a)
    if s is None or s['execute']:
        return None
    j = off
    while j < len(img.data) and j - off < 4 and img.data[j] in PRINTABLE:
        j += 1
    if j < len(img.data) and img.data[j] == 0 and 1 <= j - off <= 3 and (off == 0 or img.data[off - 1] == 0 or (a % 4 == 0)):
        return dict(address=a, section=s['name'], encoding='ascii', text=img.data[off:j].decode(), bytes=j - off + 1, short=True)
    return None


def esc(text):
    return json.dumps(text, ensure_ascii=False)[1:-1]


def write_tsv(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write('\t'.join(header) + '\n')
        for r in rows:
            f.write('\t'.join('' if x is None else str(x) for x in r) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ghidra-call-refs', type=Path, help='ghidra-call-refs.tsv from export_all_decompiled.java, for a cross-check of direct calls')
    args = ap.parse_args()
    t0 = time.time()
    img = Image()
    names = {int(r['address'], 16): (r['name'], r['file']) for r in tsv(WORK / 'decompiled-index.tsv')}
    functions = []
    for r in tsv(WORK / 'functions.tsv'):
        s, e = int(r['start'], 16), int(r['end'], 16)
        functions.append(Function(s, e, names.get(s, (f'pdata_{s:08x}', ''))[0]))
    labels = {int(r['address'], 16): r['name'] for r in tsv(WORK / 'helper-entries.tsv')}
    an = Analysis(img, functions, labels)
    pdata_count = len(functions)
    pending = list(functions)
    rounds = []
    code_sections = [s for s in img.sections if s['execute']]
    while pending:
        for f in pending:
            an.run_function(f)
        if not rounds:
            # The graph before any inferred function exists: what the inferred entries change.
            pdata_only = dict(call_site_kinds=dict(sorted(collections.Counter(k for *_, k, _ in an.edges).items())),
                              graph_edges_function_to_function=len({(c, t if t in an.starts else an.containing(t).start)
                                                                    for c, s, t, k, d in an.edges if t is not None and 'branch_unresolved' not in k
                                                                    and (t in an.starts or an.containing(t) is not None)}),
                              nonentry_call_sites_outside_every_function=sum(1 for *_, k, d in an.edges if k.endswith('_nonentry') and d == 'outside every function'))
        # Entry points outside every known function: direct/tail call targets, then 4-aligned data words into code
        # gaps. Leaf functions need no .pdata record, so the exception table misses them.
        candidates = {}
        for c, s, t, k, d in an.edges:
            if t is not None and k.endswith('_nonentry') and d == 'outside every function' and any(cs['start'] <= t < cs['start'] + cs['size'] for cs in code_sections):
                candidates.setdefault(t, 'inferred_call_target')
        if not rounds:
            for s in img.sections:
                if s['execute']:
                    continue
                lo = s['start'] - BASE
                for off in range(lo + (-lo % 4), lo + s['size'] - 3, 4):
                    v = struct.unpack_from('>I', img.data, off)[0]
                    if v % 4 == 0 and v not in an.starts and any(cs['start'] <= v < cs['start'] + cs['size'] for cs in code_sections) and an.containing(v) is None and v not in img.import_thunks:
                        candidates.setdefault(v, 'inferred_data_pointer')
        candidates = {a: why for a, why in candidates.items() if a not in an.starts and an.containing(a) is None}
        if not candidates:
            break
        # Extent: up to the next known or candidate entry, the end of the code gap, or the first zero word (padding).
        known = sorted(set(an.starts) | set(candidates))
        import bisect
        new = []
        for a in sorted(candidates):
            nxt = known[bisect.bisect_right(known, a)] if bisect.bisect_right(known, a) < len(known) else None
            sec = next(cs for cs in code_sections if cs['start'] <= a < cs['start'] + cs['size'])
            end = min(x for x in (nxt, sec['start'] + sec['size']) if x is not None)
            e = a
            while e < end and img.word(e) != 0:
                e += 4
            if e > a:
                new.append(Function(a, e, f'leaf_{a:08x}', candidates[a]))
        # Edges found so far are recomputed for every function once the set is final.
        rounds.append(dict(new_functions=len(new), from_calls=sum(1 for f in new if f.label == 'inferred_call_target'), from_data=sum(1 for f in new if f.label == 'inferred_data_pointer')))
        an.add_functions(new)
        an.edges, an.refs, an.vtable_stores, an.blocks_unreached = [], [], collections.defaultdict(set), 0
        pending = list(an.functions)
        if len(rounds) > 8:
            raise SystemExit('function discovery did not settle')
    functions = sorted(an.functions, key=lambda f: f.start)
    t1 = time.time()

    # Strings and their references.
    strings = scan_strings(img)
    code_targets = collections.defaultdict(list)
    for site, fs, target, kind in an.refs:
        code_targets[target].append((site, fs, kind))
    for a in list(code_targets):
        if a not in strings:
            s = short_string_at(img, a)
            if s:
                strings[a] = s
    starts_sorted = sorted(strings)
    import bisect

    def string_containing(a):
        i = bisect.bisect_right(starts_sorted, a) - 1
        if i >= 0:
            s = strings[starts_sorted[i]]
            if s['address'] <= a < s['address'] + s['bytes'] - 1:
                return s
        return None

    pointer_cells = collections.defaultdict(list)       # string address -> data cells holding it
    code_pointer_cells = collections.defaultdict(list)  # function start -> data cells holding it
    data = img.data
    for s in img.sections:
        if s['execute']:
            continue
        lo = s['start'] - BASE
        for off in range(lo + (-lo % 4), lo + s['size'] - 3, 4):
            v = struct.unpack_from('>I', data, off)[0]
            if v in strings:
                pointer_cells[v].append(BASE + off)
            elif v in an.starts:
                code_pointer_cells[v].append(BASE + off)
    string_refs = []
    for target, uses in code_targets.items():
        s = strings.get(target) or string_containing(target)
        if s:
            for site, fs, kind in uses:
                string_refs.append((s['address'], site, fs, f'code_{kind}', '' if s['address'] == target else f'interior+{target - s["address"]}'))
    for sa, cells in pointer_cells.items():
        for cell in cells:
            string_refs.append((sa, cell, an.containing(cell).start if an.containing(cell) else '', 'data_pointer', ''))
            for site, fs, kind in code_targets.get(cell, []):
                string_refs.append((sa, site, fs, f'code_{kind}_via_pointer', f'cell {cell:08x}'))
    string_refs.sort(key=lambda r: (r[0], r[1]))
    refs_by_string = collections.defaultdict(set)
    for sa, site, fs, kind, _ in string_refs:
        if fs != '':
            refs_by_string[sa].add(fs)

    # Call graph tables.
    callees = collections.defaultdict(set)
    callers = collections.defaultdict(set)
    kinds_by_fn = collections.defaultdict(collections.Counter)
    for caller, site, callee, kind, detail in an.edges:
        kinds_by_fn[caller][kind] += 1
        if callee is not None:
            c = callee if callee in an.starts else (an.containing(callee).start if an.containing(callee) else None)
            if c is not None and 'branch_unresolved' not in kind:
                callees[caller].add(c)
                callers[c].add(caller)
    OUT.mkdir(parents=True, exist_ok=True)

    def fname(a):
        if a in an.starts:
            return an.starts[a].name
        if a in img.import_thunks:
            return img.import_thunks[a]['name']
        return labels.get(a, '')

    write_tsv(OUT / 'callgraph-edges.tsv', ['caller', 'caller_name', 'site', 'callee', 'callee_name', 'kind', 'detail'],
              [(f'{c:08x}', fname(c), f'{s:08x}', '' if t is None else f'{t:08x}', '' if t is None else fname(t), k, d) for c, s, t, k, d in an.edges])
    unresolved = lambda k: 'unresolved' in k or k == 'indirect_unknown' or k.endswith('_indirect_unknown')
    rows = []
    for f in functions:
        kc = kinds_by_fn[f.start]
        direct = sum(v for k, v in kc.items() if k in ('direct_call', 'conditional_call', 'tail_call', 'conditional_tail_call', 'helper_call', 'import_call', 'import_tail_call') or k.endswith('_nonentry'))
        resolved = sum(v for k, v in kc.items() if 'resolved' in k and 'unresolved' not in k)
        unres = sum(v for k, v in kc.items() if unresolved(k) and 'branch' not in k)
        rows.append((f'{f.start:08x}', f'{f.end:08x}', f.name, an.source[f.start], names.get(f.start, ('', ''))[1], len(callers[f.start]), len(callees[f.start]), direct, resolved, unres,
                     kc.get('indirect_branch_unresolved', 0), ','.join(f'{c:08x}' for c in sorted(callers[f.start])), ','.join(f'{c:08x}' for c in sorted(callees[f.start]))))
    write_tsv(OUT / 'callgraph-functions.tsv', ['address', 'end', 'name', 'source', 'decompiled_file', 'callers', 'callees', 'direct_call_sites', 'indirect_resolved_sites',
                                                 'indirect_unresolved_sites', 'indirect_branches_unresolved', 'caller_list', 'callee_list'], rows)
    write_tsv(OUT / 'address-refs.tsv', ['site', 'function', 'target', 'kind', 'section'],
              [(f'{s:08x}', f'{fs:08x}', f'{t:08x}', k, (img.section(t) or {}).get('name', 'gap')) for s, fs, t, k in sorted(an.refs)])
    write_tsv(OUT / 'strings.tsv', ['address', 'section', 'encoding', 'chars', 'bytes', 'referencing_functions', 'pointer_cells', 'text'],
              [(f'{a:08x}', s['section'], s['encoding'], len(s['text']), s['bytes'], len(refs_by_string.get(a, ())), len(pointer_cells.get(a, ())), esc(s['text']))
               for a, s in sorted(strings.items())])
    write_tsv(OUT / 'string-refs.tsv', ['string', 'from', 'function', 'kind', 'detail'],
              [(f'{sa:08x}', f'{site:08x}', '' if fs == '' else f'{fs:08x}', k, d) for sa, site, fs, k, d in string_refs])
    vt = []
    for a in sorted(an.vtable_stores):
        n = 0
        while img.readonly(a + 4 * n) and img.word(a + 4 * n) in an.starts:
            n += 1
        vt.append((f'{a:08x}', n, ','.join(f'{x:08x}' for x in sorted(an.vtable_stores[a]))))
    write_tsv(OUT / 'vtable-candidates.tsv', ['address', 'leading_function_pointers', 'stored_at_offset_0_by'], vt)
    write_tsv(OUT / 'inferred-functions.tsv', ['start', 'end', 'name', 'source'],
              [(f'{f.start:08x}', f'{f.end:08x}', f.name, f.label) for f in functions if f.label])
    write_tsv(OUT / 'data-pointers-to-functions.tsv', ['cell', 'section', 'function'],
              sorted((f'{c:08x}', img.section(c)['name'], f'{t:08x}') for t, cells in code_pointer_cells.items() for c in cells))

    kinds = collections.Counter(k for *_, k, _ in an.edges)
    enc = collections.Counter(s['encoding'] for s in strings.values())
    summary = dict(
        tool='bf1943-xex-tools/export_callgraph_strings.py', version=VERSION, tool_sha256=sha256_file(__file__),
        python=sys.version.split()[0], seconds=round(time.time() - t0, 1), seconds_dataflow=round(t1 - t0, 1),
        inputs={p: sha256_file(WORK / p) for p in ['default.checked.memory.bin', 'functions.tsv', 'sections.tsv', 'imports.tsv', 'helper-entries.tsv', 'decompiled-index.tsv']},
        functions=len(functions), functions_pdata_and_helpers=pdata_count, functions_inferred=len(functions) - pdata_count,
        functions_inferred_from_calls=sum(1 for f in functions if f.label == 'inferred_call_target'),
        functions_inferred_from_data_pointers=sum(1 for f in functions if f.label == 'inferred_data_pointer'),
        inferred_code_bytes=sum(f.end - f.start for f in functions if f.label), discovery_rounds=rounds, pdata_only_graph=pdata_only,
        basic_blocks_seeded_unreached=an.blocks_unreached,
        call_sites=len(an.edges), call_site_kinds=dict(sorted(kinds.items())),
        functions_with_callers=sum(1 for f in functions if callers[f.start]), functions_with_callees=sum(1 for f in functions if callees[f.start]),
        graph_edges_function_to_function=sum(len(v) for v in callees.values()),
        address_references=len(an.refs), strings=len(strings), string_encodings=dict(sorted(enc.items())),
        strings_short_referenced=sum(1 for s in strings.values() if s.get('short')),
        strings_referenced_by_code=sum(1 for a in strings if refs_by_string.get(a)), string_reference_rows=len(string_refs),
        string_pointer_cells=sum(len(v) for v in pointer_cells.values()), vtable_candidates=len(vt), data_pointers_to_functions=sum(len(v) for v in code_pointer_cells.values()))
    if args.ghidra_call_refs:
        g = tsv(args.ghidra_call_refs)
        ghidra = {(int(r['instruction'], 16), int(r['target'], 16)) for r in g if r['kind'] in ('call', 'conditional_call') and r['target']}
        ours = {(s, t) for c, s, t, k, d in an.edges if t is not None and (k in ('direct_call', 'conditional_call', 'helper_call', 'import_call') or k.startswith('direct_call_') or k.startswith('conditional_call_'))}
        summary['ghidra_cross_check'] = dict(file=str(args.ghidra_call_refs), file_sha256=sha256_file(args.ghidra_call_refs),
                                             ghidra_direct_call_refs=len(ghidra), ours_direct_calls=len(ours), both=len(ghidra & ours),
                                             only_ghidra=len(ghidra - ours), only_ours=len(ours - ghidra),
                                             only_ghidra_examples=[f'{s:08x}->{t:08x}' for s, t in sorted(ghidra - ours)[:10]],
                                             only_ours_examples=[f'{s:08x}->{t:08x}' for s, t in sorted(ours - ghidra)[:10]],
                                             ghidra_computed_calls=sum(1 for r in g if r['kind'] == 'computed_call'))
    outputs = sorted(p.name for p in OUT.iterdir() if p.suffix == '.tsv')
    summary['outputs'] = {f'work/xex-analysis/callgraph/{p}': sha256_file(OUT / p) for p in outputs}
    (OUT / 'summary.json').write_bytes((json.dumps(summary, indent=2) + '\n').encode())
    # Commit-able evidence: counts, hashes and paths only (the outputs themselves derive from EA's binary).
    evidence = dict(summary, note='Outputs are under the ignored work area; they derive from the game binary and are not committed.',
                    limits=['Static: constants are followed within one function (no interprocedural values); loads fold only from read-only sections',
                            'Indirect calls are resolved only when CTR/LR is a certain constant; virtual calls are classified with their vtable slot, not resolved',
                            'Inferred leaf functions (no .pdata record) end at the next known entry or the first zero word; their extents are not proven',
                            'Strings: ASCII/UTF-8 and Latin-script UTF-16BE only; references are exact-address code references and one level of data pointer cells'])
    (ROOT / 'research/evidence/xex-callgraph-strings.json').write_bytes((json.dumps(evidence, indent=2) + '\n').encode())
    print(json.dumps({k: v for k, v in summary.items() if k not in ('inputs', 'outputs')}, indent=1))
    return summary


if __name__ == '__main__':
    main()
