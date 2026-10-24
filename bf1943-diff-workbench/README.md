# BF1943 Diff Workbench (bf1943-diff-workbench)

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-23) for LevelDiff on BF1943's binary partitions.

- `level-diff/`: LevelDiff, which compares two binary `.dbx` partitions instance by instance (see its README).
  Tested on BF1943: a partition against itself shows no change (the suite's `tests\test_bf1943_tools.py dbxedit`).
- The rest of the workbench is planned in OPFOR's design ([DESIGN.md](../docs/opfor-bfbc2/DESIGN.md)).
