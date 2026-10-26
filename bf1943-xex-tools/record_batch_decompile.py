"""Record the batch decompilation run: per-file hashes, counts and the validation it depends on."""
from pathlib import Path
import csv,hashlib,json,collections
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
def at(f):
    return Path(__file__).resolve().parent / f[len('bf1943-xex-tools/'):] if f.startswith('bf1943-xex-tools/') else ROOT / f
WORK=ROOT/'work/xex-analysis'
image='cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(WORK/'default.checked.memory.bin')==image and sha(WORK/'default.memory.bin')==image
saved=(WORK/'saved-validation.txt').read_text()
assert saved.startswith('PASS functions=33232') and image in saved
rows=list(csv.DictReader((WORK/'decompiled-index.tsv').open(),delimiter='\t'))
assert len(rows)==33232
with (WORK/'decompiled-manifest.tsv').open('w',newline='') as out:
    out.write('file\tsha256\n')
    for r in rows:
        if r['file']:out.write(f"{r['file']}\t{sha(WORK/'decompiled'/r['file'])}\n")
count=lambda k,v:sum(r[k]==v for r in rows)
failures=[dict(address=r['address'],name=r['name'],decoded=r['decoded'],decompiled=r['decompiled'],style=r['style'],diagnostic=r['diagnostic']) for r in rows if r['decoded']!='true' or r['decompiled']!='true']
report=dict(summary=(WORK/'batch-decompile.txt').read_text().strip(),image_sha256=image,saved_validation=saved.strip(),language='PowerPC:BE:64:XenonVMX128',
    functions=len(rows),decoded=count('decoded','true'),decompiled_full_style=count('decompiled','true'),c_files=sum(bool(r['file']) for r in rows),
    terminal_call_overrides=[r['address'] for r in rows if r['terminal_call_override']=='true'],failures=failures,
    functions_with_opaque_vmx=sum(int(r['opaque_vmx_ops'])>0 for r in rows),opaque_vmx_ops=sum(int(r['opaque_vmx_ops']) for r in rows),
    words=sum(int(r['words']) for r in rows),instructions=sum(int(r['instructions']) for r in rows),unreached_words=sum(int(r['unreached_words']) for r in rows),
    sha256={f:sha(at(f)) for f in ['bf1943-xex-tools/export_all_decompiled.java','bf1943-xex-tools/run_batch_decompile.ps1','work/xex-analysis/decompiled-index.tsv',
        'work/xex-analysis/decompile-diagnostics.tsv','work/xex-analysis/decompiled-opaque-tally.tsv','work/xex-analysis/decompiled-manifest.tsv',
        'work/xex-analysis/batch-project-identity.txt','work/vmx128/XenonVMX128/data/languages/xenon.sla']},
    limits=['Pseudocode, not recovered source; no types, signatures or calling conventions were recovered','Remaining opaque calls (VMX128_*, vector2RaisedToThe*/vectorLog2*) are unknown values',
        'FP/vector counts: an instruction is opaque when its p-code holds a CALLOTHER other than a stream hint (dst/dss)',
        'Opaque counts cover flow-reached listing instructions only; words reached only via unrecovered jump tables are not counted',
        'Terminal-call overrides infer non-returning calls from .pdata layout; they bound decompilation, they do not prove noreturn',
        'Main project opened read-only; language switch and overrides exist only in-session'])
# Floating-point / vector coverage, and the before/after comparison with a baseline run (run_batch_decompile.ps1
# -Out batch-baseline-<date> -Module <saved language>) when one exists.
def fpvmx(rows):
    has=[r for r in rows if int(r.get('fp_instructions') or 0)+int(r.get('vector_instructions') or 0)>0]
    return dict(functions_with_fp_or_vector=len(has),fully_modelled=sum(int(r['fp_opaque'])+int(r['vector_opaque'])==0 for r in has),
        with_opaque=sum(int(r['fp_opaque'])+int(r['vector_opaque'])>0 for r in has),
        fp_instructions=sum(int(r['fp_instructions']) for r in has),fp_opaque=sum(int(r['fp_opaque']) for r in has),
        vector_instructions=sum(int(r['vector_instructions']) for r in has),vector_opaque=sum(int(r['vector_opaque']) for r in has),
        opaque_ops_by_functions=dict(collections.Counter(n for r in has for n in r['fpvmx_opaque_op_names'].split(',') if n).most_common()))
def seconds(summary):
    return {k:float(v) for k,v in (x.split('=') for x in summary.split() if '=' in x) if k.startswith('seconds_')}
if 'fp_instructions' in rows[0]:
    report['fpvmx']=fpvmx(rows);report['seconds']=seconds(report['summary'])
    baselines=sorted(WORK.glob('batch-baseline-*/batch-decompile.txt'))
    if baselines:
        b=baselines[-1].parent;brows=list(csv.DictReader((b/'decompiled-index.tsv').open(),delimiter='\t'))
        assert len(brows)==len(rows)
        bm={r['address']:r for r in brows}
        newly=[r['address'] for r in rows if int(r['fp_opaque'])+int(r['vector_opaque'])==0 and int(bm[r['address']]['fp_opaque'])+int(bm[r['address']]['vector_opaque'])>0]
        report['baseline']=dict(folder=b.relative_to(ROOT).as_posix(),summary=(b/'batch-decompile.txt').read_text().strip(),module=(b/'batch-module.txt').read_text().strip(),
            fpvmx=fpvmx(brows),seconds=seconds((b/'batch-decompile.txt').read_text()),
            decompiled_full_style=sum(r['decompiled']=='true' for r in brows),failures=[r['address'] for r in brows if r['decoded']!='true' or r['decompiled']!='true'],
            diagnostics=[r['address']+' '+r['diagnostic'] for r in brows if r['diagnostic']],
            index_sha256=sha(b/'decompiled-index.tsv'))
        report['before_after']=dict(functions_newly_fully_modelled=len(newly),
            functions_still_with_opaque=report['fpvmx']['with_opaque'],
            functions_with_c_warnings_before=sum(int(r['warnings'])>0 for r in brows),functions_with_c_warnings_after=sum(int(r['warnings'])>0 for r in rows),
            diagnostics_after=[r['address']+' '+r['diagnostic'] for r in rows if r['diagnostic']])
(ROOT/'research/evidence/xex-batch-decompile.json').write_text(json.dumps(report,indent=2)+'\n')
print(report['summary']);print('failures',failures)
