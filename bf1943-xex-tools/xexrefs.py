"""Shared static reference scanning over the checked BF1943 image (no Ghidra, no execution).

Importing this module loads the checked image and the section and function tables from the research workspace
(workspace.py: BF1943_RESEARCH, else the current folder), so set BF1943_RESEARCH before the import. The investigations
import its names (ROOT, data, word, functions, ...) directly, and code_references() reads the module's `word` and
`functions` when it runs, so a test can replace them with a made-up function.
"""
import collections
import csv
import hashlib
import struct

from workspace import research_root  # noqa: E402

type Pointers = collections.defaultdict[int, list[int]]     # value -> the addresses of the words holding it
type CodeReferences = collections.defaultdict[int, list[tuple[int, int]]]   # target -> [(instruction, function)]

ROOT = research_root()                  # the research workspace (workspace.py)
WORK = ROOT / 'work/xex-analysis'
IMAGE = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
BASE = 0x82000000
data: bytes = (WORK / 'default.checked.memory.bin').read_bytes()
assert hashlib.sha256(data).hexdigest() == IMAGE
END = BASE + len(data)
CODE_PATTERN = ('lis/addis rX,0,hi paired within one .pdata range with addi/addic, ori, or D-form displacement on rX; '
                'pairing resets at branches and at any write of rX')
# Before 2026-10-30 the pairing reset only at branches and at a few instruction forms, so a register overwritten by,
# say, srwi or mr still paired with its old lis (212 of 55,036 references on the BF1943 image were such).
sections: list[tuple[str, int, int]] = [(row['name'], int(row['start'], 16), int(row['size']))
            for row in csv.DictReader((WORK / 'sections.tsv').open(), delimiter='\t')]
functions: list[tuple[int, int]] = [(int(row['start'], 16), int(row['end'], 16))
             for row in csv.DictReader((WORK / 'functions.tsv').open(), delimiter='\t')]


def section(address: int) -> str:
    """The name of the section holding address: 'gap' between sections, 'unmapped' outside the image."""
    default = 'unmapped' if not BASE <= address < END else 'gap'
    return next((name for name, start, size in sections if start <= address < start + size), default)


def word(address: int) -> int:
    """The big-endian word at an image address."""
    return struct.unpack_from('>I', data, address - BASE)[0]


def cstring(address: int) -> str | None:
    """The printable ASCII NUL-terminated string at address (1 to 127 characters), else None."""
    if not BASE <= address < END:
        return None
    offset = address - BASE
    text = data[offset:data.find(b'\0', offset)]
    if 0 < len(text) < 128 and all(32 <= byte < 127 for byte in text):
        return text.decode()
    return None


def data_pointers() -> Pointers:
    """Every aligned big-endian word in the image whose value is an image address, indexed by value."""
    pointers: Pointers = collections.defaultdict(list)
    for offset in range(0, len(data) - 3, 4):
        value = struct.unpack_from('>I', data, offset)[0]
        if BASE <= value < END:
            pointers[value].append(BASE + offset)
    return pointers


# Opcode-31 (X/XO-form) extended opcodes by which register field they write (the same sets as
# export_callgraph_strings.py's decoder, for general-purpose registers only).
X_WRITES_RA = {28, 60, 124, 284, 316, 412, 444, 476, 24, 536, 792, 824, 27, 539, 794, 26, 58, 954, 922, 986, 413, 826,
               827}  # logical, shifts, extends
X_STORE = {151, 215, 407, 149, 662, 918, 660, 150, 214, 663, 727, 983, 231, 487, 135, 167, 199, 647, 679, 903, 935, 725,
           661}
X_STORE_UPDATE = {183, 247, 439, 181, 695, 759}
X_NO_GPR = {0, 32, 4, 68, 86, 54, 278, 246, 1014, 470, 982, 598, 854, 144, 146, 178, 467, 342, 374, 822, 535, 599, 103,
            359, 7, 39, 71, 6, 38, 519, 551, 775, 807}
X_LOAD_UPDATE = {55, 119, 311, 375, 53, 373, 567, 631}
# Extended opcodes that write rD only: the XO-form arithmetic (compared without the OE bit, under the 9-bit mask) and
# the indexed loads without update, mfspr and friends.
X_WRITES_RD_ARITHMETIC = {266, 40, 8, 10, 138, 136, 200, 202, 232, 234, 104, 235, 233, 491, 459, 489, 457, 73, 9, 75,
                          11}
X_WRITES_RD = {23, 87, 279, 343, 21, 341, 19, 83, 371, 339}


def gpr_writes(instruction: int) -> set[int]:
    """The general-purpose registers an instruction word may write (conservative: an unknown form writes both of its
    register fields), for forgetting a lis value that the register no longer holds."""
    op = instruction >> 26
    rd = (instruction >> 21) & 31
    ra = (instruction >> 16) & 31
    if op in (7, 8, 12, 13, 14, 15, 32, 34, 40, 42):
        return {rd}
    # loads with update also write the base
    if op in (33, 35, 41, 43):
        return {rd, ra}
    # rotates, ori/oris/xori/xoris/andi./andis., 64-bit rotates
    if op in (20, 21, 23, 24, 25, 26, 27, 28, 29, 30):
        return {ra}
    # updates of the base: stwu, stbu, sthu, lfsu, lfdu, stfsu, stfdu
    if op in (37, 39, 45, 49, 51, 53, 55):
        return {ra}
    # lmw
    if op == 46:
        return set(range(rd, 32))
    # ld, ldu, lwa
    if op == 58:
        return {rd, ra} if instruction & 3 == 1 else {rd}
    # std, stdu
    if op == 62:
        return {ra} if instruction & 3 == 1 else set()
    if op == 31:
        xo = (instruction >> 1) & 1023
        if xo in X_STORE or xo in X_NO_GPR:
            return set()
        if xo in X_STORE_UPDATE:
            return {ra}
        if xo in X_LOAD_UPDATE:
            return {rd, ra}
        # (or, so mr, writes rA)
        if xo in X_WRITES_RA or (xo >> 1) == 413:
            return {ra}
        if xo & 511 in X_WRITES_RD_ARITHMETIC or xo in X_WRITES_RD:
            return {rd}
        return {rd, ra}
    # traps, vector, compares, stores, FP
    if op in (2, 3, 4, 5, 6, 10, 11, 36, 38, 44, 47, 48, 50, 52, 54, 59, 63):
        return set()
    # anything else (sc, 56/57/60/61...): forget both fields
    return {rd, ra}


def code_references() -> tuple[CodeReferences, int]:
    """Image addresses formed by lis-paired instructions: {target: [(instruction, function start)]}, words scanned.
    A lis value is forgotten once its register is written by anything (gpr_writes) and at every branch."""
    references: CodeReferences = collections.defaultdict(list)
    scanned = 0
    d_form = set(range(32, 56)) | {58, 62}
    for start, end in functions:
        high: dict[int, int] = {}                           # register -> the value its lis set
        for at in range(start, end, 4):
            instruction = word(at)
            op = instruction >> 26
            rd = (instruction >> 21) & 31
            ra = (instruction >> 16) & 31
            imm = instruction & 0xffff
            simm = imm - 0x10000 if imm & 0x8000 else imm
            scanned += 1
            target = None
            # lis
            if op == 15 and ra == 0:
                high[rd] = (imm << 16) & 0xffffffff
                continue
            # addi and the D/DS-form loads and stores read a base field of 0 as the literal 0, not r0 (li r3, 0x10 after
            # lis r0 is no reference; 2026-10-31); addic and addic. read r0 itself, and ori's rS is a register
            if op == 14 and ra and ra in high:
                target = (high[ra] + simm) & 0xffffffff
            elif op in (12, 13) and ra in high:
                target = (high[ra] + simm) & 0xffffffff
            # ori rA, rS, imm: rS is the rd field
            elif op == 24 and rd in high:
                target = high[rd] | imm
            elif op in d_form and ra and ra in high:
                # DS-form (ld, std): the low two bits are not offset
                displacement = simm & ~3 if op in (58, 62) else simm
                target = (high[ra] + displacement) & 0xffffffff
            if target is not None and BASE <= target < END:
                references[target].append((at, start))
            for register in gpr_writes(instruction):
                high.pop(register, None)
            # branches (and sc)
            if op in (16, 17, 18, 19):
                high.clear()
    return references, scanned
