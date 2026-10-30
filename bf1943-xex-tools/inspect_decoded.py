"""Record decoded-image provenance, PE layout, and bounded ASCII leads."""
import hashlib
import json
from pathlib import Path
import re
import struct

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    source = ROOT / 'work/extracted/default.xex'
    image = ROOT / 'work/xex-analysis/default.checked.memory.bin'
    original = ROOT / 'work/xex-analysis/default.memory.bin'
    data = image.read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    assert data[:2] == b'MZ' and data[pe:pe+4] == b'PE\0\0'
    machine, count = struct.unpack_from('<HH', data, pe+4)
    optsize = struct.unpack_from('<H', data, pe+20)[0]
    sections = []
    for i in range(count):
        off = pe+24+optsize+i*40
        name, size, rva, rawsize, rawoff = struct.unpack_from('<8sIIII', data, off)
        sections.append(dict(name=name.rstrip(b'\0').decode(), virtual_size=size,
                             rva=hex(rva), raw_size=rawsize, raw_offset=hex(rawoff),
                             within_image=rva+size <= len(data)))
    terms = re.compile(r'kynapse|mapbuilder|pathfind|pathdata|navmesh|bot|AiSystem', re.I)
    leads = [dict(offset=m.start(), address=hex(0x82000000+m.start()), text=m.group().decode())
             for m in re.finditer(rb'[\x20-\x7e]{5,}', data) if terms.search(m.group().decode())]
    loader = ROOT / 'work/tools/XEXLoaderWV'
    report = dict(source_sha256=digest(source), image_sha256=digest(image),
        image_bytes=len(data), original_decode_identical=data == original.read_bytes(),
        loader_repository='https://github.com/zeroKilo/XEXLoaderWV',
        loader_commit='4bcf58da4caa5260644fc19f5eef5ccde3b7ff46',
        loader_java_hashes={str(p.relative_to(loader)):digest(p) for p in sorted(loader.rglob('*.java'))},
        local_tool_hashes={p.name:digest(p) for p in sorted(Path(__file__).parent.glob('*')) if p.is_file()},
        compressed_block_sha1_verified=80, pe_machine=hex(machine), sections=sections,
        base='0x82000000', entry='0x827d78b0', ascii_leads=leads,
        limitations=['Block integrity is not signature authentication.',
          'XEX page digests: all 219 pages verified by verify_xex_image.py (each digest covers the next page and its descriptor; research/evidence/xex-image-integrity.json). The earlier naive same-page comparison matched 0/219.',
          '.reloc: within_image is false because the XEX image ends at the page after .XBLD; the 65,024 mapped .reloc bytes are zero (verify_xex_image.py).',
          'Raw memory image import does not yet model PE section permissions, imports, relocations or VMX128.',
          'ASCII hits are leads, not implemented-feature evidence.'])
    output = ROOT / 'research/evidence/decoded-xex-pilot.json'
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({k:report[k] for k in ('image_sha256','original_decode_identical','pe_machine')}))
    print(f'{len(sections)} sections; {len(leads)} ASCII leads; {output}')

if __name__ == '__main__':
    main()
