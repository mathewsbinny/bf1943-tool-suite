"""A copy of Xenia Canary with its UDP address byte-order bugs removed (build e6cd2e4bc only).

In this build, bind converts guest addresses correctly, but sendto and WSASendTo byte-swap sin_addr and
sin_port before the host call (127.0.0.1:25003 is sent to 1.0.0.127:43873, a public address), and recvfrom
returns the sender's port swapped. The copy replaces exactly those five instructions with NOPs; the sin_family
swaps, which are needed, are untouched. Every original byte is checked first, so another build is refused.
"""
import hashlib
import struct
import sys

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


def file_offset(data, va):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    opt = pe + 24
    first = opt + struct.unpack_from('<H', data, pe + 20)[0]
    for k in range(struct.unpack_from('<H', data, pe + 6)[0]):
        name, vsize, rva, rsize, raw = struct.unpack_from('<8sIIII', data, first + 40 * k)
        if BASE + rva <= va < BASE + rva + rsize:
            return va - BASE - rva + raw
    raise ValueError(f'0x{va:x} is not in a section')


def build(exe_bytes):
    """(fixed exe bytes, change log). Raises if any site does not hold the expected original bytes."""
    data = bytearray(exe_bytes)
    changes = []
    for va, expected, why in SITES:
        at = file_offset(data, va)
        n = len(expected) // 2
        found = bytes(data[at:at + n]).hex()
        if found != expected:
            raise ValueError(f'Xenia 0x{va:x}: expected {expected}, found {found} - this is not the Xenia build the fix is for')
        data[at:at + n] = b'\x90' * n
        changes.append(dict(va=f'0x{va:x}', file_offset=at, before=expected, after='90' * n, why=why))
    return bytes(data), changes


def sha256(b):
    return hashlib.sha256(b).hexdigest()
