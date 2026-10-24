"""Verify entire BF1943 register helper bodies, not merely their first instruction."""
from pathlib import Path
import hashlib, json, struct
import sys
sys.dont_write_bytecode = True
import xenonrecomp_reference
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
IMAGE_HASH='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'

def verified_blocks(data):
    if hashlib.sha256(data).hexdigest()!=IMAGE_HASH: raise ValueError('Unexpected image')
    blocks=[]
    for start,kind,restore in [(0x827be800,'gprlr',False),(0x827be850,'gprlr',True),
        (0x827beed0,'fpr',False),(0x827bef1c,'fpr',True),
        (0x827bef70,'vmx',False),(0x827bf208,'vmx',True)]:
        for first,last in ([(14,31),(64,127)] if kind=='vmx' else [(14,31)]):
            words=[]
            for reg in range(first,last+1):
                if kind=='gprlr': words.append(((58 if restore else 62)<<26)|(reg<<21)|(1<<16)|((-152+(reg-14)*8)&65535))
                elif kind=='fpr': words.append(((50 if restore else 54)<<26)|(reg<<21)|(12<<16)|((-144+(reg-14)*8)&65535))
                else:
                    words.append(0x39600000|((-(last-first+1)*16+(reg-first)*16)&65535))
                    if first==14: words.append(0x7c0b6000|(reg<<21)|(0xce if restore else 0x1ce))
                    else: words.append(0x100b6000|((reg&31)<<21)|(0xcb if restore else 0x1cb)|((reg&32)>>3))
            if kind=='gprlr': words += [0x8181fff8,0x7d8803a6] if restore else [0x9181fff8]
            words.append(0x4e800020)
            expected=struct.pack('>'+str(len(words))+'I',*words)
            actual=data[start-0x82000000:start-0x82000000+len(expected)]
            if actual!=expected: raise ValueError(f'Full helper mismatch {start:x}')
            prefix=('__rest' if restore else '__save')+kind
            entries=[dict(address=start+(r-first)*(8 if kind=='vmx' else 4),name=f'{prefix}_{r}',register=r) for r in range(first,last+1)]
            blocks.append(dict(start=start,end=start+len(expected),name=f'{prefix}_{first}',kind=kind,
                first_register=first,last_register=last,restore=restore,sha256=hashlib.sha256(actual).hexdigest(),entries=entries))
            start+=len(expected)
    return blocks

def main():
    data=(ROOT/'work/xex-analysis/default.checked.memory.bin').read_bytes(); blocks=verified_blocks(data)
    old=json.loads((ROOT/'research/evidence/xex-functions.json').read_text())
    targets={e['address']:e for b in blocks for e in b['entries']}
    calls=[]
    for section in old['sections']:
        if not section['execute']: continue
        for at in range(section['start'],section['start']+section['available_size']-3,4):
            word=struct.unpack_from('>I',data,at-0x82000000)[0]
            if word>>26!=18: continue
            disp=word&0x3fffffc
            if disp&0x2000000: disp-=0x4000000
            dest=(disp if word&2 else at+disp)&0xffffffff
            if dest in targets: calls.append(dict(address=at,target=dest,link=bool(word&1),name=targets[dest]['name']))
    for entry in targets.values():
        matches=[c for c in calls if c['target']==entry['address']]
        entry['branch_candidates']=len(matches);entry['examples']=matches[:4]
    refs={}
    for suffix in ('/README.md','/XenonRecomp/recompiler.cpp','/XenonUtils/xbox.h','/thirdparty/disasm/ppc-dis.c'):
        name,content=xenonrecomp_reference.read(suffix);refs[name]=hashlib.sha256(content).hexdigest()
    result=dict(image_sha256=IMAGE_HASH,parser_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        reference_archive_sha256=xenonrecomp_reference.ARCHIVE_SHA256,reference_source=xenonrecomp_reference.source(),reference_members=refs,blocks=blocks,
        summary=dict(resolved_pdata_records=6,helper_blocks=len(blocks),suffix_entries=len(targets),direct_branch_candidates=len(calls)),
        limitations=['Helper identities are analyst-assigned conventional names, not recovered symbols.',
        'Bit 30/31 Xbox unwind semantics remain unresolved; full-body verification overrides only these exact records.',
        'Branch scan is static aligned opcode matching; candidate counts are not observed execution or complete xrefs.',
        'VMX128 words are validated by explicit encoding patterns, not proof of Ghidra semantic support.'])
    (ROOT/'research/evidence/xex-helper-resolution.json').write_text(json.dumps(result,indent=2)+'\n')
    with (ROOT/'work/xex-analysis/helpers.tsv').open('w') as f:
        f.write('start\tend\tname\n')
        for b in blocks:f.write(f"{b['start']:08x}\t{b['end']:08x}\t{b['name']}\n")
    with (ROOT/'work/xex-analysis/helper-entries.tsv').open('w') as f:
        f.write('address\tname\n')
        for e in targets.values():f.write(f"{e['address']:08x}\t{e['name']}\n")
    with (ROOT/'work/xex-analysis/helper-branches.tsv').open('w') as f:
        f.write('address\ttarget\tlink\tname\n')
        for c in calls:f.write(f"{c['address']:08x}\t{c['target']:08x}\t{int(c['link'])}\t{c['name']}\n")
    print(result['summary'])

if __name__=='__main__':main()
