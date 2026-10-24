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
    limits=['Pseudocode, not recovered source; no types, signatures or calling conventions were recovered','VMX128_* calls are opaque: unknown values, memory effects not modeled',
        'Opaque counts cover flow-reached listing instructions only; words reached only via unrecovered jump tables are not counted',
        'Terminal-call overrides infer non-returning calls from .pdata layout; they bound decompilation, they do not prove noreturn',
        'Main project opened read-only; language switch and overrides exist only in-session'])
(ROOT/'research/evidence/xex-batch-decompile.json').write_text(json.dumps(report,indent=2)+'\n')
print(report['summary']);print('failures',failures)
