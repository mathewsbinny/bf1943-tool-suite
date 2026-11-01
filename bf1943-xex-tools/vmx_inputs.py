"""The inputs a VMX128 validation run tests, and one fingerprint over them, so that a result file can name exactly
what it tested: run_vmx.ps1 stamps "INPUTS <fingerprint>" into each result it produced (after the SLEIGH compile),
and record_vmx_validation.py refuses results whose stamp is not the current inputs' fingerprint (a result left
over from an earlier, partial run).

  python -B vmx_inputs.py            prints the fingerprint of the current inputs
"""
import hashlib
from pathlib import Path

from workspace import research_root  # noqa: E402

HERE = Path(__file__).resolve().parent
SUITES = ['validation.txt', 'logical-validation.txt', 'permutation-validation.txt', 'rlimi-validation.txt',
          'unaligned-validation.txt', 'decode-coverage.txt', 'vector-validation.txt', 'scalar-fp-validation.txt']
PROBES = ['caller-validation.txt', 'permutation-caller-validation.txt']
# every input of a run (tools, test scripts, the compiled language, its tables, the image); not the results
INPUTS = ['bf1943-xex-tools/build_vmx_language.py', 'bf1943-xex-tools/run_vmx.ps1', 'bf1943-xex-tools/TestVmx.java',
          'bf1943-xex-tools/TestLogical.java', 'bf1943-xex-tools/TestPermutation.java',
          'bf1943-xex-tools/TestRlimi.java',
          'bf1943-xex-tools/TestUnaligned.java', 'bf1943-xex-tools/TestDecodeCoverage.java',
          'bf1943-xex-tools/InspectVmxCaller.java', 'bf1943-xex-tools/TestVectorSemantics.java',
          'bf1943-xex-tools/TestScalarFp.java', 'bf1943-xex-tools/xenon_vector_semantics.py',
          'bf1943-xex-tools/xenon_splice.py', 'work/vmx128/XenonVMX128/data/languages/xenon.slaspec',
          'work/vmx128/XenonVMX128/data/languages/xenon.sla', 'work/vmx128/vx128-table.tsv',
          'work/vmx128/classified.tsv', 'work/xex-analysis/functions.tsv',
          'work/xex-analysis/default.checked.memory.bin']
# decode-coverage.tsv and unclassified-decode.tsv are not inputs: TestDecodeCoverage.java writes them during the run
# (after the fingerprint is taken); the recorder hashes them with the results


def path_of(name: str, root: str | Path | None = None) -> Path:
    """An INPUTS name's file: the suite's own tools are here, the rest are in the research workspace."""
    root = root or research_root()
    if name.startswith('bf1943-xex-tools/'):
        return HERE / name[len('bf1943-xex-tools/'):]
    return Path(root) / name


def hashes(root: str | Path | None = None) -> dict[str, str]:
    """{INPUTS name: SHA-256 of its file}."""
    return {name: hashlib.sha256(path_of(name, root).read_bytes()).hexdigest() for name in INPUTS}


def fingerprint(root: str | Path | None = None) -> str:
    """One SHA-256 over every input's name and hash, in name order."""
    digest = hashlib.sha256()
    for name, file_hash in sorted(hashes(root).items()):
        digest.update(f'{name} {file_hash}\n'.encode())
    return digest.hexdigest()


def stamp_of(text: str) -> str | None:
    """The INPUTS fingerprint a result file was stamped with, or None."""
    for line in reversed(text.splitlines()):
        if line.startswith('INPUTS '):
            return line.split()[1]
    return None


if __name__ == '__main__':
    print(fingerprint())
