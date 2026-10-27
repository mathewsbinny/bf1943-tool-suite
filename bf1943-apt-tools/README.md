# BF1943 APT tools

Part of the BF1943 Tool Suite. **BF1943 status: ready** - reads BF1943's own menu movies; the Xenia kit uses it for its menu edits.

Reader, disassembler and block rewriter for BF1943's menu movies (`Menu/*.res`: BIGF archives of EA APT, big-endian Xbox 360).

`apt.py` provides:
- `Apt(res_bytes)`: characters, frames and action blocks. `validate()` checks that every block decodes to its End, that every branch and function end lands on an instruction, and that no function body contains an End. `listing(block)` disassembles a block.
- `Rewriter(apt)`:
  - `patch()` makes same-size in-place edits after checking the original bytes.
  - `block()`, `assemble()` and `repoint()` relocate an edited block to the end of the `.apt`, and point its frame item at it.
  - `res(template)` rebuilds the BIGF archive.

```
python -B apt.py <menu .res>      # validate one movie
```

Rules that come from the game itself (pointer fixup `829748f8`):
- A block is walked linearly to its first End, so code reached only by a branch is never relocated. Relocate whole blocks.
- PushData and ConstantPool lists are converted in place at load. Never share a list between blocks; `assemble()` always emits fresh ones.

Tests: `python -B ..\tests\test_bf1943_data.py apt` (bf1943 tier) validates the three movies the Xenia kit edits (`main_nav`, `main_root`, `igm_mp_cq`). The research copy was validated on all 14 BF1943 menu movies (operation-rising-sun `research/AGENT_PROGRESS.md`); that check is not repeated here. All 1,991 action blocks relocate unchanged and decode back to the same program.

Source: copied from `operation-rising-sun/src/uiresearch/apt.py` (no longer in that repository's working tree; its history keeps it). The research copy, its evidence and its use are in `operation-rising-sun/research/AGENT_PROGRESS.md`. Provenance: `provenance/rising-sun-imports.json`.
