"""Parse Xenia function trace files (functions.N) from a probe session and map them onto BF1943 .pdata functions.

Record layout from xenia-canary src/xenia/cpu/function_trace_data.h at e6cd2e4bc (the installed build; copy in
work/tools/xenia-canary-src): u32 data_size, u32 start, u32 end, u32 type, u64 thread mask, u64 call count,
u32 caller history[4], then u64 execute count per instruction. Little-endian (host) layout.
Usage (from the research workspace): python -B <suite>/bf1943-xex-tools/parse_function_trace.py <session folder>
The session folder comes from bf1943-automated-map-and-bot-test-harness/xenia-run/run_xenia_probe.ps1 -FunctionTrace.
"""
import sys
sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402
import bisect  # noqa: E402
import csv  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import struct  # noqa: E402
from workspace import research_root  # noqa: E402

FORMAT_SHA256 = 'be26cf69639117985c7c9bd2c7c52d60a6125111039ed1221f1197f3b34eb334'
HEADER = struct.Struct('<IIIIQQ4I')


def read_records(session):
    """The trace records of every functions.N file, merged by function start: {start: record}."""
    records = {}
    for path in sorted(session.glob('functions.*'), key=lambda p: int(p.suffix[1:])):
        data = path.read_bytes()
        offset = 0
        while offset + HEADER.size <= len(data):
            size, start, end, kind, threads, calls, *callers = HEADER.unpack_from(data, offset)
            if size == 0 or start == 0:
                break
            count = (end - start) // 4 + 1
            executed = struct.unpack_from(f'<{count}Q', data, offset + HEADER.size)
            record = records.setdefault(start, dict(end=end, type=kind, threads=0, calls=0, callers=set(),
                                                    executed=[0] * count))
            record['threads'] |= threads
            record['calls'] += calls
            record['callers'].update(caller for caller in callers if caller)
            record['executed'] = [total + more for total, more in zip(record['executed'], executed)]
            offset += size
    return records


def main():
    root = research_root()              # the research workspace (workspace.py)
    trace_format = (root / 'work/tools/xenia-canary-src/function_trace_data.h').read_bytes()
    assert hashlib.sha256(trace_format).hexdigest() == FORMAT_SHA256
    session = Path(sys.argv[1])
    records = read_records(session)
    functions = [(int(row['start'], 16), int(row['end'], 16))
                 for row in csv.DictReader((root / 'work/xex-analysis/functions.tsv').open(), delimiter='\t')]
    starts = [start for start, _ in functions]

    def pdata_function(address):
        i = bisect.bisect_right(starts, address) - 1
        return functions[i] if i >= 0 and address < functions[i][1] else None

    rows = []
    hit = set()                         # .pdata functions with an executed instruction
    for start, record in sorted(records.items()):
        ran = sum(1 for count in record['executed'] if count)
        function = pdata_function(start)
        if ran and function:
            hit.add(function[0])
        rows.append(dict(start=f'{start:08x}', end=f'{record["end"]:08x}',
                         pdata_function=f'{function[0]:08x}' if function else '', called=record['calls'],
                         instructions=len(record['executed']), instructions_executed=ran,
                         threads=bin(record['threads']).count('1'),
                         callers=' '.join(f'{caller:08x}' for caller in sorted(record['callers']))))
    with (session / 'function-coverage.tsv').open('w', encoding='utf-8', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else ['start'], delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(session=session.name, format_sha256=FORMAT_SHA256, trace_records=len(records),
                   records_executed=sum(row['instructions_executed'] > 0 for row in rows),
                   pdata_functions=len(functions), pdata_functions_executed=len(hit),
                   pdata_coverage=round(len(hit) / len(functions), 4),
                   instructions_executed=sum(row['instructions_executed'] for row in rows))
    (session / 'function-coverage.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(summary)


if __name__ == '__main__':
    main()
