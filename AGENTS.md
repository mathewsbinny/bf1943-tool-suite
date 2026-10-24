# BF1943 Tool Suite (bf1943-tool-suite)

- Tools for the BF1943 PC client (`../project-opfor-bf1943`); research and evidence are in `../operation-rising-sun`.
- OPFOR's `C:/Games/Project OPFOR Toolkit/bfbc2-tool-suite` is a read-only source: never edit it, run its
  scripts, or write caches there. Copy from it only with `tools/import_opfor.py` and modify the copy here.
- Keep `provenance/opfor-imports.json` entries; never rewrite recorded hashes.
- Git stores bytes exactly (`* -text`); write text with explicit UTF-8 and `\n` newlines.
- BFBC2 behavior in copied tools is a lead, not a BF1943 fact: verify each format or rule against BF1943
  data before relying on it.
- Do not ship game binaries or assets. Do not copy decompiled code into tools.
- Each tool's README states its BF1943 status (ready / partly / not yet) and what data it reads today, and
  the suite README's layout table repeats it. Update both when a tool is adapted; never claim BF1943 support a
  tool does not have. Keep factual BFBC2 references (formats, OPFOR provenance, results measured on BFBC2 levels).
- `docs/opfor-bfbc2/` is OPFOR's own documentation, kept unchanged as reference.
