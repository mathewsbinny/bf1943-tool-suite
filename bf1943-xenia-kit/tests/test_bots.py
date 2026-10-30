"""Tests for the bots experiment (bots.py).

  python -B test_bots.py             the code assembles, fits its cave, and the hook branches to it
  python -B test_bots.py --image     + against the decoded BF1943 image (BF1943_IMAGE, or operation-rising-sun's
                                     work/xex-analysis/default.memory.bin): the hook's original word, and the cave
                                     and data areas unused (zero)
"""
import os
import re
import struct
import sys

sys.dont_write_bytecode = True
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
import bots
import ppcasm

failures = []
BASE = 0x82000000


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def layout():
    words, code, tail = bots.build()
    hook = words[0]
    check(hook[0] == bots.HOOK and hook[1] >> 26 == 18 and hook[1] & 1
          and bots.HOOK + ((hook[1] & 0x3FFFFFC) ^ 0x2000000) - 0x2000000 == bots.CODE,
          'the hook is a bl to the bot code')
    check(all(bots.CODE <= a < bots.CODE_END for a, _, _ in words[1:]) and len(words) - 1 == (len(code) + len(tail)) // 4,
          f'the bot code, table and names fit the cave ({len(code) + len(tail):#x} of {bots.CODE_END - bots.CODE:#x} bytes)')
    saves = max(int(m, 16) for m in re.findall(r'std r\d+, -0x([0-9a-f]+)\(r1\)', bots.CODE_SOURCE))
    check(bots.FRAME - saves >= bots.BUF + 0x100 and bots.FRAME % 16 == 0 and bots.BUF % 16 == 0
          and bots.BUF + 0x100 <= bots.DT and bots.DT + 4 <= bots.FRAME - saves,
          'the stack frame holds the input frame buffer and the time step below the saved registers, 16-aligned')
    text = bots.patch_toml()
    check(text.startswith(bots.HEADER) and text.count('[[patch.be32]]') == len(words) and 'hash = "872BBE31C0FBA21B"' in text,
          'the patch file names the module and holds every word')
    for bad in ([], [(3, 'Rifleman')], [(1, 'Medic')], [(1, 'Rifleman')] * (bots.MAX_BOTS + 1)):
        try:
            bots.build(bad)
            check(False, f'refuses bots {bad[:2]}')
        except ValueError:
            check(True, f'refuses bots {bad[:2]}')


def mailbox():
    """Mailbox v2: the layout fits, and capstone (an independent decoder) reads the code's mailbox accesses as the
    layout names them."""
    check(bots.DATA + bots.MAILBOX_END <= bots.DATA_END, f'the mailbox address and magic fit the data area '
          f'(to {bots.DATA + bots.MAILBOX_END:#x} of {bots.DATA_END:#x})')
    check(bots.BOT0 >= bots.H_FLAGS + 4 and bots.FLAG0 == bots.BOT0 + bots.BOT_SIZE * bots.MAX_BOTS
          and bots.C_BUTTONS_LO + 4 <= bots.BOT_SIZE and bots.R_SEQ_END < bots.C_STAMP and bots.F_SEQ_END + 4 <= bots.FLAG_SIZE
          and bots.F_Z < bots.F_SEQ_END and bots.BLOCK_SIZE <= 0x7FFF and bots.BLOCK_SIZE % 4 == 0,
          f'the block layout: header, {bots.MAX_BOTS} bot records of {bots.BOT_SIZE:#x}, {bots.MAX_FLAGS} flag records '
          f'of {bots.FLAG_SIZE:#x}, {bots.BLOCK_SIZE:#x} bytes')
    try:
        import capstone
    except ImportError:
        print('skip capstone checks: capstone is not installed')
        return
    _, code, _ = bots.build()
    names = ppcasm.assemble(bots.CODE_SOURCE, bots.CODE, bots.assembler_symbols())[1]
    end = names['FMT_HOST'] - bots.CODE          # the instructions; the format strings follow them
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)
    ins = [(i.mnemonic, i.op_str) for i in md.disasm(code[:end], bots.CODE)]
    check(len(ins) == end // 4, f'capstone decodes every instruction ({len(ins)} of {end // 4})')
    text = [f'{m} {o}'.strip() for m, o in ins]

    def offsets(op, base):
        found = set()
        for m, o in ins:
            mm = re.fullmatch(r'r\d+, (-?(?:0x)?[0-9a-f]+)\((r\d+)\)', o)
            if m == op and mm and mm.group(2) == base:
                found.add(int(mm.group(1), 0))
        return found
    check(offsets('stw', 'r22') == {bots.R_SEQ, bots.R_PLAYER, bots.R_SOLDIER, bots.R_TEAM, bots.R_X, bots.R_Y, bots.R_Z,
                                    bots.R_SEQ_END}, f'the guest writes exactly the state record\'s words: {sorted(offsets("stw", "r22"))}')
    check(offsets('lwz', 'r22') == {bots.C_STAMP, bots.C_THROTTLE, bots.C_STRAFE, bots.C_YAW, bots.C_PITCH, bots.C_BUTTONS_HI,
                                    bots.C_BUTTONS_LO}, f'and reads exactly the command record\'s: {sorted(offsets("lwz", "r22"))}')
    header = {bots.H_MAGIC, bots.H_VERSION, bots.H_BOTS, bots.H_BOT_SIZE, bots.H_FLAGS_MAX, bots.H_FLAG_SIZE, bots.H_MANAGER}
    flags = {bots.F_SEQ, bots.F_ENTITY, bots.F_OWNER, bots.F_CONTROL, bots.F_ENABLED, bots.F_RADIUS, bots.F_X, bots.F_Y,
             bots.F_Z, bots.F_SEQ_END}
    check(offsets('stw', 'r29') == header | flags, f'the header (at creation) and flag records: {sorted(offsets("stw", "r29"))}')
    check(offsets('stw', 'r3') >= {bots.H_FLAGS, bots.H_TICK}, 'the tick\'s flag count and tick go to the header')
    want = [f'std r22, -0x58(r1)', f'ld r22, -0x58(r1)', f'cmplwi r5, {bots.STALE:#x}', f'li r3, {bots.BLOCK_SIZE:#x}',
            'bl 0x82627d10', 'bl 0x825c5470', 'bl 0x825c1290', 'lbz r5, 0xad(r27)', 'lwz r5, 0x80(r27)', 'lwz r5, 0xa0(r27)',
            'lwz r5, 0x114(r5)', f'lis r4, {bots.MAGIC >> 16:#x}', f'ori r4, r4, {bots.MAGIC & 0xFFFF:#x}',
            f'lis r5, {((bots.CP_TYPE + 0x8000) >> 16) - 0x10000:#x}', f'addi r5, r5, {bots.CP_TYPE & 0xFFFF:#x}']
    missing = [w for w in want if w not in text]
    check(not missing, f'the new instructions decode as written (stale check, allocation, capture-point iteration, '
          f'magic, saved r22){": missing " + str(missing) if missing else ""}')
    k = text.index(f'cmplwi r5, {bots.STALE:#x}')
    check(text[k + 1].startswith('bgt ') and text[k - 1] == 'subf r5, r6, r5', 'a command older than STALE ticks branches '
          'past the copy (tick - stamp, unsigned)')
    k = text.index(f'li r3, {bots.BLOCK_SIZE:#x}')
    check(text[k + 1] == 'bl 0x82627d10', 'the block is allocated with the game allocator')


def image():
    path = os.environ.get('BF1943_IMAGE', os.path.join(os.path.dirname(os.path.dirname(KIT)), 'operation-rising-sun',
                                                      'work', 'xex-analysis', 'default.memory.bin'))
    if not os.path.isfile(path):
        print(f'skip image checks: no decoded image at {path}')
        return
    data = open(path, 'rb').read()
    word = lambda a: struct.unpack_from('>I', data, a - BASE)[0]
    check(word(bots.HOOK) == bots.HOOK_ORIGINAL, 'the hook site holds the expected bl 0x8238eb68')
    check(not any(data[bots.CODE - BASE:bots.CODE_END - BASE]), 'the code cave is zero in the image')
    check(not any(data[bots.DATA - BASE:bots.DATA_END - BASE]), 'the data area is zero in the image')
    words, _, _ = bots.build()
    targets = []
    for a, w, _ in words[1:]:
        if w >> 26 == 18 and w & 1:
            targets.append(a + ((w & 0x3FFFFFC) ^ 0x2000000) - 0x2000000)
    # a function start: its prologue (mflr r12), or a leaf right after another function's blr or padding
    # (or an import thunk, after the previous thunk's bctr)
    starts = [t for t in targets if word(t) == 0x7D8802A6 or word(t - 4) in (0x4E800020, 0x4E800420, 0)]
    check(len(starts) == len(targets), f'every call in the bot code targets a function start ({len(starts)} of {len(targets)})')


if __name__ == '__main__':
    layout()
    mailbox()
    if '--image' in sys.argv:
        image()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
