"""Parse Xenia function trace files (functions.N) from a probe session and map them onto BF1943 .pdata functions.

Record layout from xenia-canary src/xenia/cpu/function_trace_data.h at e6cd2e4bc (the installed build; copy in
work/tools/xenia-canary-src): u32 data_size, u32 start, u32 end, u32 type, u64 thread mask, u64 call count,
u32 caller history[4], then u64 execute count per instruction. Little-endian (host) layout.
Usage (from the research workspace): python -B <suite>/bf1943-xex-tools/parse_function_trace.py <session folder>
The session folder comes from bf1943-automated-map-and-bot-test-harness/xenia-run/run_xenia_probe.ps1 -FunctionTrace.
"""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import bisect,csv,hashlib,json,struct
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
FORMAT_SHA256='be26cf69639117985c7c9bd2c7c52d60a6125111039ed1221f1197f3b34eb334'
HEADER=struct.Struct('<IIIIQQ4I')
assert hashlib.sha256((ROOT/'work/tools/xenia-canary-src/function_trace_data.h').read_bytes()).hexdigest()==FORMAT_SHA256
session=Path(sys.argv[1])
records={}
for path in sorted(session.glob('functions.*'),key=lambda p:int(p.suffix[1:])):
    data=path.read_bytes();offset=0
    while offset+HEADER.size<=len(data):
        size,start,end,kind,threads,calls,*callers=HEADER.unpack_from(data,offset)
        if size==0 or start==0:break
        count=(end-start)//4+1
        executed=struct.unpack_from(f'<{count}Q',data,offset+HEADER.size)
        r=records.setdefault(start,dict(end=end,type=kind,threads=0,calls=0,callers=set(),executed=[0]*count))
        r['threads']|=threads;r['calls']+=calls;r['callers'].update(c for c in callers if c)
        r['executed']=[a+b for a,b in zip(r['executed'],executed)]
        offset+=size
functions=[(int(r['start'],16),int(r['end'],16)) for r in csv.DictReader((ROOT/'work/xex-analysis/functions.tsv').open(),delimiter='\t')]
starts=[s for s,_ in functions]
def pdata(a):
    i=bisect.bisect_right(starts,a)-1
    return functions[i] if i>=0 and a<functions[i][1] else None
rows=[];hit=set()
for start,r in sorted(records.items()):
    ran=sum(1 for c in r['executed'] if c);f=pdata(start)
    if ran and f:hit.add(f[0])
    rows.append(dict(start=f'{start:08x}',end=f'{r["end"]:08x}',pdata_function=f'{f[0]:08x}' if f else '',called=r['calls'],
        instructions=len(r['executed']),instructions_executed=ran,threads=bin(r['threads']).count('1'),callers=' '.join(f'{c:08x}' for c in sorted(r['callers']))))
with (session/'function-coverage.tsv').open('w',encoding='utf-8',newline='') as out:
    w=csv.DictWriter(out,fieldnames=list(rows[0]) if rows else ['start'],delimiter='\t',lineterminator='\n');w.writeheader();w.writerows(rows)
summary=dict(session=session.name,format_sha256=FORMAT_SHA256,trace_records=len(records),records_executed=sum(r['instructions_executed']>0 for r in rows),
    pdata_functions=len(functions),pdata_functions_executed=len(hit),pdata_coverage=round(len(hit)/len(functions),4),
    instructions_executed=sum(r['instructions_executed'] for r in rows))
(session/'function-coverage.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8', newline='\n')
print(summary)
