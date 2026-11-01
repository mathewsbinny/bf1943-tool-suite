r"""XEX tools: batch decompile record and function trace coverage on made-up tables

record_batch_decompile.py and parse_function_trace.py on a made-up research workspace (no game data, no Ghidra, no
Xenia; nothing read from or written to operation-rising-sun): a batch index of 33232 made-up rows with a baseline run
and a leaf run beside it, and a made-up Xenia function trace session (two functions.N files in Xenia's record layout).
The modules' pinned hashes (image, trace format) are replaced by the made-up ones for the test and restored afterwards.
Checked: the evidence each main() writes (counts, failures, the before/after comparison, the leaf run's record, the
trace's per-function coverage) and the helpers on their own.
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import struct
import sys

import parse_function_trace as pft
import record_batch_decompile as rbd

COLUMNS = ['address', 'name', 'decoded', 'decompiled', 'style', 'diagnostic', 'file', 'terminal_call_override',
           'opaque_vmx_ops', 'words', 'instructions', 'unreached_words', 'fp_instructions', 'vector_instructions',
           'fp_opaque', 'vector_opaque', 'fpvmx_opaque_op_names', 'warnings']


def sha(b):
    return hashlib.sha256(b).hexdigest()


def row(k, **kw):
    r = dict(address=f'{0x82100000 + 4 * k:08x}', name=f'fn_{k}', decoded='true', decompiled='true', style='normalize',
             diagnostic='', file='', terminal_call_override='false', opaque_vmx_ops='0', words='4', instructions='4',
             unreached_words='0', fp_instructions='0', vector_instructions='0', fp_opaque='0', vector_opaque='0',
             fpvmx_opaque_op_names='', warnings='0')
    r.update(kw)
    return r


def write_index(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'decompiled-index.tsv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def index_rows(baseline=False):
    rows = [row(k) for k in range(33232)]
    rows[0].update(file='fn_0.c', fp_instructions='3', fp_opaque='0' if not baseline else '1',
                   fpvmx_opaque_op_names='' if not baseline else 'fsqrt')
    rows[1].update(file='fn_1.c', vector_instructions='5', vector_opaque='2', fpvmx_opaque_op_names='VMX128_1,vrefp',
                   opaque_vmx_ops='2', warnings='1')
    rows[2].update(decoded='false', decompiled='false', diagnostic='bad instruction')
    rows[3].update(decompiled='false', terminal_call_override='true', diagnostic='timeout' if baseline else '')
    return rows


@contextlib.contextmanager
def research(tmp):
    saved = (os.environ.get('BF1943_RESEARCH'), rbd.IMAGE_SHA256, pft.FORMAT_SHA256, sys.argv)
    os.environ['BF1943_RESEARCH'] = str(tmp)
    (tmp / 'research/evidence').mkdir(parents=True)
    (tmp / 'work').mkdir(exist_ok=True)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        if saved[0] is None:
            os.environ.pop('BF1943_RESEARCH', None)
        else:
            os.environ['BF1943_RESEARCH'] = saved[0]
        rbd.IMAGE_SHA256, pft.FORMAT_SHA256, sys.argv = saved[1:]


def test_record_batch_decompile(tmp_path):
    work = tmp_path / 'work/xex-analysis'
    with research(tmp_path):
        image = b'made-up image'
        rbd.IMAGE_SHA256 = sha(image)
        write_index(work, index_rows())
        for name in ('default.checked.memory.bin', 'default.memory.bin'):
            (work / name).write_bytes(image)
        (work / 'saved-validation.txt').write_text(f'PASS functions=33232 image={sha(image)}\n')
        (work / 'batch-decompile.txt').write_text('functions=33232 seconds_total=12.5 seconds_decompile=10\n')
        for name in ('decompile-diagnostics.tsv', 'decompiled-opaque-tally.tsv', 'batch-project-identity.txt'):
            (work / name).write_text(name)
        (tmp_path / 'work/vmx128/XenonVMX128/data/languages').mkdir(parents=True)
        (tmp_path / 'work/vmx128/XenonVMX128/data/languages/xenon.sla').write_text('sla')
        (work / 'decompiled').mkdir()
        for name in ('fn_0.c', 'fn_1.c'):
            (work / 'decompiled' / name).write_text(f'/* {name} */')
        base = work / 'batch-baseline-2026-10-01'
        write_index(base, index_rows(baseline=True))
        (base / 'batch-decompile.txt').write_text('functions=33232 seconds_total=20\n')
        (base / 'batch-module.txt').write_text('saved language\n')
        leaf = work / 'batch-leaf-2026-10-02'
        write_index(leaf, [row(0, file='leaf_0.c'), row(1, decoded='false', diagnostic='bad'), row(2, decoded='false')])
        (leaf / 'batch-decompile.txt').write_text('functions=3 seconds_total=1\n')
        with open(leaf / 'extra-functions.tsv', 'w', encoding='utf-8', newline='') as f:
            f.write('address\tresult\tsource\n82100000\tcreated\tcall\n82100004\tcreated\tdata\n82100008\texists\tcall\n')
        (work / 'callgraph').mkdir()
        (work / 'callgraph/callgraph-edges.tsv').write_text(
            'caller\tkind\n82100004\timport_tail_call\n82100004\tdirect_call\n82100008\tdirect_call\n')
        rbd.main()
        report = json.loads((tmp_path / 'research/evidence/xex-batch-decompile.json').read_bytes())
        assert report['functions'] == 33232 and report['decoded'] == 33231 and report['decompiled_full_style'] == 33230
        assert report['c_files'] == 2 and report['terminal_call_overrides'] == ['8210000c']
        assert [f['address'] for f in report['failures']] == ['82100008', '8210000c']
        assert report['functions_with_opaque_vmx'] == 1 and report['opaque_vmx_ops'] == 2
        assert report['fpvmx'] == dict(functions_with_fp_or_vector=2, fully_modelled=1, with_opaque=1, fp_instructions=3,
                                       fp_opaque=0, vector_instructions=5, vector_opaque=2,
                                       opaque_ops_by_functions={'VMX128_1': 1, 'vrefp': 1})
        assert report['seconds'] == dict(seconds_total=12.5, seconds_decompile=10.0)
        assert report['baseline']['folder'] == 'work/xex-analysis/batch-baseline-2026-10-01'
        assert report['baseline']['diagnostics'] == ['82100008 bad instruction', '8210000c timeout'] and report['baseline']['module'] == 'saved language'
        assert report['before_after']['functions_newly_fully_modelled'] == 1
        assert report['before_after']['diagnostics_after'] == ['82100008 bad instruction']
        extra = report['extra_functions']
        assert extra['requested'] == 3 and extra['creation'] == dict(created=2, exists=1)
        assert extra['bad_instruction_functions'] == 2 and extra['bad_instruction_with_import_tail_call'] == 1
        assert extra['with_c'] == 1 and extra['fpvmx']['functions_with_fp_or_vector'] == 0
        manifest = (work / 'decompiled-manifest.tsv').read_text(encoding='utf-8').splitlines()
        assert manifest == ['file\tsha256', f'fn_0.c\t{sha(b"/* fn_0.c */")}', f'fn_1.c\t{sha(b"/* fn_1.c */")}']
        assert set(report['sha256']) == set(rbd.HASHED)
        assert rbd.path_of('bf1943-xex-tools/run_batch_decompile.ps1', tmp_path) == rbd.HERE / 'run_batch_decompile.ps1'


def test_batch_helpers(tmp_path):
    assert rbd.seconds('functions=3 seconds_a=1.5 note=x seconds_b=2') == dict(seconds_a=1.5, seconds_b=2.0)
    assert rbd.failed(dict(decoded='true', decompiled='false')) and not rbd.failed(dict(decoded='true', decompiled='true'))
    assert rbd.diagnostics([dict(address='a', diagnostic=''), dict(address='b', diagnostic='d')]) == ['b d']
    report = {}
    rbd.baseline_comparison(tmp_path, tmp_path, [], report)
    assert report == {} and rbd.extra_functions(tmp_path, tmp_path) is None, 'no baseline or leaf run: nothing added'


def trace_record(start, end, threads, calls, callers, executed):
    count = (end - start) // 4 + 1
    assert len(executed) == count
    body = pft.HEADER.pack(pft.HEADER.size + 8 * count, start, end, 0, threads, calls, *callers) + \
        struct.pack(f'<{count}Q', *executed)
    return body


def test_parse_function_trace(tmp_path):
    with research(tmp_path):
        header = b'// made-up function_trace_data.h\n'
        (tmp_path / 'work/tools/xenia-canary-src').mkdir(parents=True)
        (tmp_path / 'work/tools/xenia-canary-src/function_trace_data.h').write_bytes(header)
        pft.FORMAT_SHA256 = sha(header)
        (tmp_path / 'work/xex-analysis').mkdir(parents=True)
        (tmp_path / 'work/xex-analysis/functions.tsv').write_text(
            'start\tend\tprolog_bytes\texception_flag\n82000100\t82000110\t0\t0\n82000200\t82000208\t0\t0\n')
        session = tmp_path / 'session'
        session.mkdir()
        f2 = trace_record(0x82000100, 0x8200010C, 1, 3, (0x82000500, 0, 0, 0), [1, 0, 2, 0])
        f2 += trace_record(0x82000200, 0x82000204, 2, 0, (0, 0, 0, 0), [0, 0])         # traced, never run
        f2 += trace_record(0x82000900, 0x82000900, 4, 1, (0, 0, 0, 0), [5])            # outside .pdata
        f2 += bytes(pft.HEADER.size)                                                  # a zero record ends the file
        (session / 'functions.2').write_bytes(f2)
        (session / 'functions.10').write_bytes(
            trace_record(0x82000100, 0x8200010C, 2, 1, (0x82000600, 0, 0, 0), [0, 1, 0, 0]))
        sys.argv = ['parse_function_trace.py', str(session)]
        pft.main()
        summary = json.loads((session / 'function-coverage.json').read_text(encoding='utf-8'))
        assert summary == dict(session='session', format_sha256=sha(header), trace_records=3, records_executed=2,
                               pdata_functions=2, pdata_functions_executed=1, pdata_coverage=0.5, instructions_executed=4)
        rows = list(csv.DictReader(open(session / 'function-coverage.tsv', encoding='utf-8'), delimiter='\t'))
        assert rows[0] == dict(start='82000100', end='8200010c', pdata_function='82000100', called='4', instructions='4',
                               instructions_executed='3', threads='2', callers='82000500 82000600')
        assert rows[2]['pdata_function'] == '' and rows[1]['instructions_executed'] == '0'
        empty = tmp_path / 'empty-session'
        empty.mkdir()
        sys.argv = ['parse_function_trace.py', str(empty)]
        pft.main()
        assert (empty / 'function-coverage.tsv').read_text(encoding='utf-8') == 'start\n'
