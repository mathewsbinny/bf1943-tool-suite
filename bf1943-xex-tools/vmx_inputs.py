"""The inputs a VMX128 validation run tests, and one fingerprint over them, so that a result file can name exactly
what it tested: run_vmx.ps1 stamps "INPUTS <fingerprint>" into each result it produced (after the SLEIGH compile),
and record_vmx_validation.py refuses results whose stamp is not the current inputs' fingerprint (a result left
over from an earlier, partial run).

  python -B vmx_inputs.py            prints the fingerprint of the current inputs
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402

HERE = Path(__file__).resolve().parent
SUITES = ['validation.txt', 'logical-validation.txt', 'permutation-validation.txt', 'rlimi-validation.txt',
          'unaligned-validation.txt', 'decode-coverage.txt', 'vector-validation.txt', 'scalar-fp-validation.txt']
PROBES = ['caller-validation.txt', 'permutation-caller-validation.txt']
# every input of a run (tools, test scripts, the compiled language, its tables, the image); not the results
INPUTS = ['bf1943-xex-tools/build_vmx_language.py', 'bf1943-xex-tools/run_vmx.ps1', 'bf1943-xex-tools/TestVmx.java',
          'bf1943-xex-tools/TestLogical.java', 'bf1943-xex-tools/TestPermutation.java', 'bf1943-xex-tools/TestRlimi.java',
          'bf1943-xex-tools/TestUnaligned.java', 'bf1943-xex-tools/TestDecodeCoverage.java',
          'bf1943-xex-tools/InspectVmxCaller.java', 'bf1943-xex-tools/TestVectorSemantics.java',
          'bf1943-xex-tools/TestScalarFp.java', 'bf1943-xex-tools/xenon_vector_semantics.py',
          'bf1943-xex-tools/xenon_splice.py', 'work/vmx128/XenonVMX128/data/languages/xenon.slaspec',
          'work/vmx128/XenonVMX128/data/languages/xenon.sla', 'work/vmx128/vx128-table.tsv',
          'work/vmx128/classified.tsv', 'work/xex-analysis/functions.tsv',
          'work/xex-analysis/default.checked.memory.bin']
# decode-coverage.tsv and unclassified-decode.tsv are not inputs: TestDecodeCoverage.java writes them during the run
# (after the fingerprint is taken); the recorder hashes them with the results


def path_of(f, root=None):
    root = root or research_root()
    return HERE / f[len('bf1943-xex-tools/'):] if f.startswith('bf1943-xex-tools/') else Path(root) / f


def hashes(root=None):
    return {f: hashlib.sha256(path_of(f, root).read_bytes()).hexdigest() for f in INPUTS}


def fingerprint(root=None):
    h = hashlib.sha256()
    for f, d in sorted(hashes(root).items()):
        h.update(f'{f} {d}\n'.encode())
    return h.hexdigest()


def stamp_of(text):
    """The INPUTS fingerprint a result file was stamped with, or None."""
    for line in reversed(text.splitlines()):
        if line.startswith('INPUTS '):
            return line.split()[1]
    return None


if __name__ == '__main__':
    print(fingerprint())
