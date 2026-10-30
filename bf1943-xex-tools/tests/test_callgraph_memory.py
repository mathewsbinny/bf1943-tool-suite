"""export_callgraph_strings.py's remembered stores: a slot overwritten by a float store, dcbz or a VMX128 store is not
folded into a later load (no game data: a made-up function on a stand-in image).

  python -B test_callgraph_memory.py

Each case: r4 = a function's address, stw r4 to 0x10(r3), then the overwriting store, lwz r5 from 0x10(r3), mtctr r5,
bctrl. With the store remembered past the overwrite the call is "resolved" to that function; it must stay unresolved.
A control case (an unrelated slot overwritten) keeps the call resolved.
"""
import os
import sys
import tempfile

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
F1, F2 = 0x82100000, 0x82200000


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def function(overwrite):
    return [15 << 26 | 4 << 21 | F2 >> 16,                     # lis  r4, F2 >> 16
            14 << 26 | 4 << 21 | 4 << 16,                      # addi r4, r4, 0
            36 << 26 | 4 << 21 | 3 << 16 | 0x10,               # stw  r4, 0x10(r3)
            overwrite,
            32 << 26 | 5 << 21 | 3 << 16 | 0x10,               # lwz  r5, 0x10(r3)
            31 << 26 | 5 << 21 | 9 << 16 | 467 << 1,           # mtctr r5
            0x4E800421, 0x4E800020]                            # bctrl, blr


def main():
    with tempfile.TemporaryDirectory() as root:          # the module asks for a workspace; nothing is read from it
        os.makedirs(os.path.join(root, 'work'))
        os.makedirs(os.path.join(root, 'research'))
        os.environ['BF1943_RESEARCH'] = root
        sys.path.insert(0, TOOLS)
        import export_callgraph_strings as E
    cases = [('stfs f1, 0x10(r3)', 52 << 26 | 1 << 21 | 3 << 16 | 0x10, False),
             ('stfd f1, 0x0c(r3) (8 bytes over the slot)', 54 << 26 | 1 << 21 | 3 << 16 | 0x0C, False),
             ('dcbz r3, r6', 31 << 26 | 3 << 16 | 6 << 11 | 1014 << 1, False),
             ('stvx128 v1, r3, r6', 0x100001C3 | 1 << 21 | 3 << 16 | 6 << 11, False),
             ('stvlx128 v1, r3, r6', 0x10000503 | 1 << 21 | 3 << 16 | 6 << 11, False),
             ('stfs f1, 0x20(r3) (another slot)', 52 << 26 | 1 << 21 | 3 << 16 | 0x20, True)]
    for what, word, resolved in cases:
        code = function(word)

        class Image:
            end = 0x83000000
            import_thunks, import_slots = {}, {}

            def word(self, a):
                return code[(a - F1) // 4] if F1 <= a < F1 + 4 * len(code) else 0x4E800020

            def section(self, a):
                return dict(name='.text', write=False)

            def readonly(self, a):
                return False
        an = E.Analysis(Image(), [E.Function(F1, F1 + 4 * len(code), 'f1'), E.Function(F2, F2 + 4, 'f2')], {})
        an.run_function(an.starts[F1])
        kinds = [(kind, callee) for _, _, callee, kind, _ in an.edges]
        got = ('indirect_resolved', F2) in kinds
        check(got == resolved, f'{what}: the call through the reloaded slot is {"resolved" if got else "unresolved"}'
                               f' (expected {"resolved" if resolved else "unresolved"})')


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
