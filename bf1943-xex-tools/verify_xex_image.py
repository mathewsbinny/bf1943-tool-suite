"""Verify the decoded image against the XEX's own page digests, and account for the PE .reloc extent.

  python -B verify_xex_image.py        (from the research workspace, or with BF1943_RESEARCH set)

Page digests: the XEX security info holds one 24-byte descriptor per 64 KiB page (a big-endian word,
pages << 4 | page kind, then a 20-byte SHA-1). Each digest covers the *next* page and that page's own
descriptor: descriptor[k-1].digest == SHA-1(page[k] || descriptor[k]); the security info's image digest
anchors the chain with SHA-1(page[0] || descriptor[0]); the last descriptor's digest is zero. This is
established here by testing it on every page, not taken from any other tool.

.reloc: the PE section table and base-relocation directory are the linker's; the XEX image (whose size the
security info declares and whose every page the chain authenticates) stops at the page after .XBLD. The
script reports what of .reloc is inside the image and whether those bytes hold relocation blocks.

Writes research/evidence/xex-image-integrity.json. Reads only; the XEX and the image are never modified.
"""
from pathlib import Path
from typing import Any
import hashlib
import json
import struct
import sys

sys.dont_write_bytecode = True
from workspace import research_root  # noqa: E402

IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
PAGE = 0x10000          # image flag bit 0x10000000 (4 KiB pages) is clear: 64 KiB pages


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()


def read(data: bytes, offset: int, fmt: str, what: str) -> tuple[Any, ...]:
    """struct.unpack_from(fmt, data, offset); ValueError naming what when it lies outside the data (2026-10-31
    fuzzing: struct.error before, and a slice past the end read short)."""
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise ValueError(f'damaged file: {what} ({size} bytes at {offset:#x}) lies outside its {len(data)} bytes')
    return struct.unpack_from(fmt, data, offset)


def security_info(xex: bytes) -> dict[str, Any]:
    """The XEX2 security info: sizes, flags, load address, image digest and page descriptors. ValueError for a file
    that is not an XEX2 or whose security info lies outside it."""
    magic, flags, pe_offset, _, security_offset, optional_count = read(xex, 0, '>4sIIIII', 'the XEX header')
    if magic != b'XEX2':
        raise ValueError('not an XEX2 file')
    header_size, image_size = read(xex, security_offset, '>II', 'the security info')
    info = security_offset + 8 + 256                   # RSA signature (256 bytes) follows the two sizes
    info_size, image_flags, load_address = read(xex, info, '>III', 'the signed image info')
    read(xex, info + 12, '20s', 'the image digest')
    image_digest = xex[info + 12:info + 32]
    count = read(xex, security_offset + 0x180, '>I', 'the page descriptor count')[0]
    read(xex, security_offset + 0x184, f'{24 * count}s', f'{count} page descriptors')
    descriptors = []
    for i in range(count):
        offset = security_offset + 0x184 + 24 * i
        word = struct.unpack_from('>I', xex, offset)[0]
        descriptors.append(dict(offset=offset, pages=word >> 4, kind=word & 15, raw=xex[offset:offset + 24],
                                digest=xex[offset + 4:offset + 24]))
    return dict(pe_offset=pe_offset, security_offset=security_offset, header_size=header_size, image_size=image_size,
                info_size=info_size, image_flags=image_flags, load_address=load_address, image_digest=image_digest,
                descriptors=descriptors, optional_header_count=optional_count, module_flags=flags)


def pe_layout(image: bytes) -> dict[str, Any]:
    """The PE headers of the decoded image: base, sizes, data directories and sections. ValueError for headers
    that lie outside the image."""
    pe = read(image, 60, '<I', 'the PE header offset')[0]
    count = read(image, pe + 6, '<H', 'the section count')[0]
    optional_size = read(image, pe + 20, '<H', 'the optional header size')[0]
    optional = pe + 24
    image_base, = read(image, optional + 28, '<I', 'the image base')
    size_of_image, size_of_headers = read(image, optional + 56, '<II', 'the image sizes')
    directory_count = read(image, optional + 92, '<I', 'the data directory count')[0]
    read(image, optional + 96, f'{8 * directory_count}s', f'{directory_count} data directories')
    directories = [struct.unpack_from('<II', image, optional + 96 + 8 * i) for i in range(directory_count)]
    read(image, optional + optional_size, f'{40 * count}s', f'{count} section headers')
    sections = []
    for i in range(count):
        offset = optional + optional_size + 40 * i
        name, virtual_size, rva, raw_size, raw_offset = struct.unpack_from('<8sIIII', image, offset)
        sections.append(dict(name=name.rstrip(b'\0').decode(), rva=rva, virtual_size=virtual_size, raw_size=raw_size,
                             raw_offset=raw_offset))
    return dict(image_base=image_base, size_of_image=size_of_image, size_of_headers=size_of_headers,
                directories=directories, sections=sections)


def walk_relocations(block: bytes) -> dict[str, Any]:
    """IMAGE_BASE_RELOCATION blocks (little-endian PE layout) from the start of the mapped bytes."""
    offset = blocks = entries = 0
    while offset + 8 <= len(block):
        page_rva, size = struct.unpack_from('<II', block, offset)
        if size == 0:
            break
        if size < 8 or offset + size > len(block):
            return dict(blocks=blocks, entries=entries, stopped_at=offset, reason=f'invalid block size {size}')
        blocks += 1
        entries += (size - 8) // 2
        offset += size
    reason = 'zero block size' if offset + 8 <= len(block) else 'end of mapped bytes'
    return dict(blocks=blocks, entries=entries, stopped_at=offset, reason=reason)


def main() -> None:
    root = research_root()              # the research workspace (workspace.py)
    xex_path = root / 'work/extracted/default.xex'
    image_path = root / 'work/xex-analysis/default.checked.memory.bin'
    xex = xex_path.read_bytes()
    image = image_path.read_bytes()
    if sha256(image) != IMAGE_SHA256:
        raise SystemExit('unexpected decoded image; decode the extracted XEX first (decode.ps1)')
    try:
        info = security_info(xex)
    except ValueError as e:
        # in words, as before (a SystemExit with the reason)
        raise SystemExit(f'{xex_path}: {e}') from None
    descriptors = info['descriptors']
    if info['image_flags'] & 0x10000000:
        raise SystemExit('4 KiB-page image: not handled here')
    total_pages = sum(descriptor['pages'] for descriptor in descriptors)
    checks = dict(declared_image_size_equals_decoded=info['image_size'] == len(image),
                  descriptor_pages_cover_image=total_pages * PAGE == len(image),
                  every_descriptor_one_page=all(descriptor['pages'] == 1 for descriptor in descriptors))
    # Offsets of each descriptor's page in the image.
    starts: list[int] = []
    offset = 0
    for descriptor in descriptors:
        starts.append(offset)
        offset += descriptor['pages'] * PAGE

    def page(k: int) -> bytes:
        return image[starts[k]:starts[k] + descriptors[k]['pages'] * PAGE]

    def page_digest(k: int, descriptor: dict[str, Any]) -> bytes:
        """SHA-1 of page k followed by a descriptor's 24 bytes."""
        return sha1(page(k) + descriptor['raw'])

    count = len(descriptors)
    anchor = page_digest(0, descriptors[0]) == info['image_digest']
    chain = [k for k in range(1, count) if page_digest(k, descriptors[k]) == descriptors[k - 1]['digest']]
    last_zero = descriptors[-1]['digest'] == bytes(20)
    # The naive comparison recorded on 2026-10-16 (descriptor k against SHA-1 of page k alone), kept for the record.
    naive = sum(sha1(page(k)) == descriptors[k]['digest'] for k in range(count))
    same_page_with_next_descriptor = sum(page_digest(k, descriptors[k + 1]) == descriptors[k]['digest']
                                         for k in range(count - 1))
    zero_pages = [k for k in range(count) if not any(page(k))]
    kinds: dict[int, int] = {}
    for descriptor in descriptors:
        kinds[descriptor['kind']] = kinds.get(descriptor['kind'], 0) + 1

    pe = pe_layout(image)
    reloc = next(section for section in pe['sections'] if section['name'] == '.reloc')
    reloc_dir_rva, reloc_dir_size = pe['directories'][5]
    mapped = image[reloc['rva']:]
    before_reloc = [section for section in pe['sections'] if section['rva'] < reloc['rva']]
    previous = max(before_reloc, key=lambda section: section['rva'])
    rounded_end = -(-(previous['rva'] + previous['virtual_size']) // PAGE) * PAGE
    relocation_walk = walk_relocations(mapped)
    report: dict[str, Any] = dict(
        tool='bf1943-xex-tools/verify_xex_image.py', tool_sha256=sha256(Path(__file__).read_bytes()),
        inputs=dict(xex=dict(path='work/extracted/default.xex', sha256=sha256(xex), bytes=len(xex)),
                    image=dict(path='work/xex-analysis/default.checked.memory.bin', sha256=sha256(image),
                               bytes=len(image))),
        security_info=dict(offset=info['security_offset'], header_size=info['header_size'],
                           image_size=info['image_size'],
                           image_flags=hex(info['image_flags']), load_address=hex(info['load_address']),
                           image_digest=info['image_digest'].hex(), descriptors=count, page_bytes=PAGE,
                           descriptor_kinds={str(kind): pages for kind, pages in sorted(kinds.items())}),
        page_digests=dict(rule='descriptor[k-1].digest == SHA-1(page[k] || descriptor[k] (24 bytes)); image digest == '
                               'SHA-1(page[0] || descriptor[0]); last digest zero',
                          anchor_matches=anchor, chain_matches=len(chain), chain_links=count - 1,
                          last_digest_zero=last_zero,
                          all_pages_authenticated=anchor and len(chain) == count - 1 and last_zero,
                          earlier_naive_matches=naive, earlier_naive_rule='descriptor[k].digest == SHA-1(page[k])',
                          same_page_plus_next_descriptor_matches=same_page_with_next_descriptor,
                          zero_pages=[f'{k}' for k in zero_pages],
                          note='The naive rule matched 0 because each digest covers the following page; the "same page '
                               '+ next descriptor" rule matches only where page k and page k+1 are identical '
                               '(consecutive zero pages of .data).',
                          checks=checks),
        reloc=dict(section=dict(rva=hex(reloc['rva']), virtual_size=reloc['virtual_size'], raw_size=reloc['raw_size']),
                   directory=dict(rva=hex(reloc_dir_rva), size=reloc_dir_size),
                   pe_size_of_image=pe['size_of_image'], xex_image_size=info['image_size'],
                   mapped_bytes=len(mapped), mapped_nonzero_bytes=sum(1 for byte in mapped if byte),
                   relocation_blocks_in_mapped_bytes=relocation_walk,
                   previous_section=dict(name=previous['name'], end_rva=hex(previous['rva'] + previous['virtual_size']),
                                         end_rounded_to_page=hex(rounded_end)),
                   image_ends_at_page_after_previous_section=rounded_end == info['image_size'],
                   pe_image_base=hex(pe['image_base']), equals_load_address=pe['image_base'] == info['load_address']),
        conclusions=[
            '[O] All 219 pages of the decoded image match the XEX page-digest chain, anchored at the security-info '
            'image digest: the decoded image is byte-identical to the image the XEX build hashed. (The RSA signature '
            'over the security info is not checked here.)',
            '[O] The earlier 0/219 came from our comparison, not the image: it compared each digest with its own page; '
            'each digest covers the next page plus its descriptor.',
            '[O] The XEX image ends at the 64 KiB page after .XBLD; the security info declares that size and the '
            'digests cover exactly it. The 65,024 bytes of .reloc inside it are all zero and hold no relocation block.',
            '[H] The XEX build tool dropped the base relocations: the image is fixed at 0x82000000 (PE ImageBase '
            'equals the XEX load address), so the loader never relocates it; the PE section header and data directory '
            'still carry the linker\'s .reloc extent.',
        ])
    out = root / 'research/evidence/xex-image-integrity.json'
    out.write_bytes((json.dumps(report, indent=2) + '\n').encode())
    print(f"page digests: anchor={anchor} chain={len(chain)}/{count - 1} last_zero={last_zero} "
          f"(naive rule: {naive}/{count})")
    print(f".reloc: {len(mapped)} bytes mapped, {report['reloc']['mapped_nonzero_bytes']} nonzero; "
          f"image size {info['image_size']:#x} = page after {previous['name']}: {rounded_end == info['image_size']}")
    print(out)
    if not report['page_digests']['all_pages_authenticated'] or not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
