r"""executable tools: map_imports.py rewrites the research workspace's imports.tsv byte for byte

map_imports.py on a scratch copy of the research workspace's inputs (BF1943_RESEARCH, else operation-rising-sun next
to the suite; nothing is written there): the extracted XEX, the checked image and XEXLoaderWV's two reference files.
It exits 0, its imports.tsv is byte-identical to the workspace's recorded work/xex-analysis/imports.tsv, and its
evidence differs from the recorded research/evidence/xex-imports.json only in the parser it names (its path in the
suite, not the research workspace's src/xexresearch/map_imports.py of 2026-10-16) and in its line endings (the
recorded file has CRLF). Skips without a workspace.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

TIER = 'bf1943'
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(TOOLS)
SOURCE = os.environ.get('BF1943_RESEARCH') or os.path.join(os.path.dirname(SUITE), 'operation-rising-sun')
LOADER = 'work/tools/XEXLoaderWV/XEXLoaderWV/src/main/java/xexloaderwv'
INPUTS = ['work/extracted/default.xex', 'work/xex-analysis/default.checked.memory.bin',
          f'{LOADER}/ImportRenamer.java', f'{LOADER}/XEXHeader.java']
RECORDED = ['work/xex-analysis/imports.tsv', 'research/evidence/xex-imports.json']


def test_imports_tsv_as_recorded(tmp_path):
    if not all(os.path.isfile(os.path.join(SOURCE, f)) for f in INPUTS + RECORDED):
        raise unittest.SkipTest(f'no research workspace with the XEX inputs at {SOURCE}')
    root = tmp_path / 'workspace'
    (root / 'research' / 'evidence').mkdir(parents=True)
    for f in INPUTS:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(SOURCE, f), root / f)
    r = subprocess.run([sys.executable, '-B', os.path.join(TOOLS, 'map_imports.py')], capture_output=True, text=True,
                       cwd=str(root), env=dict(os.environ, BF1943_RESEARCH=str(root)))
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(SOURCE, RECORDED[0]), 'rb') as f:
        assert (root / RECORDED[0]).read_bytes() == f.read(), 'imports.tsv differs from the recorded one'
    with open(os.path.join(SOURCE, RECORDED[1]), encoding='utf-8') as f:
        recorded = json.load(f)
    written = json.loads((root / RECORDED[1]).read_text(encoding='utf-8'))
    assert written['provenance'].pop('parser')['path'] == 'bf1943-xex-tools/map_imports.py'
    recorded['provenance'].pop('parser')
    assert written == recorded, 'the evidence differs from the recorded one beyond the parser it names'
