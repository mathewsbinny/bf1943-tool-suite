"""record_vmx_validation.py on a scratch workspace: what the INPUTS fingerprint does not cover is checked and recorded.

  python -B test_record_vmx.py

Needs the checked image (the recorder checks its hash): BF1943_RESEARCH, else operation-rising-sun next to the suite;
only the image is copied from there, into a temporary folder, and nothing is written to the workspace. Every other
input is made up: the results are stamped with the scratch inputs' fingerprint as run_vmx.ps1 stamps them. Checked:
a whole run is recorded with its `environment` (every language-module file's hash, Ghidra, Java, the tested program);
a module file changed after the run is refused; caller probes whose program memory is not the image are refused.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.environ.get('BF1943_RESEARCH') or os.path.join(os.path.dirname(os.path.dirname(TOOLS)), 'operation-rising-sun')
IMAGE = 'work/xex-analysis/default.checked.memory.bin'
IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def main():
    if not os.path.isfile(os.path.join(SOURCE, IMAGE)):
        print(f'skip VMX recorder: no checked image at {os.path.join(SOURCE, IMAGE)}')
        return
    sys.path.insert(0, TOOLS)
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, 'research', 'evidence'))
        os.makedirs(os.path.join(root, 'work', 'xex-analysis'))
        shutil.copyfile(os.path.join(SOURCE, IMAGE), os.path.join(root, IMAGE))
        os.environ['BF1943_RESEARCH'] = root
        import vmx_inputs
        module = 'work/vmx128/XenonVMX128/data/languages/'
        for rel in vmx_inputs.INPUTS + [module + 'ppc_64.pspec', module + 'ppc_64_32.cspec', module + 'xenon.ldefs',
                                        'work/vmx128/decode-coverage.tsv', 'work/vmx128/unclassified-decode.tsv',
                                        'work/tools/xenia-95a5c3ee/ppc_emit_altivec.cc', 'work/tools/xenia-95a5c3ee/ppc_instr.h']:
            if not rel.startswith('bf1943-xex-tools/') and rel != IMAGE:
                write(root, rel, f'made up: {rel}\n')
        write(root, 'research/evidence/vmx128-language-inputs.json', json.dumps(dict(
            validated=[], opaque=[], modelled_vmx128=[], modelled_altivec=[], scalar_fp_overrides=[], semantics_version='x',
            still_opaque_reasons={}, removed_inherited=[], reference_archive_sha256='a', reference_member='m',
            reference_member_sha256='b')))
        time.sleep(0.05)
        stamp = vmx_inputs.fingerprint(root)

        def results(program_image=IMAGE_SHA256):
            for name in vmx_inputs.SUITES:
                write(root, f'work/vmx128/{name}', f'PASS COMPLETE made up\nINPUTS {stamp}\n')
            for name in vmx_inputs.PROBES:
                write(root, f'work/vmx128/{name}', f'decoded=1 unknown_words=0 vor128=0 decompile_completed=true diagnostic=\n'
                                                   f'image_sha256={program_image}\nINPUTS {stamp}\n')

        def record():
            return subprocess.run([sys.executable, '-B', os.path.join(TOOLS, 'record_vmx_validation.py')], cwd=root,
                                  env=dict(os.environ, BF1943_RESEARCH=root), capture_output=True, text=True)
        evidence = os.path.join(root, 'research', 'evidence', 'vmx128-decode-validation.json')
        results()
        r = record()
        env = json.load(open(evidence, encoding='utf-8')).get('environment') if r.returncode == 0 else None
        check(r.returncode == 0 and env is not None, 'a whole run is recorded with its environment'
              + ('' if r.returncode == 0 else ': ' + (r.stderr or r.stdout)[-300:]))
        if env:
            pspec = open(os.path.join(root, module, 'ppc_64.pspec'), 'rb').read()
            check(env['module_files'].get('data/languages/ppc_64.pspec') == hashlib.sha256(pspec).hexdigest()
                  and 'data/languages/ppc_64_32.cspec' in env['module_files'] and 'ghidra' in env and 'java' in env
                  and IMAGE_SHA256 in env['program'],
                  'the environment names every module file (the pspec and cspec too), Ghidra, Java and the tested program')
        os.remove(evidence)
        time.sleep(0.05)
        write(root, module + 'ppc_64.pspec', 'changed after the run\n')
        r = record()
        check(r.returncode != 0 and 'module changed after the run' in r.stderr and not os.path.exists(evidence),
              'a pspec changed after the run (not in the fingerprint) is refused')
        time.sleep(0.05)
        results(program_image='0' * 64)
        r = record()
        check(r.returncode != 0 and 'not the checked image' in r.stderr and not os.path.exists(evidence),
              'caller probes that hashed another program (a stale project) are refused')


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
