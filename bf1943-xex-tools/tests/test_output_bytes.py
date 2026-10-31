"""The xex-tools' outputs that evidence (or vmx_inputs.INPUTS) pins by hash are written byte for byte as recorded.

  python -B test_output_bytes.py

Runs map_functions.py, classify_vmx.py, inventory_vmx.py and resolve_helpers.py on a scratch copy of the research
workspace's inputs (BF1943_RESEARCH, else operation-rising-sun next to the suite; nothing is written there) and compares
each pinned output with the workspace's recorded file: a rerun must not change the bytes the evidence names (a
line-ending change alone would stale the VMX fingerprint and five evidence files). Skips without a workspace.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(TOOLS)
SOURCE = os.environ.get('BF1943_RESEARCH') or os.path.join(os.path.dirname(SUITE), 'operation-rising-sun')
failures = []
# (tool, [pinned outputs]): each output is compared with the workspace's copy
STEPS = [('map_functions.py', ['work/xex-analysis/functions.tsv', 'work/xex-analysis/sections.tsv']),
         ('classify_vmx.py', ['work/vmx128/classified.tsv']),
         ('inventory_vmx.py', ['work/vmx128/inventory.tsv']),
         ('resolve_helpers.py', ['work/xex-analysis/helpers.tsv', 'work/xex-analysis/helper-entries.tsv',
                                 'work/xex-analysis/helper-branches.tsv'])]   # not pinned by hash, but a rerun keeps it
# pinned evidence whose bytes also hold the tool's own hash: only its line endings are compared
LINE_ENDINGS = ['research/evidence/xex-functions.json', 'research/evidence/xex-helper-resolution.json']
INPUTS = ['work/xex-analysis/default.checked.memory.bin']
XENONRECOMP = ['XenonRecomp-main/thirdparty/disasm/ppc-dis.c', 'XenonRecomp-main/XenonRecomp/recompiler.cpp',
               'XenonRecomp-main/XenonUtils/ppc_context.h', 'XenonRecomp-main/XenonUtils/xbox.h', 'XenonRecomp-main/README.md']


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def endings(path):
    data = open(path, 'rb').read()
    crlf = data.count(b'\r\n')
    return 'crlf' if crlf and crlf == data.count(b'\n') else 'lf' if not crlf else 'mixed'


def main():
    needed = INPUTS + [o for _, outs in STEPS for o in outs] + LINE_ENDINGS
    if not all(os.path.isfile(os.path.join(SOURCE, p)) for p in needed):
        print(f'skip pinned output bytes: no research workspace with the recorded outputs at {SOURCE}')
        return
    with tempfile.TemporaryDirectory() as root:
        for d in ('work/xex-analysis', 'work/vmx128', 'research/evidence'):
            os.makedirs(os.path.join(root, d))
        for p in INPUTS:
            shutil.copyfile(os.path.join(SOURCE, p), os.path.join(root, p))
        for m in XENONRECOMP:
            src = os.path.join(SOURCE, 'Tools', 'XenonRecomp', m)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(os.path.join(root, 'Tools', 'XenonRecomp', m)), exist_ok=True)
                shutil.copyfile(src, os.path.join(root, 'Tools', 'XenonRecomp', m))
        env = dict(os.environ, BF1943_RESEARCH=root)
        for tool, outs in STEPS:
            r = subprocess.run([sys.executable, '-B', os.path.join(TOOLS, tool)], cwd=root, env=env, capture_output=True,
                               text=True)
            check(r.returncode == 0, f'{tool} runs on the scratch workspace' + ('' if r.returncode == 0 else ': ' + r.stderr[-300:]))
            if r.returncode:
                return
            for o in outs:
                check(sha(os.path.join(root, o)) == sha(os.path.join(SOURCE, o)),
                      f'{tool}: {o} is byte-identical to the recorded file (its hash is pinned)')
        for o in LINE_ENDINGS:
            check(endings(os.path.join(root, o)) == endings(os.path.join(SOURCE, o)),
                  f'{o}: written with the recorded line endings ({endings(os.path.join(SOURCE, o))})')


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
