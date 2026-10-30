"""Classify explicit VX128-table encodings; preserve ambiguous/unknown candidates."""
from pathlib import Path
import csv,struct,re,json,hashlib,collections
import sys
sys.dont_write_bytecode = True
import xenonrecomp_reference
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
data=(ROOT/'work/xex-analysis/default.checked.memory.bin').read_bytes()
assert hashlib.sha256(data).hexdigest()=='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
member,source=xenonrecomp_reference.read('/ppc-dis.c')
masks={'VX128':0x3d0,'VX128_1':0x7f3,'VX128_2':0x210,'VX128_3':0x7f0,'VX128_P':0x630,'VX128_4':0x730,'VX128_5':0x10}
table=[]
for line in source.decode().splitlines():
    m=re.match(r'^\{ "([^"]+)", (VX128\w*)\((\d+), (\d+)\),',line)
    if m:
        name,form,op,xop=m.groups();mask=0xfc000000|masks[form]
        table.append((name,mask,(int(op)<<26)|(int(xop)&masks[form])))
counts=collections.Counter();ambiguous=0;unknown=0;functions=collections.Counter()
with (ROOT/'work/vmx128/classified.tsv').open('w',encoding='utf-8',newline='') as out:
    writer=csv.writer(out,delimiter='\t',lineterminator='\r\n');  # CRLF, as recorded: vmx_inputs.INPUTS and evidence pin these bytes
    writer.writerow(['address','function','word','matches'])
    for f in csv.DictReader((ROOT/'work/xex-analysis/functions.tsv').open(),delimiter='\t'):
        for at in range(int(f['start'],16),int(f['end'],16),4):
            word=struct.unpack_from('>I',data,at-0x82000000)[0]
            if word>>26 not in (4,5,6):continue
            names=[n for n,m,v in table if word&m==v]
            if not names:unknown+=1;continue
            if len(names)>1:ambiguous+=1
            else:counts[names[0]]+=1
            functions[f['start']]+=1;writer.writerow([f'{at:08x}',f['start'],f'{word:08x}',','.join(names)])
report=dict(reference_member=member,reference_sha256=hashlib.sha256(source).hexdigest(),definitions=len(table),counts=dict(counts.most_common()),ambiguous=ambiguous,unmatched_primary_4_5_6=unknown,functions=len(functions),
    limitations=['Mask candidates in declared function ranges; embedded data and reserved operand combinations are not excluded.','Only explicit VX128 table entries; unmatched words may be standard AltiVec, data or unsupported instructions.'])
(ROOT/'research/evidence/vmx128-classification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8', newline='\n')
print(json.dumps(report,indent=2))
