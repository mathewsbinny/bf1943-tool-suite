"""Record the completed VMX128 regression run; refuses to write evidence unless every suite passed."""
from pathlib import Path
import hashlib,json
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
def at(f):
    return Path(__file__).resolve().parent / f[len('bf1943-xex-tools/'):] if f.startswith('bf1943-xex-tools/') else ROOT / f
VMX=ROOT/'work/vmx128'
suites=['validation.txt','logical-validation.txt','permutation-validation.txt','rlimi-validation.txt','unaligned-validation.txt','decode-coverage.txt']
probes=['caller-validation.txt','permutation-caller-validation.txt']
results={name:(VMX/name).read_text() for name in suites+probes}
for name in suites:
    assert results[name].splitlines()[-1].startswith('PASS COMPLETE'),name
for name in probes:
    assert 'unknown_words=0' in results[name] and 'decompile_completed=true' in results[name],name
image='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
assert hashlib.sha256((ROOT/'work/xex-analysis/default.checked.memory.bin').read_bytes()).hexdigest()==image
language=json.loads((ROOT/'research/evidence/vmx128-language-inputs.json').read_text())
files=['bf1943-xex-tools/build_vmx_language.py','bf1943-xex-tools/run_vmx.ps1','bf1943-xex-tools/TestVmx.java','bf1943-xex-tools/TestLogical.java',
    'bf1943-xex-tools/TestPermutation.java','bf1943-xex-tools/TestRlimi.java','bf1943-xex-tools/TestUnaligned.java','bf1943-xex-tools/TestDecodeCoverage.java','bf1943-xex-tools/InspectVmxCaller.java',
    'work/vmx128/XenonVMX128/data/languages/xenon.slaspec','work/vmx128/XenonVMX128/data/languages/xenon.sla','work/vmx128/vx128-table.tsv',
    'work/vmx128/classified.tsv','work/vmx128/decode-coverage.tsv','work/vmx128/unclassified-decode.tsv',
    'work/tools/xenia-95a5c3ee/ppc_emit_altivec.cc','work/tools/xenia-95a5c3ee/ppc_instr.h']+[f'work/vmx128/{n}' for n in suites+probes]
report=dict(status='pass',image_sha256=image,validated=language['validated'],opaque_mnemonics=sorted(o['mnemonic'] for o in language['opaque'] if o['pcodeop'].startswith('VMX128_unimplemented_')),
    partially_opaque=['vrlimi128 when UIMM bit 4 is set (VMX128_unresolved_vrlimi128_imm4)'],
    removed_inherited=language['removed_inherited'],results=results,
    references=dict(xenonrecomp_archive_sha256=language['reference_archive_sha256'],ppc_dis_member=language['reference_member'],ppc_dis_sha256=language['reference_member_sha256'],
        xenia_commit='95a5c3ee250f80c3b9d139658649d9ffb6db3eec',xenia_files='work/tools/xenia-95a5c3ee'),
    sha256={f:hashlib.sha256(at(f).read_bytes()).hexdigest() for f in files},
    limits=['vmulfp128 and vmsum3fp128 are opaque by design; no numeric float semantics were added','Opaque results are unknown values, not approximations',
        'Opaque loads/stores do not model memory effects; VSCR effects are not modeled','vrlimi128 with UIMM bit 4 set is opaque (0 occurrences in image)',
        'Inherited original-AltiVec semantics are Ghidra stubs and were not audited','Synthetic p-code tests are not hardware conformance'])
(ROOT/'research/evidence/vmx128-decode-validation.json').write_text(json.dumps(report,indent=2)+'\n')
print('recorded',len(files),'hashes')
