"""Record the completed VMX128 regression run; refuses to write evidence unless every suite passed, and every result
was stamped by run_vmx.ps1 with the fingerprint of the current inputs (vmx_inputs.py): a result left over from an
earlier run, or a partial run (-Only), cannot be recorded against today's sources.

  python -B record_vmx_validation.py [--module-changed]

The language module's other files must also equal the ones the last record names; --module-changed accepts a module
that differs from it (a change made on purpose, then run with run_vmx.ps1)."""
from pathlib import Path
import hashlib,json
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
import vmx_inputs  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
def at(f):
    return Path(__file__).resolve().parent / f[len('bf1943-xex-tools/'):] if f.startswith('bf1943-xex-tools/') else ROOT / f
VMX=ROOT/'work/vmx128'
suites=vmx_inputs.SUITES
probes=vmx_inputs.PROBES
results={name:(VMX/name).read_text() for name in suites+probes}
current=vmx_inputs.fingerprint(ROOT)
stale=[name for name in suites+probes if vmx_inputs.stamp_of(results[name])!=current]
if stale:
    _sys.exit(f'refused: these results were not produced from the current inputs (fingerprint {current[:12]}): '
              f'{", ".join(stale)}; run run_vmx.ps1 in full (no -Only), then record')
for name in suites:
    assert any(line.startswith('PASS COMPLETE') for line in results[name].splitlines()),name
for name in probes:
    assert 'unknown_words=0' in results[name] and 'decompile_completed=true' in results[name],name
image='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
assert hashlib.sha256((ROOT/'work/xex-analysis/default.checked.memory.bin').read_bytes()).hexdigest()==image
language=json.loads((ROOT/'research/evidence/vmx128-language-inputs.json').read_text())
# What the INPUTS fingerprint does not cover (2026-10-30; the fingerprint itself is unchanged, since run_vmx.ps1 is one of
# its inputs): checked here and recorded in the evidence as `environment`.
# - The program the tests ran on: run_vmx.ps1 re-uses the Ghidra project's saved program once it exists, so the hashed
#   image file is not necessarily what was tested. The caller probes hash that program's memory: it must be the image.
wrong_program=[name for name in probes if f'image_sha256={image}' not in results[name].splitlines()]
if wrong_program:
    _sys.exit(f'refused: the tested program\'s memory is not the checked image ({", ".join(wrong_program)}); '
              'delete work/vmx128/project and run run_vmx.ps1 again')
# - The rest of the language module (the copied Ghidra pspec/cspec, includes, ldefs): none may be newer than the run's
#   results (it would have changed after the tests ran); every file's hash goes into the evidence.
MODULE=VMX/'XenonVMX128'
run_written=min((VMX/name).stat().st_mtime for name in suites+probes)
module_files={p.relative_to(MODULE).as_posix():p for p in sorted(MODULE.rglob('*')) if p.is_file()}
newer=[rel for rel,p in module_files.items() if p.stat().st_mtime>run_written]
if newer:
    _sys.exit(f'refused: the language module changed after the run ({", ".join(newer)}); run run_vmx.ps1 again, then record')
module_hashes={rel:hashlib.sha256(p.read_bytes()).hexdigest() for rel,p in module_files.items()}
#   The mtime check misses a file replaced by a copy that keeps an older mtime (Copy-Item, shutil.copy2, unzip), and
#   run_vmx.ps1 cannot hash the module at run time (it is a fingerprint input). So the module must also equal the one
#   the evidence's last record names (2026-10-31): a module that differs is refused unless --module-changed says the
#   change is meant (and the run came after it: the mtime check above). The first record with module hashes has
#   nothing to compare with, and says so.
EVIDENCE=ROOT/'research/evidence/vmx128-decode-validation.json'
previous=(json.loads(EVIDENCE.read_text(encoding='utf-8')).get('environment') or {}).get('module_files') if EVIDENCE.is_file() else None
if previous is not None and previous!=module_hashes and '--module-changed' not in _sys.argv[1:]:
    changed=sorted(rel for rel in set(previous)|set(module_hashes) if previous.get(rel)!=module_hashes.get(rel))
    _sys.exit(f'refused: the language module differs from the last record ({", ".join(changed)}); if that change is meant, '
              'run run_vmx.ps1 after it and record with --module-changed')
module_compared=('none: the last record named no module files' if previous is None else
                 'the last record\'s module files: the same' if previous==module_hashes else
                 'the last record\'s module files: changed, accepted with --module-changed')
# - Ghidra and Java: the installs run_vmx.ps1 runs, read from run_vmx.ps1 itself (a fingerprint input, so what ran).
#   Before 2026-10-31 the recorder took BF1943_GHIDRA / BF1943_JAVA, which run_vmx.ps1 ignores (only map_project.ps1
#   reads them): with either set, the evidence named an install the run did not use.
import re as _re
run_vmx=(Path(__file__).resolve().parent/'run_vmx.ps1').read_text(encoding='utf-8')
java_pins=set(_re.findall(r"& '([^']+?java\.exe)'",run_vmx))
ghidra_pins=set(_re.findall(r"'([^']+?)/Ghidra/Framework/Utility/lib/Utility\.jar'",run_vmx))
if len(java_pins)!=1 or len(ghidra_pins)!=1:
    _sys.exit(f'refused: run_vmx.ps1 does not name one Java and one Ghidra (Java {sorted(java_pins)}, Ghidra {sorted(ghidra_pins)})')
GHIDRA=Path(ghidra_pins.pop())
JAVA=Path(java_pins.pop())
properties=GHIDRA/'Ghidra/application.properties'
environment=dict(
    note='Not in the INPUTS fingerprint; module files are checked to be no newer than the results and compared with the last record; Ghidra and Java are the installs run_vmx.ps1 names, as installed when recorded',
    program=f'the caller probes hashed the tested program\'s memory: {image}',
    module_files=module_hashes,
    module_compared=module_compared,
    ghidra=dict(path=GHIDRA.as_posix(),
                application_properties=[l for l in properties.read_text(encoding='utf-8',errors='replace').splitlines()
                                        if l.startswith(('application.version=','application.release.name='))] if properties.is_file() else None,
                properties_sha256=hashlib.sha256(properties.read_bytes()).hexdigest() if properties.is_file() else None),
    java=dict(path=JAVA.as_posix(),sha256=hashlib.sha256(JAVA.read_bytes()).hexdigest() if JAVA.is_file() else None))
files=['bf1943-xex-tools/build_vmx_language.py','bf1943-xex-tools/run_vmx.ps1','bf1943-xex-tools/TestVmx.java','bf1943-xex-tools/TestLogical.java',
    'bf1943-xex-tools/TestPermutation.java','bf1943-xex-tools/TestRlimi.java','bf1943-xex-tools/TestUnaligned.java','bf1943-xex-tools/TestDecodeCoverage.java','bf1943-xex-tools/InspectVmxCaller.java',
    'bf1943-xex-tools/TestVectorSemantics.java','bf1943-xex-tools/TestScalarFp.java','bf1943-xex-tools/xenon_vector_semantics.py','bf1943-xex-tools/xenon_splice.py',
    'work/vmx128/XenonVMX128/data/languages/xenon.slaspec','work/vmx128/XenonVMX128/data/languages/xenon.sla','work/vmx128/vx128-table.tsv',
    'work/vmx128/classified.tsv','work/vmx128/decode-coverage.tsv','work/vmx128/unclassified-decode.tsv',
    'work/tools/xenia-95a5c3ee/ppc_emit_altivec.cc','work/tools/xenia-95a5c3ee/ppc_instr.h']+[f'work/vmx128/{n}' for n in suites+probes]
report=dict(status='pass',image_sha256=image,validated=language['validated'],opaque_mnemonics=sorted(o['mnemonic'] for o in language['opaque'] if o['pcodeop'].startswith('VMX128_unimplemented_')),
    modelled_vmx128=language['modelled_vmx128'],modelled_altivec=language['modelled_altivec'],scalar_fp_overrides=language['scalar_fp_overrides'],
    semantics_version=language['semantics_version'],still_opaque_reasons=language['still_opaque_reasons'],
    partially_opaque=['vrlimi128 when UIMM bit 4 is set (VMX128_unresolved_vrlimi128_imm4)'],
    removed_inherited=language['removed_inherited'],results=results,environment=environment,
    references=dict(xenonrecomp_archive_sha256=language['reference_archive_sha256'],ppc_dis_member=language['reference_member'],ppc_dis_sha256=language['reference_member_sha256'],
        xenia_commit='95a5c3ee250f80c3b9d139658649d9ffb6db3eec',xenia_files='work/tools/xenia-95a5c3ee'),
    sha256={f:hashlib.sha256(at(f).read_bytes()).hexdigest() for f in files},
    limits=['Float lanes are IEEE single precision, round to nearest even: VSCR NJ (denormal flush) and SAT, NaN payloads and fused multiply-add rounding are not modelled',
        'vrefp/vrsqrtefp (and fres/frsqrte) are modelled as exact reciprocals; the hardware returns estimates',
        'vmsum3fp128/vmsum4fp128 sum in lane order with each product rounded; the hardware summation order and rounding are not known',
        'vexptefp/vlogefp (+128) are the named lane ops exp2_estimate / log2_estimate (p-code has no 2^x or log2; the hardware estimates are not modelled bit for bit)',
        'vupkd3d128: UIMM 20 is IEEE half to float on bytes 8-15; UIMM 4 has lanes 2-3 = 0.0 / 1.0 and lanes 0-1 opaque (vupkd3d128_xy_unknown); other UIMM values opaque',
        'Still opaque: vpkd3d128 (placement and conversion not established), vupkd3d128 lanes 0-1 of UIMM 4, vrlimi128 with UIMM bit 4 (0 in image)',
        'vupkhsb128/vupklsb128 follow AltiVec vupkhsb/vupklsb (signed byte to halfword); the VMX128 forms are not checked against hardware',
        'Element loads (lvebx/lvehx/lvewx/lvewx128) zero the other lanes, which the architecture leaves undefined; fctiw/fctiwz leave the high word as a sign extension (architecturally undefined)',
        'Tests compare against our own models of the instruction definitions plus hand-computed literal vectors; they are not hardware conformance'])
(ROOT/'research/evidence/vmx128-decode-validation.json').write_text(encoding='utf-8',newline='',data=json.dumps(report,indent=2)+'\n')
print('recorded',len(files),'hashes')
