"""Decode PE section layout and Xbox runtime-function records without guessing gaps."""
from pathlib import Path
import hashlib
import json
import struct
import sys
sys.dont_write_bytecode = True
import xenonrecomp_reference
from resolve_helpers import verified_blocks

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
def sha(data): return hashlib.sha256(data).hexdigest()

def main():
    image = ROOT/'work/xex-analysis/default.checked.memory.bin'
    data = image.read_bytes()
    helpers = verified_blocks(data)
    assert sha(data) == 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
    pe = struct.unpack_from('<I', data, 60)[0]
    count = struct.unpack_from('<H', data, pe+6)[0]
    optsize = struct.unpack_from('<H', data, pe+20)[0]
    base = struct.unpack_from('<I', data, pe+24+28)[0]
    sections = []
    for i in range(count):
        off = pe+24+optsize+40*i
        name, size, rva, rawsize, rawoff = struct.unpack_from('<8sIIII', data, off)
        flags = struct.unpack_from('<I', data, off+36)[0]
        sections.append(dict(name=name.rstrip(b'\0').decode(), rva=rva, start=base+rva,
            size=size, end=base+rva+size, characteristics=flags,
            read=bool(flags&0x40000000),write=bool(flags&0x80000000),execute=bool(flags&0x20000000),
            available_size=max(0,min(size,len(data)-rva)), raw_offset=rawoff,raw_size=rawsize))
    pdata = next(s for s in sections if s['name']=='.pdata')
    assert pdata['size']%8 == 0 and pdata['size']==pdata['available_size']
    exception_rva, exception_size = struct.unpack_from('<II',data,pe+24+96+3*8)
    assert (exception_rva,exception_size)==(pdata['rva'],pdata['size'])
    rows=[]
    for off in range(pdata['rva'],pdata['rva']+pdata['size'],8):
        start,word=struct.unpack_from('>II',data,off)
        length=((word>>8)&0x3fffff)*4
        prolog=(word&255)*4
        section=next((s for s in sections if s['execute'] and s['start']<=start and start+length<=s['start']+s['available_size']),None)
        errors=[]
        if start%4 or not length: errors.append('alignment/length')
        matching_helpers=[h for h in helpers if start<=h['start'] and h['end']<=start+length]
        helper_verified=(matching_helpers and matching_helpers[0]['start']==start and matching_helpers[-1]['end']==start+length
                         and sum(h['end']-h['start'] for h in matching_helpers)==length and prolog==length)
        if not word&0x40000000 and not helper_verified: errors.append('unresolved bit30-clear record')
        if prolog>length: errors.append('prologue exceeds function')
        if section is None: errors.append('outside available executable section')
        rows.append(dict(record_offset=off,start=start,end=start+length,length=length,prolog_bytes=prolog,
                         exception_flag=bool(word&0x80000000),packed_word=word,section=section['name'] if section else None,
                         helper_blocks=[dict(start=h['start'],end=h['end'],name=h['name']) for h in matching_helpers] if helper_verified else [],errors=errors))
    ordered=sorted(rows,key=lambda r:r['start'])
    overlaps=[(a['start'],b['start']) for a,b in zip(ordered,ordered[1:]) if a['end']>b['start']]
    print('Overlaps:', overlaps[:8]); print('Invalid:', [r for r in rows if r['errors']][:8])
    member,content=xenonrecomp_reference.read('/XenonUtils/xbox.h')
    reference_sha=sha(content)
    report=dict(image_sha256=sha(data),parser_sha256=sha(Path(__file__).read_bytes()),
        helper_verifier_sha256=sha(Path(__file__).with_name('resolve_helpers.py').read_bytes()),base=base,
        reference=dict(zip_sha256=xenonrecomp_reference.ARCHIVE_SHA256,source=xenonrecomp_reference.source(),member=member,member_sha256=reference_sha),
        exception_directory=dict(rva=exception_rva,size=exception_size),sections=sections,functions=rows,
        summary=dict(records=len(rows),valid=sum(not r['errors'] for r in rows),overlaps=len(overlaps),sorted=rows==ordered,
                     covered_bytes=sum(r['length'] for r in rows),entry_in_pdata=any(r['start']==0x827d78b0 for r in rows)),
        limitations=['Runtime-function records are not an exhaustive list of leaf functions.',
                    'Declared ranges may contain embedded data or unsupported instructions.',
                    'Only available bytes are mapped; out-of-image section tails are not synthesized.'])
    (ROOT/'research/evidence/xex-functions.json').write_text(json.dumps(report,indent=2)+'\n')
    with (ROOT/'work/xex-analysis/functions.tsv').open('w') as f:
        f.write('start\tend\tprolog_bytes\texception_flag\n')
        for r in rows:
            if r['errors']: continue
            if r['helper_blocks']:
                for h in r['helper_blocks']: f.write(f"{h['start']:08x}\t{h['end']:08x}\t0\t0\t{h['name']}\n")
                continue
            f.write(f"{r['start']:08x}\t{r['end']:08x}\t{r['prolog_bytes']}\t{int(r['exception_flag'])}\n")
    with (ROOT/'work/xex-analysis/sections.tsv').open('w') as f:
        f.write('name\tstart\tsize\tread\twrite\texecute\n')
        for s in sections:
            if s['available_size']:f.write(f"{s['name']}\t{s['start']:08x}\t{s['available_size']}\t{int(s['read'])}\t{int(s['write'])}\t{int(s['execute'])}\n")
    print(json.dumps(report['summary']))

if __name__=='__main__':main()
