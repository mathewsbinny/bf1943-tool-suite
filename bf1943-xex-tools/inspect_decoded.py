"""Record decoded-image provenance, PE layout, and bounded ASCII leads."""
import hashlib
import json
from pathlib import Path
import re
import struct

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402

LEAD_TERMS = re.compile(r'kynapse|mapbuilder|pathfind|pathdata|navmesh|bot|AiSystem', re.I)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = research_root()              # the research workspace (workspace.py)
    source = root / 'work/extracted/default.xex'
    image = root / 'work/xex-analysis/default.checked.memory.bin'
    original = root / 'work/xex-analysis/default.memory.bin'
    data = image.read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    assert data[:2] == b'MZ' and data[pe:pe+4] == b'PE\0\0'
    machine, count = struct.unpack_from('<HH', data, pe+4)
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    sections = []
    for i in range(count):
        offset = pe + 24 + optional_size + i * 40
        name, size, rva, raw_size, raw_offset = struct.unpack_from('<8sIIII', data, offset)
        sections.append(dict(name=name.rstrip(b'\0').decode(), virtual_size=size,
                             rva=hex(rva), raw_size=raw_size, raw_offset=hex(raw_offset),
                             within_image=rva+size <= len(data)))
    leads = [dict(offset=run.start(), address=hex(0x82000000 + run.start()), text=run.group().decode())
             for run in re.finditer(rb'[\x20-\x7e]{5,}', data) if LEAD_TERMS.search(run.group().decode())]
    loader = root / 'work/tools/XEXLoaderWV'
    tool_files = [path for path in sorted(Path(__file__).parent.glob('*')) if path.is_file()]
    report = dict(source_sha256=digest(source), image_sha256=digest(image),
        image_bytes=len(data), original_decode_identical=data == original.read_bytes(),
        loader_repository='https://github.com/zeroKilo/XEXLoaderWV',
        loader_commit='4bcf58da4caa5260644fc19f5eef5ccde3b7ff46',
        loader_java_hashes={str(path.relative_to(loader)): digest(path) for path in sorted(loader.rglob('*.java'))},
        local_tool_hashes={path.name: digest(path) for path in tool_files},
        compressed_block_sha1_verified=80, pe_machine=hex(machine), sections=sections,
        base='0x82000000', entry='0x827d78b0', ascii_leads=leads,
        limitations=['Block integrity is not signature authentication.',
          'XEX page digests: all 219 pages verified by verify_xex_image.py (each digest covers the next page and its descriptor; research/evidence/xex-image-integrity.json). The earlier naive same-page comparison matched 0/219.',
          '.reloc: within_image is false because the XEX image ends at the page after .XBLD; the 65,024 mapped .reloc bytes are zero (verify_xex_image.py).',
          'Raw memory image import does not yet model PE section permissions, imports, relocations or VMX128.',
          'ASCII hits are leads, not implemented-feature evidence.'])
    output = root / 'research/evidence/decoded-xex-pilot.json'
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({key: report[key] for key in ('image_sha256', 'original_decode_identical', 'pe_machine')}))
    print(f'{len(sections)} sections; {len(leads)} ASCII leads; {output}')


if __name__ == '__main__':
    main()
