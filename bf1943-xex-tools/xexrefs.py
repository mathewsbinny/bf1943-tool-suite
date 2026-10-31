"""Shared static reference scanning over the checked BF1943 image (no Ghidra, no execution)."""
from pathlib import Path
import csv,hashlib,struct,collections
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
WORK=ROOT/'work/xex-analysis'
IMAGE='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
BASE=0x82000000
data=(WORK/'default.checked.memory.bin').read_bytes()
assert hashlib.sha256(data).hexdigest()==IMAGE
END=BASE+len(data)
CODE_PATTERN='lis/addis rX,0,hi paired within one .pdata range with addi/addic, ori, or D-form displacement on rX; pairing resets at branches and at any write of rX'
# Before 2026-10-30 the pairing reset only at branches and at a few instruction forms, so a register overwritten by,
# say, srwi or mr still paired with its old lis (212 of 55,036 references on the BF1943 image were such).
sections=[(r['name'],int(r['start'],16),int(r['size'])) for r in csv.DictReader((WORK/'sections.tsv').open(),delimiter='\t')]
functions=[(int(r['start'],16),int(r['end'],16)) for r in csv.DictReader((WORK/'functions.tsv').open(),delimiter='\t')]
def section(a):return next((n for n,s,z in sections if s<=a<s+z),'unmapped' if not BASE<=a<END else 'gap')
def word(a):return struct.unpack_from('>I',data,a-BASE)[0]
def cstring(a):
    if not BASE<=a<END:return None
    s=data[a-BASE:data.find(b'\0',a-BASE)]
    return s.decode() if 0<len(s)<128 and all(32<=c<127 for c in s) else None
def data_pointers():
    """Every aligned big-endian word in the image whose value is an image address, indexed by value."""
    pointers=collections.defaultdict(list)
    for off in range(0,len(data)-3,4):
        v=struct.unpack_from('>I',data,off)[0]
        if BASE<=v<END:pointers[v].append(BASE+off)
    return pointers
# Opcode-31 (X/XO-form) extended opcodes by which register field they write (the same sets as
# export_callgraph_strings.py's decoder, for general-purpose registers only).
X_WRITES_RA={28,60,124,284,316,412,444,476,24,536,792,824,27,539,794,26,58,954,922,986,413,826,827}  # logical, shifts, extends
X_STORE={151,215,407,149,662,918,660,150,214,663,727,983,231,487,135,167,199,647,679,903,935,725,661}
X_STORE_UPDATE={183,247,439,181,695,759}
X_NO_GPR={0,32,4,68,86,54,278,246,1014,470,982,598,854,144,146,178,467,342,374,822,535,599,103,359,7,39,71,6,38,519,551,775,807}
X_LOAD_UPDATE={55,119,311,375,53,373,567,631}
def gpr_writes(w):
    """The general-purpose registers an instruction word may write (conservative: an unknown form writes both of its
    register fields), for forgetting a lis value that the register no longer holds."""
    op=w>>26;rd=(w>>21)&31;ra=(w>>16)&31
    if op in(7,8,12,13,14,15,32,34,40,42):return{rd}
    if op in(33,35,41,43):return{rd,ra}                     # loads with update also write the base
    if op in(20,21,23,24,25,26,27,28,29,30):return{ra}      # rotates, ori/oris/xori/xoris/andi./andis., 64-bit rotates
    if op in(37,39,45,49,51,53,55):return{ra}               # updates of the base: stwu, stbu, sthu, lfsu, lfdu, stfsu, stfdu
    if op==46:return set(range(rd,32))                       # lmw
    if op==58:return{rd,ra} if w&3==1 else{rd}             # ld, ldu, lwa
    if op==62:return{ra} if w&3==1 else set()              # std, stdu
    if op==31:
        xo=(w>>1)&1023
        if xo in X_STORE or xo in X_NO_GPR:return set()
        if xo in X_STORE_UPDATE:return{ra}
        if xo in X_LOAD_UPDATE:return{rd,ra}
        if xo in X_WRITES_RA or (xo>>1)==413:return{ra}         # (or, so mr, writes rA)
        return{rd} if xo&511 in{266,40,8,10,138,136,200,202,232,234,104,235,233,491,459,489,457,73,9,75,11} or xo in{23,87,279,343,21,341,19,83,371,339} else{rd,ra}
    if op in(2,3,4,5,6,10,11,36,38,44,47,48,50,52,54,59,63):return set()   # traps, vector, compares, stores, FP
    return{rd,ra}                                           # anything else (sc, 56/57/60/61...): forget both fields
def code_references():
    """Image addresses formed by lis-paired instructions: {target: [(instruction, function start)]}, words scanned.
    A lis value is forgotten once its register is written by anything (gpr_writes) and at every branch."""
    refs=collections.defaultdict(list);scanned=0
    dform=set(range(32,56))|{58,62}
    for start,end in functions:
        high={}
        for at in range(start,end,4):
            w=word(at);op=w>>26;rd=(w>>21)&31;ra=(w>>16)&31;imm=w&0xffff;simm=imm-0x10000 if imm&0x8000 else imm;scanned+=1
            target=None
            if op==15 and ra==0:high[rd]=(imm<<16)&0xffffffff;continue
            # addi and the D/DS-form loads and stores read a base field of 0 as the literal 0, not r0 (li r3, 0x10 after
            # lis r0 is no reference; 2026-10-31); addic and addic. read r0 itself, and ori's rS is a register
            if op==14 and ra and ra in high:target=(high[ra]+simm)&0xffffffff
            elif op in(12,13) and ra in high:target=(high[ra]+simm)&0xffffffff
            elif op==24 and rd in high:target=high[rd]|imm               # ori rA, rS, imm: rS is the rd field
            elif op in dform and ra and ra in high:target=(high[ra]+(simm&~3 if op in(58,62) else simm))&0xffffffff
            if target is not None and BASE<=target<END:refs[target].append((at,start))
            for r in gpr_writes(w):high.pop(r,None)
            if op in(16,17,18,19):high.clear()
    return refs,scanned
