"""Verify entire BF1943 register helper bodies, not merely their first instruction.

  python -B resolve_helpers.py      (from the research workspace, or with BF1943_RESEARCH set)

Reads the checked image, research/evidence/xex-functions.json (map_functions.py) and XenonRecomp's reference files
(xenonrecomp_reference.py); writes research/evidence/xex-helper-resolution.json and work/xex-analysis/helpers.tsv,
helper-entries.tsv and helper-branches.tsv, all CRLF as recorded. map_functions.py imports verified_blocks.
"""
from pathlib import Path
from typing import Any
import hashlib
import json
import struct
import sys
sys.dont_write_bytecode = True
import xenonrecomp_reference  # noqa: E402
from workspace import research_root  # noqa: E402

IMAGE_HASH = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
BASE = 0x82000000
BLR = 0x4e800020
# The helper blocks: (address, kind, restore). gprlr saves or restores r14-r31 and LR (through r12) at r1; fpr saves
# or restores f14-f31 at r12; vmx saves or restores v14-v31, then (a second block right after it) v64-v127, at r12.
HELPER_BLOCKS = [(0x827be800, 'gprlr', False), (0x827be850, 'gprlr', True),
                 (0x827beed0, 'fpr', False), (0x827bef1c, 'fpr', True),
                 (0x827bef70, 'vmx', False), (0x827bf208, 'vmx', True)]


def helper_words(kind: str, restore: bool, first: int, last: int) -> list[int]:
    """The instruction words of one helper block that saves (or restores) registers first..last, ending in blr."""
    words: list[int] = []
    for reg in range(first, last + 1):
        if kind == 'gprlr':
            # std / ld reg, -152 + 8 * (reg - 14)(r1)
            op = 58 if restore else 62
            displacement = -152 + (reg - 14) * 8
            words.append((op << 26) | (reg << 21) | (1 << 16) | (displacement & 65535))
        elif kind == 'fpr':
            # stfd / lfd reg, -144 + 8 * (reg - 14)(r12)
            op = 50 if restore else 54
            displacement = -144 + (reg - 14) * 8
            words.append((op << 26) | (reg << 21) | (12 << 16) | (displacement & 65535))
        else:
            # li r11, offset; then stvx / lvx (v0-v31) or stvx128 / lvx128 reg, r11, r12
            offset = -(last - first + 1) * 16 + (reg - first) * 16
            words.append(0x39600000 | (offset & 65535))
            if first == 14:
                words.append(0x7c0b6000 | (reg << 21) | (0xce if restore else 0x1ce))
            else:
                words.append(0x100b6000 | ((reg & 31) << 21) | (0xcb if restore else 0x1cb) | ((reg & 32) >> 3))
    if kind == 'gprlr':
        # restore: lwz r12, -8(r1); mtlr r12. save: stw r12, -8(r1)
        words += [0x8181fff8, 0x7d8803a6] if restore else [0x9181fff8]
    words.append(BLR)
    return words


def verified_blocks(data: bytes) -> list[dict[str, Any]]:
    """The helper blocks (HELPER_BLOCKS), each checked word for word against helper_words(), with their entries;
    ValueError for another image or a block that differs."""
    if hashlib.sha256(data).hexdigest() != IMAGE_HASH:
        raise ValueError('Unexpected image')
    blocks = []
    for start, kind, restore in HELPER_BLOCKS:
        for first, last in ([(14, 31), (64, 127)] if kind == 'vmx' else [(14, 31)]):
            words = helper_words(kind, restore, first, last)
            expected = struct.pack('>' + str(len(words)) + 'I', *words)
            actual = data[start - BASE:start - BASE + len(expected)]
            if actual != expected:
                raise ValueError(f'Full helper mismatch {start:x}')
            prefix = ('__rest' if restore else '__save') + kind
            entry_size = 8 if kind == 'vmx' else 4      # a vmx entry is two words (li, then the store or load)
            entries = [dict(address=start + (reg - first) * entry_size, name=f'{prefix}_{reg}', register=reg)
                       for reg in range(first, last + 1)]
            blocks.append(dict(start=start, end=start + len(expected), name=f'{prefix}_{first}', kind=kind,
                               first_register=first, last_register=last, restore=restore,
                               sha256=hashlib.sha256(actual).hexdigest(), entries=entries))
            start += len(expected)
    return blocks


def branch_target(instruction: int, address: int) -> int:
    """The target of an I-form branch (b, bl, ba, bla) at address."""
    displacement = instruction & 0x3fffffc
    if displacement & 0x2000000:
        displacement -= 0x4000000
    absolute = instruction & 2
    return (displacement if absolute else address + displacement) & 0xffffffff


def main() -> None:
    root = research_root()              # the research workspace (workspace.py)
    data = (root / 'work/xex-analysis/default.checked.memory.bin').read_bytes()
    blocks = verified_blocks(data)
    old = json.loads((root / 'research/evidence/xex-functions.json').read_text())
    targets = {entry['address']: entry for block in blocks for entry in block['entries']}
    calls: list[dict[str, Any]] = []
    for section in old['sections']:
        if not section['execute']:
            continue
        for address in range(section['start'], section['start'] + section['available_size'] - 3, 4):
            instruction = struct.unpack_from('>I', data, address - BASE)[0]
            if instruction >> 26 != 18:
                continue
            target = branch_target(instruction, address)
            if target in targets:
                calls.append(dict(address=address, target=target, link=bool(instruction & 1),
                                  name=targets[target]['name']))
    for entry in targets.values():
        matches = [call for call in calls if call['target'] == entry['address']]
        entry['branch_candidates'] = len(matches)
        entry['examples'] = matches[:4]
    references = {}
    for suffix in ('/README.md', '/XenonRecomp/recompiler.cpp', '/XenonUtils/xbox.h', '/thirdparty/disasm/ppc-dis.c'):
        name, content = xenonrecomp_reference.read(suffix, root)
        references[name] = hashlib.sha256(content).hexdigest()
    result = dict(
        image_sha256=IMAGE_HASH, parser_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        reference_archive_sha256=xenonrecomp_reference.ARCHIVE_SHA256,
        reference_source=xenonrecomp_reference.source(root), reference_members=references, blocks=blocks,
        summary=dict(resolved_pdata_records=6, helper_blocks=len(blocks), suffix_entries=len(targets),
                     direct_branch_candidates=len(calls)),
        limitations=['Helper identities are analyst-assigned conventional names, not recovered symbols.',
                     'Bit 30/31 Xbox unwind semantics remain unresolved; full-body verification overrides only these '
                     'exact records.',
                     'Branch scan is static aligned opcode matching; candidate counts are not observed execution or '
                     'complete xrefs.',
                     'VMX128 words are validated by explicit encoding patterns, not proof of Ghidra semantic support.'])
    # CRLF, as recorded: evidence pins the bytes of the resolution, helpers.tsv and helper-entries.tsv
    (root / 'research/evidence/xex-helper-resolution.json').write_text(json.dumps(result, indent=2) + '\n',
                                                                       encoding='utf-8', newline='\r\n')
    with (root / 'work/xex-analysis/helpers.tsv').open('w', encoding='utf-8', newline='\r\n') as f:
        f.write('start\tend\tname\n')
        for block in blocks:
            f.write(f"{block['start']:08x}\t{block['end']:08x}\t{block['name']}\n")
    with (root / 'work/xex-analysis/helper-entries.tsv').open('w', encoding='utf-8', newline='\r\n') as f:
        f.write('address\tname\n')
        for entry in targets.values():
            f.write(f"{entry['address']:08x}\t{entry['name']}\n")
    # helper-branches.tsv is CRLF too, as recorded: before 2026-10-31 it was written LF, so a rerun changed its bytes
    with (root / 'work/xex-analysis/helper-branches.tsv').open('w', encoding='utf-8', newline='\r\n') as f:
        f.write('address\ttarget\tlink\tname\n')
        for call in calls:
            f.write(f"{call['address']:08x}\t{call['target']:08x}\t{int(call['link'])}\t{call['name']}\n")
    print(result['summary'])


if __name__ == '__main__':
    main()
