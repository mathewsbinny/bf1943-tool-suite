"""xexrefs.code_references: a lis value pairs only while its register still holds it.

  python -B test_xexrefs.py

xexrefs loads the checked image when imported, so this needs the research workspace (BF1943_RESEARCH, else
operation-rising-sun next to the suite); it only reads it. Checked: synthetic code (an overwrite by mr, srwi, mulli,
a load with update or lmw ends the pairing; a write of another register, or a store, does not); and on the image, the
reference a review found (821687a0: lis r11, then srwi r11, then addi r4, r11, 0x200, once taken as 0x82110200) is gone.
"""
import os
import sys

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.environ.get('BF1943_RESEARCH') or os.path.join(os.path.dirname(os.path.dirname(TOOLS)), 'operation-rising-sun')
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def lis(r, hi): return 15 << 26 | r << 21 | hi
def addi(d, a, v): return 14 << 26 | d << 21 | a << 16 | v & 0xFFFF
def lwz(d, off, a): return 32 << 26 | d << 21 | a << 16 | off
def lwzu(d, off, a): return 33 << 26 | d << 21 | a << 16 | off
def stw(s, off, a): return 36 << 26 | s << 21 | a << 16 | off
def mr(d, s): return 31 << 26 | s << 21 | d << 16 | s << 11 | 444 << 1
def add(d, a, b): return 31 << 26 | d << 21 | a << 16 | b << 11 | 266 << 1
def srwi(d, s, n): return 21 << 26 | s << 21 | d << 16 | (32 - n) << 11 | n << 6 | 31 << 1
def mulli(d, a, v): return 7 << 26 | d << 21 | a << 16 | v
def lmw(d, off, a): return 46 << 26 | d << 21 | a << 16 | off


def main():
    if not os.path.isfile(os.path.join(SOURCE, 'work', 'xex-analysis', 'default.checked.memory.bin')):
        print(f'skip xexrefs: no research workspace at {SOURCE}')
        return
    os.environ['BF1943_RESEARCH'] = SOURCE
    sys.path.insert(0, TOOLS)
    import xexrefs as X
    real_word, real_functions = X.word, X.functions
    hi = 0x8200                                  # r11 = 0x82000000: + 0x10 is in the image
    cases = [('lis, lwz 0x10(r11)', [lis(11, hi), lwz(4, 0x10, 11)], True),
             ('lis, mr r11, r3, lwz 0x10(r11)', [lis(11, hi), mr(11, 3), lwz(4, 0x10, 11)], False),
             ('lis, srwi r11, addi r4, r11', [lis(11, hi), srwi(11, 10, 20), addi(4, 11, 0x10)], False),
             ('lis, mulli r11, lwz', [lis(11, hi), mulli(11, 3, 12), lwz(4, 0x10, 11)], False),
             ('lis, lwzu r4, 4(r11) (base updated), lwz 0x10(r11)', [lis(11, hi), lwzu(4, 4, 11), lwz(5, 0x10, 11)], 'first'),
             ('lis, lmw r10 (writes r10..r31), lwz', [lis(11, hi), lmw(10, 0, 1), lwz(4, 0x10, 11)], False),
             ('lis, add r3, r11, r4 (writes r3 only), lwz 0x10(r11)', [lis(11, hi), add(3, 11, 4), lwz(5, 0x10, 11)], True),
             ('lis, stw r11, 0(r3) (a store writes no register), lwz 0x10(r11)', [lis(11, hi), stw(11, 0, 3), lwz(5, 0x10, 11)], True)]
    try:
        for what, code, expect in cases:
            X.word = lambda a, code=code: code[(a - 0x1000) // 4]
            X.functions = [(0x1000, 0x1000 + 4 * len(code))]
            refs, _ = X.code_references()
            sites = sorted(at for v in refs.values() for at, _ in v)
            last = 0x1000 + 4 * (len(code) - 1)
            want = [0x1004] if expect == 'first' else [last] if expect else []
            check(sites == want, f'{what}: references at {[hex(s) for s in sites]}, expected {[hex(s) for s in want]}')
    finally:
        X.word, X.functions = real_word, real_functions
    refs, _ = X.code_references()
    check(not any(at == 0x821687A0 for at, _ in refs.get(0x82110200, [])),
          'image: 821687a0 (addi r4, r11, 0x200 after srwi r11) is no longer a reference to 0x82110200')
    check(sum(len(v) for v in refs.values()) > 50000, f'image: the scan still finds the references '
          f'({sum(len(v) for v in refs.values())})')


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
