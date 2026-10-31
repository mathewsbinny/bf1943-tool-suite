"""Classify explicit VX128-table encodings; preserve ambiguous/unknown candidates."""
import collections
import csv
import hashlib
import json
import re
import struct
import sys
sys.dont_write_bytecode = True
import xenonrecomp_reference  # noqa: E402
from workspace import research_root  # noqa: E402

IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
# the extended-opcode bits each VX128 form compares (ppc-dis.c's VX128* masks, below the primary opcode)
FORM_MASKS = {'VX128': 0x3d0, 'VX128_1': 0x7f3, 'VX128_2': 0x210, 'VX128_3': 0x7f0, 'VX128_P': 0x630, 'VX128_4': 0x730,
              'VX128_5': 0x10}


def vx128_table(ppc_dis):
    """[(mnemonic, mask, value)] for every VX128 entry of ppc-dis.c's opcode table."""
    table = []
    for line in ppc_dis.decode().splitlines():
        match = re.match(r'^\{ "([^"]+)", (VX128\w*)\((\d+), (\d+)\),', line)
        if match:
            name, form, op, xop = match.groups()
            mask = 0xfc000000 | FORM_MASKS[form]
            table.append((name, mask, (int(op) << 26) | (int(xop) & FORM_MASKS[form])))
    return table


def main():
    root = research_root()              # the research workspace (workspace.py)
    data = (root / 'work/xex-analysis/default.checked.memory.bin').read_bytes()
    assert hashlib.sha256(data).hexdigest() == IMAGE_SHA256
    member, ppc_dis = xenonrecomp_reference.read('/ppc-dis.c', root)
    table = vx128_table(ppc_dis)
    counts = collections.Counter()
    ambiguous = 0
    unknown = 0
    functions = collections.Counter()
    with (root / 'work/vmx128/classified.tsv').open('w', encoding='utf-8', newline='') as out:
        # CRLF, as recorded: vmx_inputs.INPUTS and evidence pin these bytes
        writer = csv.writer(out, delimiter='\t', lineterminator='\r\n')
        writer.writerow(['address', 'function', 'word', 'matches'])
        for function in csv.DictReader((root / 'work/xex-analysis/functions.tsv').open(), delimiter='\t'):
            for at in range(int(function['start'], 16), int(function['end'], 16), 4):
                word = struct.unpack_from('>I', data, at - 0x82000000)[0]
                if word >> 26 not in (4, 5, 6):
                    continue
                names = [name for name, mask, value in table if word & mask == value]
                if not names:
                    unknown += 1
                    continue
                if len(names) > 1:
                    ambiguous += 1
                else:
                    counts[names[0]] += 1
                functions[function['start']] += 1
                writer.writerow([f'{at:08x}', function['start'], f'{word:08x}', ','.join(names)])
    report = dict(reference_member=member, reference_sha256=hashlib.sha256(ppc_dis).hexdigest(), definitions=len(table),
                  counts=dict(counts.most_common()), ambiguous=ambiguous, unmatched_primary_4_5_6=unknown,
                  functions=len(functions),
                  limitations=['Mask candidates in declared function ranges; embedded data and reserved operand combinations are not excluded.',
                               'Only explicit VX128 table entries; unmatched words may be standard AltiVec, data or unsupported instructions.'])
    (root / 'research/evidence/vmx128-classification.json').write_text(json.dumps(report, indent=2) + '\n',
                                                                       encoding='utf-8', newline='\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
