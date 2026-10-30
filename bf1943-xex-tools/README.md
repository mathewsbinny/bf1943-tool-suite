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

## Sources and credits

These tools read third-party sources that you supply. None of them is copied into this repository. Each file is
pinned by hash, and its hash goes into the evidence the tools write.

| Source | License | What we use |
|---|---|---|
| [XenonRecomp](https://github.com/hedge-dev/XenonRecomp), hedge-dev and contributors | MIT | `XenonUtils/xbox.h` (Xbox runtime structures, `map_functions.py`); `recompiler.cpp` (unaligned vector loads and stores, helper bodies) |
| GNU binutils `ppc-dis.c`, Free Software Foundation (bundled in XenonRecomp's `thirdparty/disasm`) | GPL-2.0-or-later | its VMX128 opcode table: the mnemonics, encodings and operand fields that `classify_vmx.py` and `build_vmx_language.py` decode |
| [Xenia](https://github.com/xenia-project/xenia), Ben Vanik and contributors, commit `95a5c3ee` | BSD-3-Clause | `ppc_emit_altivec.cc` and `ppc_instr.h` (VMX128 semantics, the unaligned loads and stores) |
| [Xenia Canary](https://github.com/xenia-canary/xenia-canary), commit `e6cd2e4bc` | BSD-3-Clause | `function_trace_data.h` (the function trace format, `parse_function_trace.py`) |
| [Ghidra](https://github.com/NationalSecurityAgency/ghidra) 12.1.4 | Apache-2.0 | the PowerPC language files that the Xenon module starts from |
| XEXLoaderWV, commit `4bcf58da` | its own | its Java sources decode the XEX |

The p-code we wrote follows the AltiVec PEM and these references. Where a reference decides a behaviour, the comment
in the code and the research write-up name the file and lines.

The language module that `build_vmx_language.py` writes (`work\vmx128\XenonVMX128`) combines Ghidra's files (Apache-2.0)
with entries taken from `ppc-dis.c` (GPL-2.0-or-later). It stays in your workspace; if you share it, its GPL part
comes with GPL terms.

## The pipeline

Run from the workspace, in this order (`<suite>` is this suite's folder):

```powershell
powershell -File <suite>\bf1943-xex-tools\decode.ps1                       # XEX -> checked memory image (DecodeXex.java over XEXLoaderWV)
python -B <suite>\bf1943-xex-tools\inspect_decoded.py                      # image provenance, PE layout, ASCII leads
python -B <suite>\bf1943-xex-tools\verify_xex_image.py                     # all 219 page digests of the XEX against the image; the .reloc extent
python -B <suite>\bf1943-xex-tools\map_functions.py                        # PE sections and .pdata runtime functions -> functions.tsv
python -B <suite>\bf1943-xex-tools\map_imports.py                          # XEX import records
python -B <suite>\bf1943-xex-tools\resolve_helpers.py                      # register save/restore helper bodies, verified whole
powershell -File <suite>\bf1943-xex-tools\map_project.ps1                  # apply the tables to the Ghidra project (MapXex.java; -ValidateOnly: ValidateXex.java)
python -B <suite>\bf1943-xex-tools\build_vmx_language.py                   # the isolated Xenon language: every VX128 encoding decodes
python -B <suite>\bf1943-xex-tools\classify_vmx.py                         # classify the image's VX128 words
powershell -ExecutionPolicy Bypass -File <suite>\bf1943-xex-tools\run_vmx.ps1            # compile the language, run every test suite
python -B <suite>\bf1943-xex-tools\record_vmx_validation.py                # evidence, only when every suite passed on the current inputs
powershell -ExecutionPolicy Bypass -File <suite>\bf1943-xex-tools\run_batch_decompile.ps1  # decompile every function (read-only project)
python -B <suite>\bf1943-xex-tools\record_batch_decompile.py               # evidence of the batch run (with the before/after comparison when a baseline run exists)
python -B <suite>\bf1943-xex-tools\export_callgraph_strings.py --ghidra-call-refs work\xex-analysis\ghidra-call-refs.tsv  # call graph, strings
```

`run_batch_decompile.ps1 -Out <folder> -Module <language module>` runs the batch with another (saved) language into
`work\xex-analysis\<folder>`; a folder named `batch-baseline-<date>` is what `record_batch_decompile.py` compares
against. The batch also writes `fpvmx-mnemonics.tsv` (floating-point and vector instructions, modelled or opaque) and
`ghidra-call-refs.tsv` (the listing's call references, a cross-check for the call graph). `-Extra <tsv> -OnlyExtra` creates functions in-session
(start, end, name, source; e.g. `callgraph\inferred-functions.tsv`, the leaf functions without `.pdata`), refusing any
that overlap an existing function, and decompiles only them; `-Heap` sets the Java heap (default `6g`). Each function's
listing is cleared before flow disassembly (the saved project's stock-language listing stops at words that language
cannot decode), and `unreached-words.tsv` lists every word the flow does not reach.

Every step refuses to overwrite source evidence; the recorders write evidence only after a complete pass. Output is
pseudocode, not original source.

## The files

| File | What it does |
|---|---|
| `decode.ps1`, `DecodeXex.java` | decode the XEX into a memory image (decode only: imports are not patched) |
| `inspect_decoded.py` | the decoded image's provenance, PE layout and bounded ASCII leads |
| `map_functions.py`, `map_imports.py`, `resolve_helpers.py` | functions (`.pdata`), imports, register helpers |
| `xenonrecomp_reference.py` | hash-checked access to the XenonRecomp reference (archive or extracted folder) |
| `map_project.ps1`, `MapXex.java`, `ValidateXex.java`, `ApplyHelpers.java`, `InspectHelpers.java` | apply and check the tables in the Ghidra project. Ghidra's headless analyzer exits 0 when a post-script throws, so the step also requires the script's own result from this run (`-ValidateOnly`: `saved-validation.txt` starting `PASS`; `MapXex.java`: `mapping-report.txt`), and removes the old one first (before 2026-10-30 a failed validation left the previous PASS for `record_batch_decompile.py`). `BF1943_JAVA` / `BF1943_GHIDRA` name other installs (the tests' stand-ins) |
| `verify_xex_image.py` | the XEX page-digest chain over the decoded image (each digest covers the next page and its descriptor), the `.reloc` extent; `research\evidence\xex-image-integrity.json` |
| `build_vmx_language.py`, `classify_vmx.py`, `inventory_vmx.py` | the Xenon language and the image's VX128 words |
| `xenon_vector_semantics.py`, `xenon_splice.py` | our own p-code for AltiVec, VMX128 and the scalar FP conversions, spliced into the language by `build_vmx_language.py` |
| `run_vmx.ps1`, `TestVmx.java`, `TestLogical.java`, `TestPermutation.java`, `TestRlimi.java`, `TestUnaligned.java`, `TestDecodeCoverage.java`, `TestVectorSemantics.java`, `TestScalarFp.java`, `InspectVmxCaller.java`, `record_vmx_validation.py` | the language's test suites and their evidence (`run_vmx.ps1 -Only @('TestVectorSemantics.java')` runs a subset; `-CallerOnly` runs only the two caller probes) |
| `vmx_inputs.py` | the fingerprint of everything a VMX128 run tests (tools, test scripts, the compiled language, its tables, the image): `run_vmx.ps1` stamps it into each result it produces, and `record_vmx_validation.py` refuses results from other inputs or from a partial (`-Only`) run. What the fingerprint does not cover is handled by the recorder, not by changing the fingerprint (it stays `465a75c8...`; `run_vmx.ps1` is one of its inputs, so that script is unchanged): the tested program must be the image (the caller probes' `image_sha256`, since `run_vmx.ps1` re-uses the Ghidra project's saved program once it exists), no file of the language module (the copied Ghidra pspec/cspec, includes, ldefs) may be newer than the run's results, and every module file's hash, Ghidra's version and Java's hash go into the evidence as `environment` |
| `export_callgraph_strings.py` | call graph (direct, tail, import and indirect calls, resolved or classified unresolved; leaf functions without `.pdata` inferred), address references, strings with encodings and referencing functions; `work\xex-analysis\callgraph\`. Version 1.2 (2026-10-30): float stores, `dcbz` and VMX128 stores drop the remembered stores they may overwrite (before, a float store kept exactly the slots it could alias, so a reloaded slot could resolve an indirect call wrongly) |
| `run_batch_decompile.ps1`, `export_all_decompiled.java`, `record_batch_decompile.py` | decompile all functions; evidence |
| `DumpCode.java`, `EntryPilot.java`, `InspectMainFunctions.java`, `ExperimentDecompile.java` | Ghidra inspection scripts (run through `map_project.ps1 -Script <name>`) |
| `xexrefs.py` | static reference scanning over the image (no Ghidra): words, strings, `lis` pairs; used by operation-rising-sun's investigations. A `lis` value pairs only until its register is written by anything (2026-10-30; before, only at branches and a few forms, so 212 of 55,036 references came from overwritten registers) |
| `ppcdis.py` | `python -B <suite>\bf1943-xex-tools\ppcdis.py <hex address> [count]`: disassembly of the image (capstone, PowerPC 64 big-endian) |
| `bfbc2dis.py` | two images (BF1943's and BFBC2 Xbox 360's): functions, normalized fingerprints, disassembly |
| `parse_function_trace.py` | a Xenia function trace (`run_xenia_probe.ps1 -FunctionTrace` in the test harness) mapped onto the image's functions |
| `workspace.py`, `workspace.ps1` | the research workspace lookup |

## Tests

`python -B tests\test_workspace.py` (the `quick` tier of `run_tests.py`; no game data): `workspace.py` and
`workspace.ps1` accept a folder with `work\` and `research\` and refuse one without. `python -B tests\test_vector_semantics.py`
(also `quick`): the p-code generator gives every modelled mnemonic a body with unique sized locals that writes its
destination (CR6 for record forms); vexptefp/vlogefp (+128) use one named estimate op per lane; vupkd3d128 is
modelled for UIMM 20 and 4 only and vpkd3d128 stays opaque; the fctiw/fctiwz/fctid/fctidz bodies write fD (CR1 for
record forms; fctiw rounds, fctiwz truncates); and it splices without touching decoding.
The emulated semantics tests are Ghidra scripts run by `run_vmx.ps1`. The `bf1943` tier also runs
`ppcdis.py` from the research workspace (`BF1943_RESEARCH`, else operation-rising-sun next to the suite) on the
decoded image; it is skipped without one.

Added 2026-10-30, not in `run_tests.py` yet: `tests\test_callgraph_memory.py` (no game data: float stores, `dcbz` and
VMX128 stores end a remembered store), `tests\test_map_project.py` (Windows; a stand-in java: a post-script that
threw fails the step, an old PASS is gone), and, reading the research workspace (only the image is copied into a
temporary folder; nothing is written there): `tests\test_xexrefs.py` (overwritten registers end a `lis` pairing),
`tests\test_record_vmx.py` (the recorder's environment and its refusals) and `tests\test_output_bytes.py` (map_functions,
classify_vmx, inventory_vmx and resolve_helpers write the outputs that evidence pins by hash byte for byte: CRLF,
as recorded; an LF change on 2026-10-30 was reverted for them and kept for outputs nothing pins).

## Checked

Moved and run from operation-rising-sun on 2026-10-23: `map_functions.py` and `inspect_decoded.py` give the same
results as before the move (only their own tool hashes in the evidence differ), and the investigations
`trace_player_pipeline.py`, `trace_ai_soldier_control.py` and `trace_string_leads.py` reproduce their evidence byte for
byte through `xexrefs.py`, `ppcdis.py` and `bfbc2dis.py` from here. The Ghidra steps were not re-run.

Run on 2026-10-26 from operation-rising-sun: `verify_xex_image.py` (all 219 page digests verify), `run_vmx.ps1` (all ten
suites pass), the batch before and after the new semantics (33,232 of 33,232 functions decompile in both), and
`export_callgraph_strings.py` (it includes all 144,917 of Ghidra's direct-call references). See operation-rising-sun's
`research/XENON_FP_VMX_SEMANTICS.md`, `XEX_IMAGE_INTEGRITY.md` and `XEX_CALLGRAPH_STRINGS.md`.
