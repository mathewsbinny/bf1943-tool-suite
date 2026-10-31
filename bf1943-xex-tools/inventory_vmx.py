"""Bounded lvx128/stvx128 candidate inventory within known BF1943 function ranges."""
from pathlib import Path
import collections
import csv
import hashlib
import json
import struct
import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402

IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
VX128_1_MASK = 0xfc0007f3               # primary opcode and the VX128_1 extended-opcode bits
LVX128 = 0x100000c3
STVX128 = 0x100001c3


def main():
    root = research_root()              # the research workspace (workspace.py)
    data = (root / 'work/xex-analysis/default.checked.memory.bin').read_bytes()
    assert hashlib.sha256(data).hexdigest() == IMAGE_SHA256
    rows = []
    other = 0                           # other opcode-4 words in the function bodies
    with (root / 'work/xex-analysis/functions.tsv').open() as f:
        for function in csv.DictReader(f, delimiter='\t'):
            start, end = int(function['start'], 16), int(function['end'], 16)
            for address in range(start, end, 4):
                word = struct.unpack_from('>I', data, address - 0x82000000)[0]
                opcode = word & VX128_1_MASK
                if opcode not in (LVX128, STVX128):
                    other += word >> 26 == 4
                    continue
                # VD128: the VD field is the low five bits, the word's 0xc bits the high two
                vector = ((word >> 21) & 31) | ((word << 3) & 96)
                rows.append(dict(address=f'{address:08x}', function=f'{start:08x}', word=f'{word:08x}',
                                 mnemonic='lvx128' if opcode == LVX128 else 'stvx128',
                                 vector=vector, ra=(word >> 16) & 31, rb=(word >> 11) & 31))
    with (root / 'work/vmx128/inventory.tsv').open('w', encoding='utf-8', newline='') as f:
        # CRLF, as recorded: evidence pins these bytes
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\r\n')
        writer.writeheader()
        writer.writerows(rows)
    report = dict(image_sha256=hashlib.sha256(data).hexdigest(),
                  counts=dict(collections.Counter(row['mnemonic'] for row in rows)),
                  containing_functions=len({row['function'] for row in rows}), other_primary_opcode4_words=other,
                  scope='Two supported opcodes only, aligned scan of mapped function bodies. May include embedded data; not execution or full VMX128 coverage.',
                  inventory_sha256=hashlib.sha256((root / 'work/vmx128/inventory.tsv').read_bytes()).hexdigest())
    (root / 'research/evidence/vmx128-inventory.json').write_text(json.dumps(report, indent=2) + '\n',
                                                                  encoding='utf-8', newline='\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
