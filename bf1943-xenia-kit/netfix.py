"""A copy of Xenia Canary with its UDP address byte-order bugs removed (build e6cd2e4bc only).

In this build, bind converts guest addresses correctly, but sendto and WSASendTo byte-swap sin_addr and
sin_port before the host call (127.0.0.1:25003 is sent to 1.0.0.127:43873, a public address), and recvfrom
returns the sender's port swapped. The copy replaces exactly those five instructions with NOPs; the sin_family
swaps, which are needed, are untouched. Every original byte is checked first, so another build is refused.
"""
import hashlib
import struct
import sys
from typing import Any

sys.dont_write_bytecode = True
EXE = 'xenia_canary.exe'
FIXED = 'xenia_canary_netfix.exe'
BASE = 0x140000000
SITES = [
    (0x14029F1AE, '0fc8', 'sendto: bswap sin_addr'),
    (0x14029F1C4, '66c1c808', 'sendto: ror sin_port'),
    (0x14029B8E8, '0fc8', 'WSASendTo: bswap sin_addr'),
    (0x14029B8F6, '66c1c808', 'WSASendTo: ror sin_port'),
    (0x14029ECF0, '66c1c908', 'recvfrom: ror sender sin_port'),
]


def file_offset(data: bytes | bytearray, va: int) -> int:
    """The file offset of a virtual address in a PE image (its section table)."""
    pe_header = struct.unpack_from('<I', data, 0x3C)[0]
    optional_header = pe_header + 24
    first_section = optional_header + struct.unpack_from('<H', data, pe_header + 20)[0]
    section_count = struct.unpack_from('<H', data, pe_header + 6)[0]
    for k in range(section_count):
        _name, _vsize, rva, raw_size, raw_offset = struct.unpack_from('<8sIIII', data, first_section + 40 * k)
        if BASE + rva <= va < BASE + rva + raw_size:
            return va - BASE - rva + raw_offset
    raise ValueError(f'0x{va:x} is not in a section')


def build(exe_bytes: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    """(fixed exe bytes, change log). Raises if any site does not hold the expected original bytes."""
    data = bytearray(exe_bytes)
    changes: list[dict[str, Any]] = []
    for va, expected, why in SITES:
        offset = file_offset(data, va)
        size = len(expected) // 2
        found = bytes(data[offset:offset + size]).hex()
        if found != expected:
            raise ValueError(f'Xenia 0x{va:x}: expected {expected}, found {found} - '
                             'this is not the Xenia build the fix is for')
        data[offset:offset + size] = b'\x90' * size
        changes.append(dict(va=f'0x{va:x}', file_offset=offset, before=expected, after='90' * size, why=why))
    return bytes(data), changes


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
