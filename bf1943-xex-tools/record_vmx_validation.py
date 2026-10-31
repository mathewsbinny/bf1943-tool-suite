"""Record the completed VMX128 regression run; refuses to write evidence unless every suite passed, and every result
was stamped by run_vmx.ps1 with the fingerprint of the current inputs (vmx_inputs.py): a result left over from an
earlier run, or a partial run (-Only), cannot be recorded against today's sources.

  python -B record_vmx_validation.py [--module-changed]

The language module's other files must also equal the ones the last record names; --module-changed accepts a module
that differs from it (a change made on purpose, then run with run_vmx.ps1)."""
from pathlib import Path
import hashlib
import json
import re
import sys as _sys

from workspace import research_root  # noqa: E402
import vmx_inputs  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
# the files whose hashes the evidence records (the INPUTS, the decode-coverage tables the run writes, Xenia's
# reference sources), then each result file
HASHED = ['bf1943-xex-tools/build_vmx_language.py', 'bf1943-xex-tools/run_vmx.ps1', 'bf1943-xex-tools/TestVmx.java',
          'bf1943-xex-tools/TestLogical.java', 'bf1943-xex-tools/TestPermutation.java', 'bf1943-xex-tools/TestRlimi.java',
          'bf1943-xex-tools/TestUnaligned.java', 'bf1943-xex-tools/TestDecodeCoverage.java',
          'bf1943-xex-tools/InspectVmxCaller.java', 'bf1943-xex-tools/TestVectorSemantics.java',
          'bf1943-xex-tools/TestScalarFp.java', 'bf1943-xex-tools/xenon_vector_semantics.py',
          'bf1943-xex-tools/xenon_splice.py', 'work/vmx128/XenonVMX128/data/languages/xenon.slaspec',
          'work/vmx128/XenonVMX128/data/languages/xenon.sla', 'work/vmx128/vx128-table.tsv',
          'work/vmx128/classified.tsv', 'work/vmx128/decode-coverage.tsv', 'work/vmx128/unclassified-decode.tsv',
          'work/tools/xenia-95a5c3ee/ppc_emit_altivec.cc', 'work/tools/xenia-95a5c3ee/ppc_instr.h']
LIMITS = ['Float lanes are IEEE single precision, round to nearest even: VSCR NJ (denormal flush) and SAT, NaN payloads and fused multiply-add rounding are not modelled',
          'vrefp/vrsqrtefp (and fres/frsqrte) are modelled as exact reciprocals; the hardware returns estimates',
          'vmsum3fp128/vmsum4fp128 sum in lane order with each product rounded; the hardware summation order and rounding are not known',
          'vexptefp/vlogefp (+128) are the named lane ops exp2_estimate / log2_estimate (p-code has no 2^x or log2; the hardware estimates are not modelled bit for bit)',
          'vupkd3d128: UIMM 20 is IEEE half to float on bytes 8-15; UIMM 4 has lanes 2-3 = 0.0 / 1.0 and lanes 0-1 opaque (vupkd3d128_xy_unknown); other UIMM values opaque',
          'Still opaque: vpkd3d128 (placement and conversion not established), vupkd3d128 lanes 0-1 of UIMM 4, vrlimi128 with UIMM bit 4 (0 in image)',
          'vupkhsb128/vupklsb128 follow AltiVec vupkhsb/vupklsb (signed byte to halfword); the VMX128 forms are not checked against hardware',
          'Element loads (lvebx/lvehx/lvewx/lvewx128) zero the other lanes, which the architecture leaves undefined; fctiw/fctiwz leave the high word as a sign extension (architecturally undefined)',
          'Tests compare against our own models of the instruction definitions plus hand-computed literal vectors; they are not hardware conformance']


def path_of(name, root):
    """A HASHED name's file: the suite's own tools are here, the rest are in the research workspace."""
    if name.startswith('bf1943-xex-tools/'):
        return HERE / name[len('bf1943-xex-tools/'):]
    return root / name


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_module(vmx, names, module_changed, evidence):
    """The rest of the language module (the copied Ghidra pspec/cspec, includes, ldefs): none may be newer than the
    run's results (it would have changed after the tests ran), and it must equal the last record's (below). Returns
    ({file: sha256}, how it compared with the last record); exits when it is refused."""
    module = vmx / 'XenonVMX128'
    run_written = min((vmx / name).stat().st_mtime for name in names)
    module_files = {path.relative_to(module).as_posix(): path for path in sorted(module.rglob('*')) if path.is_file()}
    newer = [rel for rel, path in module_files.items() if path.stat().st_mtime > run_written]
    if newer:
        _sys.exit(f'refused: the language module changed after the run ({", ".join(newer)}); run run_vmx.ps1 again, then record')
    module_hashes = {rel: sha(path) for rel, path in module_files.items()}
    #   The mtime check misses a file replaced by a copy that keeps an older mtime (Copy-Item, shutil.copy2, unzip), and
    #   run_vmx.ps1 cannot hash the module at run time (it is a fingerprint input). So the module must also equal the one
    #   the evidence's last record names (2026-10-31): a module that differs is refused unless --module-changed says the
    #   change is meant (and the run came after it: the mtime check above). The first record with module hashes has
    #   nothing to compare with, and says so.
    previous = None
    if evidence.is_file():
        previous = (json.loads(evidence.read_text(encoding='utf-8')).get('environment') or {}).get('module_files')
    if previous is not None and previous != module_hashes and not module_changed:
        changed = sorted(rel for rel in set(previous) | set(module_hashes) if previous.get(rel) != module_hashes.get(rel))
        _sys.exit(f'refused: the language module differs from the last record ({", ".join(changed)}); if that change is meant, '
                  'run run_vmx.ps1 after it and record with --module-changed')
    if previous is None:
        compared = 'none: the last record named no module files'
    elif previous == module_hashes:
        compared = 'the last record\'s module files: the same'
    else:
        compared = 'the last record\'s module files: changed, accepted with --module-changed'
    return module_hashes, compared


def pinned_installs():
    """(Ghidra, Java): the installs run_vmx.ps1 runs, read from run_vmx.ps1 itself (a fingerprint input, so what ran).
    Before 2026-10-31 the recorder took BF1943_GHIDRA / BF1943_JAVA, which run_vmx.ps1 ignores (only map_project.ps1
    reads them): with either set, the evidence named an install the run did not use."""
    run_vmx = (HERE / 'run_vmx.ps1').read_text(encoding='utf-8')
    java_pins = set(re.findall(r"& '([^']+?java\.exe)'", run_vmx))
    ghidra_pins = set(re.findall(r"'([^']+?)/Ghidra/Framework/Utility/lib/Utility\.jar'", run_vmx))
    if len(java_pins) != 1 or len(ghidra_pins) != 1:
        _sys.exit(f'refused: run_vmx.ps1 does not name one Java and one Ghidra (Java {sorted(java_pins)}, Ghidra {sorted(ghidra_pins)})')
    return Path(ghidra_pins.pop()), Path(java_pins.pop())


def ghidra_record(ghidra):
    properties = ghidra / 'Ghidra/application.properties'
    if not properties.is_file():
        return dict(path=ghidra.as_posix(), application_properties=None, properties_sha256=None)
    lines = properties.read_text(encoding='utf-8', errors='replace').splitlines()
    return dict(path=ghidra.as_posix(),
                application_properties=[line for line in lines
                                        if line.startswith(('application.version=', 'application.release.name='))],
                properties_sha256=sha(properties))


def main():
    root = research_root()              # the research workspace (workspace.py)
    vmx = root / 'work/vmx128'
    suites = vmx_inputs.SUITES
    probes = vmx_inputs.PROBES
    results = {name: (vmx / name).read_text() for name in suites + probes}
    current = vmx_inputs.fingerprint(root)
    stale = [name for name in suites + probes if vmx_inputs.stamp_of(results[name]) != current]
    if stale:
        _sys.exit(f'refused: these results were not produced from the current inputs (fingerprint {current[:12]}): '
                  f'{", ".join(stale)}; run run_vmx.ps1 in full (no -Only), then record')
    for name in suites:
        assert any(line.startswith('PASS COMPLETE') for line in results[name].splitlines()), name
    for name in probes:
        assert 'unknown_words=0' in results[name] and 'decompile_completed=true' in results[name], name
    assert sha(root / 'work/xex-analysis/default.checked.memory.bin') == IMAGE_SHA256
    language = json.loads((root / 'research/evidence/vmx128-language-inputs.json').read_text())
    # What the INPUTS fingerprint does not cover (2026-10-30; the fingerprint itself is unchanged, since run_vmx.ps1 is one of
    # its inputs): checked here and recorded in the evidence as `environment`.
    # - The program the tests ran on: run_vmx.ps1 re-uses the Ghidra project's saved program once it exists, so the hashed
    #   image file is not necessarily what was tested. The caller probes hash that program's memory: it must be the image.
    wrong_program = [name for name in probes if f'image_sha256={IMAGE_SHA256}' not in results[name].splitlines()]
    if wrong_program:
        _sys.exit(f'refused: the tested program\'s memory is not the checked image ({", ".join(wrong_program)}); '
                  'delete work/vmx128/project and run run_vmx.ps1 again')
    # - The rest of the language module: no newer than the run, and the same as the last record's (check_module).
    evidence = root / 'research/evidence/vmx128-decode-validation.json'
    module_hashes, module_compared = check_module(vmx, suites + probes, '--module-changed' in _sys.argv[1:], evidence)
    # - Ghidra and Java: the installs run_vmx.ps1 names (pinned_installs).
    ghidra, java = pinned_installs()
    environment = dict(
        note='Not in the INPUTS fingerprint; module files are checked to be no newer than the results and compared with the last record; Ghidra and Java are the installs run_vmx.ps1 names, as installed when recorded',
        program=f'the caller probes hashed the tested program\'s memory: {IMAGE_SHA256}',
        module_files=module_hashes,
        module_compared=module_compared,
        ghidra=ghidra_record(ghidra),
        java=dict(path=java.as_posix(), sha256=sha(java) if java.is_file() else None))
    files = HASHED + [f'work/vmx128/{name}' for name in suites + probes]
    opaque_mnemonics = sorted(entry['mnemonic'] for entry in language['opaque']
                              if entry['pcodeop'].startswith('VMX128_unimplemented_'))
    report = dict(
        status='pass', image_sha256=IMAGE_SHA256, validated=language['validated'], opaque_mnemonics=opaque_mnemonics,
        modelled_vmx128=language['modelled_vmx128'], modelled_altivec=language['modelled_altivec'],
        scalar_fp_overrides=language['scalar_fp_overrides'],
        semantics_version=language['semantics_version'], still_opaque_reasons=language['still_opaque_reasons'],
        partially_opaque=['vrlimi128 when UIMM bit 4 is set (VMX128_unresolved_vrlimi128_imm4)'],
        removed_inherited=language['removed_inherited'], results=results, environment=environment,
        references=dict(xenonrecomp_archive_sha256=language['reference_archive_sha256'],
                        ppc_dis_member=language['reference_member'],
                        ppc_dis_sha256=language['reference_member_sha256'],
                        xenia_commit='95a5c3ee250f80c3b9d139658649d9ffb6db3eec', xenia_files='work/tools/xenia-95a5c3ee'),
        sha256={name: sha(path_of(name, root)) for name in files},
        limits=LIMITS)
    evidence.write_text(encoding='utf-8', newline='', data=json.dumps(report, indent=2) + '\n')
    print('recorded', len(files), 'hashes')


if __name__ == '__main__':
    main()
