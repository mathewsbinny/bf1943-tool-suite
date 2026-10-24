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
CODE_PATTERN='lis/addis rX,0,hi paired within one .pdata range with addi/addic/subfic, ori, or D-form displacement on rX; pairing resets at branches'
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
def code_references():
    """Image addresses formed by lis-paired instructions: {target: [(instruction, function start)]}, words scanned."""
    refs=collections.defaultdict(list);scanned=0
    dform=set(range(32,56))|{58,62}
    for start,end in functions:
        high={}
        for at in range(start,end,4):
            w=word(at);op=w>>26;rd=(w>>21)&31;ra=(w>>16)&31;imm=w&0xffff;simm=imm-0x10000 if imm&0x8000 else imm;scanned+=1
            if op==15 and ra==0:high[rd]=(imm<<16)&0xffffffff;continue
            target=None
            if op in(14,12,13) and ra in high:target=(high[ra]+simm)&0xffffffff
            elif op==24 and rd in high:target=high[rd]|imm;ra=rd;rd=(w>>16)&31
            elif op in dform and ra in high:target=(high[ra]+(simm&~3 if op in(58,62) else simm))&0xffffffff
            if target is not None and BASE<=target<END:refs[target].append((at,start))
            if op in(14,15,24,12,13) or 32<=op<=35 or op in(40,41,42,43,58):high.pop(rd if op!=24 else (w>>16)&31,None)
            if op in(16,18,19):high.clear()
    return refs,scanned
