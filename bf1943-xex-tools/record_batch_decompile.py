"""Record the batch decompilation run: per-file hashes, counts and the validation it depends on."""
from pathlib import Path
import collections
import csv
import hashlib
import json

from workspace import research_root  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'
# the files whose hashes the evidence records: the batch tools, the run's tables and the language it decompiled with
HASHED = ['bf1943-xex-tools/export_all_decompiled.java', 'bf1943-xex-tools/run_batch_decompile.ps1',
          'work/xex-analysis/decompiled-index.tsv', 'work/xex-analysis/decompile-diagnostics.tsv',
          'work/xex-analysis/decompiled-opaque-tally.tsv', 'work/xex-analysis/decompiled-manifest.tsv',
          'work/xex-analysis/batch-project-identity.txt', 'work/vmx128/XenonVMX128/data/languages/xenon.sla']
LIMITS = ['Pseudocode, not recovered source; no types, signatures or calling conventions were recovered',
          'Remaining opaque calls (VMX128_*, vector2RaisedToThe*/vectorLog2*) are unknown values',
          'FP/vector counts: an instruction is opaque when its p-code holds a CALLOTHER other than a stream hint (dst/dss)',
          'Opaque counts cover flow-reached listing instructions only; words reached only via unrecovered jump tables are not counted',
          'Terminal-call overrides infer non-returning calls from .pdata layout; they bound decompilation, they do not prove noreturn',
          'Main project opened read-only; language switch and overrides exist only in-session']


def path_of(name, root):
    """A HASHED name's file: the suite's own tools are here, the rest are in the research workspace."""
    if name.startswith('bf1943-xex-tools/'):
        return HERE / name[len('bf1943-xex-tools/'):]
    return root / name


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_index(folder):
    return list(csv.DictReader((folder / 'decompiled-index.tsv').open(), delimiter='\t'))


def failed(row):
    return row['decoded'] != 'true' or row['decompiled'] != 'true'


def opaque_count(row):
    return int(row['fp_opaque']) + int(row['vector_opaque'])


def diagnostics(rows):
    return [row['address'] + ' ' + row['diagnostic'] for row in rows if row['diagnostic']]


# Floating-point / vector coverage, and the before/after comparison with a baseline run (run_batch_decompile.ps1
# -Out batch-baseline-<date> -Module <saved language>) when one exists.
def fpvmx(rows):
    has = [row for row in rows if int(row.get('fp_instructions') or 0) + int(row.get('vector_instructions') or 0) > 0]
    opaque_names = collections.Counter(name for row in has for name in row['fpvmx_opaque_op_names'].split(',') if name)
    return dict(functions_with_fp_or_vector=len(has),
                fully_modelled=sum(opaque_count(row) == 0 for row in has),
                with_opaque=sum(opaque_count(row) > 0 for row in has),
                fp_instructions=sum(int(row['fp_instructions']) for row in has),
                fp_opaque=sum(int(row['fp_opaque']) for row in has),
                vector_instructions=sum(int(row['vector_instructions']) for row in has),
                vector_opaque=sum(int(row['vector_opaque']) for row in has),
                opaque_ops_by_functions=dict(opaque_names.most_common()))


def seconds(summary):
    """The seconds_*=<value> fields of a batch-decompile.txt summary line."""
    fields = (item.split('=') for item in summary.split() if '=' in item)
    return {key: float(value) for key, value in fields if key.startswith('seconds_')}


def baseline_comparison(root, work, rows, report):
    """The newest batch-baseline-* run, and what changed from it to this run (adds 'baseline' and 'before_after')."""
    baselines = sorted(work.glob('batch-baseline-*/batch-decompile.txt'))
    if not baselines:
        return
    baseline = baselines[-1].parent
    baseline_rows = read_index(baseline)
    assert len(baseline_rows) == len(rows)
    baseline_by_address = {row['address']: row for row in baseline_rows}
    newly = [row['address'] for row in rows
             if opaque_count(row) == 0 and opaque_count(baseline_by_address[row['address']]) > 0]
    report['baseline'] = dict(
        folder=baseline.relative_to(root).as_posix(), summary=(baseline / 'batch-decompile.txt').read_text().strip(),
        module=(baseline / 'batch-module.txt').read_text().strip(),
        fpvmx=fpvmx(baseline_rows), seconds=seconds((baseline / 'batch-decompile.txt').read_text()),
        decompiled_full_style=sum(row['decompiled'] == 'true' for row in baseline_rows),
        failures=[row['address'] for row in baseline_rows if failed(row)],
        diagnostics=diagnostics(baseline_rows),
        index_sha256=sha(baseline / 'decompiled-index.tsv'))
    report['before_after'] = dict(
        functions_newly_fully_modelled=len(newly),
        functions_still_with_opaque=report['fpvmx']['with_opaque'],
        functions_with_c_warnings_before=sum(int(row['warnings']) > 0 for row in baseline_rows),
        functions_with_c_warnings_after=sum(int(row['warnings']) > 0 for row in rows),
        diagnostics_after=diagnostics(rows))


# Functions created in-session from extra= (e.g. the leaf functions without .pdata), when such a run exists.
def extra_functions(root, work):
    """The newest batch-leaf-* run's record, or None."""
    leaves = sorted(work.glob('batch-leaf-*/batch-decompile.txt'))
    if not leaves:
        return None
    leaf = leaves[-1].parent
    leaf_rows = read_index(leaf)
    created = list(csv.DictReader((leaf / 'extra-functions.tsv').open(), delimiter='\t'))
    edges = work / 'callgraph/callgraph-edges.tsv'
    edge_kinds = collections.defaultdict(set)              # caller -> the kinds of its call graph edges
    if edges.exists():
        for edge in csv.DictReader(edges.open(), delimiter='\t'):
            edge_kinds[edge['caller']].add(edge['kind'])
    bad = [row['address'] for row in leaf_rows if row['decoded'] != 'true']
    return dict(
        folder=leaf.relative_to(root).as_posix(), summary=(leaf / 'batch-decompile.txt').read_text().strip(),
        requested=len(created), creation=dict(collections.Counter(row['result'] for row in created)),
        sources=dict(collections.Counter(row['source'] for row in created)),
        decompiled_full_style=sum(row['decompiled'] == 'true' for row in leaf_rows),
        with_c=sum(bool(row['file']) for row in leaf_rows),
        decoded=sum(row['decoded'] == 'true' for row in leaf_rows),
        bad_instruction_functions=len(bad),
        bad_instruction_with_import_tail_call=sum('import_tail_call' in edge_kinds.get(address, ()) for address in bad),
        bad_instruction_examples=bad[:12], diagnostics=diagnostics(leaf_rows),
        fpvmx=fpvmx(leaf_rows) if 'fp_instructions' in leaf_rows[0] else None,
        seconds=seconds((leaf / 'batch-decompile.txt').read_text()),
        index_sha256=sha(leaf / 'decompiled-index.tsv'), extra_functions_sha256=sha(leaf / 'extra-functions.tsv'))


def main():
    root = research_root()              # the research workspace (workspace.py)
    work = root / 'work/xex-analysis'
    assert sha(work / 'default.checked.memory.bin') == IMAGE_SHA256 and sha(work / 'default.memory.bin') == IMAGE_SHA256
    saved = (work / 'saved-validation.txt').read_text()
    assert saved.startswith('PASS functions=33232') and IMAGE_SHA256 in saved
    rows = read_index(work)
    assert len(rows) == 33232
    with (work / 'decompiled-manifest.tsv').open('w', encoding='utf-8', newline='') as out:
        out.write('file\tsha256\n')
        for row in rows:
            if row['file']:
                out.write(f"{row['file']}\t{sha(work / 'decompiled' / row['file'])}\n")

    def count(key, value):
        return sum(row[key] == value for row in rows)

    failures = [dict(address=row['address'], name=row['name'], decoded=row['decoded'], decompiled=row['decompiled'],
                     style=row['style'], diagnostic=row['diagnostic'])
                for row in rows if failed(row)]
    report = dict(
        summary=(work / 'batch-decompile.txt').read_text().strip(), image_sha256=IMAGE_SHA256,
        saved_validation=saved.strip(), language='PowerPC:BE:64:XenonVMX128',
        functions=len(rows), decoded=count('decoded', 'true'), decompiled_full_style=count('decompiled', 'true'),
        c_files=sum(bool(row['file']) for row in rows),
        terminal_call_overrides=[row['address'] for row in rows if row['terminal_call_override'] == 'true'],
        failures=failures,
        functions_with_opaque_vmx=sum(int(row['opaque_vmx_ops']) > 0 for row in rows),
        opaque_vmx_ops=sum(int(row['opaque_vmx_ops']) for row in rows),
        words=sum(int(row['words']) for row in rows), instructions=sum(int(row['instructions']) for row in rows),
        unreached_words=sum(int(row['unreached_words']) for row in rows),
        sha256={name: sha(path_of(name, root)) for name in HASHED},
        limits=LIMITS)
    if 'fp_instructions' in rows[0]:
        report['fpvmx'] = fpvmx(rows)
        report['seconds'] = seconds(report['summary'])
        baseline_comparison(root, work, rows, report)
    extra = extra_functions(root, work)
    if extra is not None:
        report['extra_functions'] = extra
    (root / 'research/evidence/xex-batch-decompile.json').write_bytes((json.dumps(report, indent=2) + '\n').encode())
    print(report['summary'])
    print('failures', failures)


if __name__ == '__main__':
    main()
