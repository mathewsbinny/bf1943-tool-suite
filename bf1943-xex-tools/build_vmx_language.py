"""Build an isolated Xenon language: all VX128-table encodings decode; unvalidated semantics stay opaque."""
from pathlib import Path
import hashlib,json,shutil
import sys
sys.dont_write_bytecode = True
import xenonrecomp_reference
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
SOURCE=Path('C:/Tools/ghidra_12.1.4_PUBLIC/Ghidra/Processors/PowerPC/data/languages')
MODULE=ROOT/'work/vmx128/XenonVMX128'
dest=MODULE/'data/languages';dest.mkdir(parents=True,exist_ok=True)
hashes={}
for p in SOURCE.iterdir():
    if p.suffix in ('.sinc','.pspec','.cspec'):
        shutil.copyfile(p,dest/p.name); hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
(MODULE/'Module.manifest').write_text('MODULE FILE LICENSE: Apache License 2.0\n')
common=dest/'ppc_common.sinc'
import re
text=common.read_text()
text,n=re.subn(r'(define token\s+\w+\(32\))',r'\1\n xmhi=(2,3) xmop=(0,1) xmmid=(4,10) xma5=(5,5) xma6=(10,10) xmlogic=(6,9) xmbit4=(4,4) xmphi=(6,8) xmfixed=(9,10) xmc=(6,8) xmshb=(6,9) xm3d0=(18,20) xm3d1=(16,17) xm3d2=(6,7) xm20=(20,20) xmimm4=(16,19) '+' '.join(f'xk{i}=({i},{i})' for i in range(11)),text,count=1)
assert n==1
common.write_text(text)
altivec=dest/'altivec.sinc'
text=altivec.read_text()
# ISA 2.07/3.0 vector instructions postdate Xenon and capture VMX128 words (vcmpequd, vcmpne*, vpermr, ...).
# Keep only the original AltiVec section; this subsumes the earlier vcmpequd-only removal.
marker='#################\n# 2.07 additions\n'
assert text.count(marker)==1
text=text[:text.index(marker)]
altivec.write_text(text)
# Stock non-ISA PowerPC names the XO=21 load (ldx) "ldarx" and has no XO=84 ldarx, so real ldarx words fail to
# decode (12 in BF1943). Rename and add ldarx, leaving the reservation unmodeled as the stock lwarx does.
instructions=dest/'ppc_instructions.sinc'
text=instructions.read_text()
wrong=':ldarx D,RA_OR_ZERO,B\tis OP=31 & D & RA_OR_ZERO & B & XOP_1_10=21 & BIT_0=0\n{\n\tea:$(REGISTER_SIZE) = RA_OR_ZERO+B;\n\tD = *:8(ea);\n}\n'
assert text.count(wrong)==1
text=text.replace(wrong,wrong.replace(':ldarx',':ldx',1)+':ldarx D,RA_OR_ZERO,B\tis OP=31 & D & RA_OR_ZERO & B & XOP_1_10=84 & BIT_0=0\n{\n\tea:$(REGISTER_SIZE) = RA_OR_ZERO+B;\n\t#RESERVE = 1;\n\t#RESERVE_ADDRSS:$(REGISTER_SIZE) = ea;\n\tD = *:8(ea);\n}\n')
instructions.write_text(text)
(dest/'xenon.ldefs').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<language_definitions><language processor="PowerPC" endian="big" size="32" variant="XenonVMX128" version="1.0" slafile="xenon.sla" processorspec="ppc_64.pspec" id="PowerPC:BE:64:XenonVMX128">
<description>Experimental Xenon: full VX128 decoding; validated subset semantics, others opaque</description><truncate_space space="ram" size="4"/><compiler name="default" spec="ppc_64_32.cspec" id="default"/>
</language></language_definitions>''')
spec='''@define ENDIAN "big"
@define REGISTER_SIZE "8"
@define BIT_64 "64"
@define EATRUNC "ea:4"
@define NoLegacyIntegerMultiplyAccumulate ""
@include "ppc_common.sinc"
@include "altivec.sinc"
@include "g2.sinc"
define register offset=0x100000 size=16 [ '''+' '.join(f'v{i}' for i in range(32,128))+''' ];
'''
for i in range(128):
    reg=f'vs{i+32}' if i<32 else f'v{i}'
    spec+=f'XV: "v{i}" is vrDR={i&31} & xmhi={i>>5} {{ export {reg}; }}\n'
    spec+=f'XA: "v{i}" is A_BITS={i&31} & xma5={(i>>5)&1} & xma6={i>>6} {{ export {reg}; }}\n'
    spec+=f'XB: "v{i}" is BITS_11_15={i&31} & xmop={i>>5} {{ export {reg}; }}\n'
spec+='''
:lvx128 XV,RA_OR_ZERO,B is OP=4 & XV & RA_OR_ZERO & B & xmop=3 & xmmid=12 {
    local ea:8 = (RA_OR_ZERO + B) & 0x00000000fffffff0;
    XV = *[ram]:16 ea;
}
:stvx128 XV,RA_OR_ZERO,B is OP=4 & XV & RA_OR_ZERO & B & xmop=3 & xmmid=28 {
    local ea:8 = (RA_OR_ZERO + B) & 0x00000000fffffff0;
    *[ram]:16 ea = XV;
}
'''
for name,opcode,expression in [('vand128',528,'XA & XB'),('vandc128',592,'XA & ~XB'),('vor128',720,'XA | XB'),('vxor128',784,'XA ^ XB')]:
    spec+=f':{name} XV,XA,XB is OP=5 & XV & XA & XB & xmbit4=1 & xmlogic={opcode>>6} {{ XV = {expression}; }}\n'
spec+='''PIMM: imm is A_BITS & xmphi [ imm = A_BITS | (xmphi << 5); ] { export *[const]:1 imm; }
:vpermwi128 XV,XB,PIMM is OP=6 & XV & XB & PIMM & xmbit4=1 & xma5=0 & xmfixed=1 {
    local source:16 = XB;
    local result:16 = 0;
'''
for lane in range(4):
    spec+=f'''    local shift{lane}:1 = (3 - ((PIMM >> {6-lane*2}) & 3)) * 32;
    local temp{lane}:16 = source >> shift{lane};
    local word{lane}:4 = temp{lane}:4;
    result = (result << 32) | zext(word{lane});
'''
spec+='    XV = result;\n}\n'
# Every remaining VX128 table entry from the hashed reference disassembler decodes with its own field layout.
member,reference=xenonrecomp_reference.read('/ppc-dis.c')
masks={'VX128':0x3d0,'VX128_1':0x7f3,'VX128_2':0x210,'VX128_3':0x7f0,'VX128_P':0x630,'VX128_4':0x730,'VX128_5':0x10}
table=[]
for line in reference.decode().splitlines():
    m=re.match(r'^\{ "([^"]+)", (VX128\w*)\((\d+), (\d+)\), \w+, \w+, \{ ([^}]*) \}',line)
    if m:
        name,form,op,xop,operands=m.groups()
        table.append((name,form,int(op),int(xop)&masks[form],[o.strip() for o in operands.split(',')]))
assert len(table)==80
for i in range(8):spec+=f'XC: "v{i}" is xmc={i} {{ export vs{i+32}; }}\n'
def pattern(op,form,value):
    return f'OP={op} & '+' & '.join(f'xk{b}={(value>>b)&1}' for b in range(11) if masks[form]>>b&1)
# Field operands display as the reference prints them; immediates reach opaque ops as sized locals.
display={'VD128':'XV','VS128':'XV','VA128':'XA','VB128':'XB','VC128':'XC','RA':'RA_OR_ZERO','RB':'B','UIMM':'A_BITS','SIMM':'A_BITSS','SHB':'xmshb','VD3D0':'xm3d0','VD3D1':'xm3d1','VD3D2':'xm3d2','VPERM128':'PIMM'}
immediates={'A_BITS','A_BITSS','xmshb','xm3d0','xm3d1','xm3d2'}
# vpkd3d128 writes only part of VD in both references, so its old value is an input.
merges={'vpkd3d128'}
validated={'lvx128','stvx128','vand128','vandc128','vor128','vxor128','vpermwi128'}
opaque=[];pcodeops=[]
# Unaligned left/right loads and stores: byte shifts within the aligned block containing EA, per
# XenonRecomp recompiler.cpp 1148-1163/1610-1631 and Xenia ppc_emit_altivec.cc 205-335 (see VMX128_UNALIGNED_SEMANTICS.md).
# lvrx/stvrx touch no memory when EA is 16-byte aligned; stores are modeled as read-modify-write of the block.
def unaligned(name):return re.fullmatch(r'(lv|stv)[lr]xl?(128)?',name) is not None
def unaligned_body(name,register):
    right=name[2 if name.startswith('lv') else 3]=='r'
    body=['local ea:8 = RA_OR_ZERO + B;','local block_ea:8 = ea & 0x00000000fffffff0;','local offset:4 = zext(ea:1 & 15);']
    if right:body.append(f'if (offset != 0) goto <access>;')
    if name.startswith('lv'):
        if right:body+=[f'{register} = 0;','goto inst_next;','<access>',f'{register} = *[ram]:16 block_ea >> ((16 - offset) * 8);']
        else:body.append(f'{register} = *[ram]:16 block_ea << (offset * 8);')
    else:
        if right:body+=['goto inst_next;','<access>']
        body+=['local ones:16 = 0;','ones = ~ones;','local tail:16 = ones >> (offset * 8);','local old:16 = *[ram]:16 block_ea;']
        if right:body.append(f'*[ram]:16 block_ea = (old & tail) | (({register} << ((16 - offset) * 8)) & ~tail);')
        else:body.append(f'*[ram]:16 block_ea = (old & ~tail) | (({register} >> (offset * 8)) & tail);')
    return body
def opaque_constructor(name,form,op,value,operands,extra='',pcodeop=None):
    pcodeop=pcodeop or 'VMX128_unimplemented_'+name.replace('.','_rc')
    fields=[display[o] for o in operands];shown=list(dict.fromkeys(fields))
    body=[]
    if operands[1:]==['RA','RB']:
        body.append('local ea:8 = RA_OR_ZERO + B;')
        body.append(f'{pcodeop}(ea, XV);' if name.startswith('stv') else f'XV = {pcodeop}(ea);')
    else:
        args=[]
        for f in fields[1:]+(['XV'] if name in merges else []):
            if f in immediates:
                body.append(f'local {f}_value:1 = {f};');f+='_value'
            args.append(f)
        body.append(f'XV = {pcodeop}({", ".join(args)});')
        if name.endswith('.'):
            body.append(f'cr6 = {pcodeop}_cr6({", ".join(args)});');pcodeops.append(pcodeop+'_cr6')
    pcodeops.append(pcodeop);opaque.append(dict(mnemonic=name,pcodeop=pcodeop,operands=operands))
    return f':{name} {",".join(shown)} is {pattern(op,form,value)} & {" & ".join(dict.fromkeys(fields))}{extra} {{\n    '+'\n    '.join(body)+'\n}\n'
generated=''
for name,form,op,value,operands in table:
    if name in validated:continue
    if unaligned(name):
        body='\n    '.join(unaligned_body(name,'XV'))
        generated+=f':{name} XV,RA_OR_ZERO,B is {pattern(op,form,value)} & XV & RA_OR_ZERO & B {{\n    {body}\n}}\n'
        continue
    if name=='vrlimi128':
        # Guest lane 0 is most significant: R[g]=B[(g+z)%4]; lane g takes R[g] when mask bit (3-g) is set.
        for mask in range(16):
            for z in range(4):
                generated+=f':vrlimi128 XV,XB,{mask:#x},{z} is {pattern(op,form,value)} & XV & XB & xm20=0 & xmimm4={mask} & xm3d2={z} {{\n    local source:16 = XB;\n    local destination:16 = XV;\n    local result:16 = 0;\n'
                for lane in range(4):
                    taken=mask>>(3-lane)&1
                    generated+=f'    local word{lane}:4 = {"source" if taken else "destination"}({(3-((lane+z)%4 if taken else lane))*4});\n    result = (result << 32) | zext(word{lane});\n'
                generated+='    XV = result;\n}\n'
        # Neither reference interprets UIMM bit 4; such words decode but stay opaque.
        generated+=opaque_constructor(name,form,op,value,operands,' & xm20=1',pcodeop='VMX128_unresolved_vrlimi128_imm4')
        continue
    generated+=opaque_constructor(name,form,op,value,operands)
# Xenon's VR0-31 unaligned loads/stores are X-form opcode 31 (reference dialect CELL|PPCVEC128), absent from AltiVec.
xform=[]
for line in reference.decode().splitlines():
    m=re.match(r'^\{ "([^"]+)",\s+X\(31, (\d+)\), X_MASK,\s+CELL \| PPCVEC128,',line)
    if m:xform.append((m.group(1),int(m.group(2))))
assert len(xform)==8
for name,xop in xform:
    register='vrS' if name.startswith('stv') else 'vrD'
    assert unaligned(name)
    body='\n    '.join(unaligned_body(name,register))
    generated+=f':{name} {register},RA_OR_ZERO,B is OP=31 & {register} & RA_OR_ZERO & B & XOP_1_10={xop} & BIT_0=0 {{\n    {body}\n}}\n'
spec+=''.join(f'define pcodeop {p};\n' for p in dict.fromkeys(pcodeops))+generated
(dest/'xenon.slaspec').write_text(spec)
with (ROOT/'work/vmx128/vx128-table.tsv').open('w') as out:
    out.write('mnemonic\tform\tmask\tvalue\tsemantics\n')
    for name,form,op,value,operands in table:
        semantics='validated' if name in validated or unaligned(name) else 'vrlimi128_imm4_clear_validated' if name=='vrlimi128' else 'opaque'
        out.write(f'{name}\t{form}\t{0xfc000000|masks[form]:08x}\t{(op<<26)|value:08x}\t{semantics}\n')
    for name,xop in xform:out.write(f'{name}\tX\tfc0007ff\t{(31<<26)|(xop<<1):08x}\tvalidated\n')
(ROOT/'research/evidence/vmx128-language-inputs.json').write_text(json.dumps(dict(base_sources=hashes,spec_sha256=hashlib.sha256(spec.encode()).hexdigest(),
    reference_archive_sha256=xenonrecomp_reference.ARCHIVE_SHA256,reference_source=xenonrecomp_reference.source(),reference_member=member,reference_member_sha256=hashlib.sha256(reference).hexdigest(),
    table_entries=len(table),xform_entries=[n for n,_ in xform],validated=sorted(validated|{'vrlimi128'}|{n for n,*_ in table if unaligned(n)}|{n for n,_ in xform}),opaque=opaque,
    removed_inherited=['altivec.sinc ISA 2.07 and 3.0 sections (everything from the "2.07 additions" marker)','PowerPC 4xx integer multiply-accumulate family via NoLegacyIntegerMultiplyAccumulate'],
    unaligned_semantics=['lvlx/lvrx/stvlx/stvrx (+l, +128): byte shifts within the aligned 16-byte block at ea&~15 (XenonRecomp recompiler.cpp 1148-1163, 1610-1631; Xenia ppc_emit_altivec.cc 205-335)',
        'lvrx/stvrx access no memory when ea&15==0; stores are modeled as read-modify-write of the block'],
    corrected_inherited=['XO=21 constructor renamed ldarx -> ldx (semantics unchanged)','XO=84 ldarx added: 8-byte load, reservation unmodeled like stock lwarx'],
    limitations=['Opaque CALLOTHER results have unknown value; they are not approximations.','Remaining opaque stores/loads (lvewx128, stvewx128, lvsl128, lvsr128, lvxl128, stvxl128) do not model their memory effects.',
        'VSCR saturation and other status effects are not modeled for any opaque operation.','vrlimi128 with UIMM bit 4 set is decoded but opaque.']),indent=2)+'\n')
print(dest/'xenon.slaspec')

