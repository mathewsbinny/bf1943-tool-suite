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
    if '--image' in sys.argv:
        image()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
