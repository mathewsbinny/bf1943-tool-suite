"""Bounded lvx128/stvx128 candidate inventory within known BF1943 function ranges."""
from pathlib import Path
import hashlib,json,struct,csv,collections
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
data=(ROOT/'work/xex-analysis/default.checked.memory.bin').read_bytes()
assert hashlib.sha256(data).hexdigest()=='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
rows=[]; other=0
with (ROOT/'work/xex-analysis/functions.tsv').open() as f:
    for fun in csv.DictReader(f,delimiter='\t'):
        start,end=int(fun['start'],16),int(fun['end'],16)
        for address in range(start,end,4):
            word=struct.unpack_from('>I',data,address-0x82000000)[0]
            opcode=word&0xfc0007f3
            if opcode not in (0x100000c3,0x100001c3):
                other+=word>>26==4
                continue
            rows.append(dict(address=f'{address:08x}',function=f'{start:08x}',word=f'{word:08x}',
                mnemonic='lvx128' if opcode==0x100000c3 else 'stvx128',
                vector=((word>>21)&31)|((word<<3)&96),ra=(word>>16)&31,rb=(word>>11)&31))
with (ROOT/'work/vmx128/inventory.tsv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader();writer.writerows(rows)
report=dict(image_sha256=hashlib.sha256(data).hexdigest(),counts=dict(collections.Counter(r['mnemonic'] for r in rows)),
    containing_functions=len({r['function'] for r in rows}),other_primary_opcode4_words=other,
    scope='Two supported opcodes only, aligned scan of mapped function bodies. May include embedded data; not execution or full VMX128 coverage.',
    inventory_sha256=hashlib.sha256((ROOT/'work/vmx128/inventory.tsv').read_bytes()).hexdigest())
(ROOT/'research/evidence/vmx128-inventory.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
