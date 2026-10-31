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
import bisect
import collections
import csv
import hashlib
import json
import struct
import sys
import time

sys.dont_write_bytecode = True
from workspace import research_root  # noqa: E402

VERSION = '1.2'   # 1.2 (2026-10-30): float stores, dcbz and VMX128 stores drop the remembered slots they may overwrite
IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
BASE = 0x82000000
M32 = 0xffffffff


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tsv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


# --- inputs -----------------------------------------------------------------------------------------------------
class Image:
    """The checked image and the section and import tables, from the workspace's work/xex-analysis folder."""

    def __init__(self, work):
        self.data = (work / 'default.checked.memory.bin').read_bytes()
        if hashlib.sha256(self.data).hexdigest() != IMAGE_SHA256:
            raise SystemExit('unexpected decoded image')
        self.end = BASE + len(self.data)
        self.sections = [dict(name=row['name'], start=int(row['start'], 16), size=int(row['size']),
                              write=row['write'] == '1', execute=row['execute'] == '1')
                         for row in tsv(work / 'sections.tsv')]
        imports = tsv(work / 'imports.tsv')
        self.import_slots = {int(row['slot'], 16): row for row in imports}
        self.import_thunks = {int(row['thunk'], 16): row for row in imports if row['thunk']}
        self.word_cache = {}

    def section(self, address):
        for section in self.sections:
            if section['start'] <= address < section['start'] + section['size']:
                return section
        return None

    def readonly(self, address):
        section = self.section(address)
        return (section is not None and not section['write'] and section['name'] != 'BINKBSS'
                and (address & ~3) not in self.import_slots)

    def word(self, address):
        return struct.unpack_from('>I', self.data, address - BASE)[0]


# --- instruction decoding (the subset that moves addresses) -----------------------------------------------------
def simm16(instruction):
    value = instruction & 0xffff
    return value - 0x10000 if value & 0x8000 else value


def branch_target(instruction, pc):
    """The target of an I-form branch (b, bl, ba, bla) at pc."""
    displacement = (instruction & 0x3fffffc) - (0x4000000 if instruction & 0x2000000 else 0)
    return (displacement + (0 if instruction & 2 else pc)) & M32


def conditional_target(instruction, pc):
    """The target of a B-form branch (bc, bcl, bca, bcla) at pc."""
    displacement = (instruction & 0xfffc) - (0x10000 if instruction & 0x8000 else 0)
    return (displacement + (0 if instruction & 2 else pc)) & M32


def offset_value(value, displacement):
    """A value plus a constant: a 32-bit constant stays one, a symbol keeps its key and moves its offset."""
    if isinstance(value, int):
        return (value + displacement) & M32
    return (value[0], value[1] + displacement)


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
WRITES_RD31 = (19, 83, 371, 20, 84)                 # mfcr, mfmsr, mftb, lwarx, ldarx: write rD only
DLOAD = {32: 4, 33: 4, 34: 1, 35: 1, 40: 2, 41: 2, 42: 2, 43: 2}
DLOAD_UPDATE = (33, 35, 41, 43)
DSTORE = {36: 4, 37: 4, 38: 1, 39: 1, 44: 2, 45: 2}
DSTORE_UPDATE = (37, 39, 45)
FPLOADSTORE = {48, 49, 50, 51, 52, 53, 54, 55}
XO_ARITH = {266, 40, 8, 10, 138, 136, 200, 202, 232, 234, 104, 235, 233, 491, 459, 489, 457, 73, 9, 75, 11}  # XO-form: write rD only
SPR_LR, SPR_CTR = 8, 9
VOLATILE_GPRS = (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
HELPER_CLOBBERS = (0, 11, 12)
CALL_KINDS = ('direct_call', 'conditional_call')


def spr_field(instruction):
    """The SPR number of mtspr / mfspr (its two 5-bit halves are swapped in the instruction)."""
    return ((instruction >> 16) & 31) | (((instruction >> 11) & 31) << 5)


def always_taken(bo):
    return (bo & 0x14) == 0x14


class Function:
    __slots__ = ('start', 'end', 'name', 'label')

    def __init__(self, start, end, name, label=''):
        self.start, self.end, self.name, self.label = start, end, name, label


class Analysis:
    def __init__(self, image, functions, labels):
        self.image = image
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

    def reset_results(self):
        """Forget the edges, references, vtable stores and unreached-block count, for a rerun over a new function set."""
        self.edges, self.refs, self.vtable_stores, self.blocks_unreached = [], [], collections.defaultdict(set), 0

    def add_functions(self, new):
        for f in new:
            self.functions.append(f)
            self.starts[f.start] = f
            self.source[f.start] = f.label
        self.sorted_starts = sorted(self.starts)

    def containing(self, address):
        i = bisect.bisect_right(self.sorted_starts, address) - 1
        if i >= 0:
            function = self.starts[self.sorted_starts[i]]
            if function.start <= address < function.end:
                return function
        return None

    def note_reference(self, site, function, address, kind):
        """Record a reference from site (in function) to address when address is in the image."""
        if BASE <= address < self.image.end:
            self.refs.append((site, function.start, address, kind))

    # A value is an int (32-bit constant) or a tuple (key, offset): a symbol plus a constant offset.
    def load(self, state, base, displacement, size, site, function, emit=True):
        """The value a load of size bytes from base + displacement gives; with emit, a constant address is also
        recorded as a reference."""
        if isinstance(base, int):
            address = (base + displacement) & M32
            if BASE <= address < self.image.end:
                if emit:
                    self.refs.append((site, function.start, address, 'read'))
                remembered = state['mem'].get(('abs', address))
                if remembered is not None and remembered[1] == size:
                    return remembered[0]
                if size == 4 and address % 4 == 0 and self.image.readonly(address):
                    return self.image.word(address)
            return (('def', site, 'ld'), 0)
        key, offset = base
        remembered = state['mem'].get((key, offset + displacement))
        if remembered is not None and remembered[1] == size:
            return remembered[0]
        depth, inner = 0, key
        while isinstance(inner, tuple) and inner[0] == 'ld':
            depth, inner = depth + 1, inner[1]
        if depth >= 4:
            return (('def', site, 'ld'), 0)      # cap symbolic depth at four nested loads
        return (('ld', key, offset + displacement, size), 0)

    def store(self, state, base, displacement, size, value, site, function, emit=True):
        """Forget the remembered slots a store may overwrite, then remember a 4-byte value (None: a store whose value
        we do not follow). With emit, a constant address is recorded as a reference and a vtable-like store as a
        candidate."""
        memory = state['mem']
        if isinstance(base, int):
            address = (base + displacement) & M32
            if emit and BASE <= address < self.image.end:
                self.refs.append((site, function.start, address, 'write'))
            slot = ('abs', address)
            key, offset = 'abs', address
        else:
            key, offset = base[0], base[1] + displacement
            slot = (key, offset)
        # Drop anything that may alias: other symbols, and overlapping slots of the same symbol.
        for other in list(memory):
            other_key, other_offset = other
            if other_key != key or (other_offset < offset + size and offset < other_offset + memory[other][1]):
                del memory[other]
        if value is not None and size == 4:
            memory[slot] = (value, size)
            # A constant .rdata address stored at offset 0 of an object whose first word is a function: a vtable.
            stored_at_object_start = displacement == 0 and not isinstance(base, int)
            if (emit and isinstance(value, int) and stored_at_object_start and self.image.readonly(value)
                    and self.image.section(value)['name'] == '.rdata'):
                if value % 4 == 0 and self.image.word(value) in self.starts:
                    self.vtable_stores[value].add(function.start)

    def classify_indirect(self, value, via):
        """Return (callee or None, kind, detail) for an indirect call or branch through CTR/LR."""
        if isinstance(value, int):
            if value in self.starts:
                return value, 'indirect_resolved', f'{via}=constant'
            if value in self.image.import_thunks:
                return value, 'import_call', f'{via}=import thunk {self.image.import_thunks[value]["name"]}'
            return value, 'indirect_resolved_nonentry', f'{via}=constant not a function start'
        key, _ = value
        if isinstance(key, tuple) and key[0] == 'ld':
            _, inner, offset, _ = key                   # a load from [inner + offset]
            if isinstance(inner, tuple) and inner[0] == 'ld' and inner[2] == 0:
                return None, 'indirect_virtual_unresolved', f'{via}=[[obj]+{offset:#x}] slot {offset // 4}'
            if isinstance(inner, tuple) and inner[0] == 'ld':
                return None, 'indirect_pointer_unresolved', f'{via}=[[x+{inner[2]:#x}]+{offset:#x}]'
            return None, 'indirect_pointer_unresolved', f'{via}=[x+{offset:#x}]'
        return None, 'indirect_unknown', f'{via} not tracked'

    def block_leaders(self, function, words):
        """The indices of the words that start a basic block: the entry, each word after a branch and each branch
        target inside the function."""
        start, end = function.start, function.end
        leaders = {0}
        for i, instruction in enumerate(words):
            op = instruction >> 26
            pc = start + 4 * i
            xo = (instruction >> 1) & 1023
            if op == 18 or op == 16 or (op == 19 and xo in (16, 528)):
                if i + 1 < len(words):
                    leaders.add(i + 1)
                link = instruction & 1
                if op == 18 and not link:
                    target = branch_target(instruction, pc)
                    if start <= target < end:
                        leaders.add((target - start) // 4)
                elif op == 16 and not link:
                    target = conditional_target(instruction, pc)
                    if start <= target < end:
                        leaders.add((target - start) // 4)
        return sorted(leaders)

    def run_function(self, function):
        start, end = function.start, function.end
        words = [self.image.word(address) for address in range(start, end, 4)]
        order = self.block_leaders(function, words)
        block_end = {block: (order[j + 1] if j + 1 < len(order) else len(words)) for j, block in enumerate(order)}
        entry = dict(regs=[(('in', r), 0) for r in range(32)], ctr=(('in', 'ctr'), 0), lr=(('in', 'lr'), 0), mem={})
        in_states = {0: entry}
        visited = set()
        work = [0]
        seeded_unreached = False
        iterations = 0
        while True:
            while work:
                block = work.pop()
                iterations += 1
                if iterations > 50 * len(order) + 1000:
                    raise RuntimeError(f'no fixpoint in {function.name}')
                visited.add(block)
                out, successors = self.step_block(function, words, block, block_end[block], in_states[block],
                                                  emit=False)
                for successor in successors:
                    if successor in in_states:
                        merged = self.merge(in_states[successor], out, successor)
                    else:
                        merged = out
                    if successor not in in_states or merged != in_states[successor]:
                        in_states[successor] = merged
                        work.append(successor)
            # Blocks not reached by direct flow (jump-table targets, or embedded data): seed with unknown state.
            missing = [block for block in order if block not in visited]
            if not missing:
                break
            seeded_unreached = True
            block = missing[0]
            pc = start + 4 * block
            in_states[block] = dict(regs=[(('phi', pc, r), 0) for r in range(32)], ctr=(('phi', pc, 'ctr'), 0),
                                    lr=(('phi', pc, 'lr'), 0), mem={}, unreached=True)
            work.append(block)
        for block in order:
            self.step_block(function, words, block, block_end[block], in_states[block], emit=True)
        if seeded_unreached:
            self.blocks_unreached += sum(1 for block in order if in_states[block].get('unreached'))

    def merge(self, a, b, block):
        """Two states meeting at block: registers that disagree become a merge-point symbol; only the remembered slots
        both agree on are kept."""
        regs = [x if x == y else (('phi', block, r), 0) for r, (x, y) in enumerate(zip(a['regs'], b['regs']))]
        memory = {slot: value for slot, value in a['mem'].items() if b['mem'].get(slot) == value}
        out = dict(regs=regs, ctr=a['ctr'] if a['ctr'] == b['ctr'] else (('phi', block, 'ctr'), 0),
                   lr=a['lr'] if a['lr'] == b['lr'] else (('phi', block, 'lr'), 0), mem=memory)
        if a.get('unreached') and b.get('unreached'):
            out['unreached'] = True
        return out

    def clobber_call(self, state, pc, target=None):
        """What a call does to the state: register save/restore helpers only touch r0, r11, r12 and the stack; other
        calls clobber the volatile set. CTR is clobbered, LR is the return address, remembered slots are forgotten."""
        helper = target is not None and target in self.helper_targets
        for r in (HELPER_CLOBBERS if helper else VOLATILE_GPRS):
            state['regs'][r] = (('def', pc, r), 0)
        state['ctr'] = (('def', pc, 'ctr'), 0)
        state['lr'] = pc + 4
        state['mem'] = {}

    def step_block(self, function, words, first, last, in_state, emit):
        """Run words[first:last] (one basic block) from in_state: (out state, successor block indices). With emit,
        record the block's edges, references and vtable stores; without, only compute the state."""
        start, end = function.start, function.end
        regs = list(in_state['regs'])
        state = dict(regs=regs, ctr=in_state['ctr'], lr=in_state['lr'], mem=dict(in_state['mem']))
        if in_state.get('unreached'):
            state['unreached'] = True
        successors = []
        falls = True
        for i in range(first, last):
            instruction = words[i]
            pc = start + 4 * i
            op = instruction >> 26
            rd = (instruction >> 21) & 31
            ra = (instruction >> 16) & 31
            rb = (instruction >> 11) & 31

            def fresh(tag='w'):
                return (('def', pc, tag), 0)

            if op == 14 or op == 15:                                        # addi / addis (li / lis)
                imm = simm16(instruction) << (16 if op == 15 else 0)
                if ra == 0:
                    regs[rd] = imm & M32
                else:
                    regs[rd] = offset_value(regs[ra], imm)
                if op == 14 and ra != 0 and isinstance(regs[rd], int) and emit:
                    self.note_reference(pc, function, regs[rd], 'addr')
            elif op in (12, 13, 7, 8):
                regs[rd] = fresh()
            elif op in (24, 25):                                            # ori / oris
                value = regs[rd]
                uimm = instruction & 0xffff
                if isinstance(value, int):
                    regs[ra] = (value | (uimm << (16 if op == 25 else 0))) & M32
                    if op == 24 and uimm and emit:
                        self.note_reference(pc, function, regs[ra], 'addr')
                elif uimm == 0:
                    regs[ra] = value
                else:
                    regs[ra] = fresh()
            elif op in (26, 27, 28, 29, 20, 21, 23, 30):
                regs[ra] = fresh()
            elif op in DLOAD:
                base = 0 if ra == 0 else regs[ra]
                value = self.load(state, base, simm16(instruction), DLOAD[op], pc, function, emit)
                if op == 32 or op == 33:                                    # lwz / lwzu: a word we follow
                    regs[rd] = value
                else:
                    regs[rd] = fresh()
                if op in DLOAD_UPDATE and ra:
                    regs[ra] = offset_value(regs[ra], simm16(instruction))
            elif op in DSTORE:
                base = 0 if ra == 0 else regs[ra]
                self.store(state, base, simm16(instruction), DSTORE[op], regs[rd], pc, function, emit)
                if op in DSTORE_UPDATE and ra:
                    regs[ra] = offset_value(regs[ra], simm16(instruction))
            elif op == 46:                                                  # lmw
                for r in range(rd, 32):
                    regs[r] = fresh(r)
            elif op == 47:                                                  # stmw
                state['mem'] = {}
            elif op in FPLOADSTORE:
                base = 0 if ra == 0 else regs[ra]
                if op < 52:
                    if isinstance(base, int) and emit:
                        self.note_reference(pc, function, (base + simm16(instruction)) & M32, 'read')
                else:       # a float store: no value we follow, but it overwrites (and may alias) remembered slots
                    size = 4 if op in (52, 53) else 8
                    self.store(state, base, simm16(instruction), size, None, pc, function, emit)
                if op & 1 and ra:                                           # the update forms
                    regs[ra] = offset_value(regs[ra], simm16(instruction))
            elif op == 58:                                                  # ld / ldu / lwa
                ds = simm16(instruction) & ~3
                base = 0 if ra == 0 else regs[ra]
                if isinstance(base, int) and emit:
                    self.note_reference(pc, function, (base + ds) & M32, 'read')
                regs[rd] = fresh()
                if instruction & 3 == 1 and ra:
                    regs[ra] = offset_value(regs[ra], ds)
            elif op == 62:                                                  # std / stdu
                ds = simm16(instruction) & ~3
                base = 0 if ra == 0 else regs[ra]
                self.store(state, base, ds, 8, None, pc, function, emit)
                if instruction & 3 == 1 and ra:
                    regs[ra] = offset_value(regs[ra], ds)
            elif op == 31:
                xo = (instruction >> 1) & 1023
                if xo == 444 and rd == rb:                                  # mr
                    regs[ra] = regs[rd]
                elif xo == 467:                                             # mtspr
                    spr = spr_field(instruction)
                    if spr == SPR_CTR:
                        state['ctr'] = regs[rd]
                    elif spr == SPR_LR:
                        state['lr'] = regs[rd]
                elif xo == 339:                                             # mfspr
                    spr = spr_field(instruction)
                    if spr == SPR_CTR:
                        regs[rd] = state['ctr']
                    elif spr == SPR_LR:
                        regs[rd] = state['lr']
                    else:
                        regs[rd] = fresh()
                elif xo in LOGICAL31 or (xo >> 1) == 413:                  # and/or/xor/shifts/extends... write rA
                    regs[ra] = fresh()
                elif xo in STORE31:
                    state['mem'] = {}
                elif xo in STORE31_UPDATE:
                    state['mem'] = {}
                    regs[ra] = fresh('u')
                elif xo in NOWRITE31:
                    pass
                elif xo in LOAD31_UPDATE:
                    regs[rd] = fresh()
                    regs[ra] = fresh('u')
                elif xo in LOAD31_WORD or (xo & 511) in XO_ARITH or xo in WRITES_RD31:
                    regs[rd] = fresh()
                else:                                                       # anything else: forget both fields
                    regs[rd] = fresh()
                    regs[ra] = fresh('a')
            elif op == 18:                                                  # b / bl
                target = branch_target(instruction, pc)
                if instruction & 1:
                    if emit:
                        self.edges.append(self.direct(function, pc, target, 'direct_call'))
                    self.clobber_call(state, pc, target)
                else:
                    falls = False
                    if start <= target < end:
                        successors.append((target - start) // 4)
                    elif emit:
                        self.edges.append(self.direct(function, pc, target, 'tail_call'))
                    break
            elif op == 16:                                                  # bc / bcl
                target = conditional_target(instruction, pc)
                always = always_taken((instruction >> 21) & 31)
                if instruction & 1:
                    if emit:
                        kind = 'direct_call' if always else 'conditional_call'
                        self.edges.append(self.direct(function, pc, target, kind))
                    self.clobber_call(state, pc, target)
                else:
                    if start <= target < end:
                        successors.append((target - start) // 4)
                    elif emit:
                        kind = 'tail_call' if always else 'conditional_tail_call'
                        self.edges.append(self.direct(function, pc, target, kind))
                    if always:
                        falls = False
                        break
            elif op == 19:
                xo = (instruction >> 1) & 1023
                always = always_taken((instruction >> 21) & 31)
                if xo == 16:                                                # bclr
                    if instruction & 1:
                        if emit:
                            callee, kind, detail = self.classify_indirect(state['lr'], 'LR')
                            self.edges.append((function.start, pc, callee, kind if always else 'conditional_' + kind, detail))
                        self.clobber_call(state, pc)
                    elif always:
                        falls = False
                        break
                elif xo == 528:                                             # bcctr
                    if instruction & 1:
                        if emit:
                            callee, kind, detail = self.classify_indirect(state['ctr'], 'CTR')
                            self.edges.append((function.start, pc, callee, kind if always else 'conditional_' + kind, detail))
                        self.clobber_call(state, pc)
                    else:
                        if emit:
                            self.indirect_branch(function, pc, state['ctr'])
                        if always:
                            falls = False
                            break
            elif op == 4 and (instruction & VX128_STORE_MASK) in VX128_STORES:      # a VMX128 store: memory changed
                state['mem'] = {}
            # opcodes 4/5/6 (vector), 59/63 (FP), 17 (sc), 2/3 (traps), 10/11 (compares) write no GPR we follow.
        if falls and last < len(words):
            successors.append(last)
        return state, successors

    def indirect_branch(self, function, pc, ctr):
        """The edge of a bcctr (no link): a resolved tail call, an import tail call, nothing for a branch inside the
        function, else an unresolved branch (a jump table or a tail call)."""
        callee, kind, detail = self.classify_indirect(ctr, 'CTR')
        branch_kind = {'indirect_resolved': 'indirect_tail_call_resolved', 'import_call': 'import_tail_call'}.get(kind)
        if branch_kind:
            self.edges.append((function.start, pc, callee, branch_kind, detail))
        elif isinstance(ctr, int) and function.start <= ctr < function.end:
            pass
        else:
            self.edges.append((function.start, pc, callee, 'indirect_branch_unresolved',
                               detail + ' (jump table or tail call)'))

    def direct(self, function, site, target, kind):
        """The edge of a direct call or branch leaving the function, by what its target is."""
        if target in self.image.import_thunks:
            thunk = self.image.import_thunks[target]
            import_kind = 'import_call' if kind in CALL_KINDS else 'import_tail_call'
            return (function.start, site, target, import_kind, f'import {thunk["library"]}:{thunk["name"]}')
        if target in self.labels:
            helper_kind = 'helper_call' if kind in CALL_KINDS else 'helper_' + kind
            return (function.start, site, target, helper_kind, self.labels[target])
        if target in self.starts:
            return (function.start, site, target, kind, '')
        inside = self.containing(target)
        detail = f'inside {inside.name}+{target - inside.start:#x}' if inside else 'outside every function'
        return (function.start, site, target, kind + '_nonentry', detail)


# --- strings ----------------------------------------------------------------------------------------------------
PRINTABLE = set(range(0x20, 0x7f)) | {9, 10, 13}


def latin_unit(unit):
    return (0x20 <= unit <= 0x7e or unit in (9, 10, 13) or 0xa0 <= unit <= 0x17f or 0x2010 <= unit <= 0x2027
            or unit in (0x20ac, 0x2122))


def latin_text(text):
    """Printable (or tab/newline/CR) text whose non-ASCII characters are all Latin-script."""
    return all((c.isprintable() or c in '\t\n\r') and (ord(c) < 0x80 or latin_unit(ord(c))) for c in text)


def scan_strings(image):
    strings = {}
    data = image.data
    for section in image.sections:
        if section['execute']:
            continue
        low, high = section['start'] - BASE, section['start'] - BASE + section['size']
        i = low
        while i < high:                                 # ASCII / UTF-8 runs ending in NUL
            if data[i] in PRINTABLE or data[i] >= 0xc2:
                j = i
                while j < high and (data[j] in PRINTABLE or data[j] >= 0x80):
                    j += 1
                if j < high and data[j] == 0 and j - i >= 4:
                    k = i
                    try:
                        text = data[i:j].decode('utf-8')
                    except UnicodeDecodeError:
                        text = None
                    if text is None or not latin_text(text):
                        # Binary bytes that happen to precede (or form UTF-8 before) a string: keep the ASCII tail only.
                        k = max(x for x in range(i, j) if data[x] >= 0x80) + 1
                        text = data[k:j].decode('ascii')
                    if len(text) >= 4:
                        encoding = 'ascii' if max(data[k:j]) < 0x80 else 'utf-8'
                        strings[BASE + k] = dict(address=BASE + k, section=section['name'], encoding=encoding, text=text,
                                                 bytes=j - k + 1)
                i = j + 1
            else:
                i += 1
        i = low + (low & 1)
        while i + 1 < high:                             # UTF-16BE runs (2-aligned) ending in a zero unit
            unit = (data[i] << 8) | data[i + 1]
            if latin_unit(unit) and unit >= 0x20:
                j = i
                units = []
                while j + 1 < high:
                    next_unit = (data[j] << 8) | data[j + 1]
                    if not latin_unit(next_unit):
                        break
                    units.append(next_unit)
                    j += 2
                terminated = j + 1 < high and data[j] == 0 and data[j + 1] == 0
                wordlike = sum(chr(x).isalnum() or x == 0x20 for x in units) >= 0.6 * len(units)
                if terminated and len(units) >= 4 and wordlike:
                    if BASE + i not in strings:
                        text = ''.join(map(chr, units))
                        strings[BASE + i] = dict(address=BASE + i, section=section['name'], encoding='utf-16be',
                                                 text=text, bytes=j - i + 2)
                i = j + 2
            else:
                i += 2
    return strings


def short_string_at(image, address):
    """A NUL-terminated printable ASCII run of 1-3 characters at address (for code-referenced short strings)."""
    offset = address - BASE
    data = image.data
    if not 0 <= offset < len(data):
        return None
    section = image.section(address)
    if section is None or section['execute']:
        return None
    j = offset
    while j < len(data) and j - offset < 4 and data[j] in PRINTABLE:
        j += 1
    starts_a_string = offset == 0 or data[offset - 1] == 0 or (address % 4 == 0)
    if j < len(data) and data[j] == 0 and 1 <= j - offset <= 3 and starts_a_string:
        return dict(address=address, section=section['name'], encoding='ascii', text=data[offset:j].decode(),
                    bytes=j - offset + 1, short=True)
    return None


def esc(text):
    return json.dumps(text, ensure_ascii=False)[1:-1]


def write_tsv(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write('\t'.join(header) + '\n')
        for row in rows:
            f.write('\t'.join('' if x is None else str(x) for x in row) + '\n')


def data_words(image):
    """(offset, word) of every aligned word of the non-code sections."""
    for section in image.sections:
        if section['execute']:
            continue
        low = section['start'] - BASE
        for offset in range(low + (-low % 4), low + section['size'] - 3, 4):
            yield offset, struct.unpack_from('>I', image.data, offset)[0]


def in_code(address, code_sections):
    return any(section['start'] <= address < section['start'] + section['size'] for section in code_sections)


def outside_every_function(kind, detail):
    return kind.endswith('_nonentry') and detail == 'outside every function'


def function_graph_edges(analysis):
    """The distinct (caller, callee function) pairs of the call edges (unresolved branches left out)."""
    pairs = set()
    for caller, site, callee, kind, detail in analysis.edges:
        if callee is None or 'branch_unresolved' in kind:
            continue
        if callee in analysis.starts:
            pairs.add((caller, callee))
        elif analysis.containing(callee) is not None:
            pairs.add((caller, analysis.containing(callee).start))
    return pairs


def discover_functions(analysis, image, functions):
    """Run the analysis to a settled set of functions, inferring leaf functions (no .pdata record) from call targets
    and data words that land outside every known function. Returns (the graph before any inferred function, the
    discovery rounds)."""
    pending = list(functions)
    rounds = []
    pdata_only = None
    code_sections = [section for section in image.sections if section['execute']]
    while pending:
        for function in pending:
            analysis.run_function(function)
        if not rounds:
            # The graph before any inferred function exists: what the inferred entries change.
            kinds = collections.Counter(kind for *_, kind, _ in analysis.edges)
            pdata_only = dict(call_site_kinds=dict(sorted(kinds.items())),
                              graph_edges_function_to_function=len(function_graph_edges(analysis)),
                              nonentry_call_sites_outside_every_function=sum(
                                  1 for *_, kind, detail in analysis.edges if outside_every_function(kind, detail)))
        # Entry points outside every known function: direct/tail call targets, then 4-aligned data words into code
        # gaps. Leaf functions need no .pdata record, so the exception table misses them.
        candidates = {}
        for caller, site, target, kind, detail in analysis.edges:
            if target is not None and outside_every_function(kind, detail) and in_code(target, code_sections):
                candidates.setdefault(target, 'inferred_call_target')
        if not rounds:
            for offset, value in data_words(image):
                if (value % 4 == 0 and value not in analysis.starts and in_code(value, code_sections)
                        and analysis.containing(value) is None and value not in image.import_thunks):
                    candidates.setdefault(value, 'inferred_data_pointer')
        candidates = {address: why for address, why in candidates.items()
                      if address not in analysis.starts and analysis.containing(address) is None}
        if not candidates:
            break
        # Extent: up to the next known or candidate entry, the end of the code gap, or the first zero word (padding).
        known = sorted(set(analysis.starts) | set(candidates))
        new = []
        for address in sorted(candidates):
            following = bisect.bisect_right(known, address)
            next_entry = known[following] if following < len(known) else None
            section = next(section for section in code_sections
                           if section['start'] <= address < section['start'] + section['size'])
            limit = min(x for x in (next_entry, section['start'] + section['size']) if x is not None)
            end = address
            while end < limit and image.word(end) != 0:
                end += 4
            if end > address:
                new.append(Function(address, end, f'leaf_{address:08x}', candidates[address]))
        # Edges found so far are recomputed for every function once the set is final.
        rounds.append(dict(new_functions=len(new),
                           from_calls=sum(1 for f in new if f.label == 'inferred_call_target'),
                           from_data=sum(1 for f in new if f.label == 'inferred_data_pointer')))
        analysis.add_functions(new)
        analysis.reset_results()
        pending = list(analysis.functions)
        if len(rounds) > 8:
            raise SystemExit('function discovery did not settle')
    return pdata_only, rounds


def string_references(analysis, image, strings, code_targets):
    """The string reference rows (string, from, function, kind, detail) and each string's data pointer cells, plus
    the data cells that hold a function start: (rows, {string: [cells]}, {function: [cells]})."""
    starts_sorted = sorted(strings)

    def string_containing(address):
        i = bisect.bisect_right(starts_sorted, address) - 1
        if i >= 0:
            string = strings[starts_sorted[i]]
            if string['address'] <= address < string['address'] + string['bytes'] - 1:
                return string
        return None

    pointer_cells = collections.defaultdict(list)       # string address -> data cells holding it
    code_pointer_cells = collections.defaultdict(list)  # function start -> data cells holding it
    for offset, value in data_words(image):
        if value in strings:
            pointer_cells[value].append(BASE + offset)
        elif value in analysis.starts:
            code_pointer_cells[value].append(BASE + offset)
    rows = []
    for target, uses in code_targets.items():
        string = strings.get(target) or string_containing(target)
        if string:
            detail = '' if string['address'] == target else f'interior+{target - string["address"]}'
            for site, function_start, kind in uses:
                rows.append((string['address'], site, function_start, f'code_{kind}', detail))
    for string_address, cells in pointer_cells.items():
        for cell in cells:
            holder = analysis.containing(cell)
            rows.append((string_address, cell, holder.start if holder else '', 'data_pointer', ''))
            for site, function_start, kind in code_targets.get(cell, []):
                rows.append((string_address, site, function_start, f'code_{kind}_via_pointer', f'cell {cell:08x}'))
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows, pointer_cells, code_pointer_cells


def is_unresolved(kind):
    return 'unresolved' in kind or kind == 'indirect_unknown' or kind.endswith('_indirect_unknown')


DIRECT_KINDS = ('direct_call', 'conditional_call', 'tail_call', 'conditional_tail_call', 'helper_call', 'import_call',
                'import_tail_call')
GHIDRA_DIRECT_KINDS = ('direct_call', 'conditional_call', 'helper_call', 'import_call')


def ghidra_cross_check(path, analysis):
    """Our direct calls against the call references Ghidra's listing holds (export_all_decompiled.java)."""
    rows = tsv(path)
    ghidra = {(int(row['instruction'], 16), int(row['target'], 16)) for row in rows
              if row['kind'] in ('call', 'conditional_call') and row['target']}
    ours = {(site, target) for caller, site, target, kind, detail in analysis.edges
            if target is not None and (kind in GHIDRA_DIRECT_KINDS or kind.startswith('direct_call_')
                                       or kind.startswith('conditional_call_'))}
    return dict(file=str(path), file_sha256=sha256_file(path),
                ghidra_direct_call_refs=len(ghidra), ours_direct_calls=len(ours), both=len(ghidra & ours),
                only_ghidra=len(ghidra - ours), only_ours=len(ours - ghidra),
                only_ghidra_examples=[f'{site:08x}->{target:08x}' for site, target in sorted(ghidra - ours)[:10]],
                only_ours_examples=[f'{site:08x}->{target:08x}' for site, target in sorted(ours - ghidra)[:10]],
                ghidra_computed_calls=sum(1 for row in rows if row['kind'] == 'computed_call'))


def main():
    root = research_root()              # the research workspace (workspace.py), looked up before the arguments are read
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--ghidra-call-refs', type=Path,
                        help='ghidra-call-refs.tsv from export_all_decompiled.java, for a cross-check of direct calls')
    args = parser.parse_args()
    work = root / 'work/xex-analysis'
    out = work / 'callgraph'
    started = time.time()
    image = Image(work)
    names = {int(row['address'], 16): (row['name'], row['file']) for row in tsv(work / 'decompiled-index.tsv')}
    functions = []
    for row in tsv(work / 'functions.tsv'):
        start, end = int(row['start'], 16), int(row['end'], 16)
        functions.append(Function(start, end, names.get(start, (f'pdata_{start:08x}', ''))[0]))
    labels = {int(row['address'], 16): row['name'] for row in tsv(work / 'helper-entries.tsv')}
    analysis = Analysis(image, functions, labels)
    pdata_count = len(functions)
    pdata_only, rounds = discover_functions(analysis, image, functions)
    functions = sorted(analysis.functions, key=lambda f: f.start)
    dataflow_done = time.time()

    # Strings and their references.
    strings = scan_strings(image)
    code_targets = collections.defaultdict(list)        # address -> [(site, function start, kind)]
    for site, function_start, target, kind in analysis.refs:
        code_targets[target].append((site, function_start, kind))
    for address in list(code_targets):
        if address not in strings:
            short = short_string_at(image, address)
            if short:
                strings[address] = short
    string_refs, pointer_cells, code_pointer_cells = string_references(analysis, image, strings, code_targets)
    refs_by_string = collections.defaultdict(set)       # string address -> functions referencing it
    for string_address, site, function_start, kind, _ in string_refs:
        if function_start != '':
            refs_by_string[string_address].add(function_start)

    # Call graph tables.
    callees = collections.defaultdict(set)
    callers = collections.defaultdict(set)
    kinds_by_function = collections.defaultdict(collections.Counter)
    for caller, site, callee, kind, detail in analysis.edges:
        kinds_by_function[caller][kind] += 1
        if callee is not None:
            if callee in analysis.starts:
                callee_function = callee
            else:
                inside = analysis.containing(callee)
                callee_function = inside.start if inside else None
            if callee_function is not None and 'branch_unresolved' not in kind:
                callees[caller].add(callee_function)
                callers[callee_function].add(caller)
    out.mkdir(parents=True, exist_ok=True)

    def function_name(address):
        if address in analysis.starts:
            return analysis.starts[address].name
        if address in image.import_thunks:
            return image.import_thunks[address]['name']
        return labels.get(address, '')

    def hex_or_blank(address):
        return '' if address is None else f'{address:08x}'

    write_tsv(out / 'callgraph-edges.tsv', ['caller', 'caller_name', 'site', 'callee', 'callee_name', 'kind', 'detail'],
              [(f'{caller:08x}', function_name(caller), f'{site:08x}', hex_or_blank(callee),
                '' if callee is None else function_name(callee), kind, detail)
               for caller, site, callee, kind, detail in analysis.edges])
    rows = []
    for f in functions:
        kind_counts = kinds_by_function[f.start]
        direct = sum(count for kind, count in kind_counts.items() if kind in DIRECT_KINDS or kind.endswith('_nonentry'))
        resolved = sum(count for kind, count in kind_counts.items() if 'resolved' in kind and 'unresolved' not in kind)
        unresolved = sum(count for kind, count in kind_counts.items() if is_unresolved(kind) and 'branch' not in kind)
        rows.append((f'{f.start:08x}', f'{f.end:08x}', f.name, analysis.source[f.start], names.get(f.start, ('', ''))[1],
                     len(callers[f.start]), len(callees[f.start]), direct, resolved, unresolved,
                     kind_counts.get('indirect_branch_unresolved', 0),
                     ','.join(f'{c:08x}' for c in sorted(callers[f.start])),
                     ','.join(f'{c:08x}' for c in sorted(callees[f.start]))))
    write_tsv(out / 'callgraph-functions.tsv',
              ['address', 'end', 'name', 'source', 'decompiled_file', 'callers', 'callees', 'direct_call_sites',
               'indirect_resolved_sites', 'indirect_unresolved_sites', 'indirect_branches_unresolved', 'caller_list',
               'callee_list'], rows)
    write_tsv(out / 'address-refs.tsv', ['site', 'function', 'target', 'kind', 'section'],
              [(f'{site:08x}', f'{function_start:08x}', f'{target:08x}', kind, (image.section(target) or {}).get('name', 'gap'))
               for site, function_start, target, kind in sorted(analysis.refs)])
    write_tsv(out / 'strings.tsv',
              ['address', 'section', 'encoding', 'chars', 'bytes', 'referencing_functions', 'pointer_cells', 'text'],
              [(f'{address:08x}', string['section'], string['encoding'], len(string['text']), string['bytes'],
                len(refs_by_string.get(address, ())), len(pointer_cells.get(address, ())), esc(string['text']))
               for address, string in sorted(strings.items())])
    write_tsv(out / 'string-refs.tsv', ['string', 'from', 'function', 'kind', 'detail'],
              [(f'{string_address:08x}', f'{site:08x}', '' if function_start == '' else f'{function_start:08x}', kind, detail)
               for string_address, site, function_start, kind, detail in string_refs])
    vtables = []
    for address in sorted(analysis.vtable_stores):
        leading = 0                                     # function pointers at the start of the table
        while image.readonly(address + 4 * leading) and image.word(address + 4 * leading) in analysis.starts:
            leading += 1
        stored_by = ','.join(f'{x:08x}' for x in sorted(analysis.vtable_stores[address]))
        vtables.append((f'{address:08x}', leading, stored_by))
    write_tsv(out / 'vtable-candidates.tsv', ['address', 'leading_function_pointers', 'stored_at_offset_0_by'], vtables)
    write_tsv(out / 'inferred-functions.tsv', ['start', 'end', 'name', 'source'],
              [(f'{f.start:08x}', f'{f.end:08x}', f.name, f.label) for f in functions if f.label])
    write_tsv(out / 'data-pointers-to-functions.tsv', ['cell', 'section', 'function'],
              sorted((f'{cell:08x}', image.section(cell)['name'], f'{target:08x}')
                     for target, cells in code_pointer_cells.items() for cell in cells))

    kinds = collections.Counter(kind for *_, kind, _ in analysis.edges)
    encodings = collections.Counter(string['encoding'] for string in strings.values())
    inputs = ['default.checked.memory.bin', 'functions.tsv', 'sections.tsv', 'imports.tsv', 'helper-entries.tsv',
              'decompiled-index.tsv']
    summary = dict(
        tool='bf1943-xex-tools/export_callgraph_strings.py', version=VERSION, tool_sha256=sha256_file(__file__),
        python=sys.version.split()[0], seconds=round(time.time() - started, 1),
        seconds_dataflow=round(dataflow_done - started, 1),
        inputs={name: sha256_file(work / name) for name in inputs},
        functions=len(functions), functions_pdata_and_helpers=pdata_count, functions_inferred=len(functions) - pdata_count,
        functions_inferred_from_calls=sum(1 for f in functions if f.label == 'inferred_call_target'),
        functions_inferred_from_data_pointers=sum(1 for f in functions if f.label == 'inferred_data_pointer'),
        inferred_code_bytes=sum(f.end - f.start for f in functions if f.label), discovery_rounds=rounds,
        pdata_only_graph=pdata_only,
        basic_blocks_seeded_unreached=analysis.blocks_unreached,
        call_sites=len(analysis.edges), call_site_kinds=dict(sorted(kinds.items())),
        functions_with_callers=sum(1 for f in functions if callers[f.start]),
        functions_with_callees=sum(1 for f in functions if callees[f.start]),
        graph_edges_function_to_function=sum(len(v) for v in callees.values()),
        address_references=len(analysis.refs), strings=len(strings), string_encodings=dict(sorted(encodings.items())),
        strings_short_referenced=sum(1 for string in strings.values() if string.get('short')),
        strings_referenced_by_code=sum(1 for address in strings if refs_by_string.get(address)),
        string_reference_rows=len(string_refs),
        string_pointer_cells=sum(len(v) for v in pointer_cells.values()), vtable_candidates=len(vtables),
        data_pointers_to_functions=sum(len(v) for v in code_pointer_cells.values()))
    if args.ghidra_call_refs:
        summary['ghidra_cross_check'] = ghidra_cross_check(args.ghidra_call_refs, analysis)
    outputs = sorted(path.name for path in out.iterdir() if path.suffix == '.tsv')
    summary['outputs'] = {f'work/xex-analysis/callgraph/{name}': sha256_file(out / name) for name in outputs}
    (out / 'summary.json').write_bytes((json.dumps(summary, indent=2) + '\n').encode())
    # Commit-able evidence: counts, hashes and paths only (the outputs themselves derive from EA's binary).
    evidence = dict(summary, note='Outputs are under the ignored work area; they derive from the game binary and are not committed.',
                    limits=['Static: constants are followed within one function (no interprocedural values); loads fold only from read-only sections',
                            'Indirect calls are resolved only when CTR/LR is a certain constant; virtual calls are classified with their vtable slot, not resolved',
                            'Inferred leaf functions (no .pdata record) end at the next known entry or the first zero word; their extents are not proven',
                            'Strings: ASCII/UTF-8 and Latin-script UTF-16BE only; references are exact-address code references and one level of data pointer cells'])
    (root / 'research/evidence/xex-callgraph-strings.json').write_bytes((json.dumps(evidence, indent=2) + '\n').encode())
    print(json.dumps({key: value for key, value in summary.items() if key not in ('inputs', 'outputs')}, indent=1))
    return summary


if __name__ == '__main__':
    main()
