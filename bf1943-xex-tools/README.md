# BF1943 executable tools (bf1943-xex-tools)

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-23) - tools for BF1943's Xbox 360 executable
(`default.xex`): decode it to a checked memory image, map its functions and imports, build an isolated Xenon
(PowerPC + VMX128) language for Ghidra and test it, decompile every function, and disassemble or search the image.
Moved here from operation-rising-sun's `src/xexresearch` on 2026-10-23 (the investigations that use them stayed
there; their history is in that repository's git log).

They are BF1943's: they check this build's image (SHA-256 `cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a`,
base `0x82000000`, entry `0x827d78b0`) and refuse another. Two are more general: `bfbc2dis.py` also reads BFBC2's
Xbox 360 image (as a comparison reference), and the language tests (`Test*.java`) test the Xenon language itself.

## The research workspace

The tools read and write a **research workspace**: a folder with `work\` and `research\` laid out as
operation-rising-sun is. It is `BF1943_RESEARCH`, else the folder you run the tool from (`workspace.py`,
`workspace.ps1`); anything else is refused with what to do. Inputs and outputs, relative to it:

| Path | What |
|---|---|
| `work\extracted\default.xex` | your executable (the same file as `bf1943-xenia-kit\output\title\default.xex`) |
| `work\xex-analysis\` | the decoded image (`default.checked.memory.bin`), `functions.tsv`, `sections.tsv`, helper tables, the Ghidra project, the decompiled functions |
| `work\vmx128\` | the Xenon language module (`XenonVMX128`), its encoding table and the test results |
| `work\tools\XEXLoaderWV\` | XEXLoaderWV at commit `4bcf58da` (its Java sources decode the XEX) |
| `work\tools\xenia-95a5c3ee\`, `work\tools\xenia-canary-src\` | Xenia's reference sources (VMX128 semantics; the function trace format) |
| `Tools\XenonRecomp.zip` or `Tools\XenonRecomp\` | the XenonRecomp reference (its `xbox.h` and `ppc-dis`), hash-checked |
| `research\evidence\` | the JSON evidence each step writes (inputs, hashes, counts, validations) |

Also needed: Ghidra 12.1.4 at `C:\Tools\ghidra_12.1.4_PUBLIC` and JDK 26.0.2 at `C:\Program Files\Java\jdk-26.0.2`
(the PowerShell steps use these paths), Python 3 with `capstone` for the disassembly helpers.

## The pipeline

Run from the workspace, in this order (`<suite>` is this suite's folder):

```powershell
powershell -File <suite>\bf1943-xex-tools\decode.ps1                       # XEX -> checked memory image (DecodeXex.java over XEXLoaderWV)
python -B <suite>\bf1943-xex-tools\inspect_decoded.py                      # image provenance, PE layout, ASCII leads
python -B <suite>\bf1943-xex-tools\map_functions.py                        # PE sections and .pdata runtime functions -> functions.tsv
python -B <suite>\bf1943-xex-tools\map_imports.py                          # XEX import records
python -B <suite>\bf1943-xex-tools\resolve_helpers.py                      # register save/restore helper bodies, verified whole
powershell -File <suite>\bf1943-xex-tools\map_project.ps1                  # apply the tables to the Ghidra project (MapXex.java; -ValidateOnly: ValidateXex.java)
python -B <suite>\bf1943-xex-tools\build_vmx_language.py                   # the isolated Xenon language: every VX128 encoding decodes
python -B <suite>\bf1943-xex-tools\classify_vmx.py                         # classify the image's VX128 words
powershell -ExecutionPolicy Bypass -File <suite>\bf1943-xex-tools\run_vmx.ps1            # compile the language, run every test suite
python -B <suite>\bf1943-xex-tools\record_vmx_validation.py                # evidence, only when every suite passed
powershell -ExecutionPolicy Bypass -File <suite>\bf1943-xex-tools\run_batch_decompile.ps1  # decompile every function (read-only project)
python -B <suite>\bf1943-xex-tools\record_batch_decompile.py               # evidence of the batch run
```

Every step refuses to overwrite source evidence; the recorders write evidence only after a complete pass. Output is
pseudocode, not original source.

## The files

| File | What it does |
|---|---|
| `decode.ps1`, `DecodeXex.java` | decode the XEX into a memory image (decode only: imports are not patched) |
| `inspect_decoded.py` | the decoded image's provenance, PE layout and bounded ASCII leads |
| `map_functions.py`, `map_imports.py`, `resolve_helpers.py` | functions (`.pdata`), imports, register helpers |
| `xenonrecomp_reference.py` | hash-checked access to the XenonRecomp reference (archive or extracted folder) |
| `map_project.ps1`, `MapXex.java`, `ValidateXex.java`, `ApplyHelpers.java`, `InspectHelpers.java` | apply and check the tables in the Ghidra project |
| `build_vmx_language.py`, `classify_vmx.py`, `inventory_vmx.py` | the Xenon language and the image's VX128 words |
| `run_vmx.ps1`, `TestVmx.java`, `TestLogical.java`, `TestPermutation.java`, `TestRlimi.java`, `TestUnaligned.java`, `TestDecodeCoverage.java`, `InspectVmxCaller.java`, `record_vmx_validation.py` | the language's test suites and their evidence |
| `run_batch_decompile.ps1`, `export_all_decompiled.java`, `record_batch_decompile.py` | decompile all functions; evidence |
| `DumpCode.java`, `EntryPilot.java`, `InspectMainFunctions.java`, `ExperimentDecompile.java` | Ghidra inspection scripts (run through `map_project.ps1 -Script <name>`) |
| `xexrefs.py` | static reference scanning over the image (no Ghidra): words, strings, `lis` pairs; used by operation-rising-sun's investigations |
| `ppcdis.py` | `python -B <suite>\bf1943-xex-tools\ppcdis.py <hex address> [count]`: disassembly of the image (capstone, PowerPC 64 big-endian) |
| `bfbc2dis.py` | two images (BF1943's and BFBC2 Xbox 360's): functions, normalized fingerprints, disassembly |
| `parse_function_trace.py` | a Xenia function trace (`run_xenia_probe.ps1 -FunctionTrace` in the test harness) mapped onto the image's functions |
| `workspace.py`, `workspace.ps1` | the research workspace lookup |

## Tests

`python -B tests\test_workspace.py` (the `quick` tier of `run_tests.py`; no game data): `workspace.py` and
`workspace.ps1` accept a folder with `work\` and `research\` and refuse one without. The `bf1943` tier also runs
`ppcdis.py` from the research workspace (`BF1943_RESEARCH`, else operation-rising-sun next to the suite) on the
decoded image; it is skipped without one.

## Checked

Moved and run from operation-rising-sun on 2026-10-23: `map_functions.py` and `inspect_decoded.py` give the same
results as before the move (only their own tool hashes in the evidence differ), and the investigations
`trace_player_pipeline.py`, `trace_ai_soldier_control.py` and `trace_string_leads.py` reproduce their evidence byte for
byte through `xexrefs.py`, `ppcdis.py` and `bfbc2dis.py` from here. The Ghidra steps were not re-run.
