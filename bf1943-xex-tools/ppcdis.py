"""Small disassembly helpers over the checked BF1943 image (capstone, PowerPC 64 big-endian).

  python -B <suite>/bf1943-xex-tools/ppcdis.py <hex address> [count]

Capstone has no VMX128 decoding; those words print as .long. For research reading only.
"""
import struct
import sys

sys.dont_write_bytecode = True
import capstone
from xexrefs import BASE, data, word, cstring

md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)


def insn(a):
    for i in md.disasm(data[a - BASE:a - BASE + 4], a):
        return i.mnemonic, i.op_str
    return '.long', f'0x{word(a):08x}'


def lines(a, n):
    for k in range(n):
        m, o = insn(a + 4 * k)
        yield a + 4 * k, word(a + 4 * k), m, o


def jump_table(a, cases):
    """Targets of a `lis/addi/lwzx/mtctr/bctr`-style table of absolute 32-bit entries at a."""
    return [word(a + 4 * k) for k in range(cases)]


if __name__ == '__main__':
    start = int(sys.argv[1], 16)
    for a, w, m, o in lines(start, int(sys.argv[2]) if len(sys.argv) > 2 else 40):
        print(f'{a:08x}  {w:08x}  {m:8s} {o}')
