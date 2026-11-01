"""Small disassembly helpers over the checked BF1943 image (capstone, PowerPC 64 big-endian).

  python -B <suite>/bf1943-xex-tools/ppcdis.py <hex address> [count]

Capstone has no VMX128 decoding; those words print as .long. For research reading only.
"""
import sys
from collections.abc import Iterator

sys.dont_write_bytecode = True
import capstone  # noqa: E402
from xexrefs import BASE, data, word  # noqa: E402

disassembler = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)


def insn(address: int) -> tuple[str, str]:
    """(mnemonic, operands) of the instruction at address; '.long' for a word capstone cannot decode."""
    offset = address - BASE
    for instruction in disassembler.disasm(data[offset:offset + 4], address):
        return instruction.mnemonic, instruction.op_str
    return '.long', f'0x{word(address):08x}'


def lines(address: int, count: int) -> Iterator[tuple[int, int, str, str]]:
    """(address, word, mnemonic, operands) of count instructions from address."""
    for k in range(count):
        at = address + 4 * k
        mnemonic, operands = insn(at)
        yield at, word(at), mnemonic, operands


def jump_table(address: int, cases: int) -> list[int]:
    """Targets of a `lis/addi/lwzx/mtctr/bctr`-style table of absolute 32-bit entries at address."""
    return [word(address + 4 * k) for k in range(cases)]


if __name__ == '__main__':
    start = int(sys.argv[1], 16)
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    for address, instruction, mnemonic, operands in lines(start, count):
        print(f'{address:08x}  {instruction:08x}  {mnemonic:8s} {operands}')
