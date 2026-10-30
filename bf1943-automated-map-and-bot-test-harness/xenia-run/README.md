# Xenia runs (BF1943 Tool Suite, test harness)

**BF1943 status: ready** (2026-10-23) - new for BF1943; `xeniarun.py` replaces OPFOR's BFBC2 crash reader. The
probes below came from operation-rising-sun's `src/xeniaprobe` on 2026-10-23.

| File | What it does |
|---|---|
| `xeniarun.py` | what happened in the last Xenia session (below) |
| `run_xenia_probe.ps1` | a traced session of the Xenia kit's build (below) |
| `capture_xenia_window.ps1` | screenshots of Xenia's own window only, a frame kept when the picture changed: `-OutDir <folder> [-Minutes 10] [-IntervalMs 1500]` (generic: any title in Xenia) |
| `udp_join_probe.py` | watch a local multiplayer join from the host side: listens at the ports a client may send to by mistake (1001 / 1003, Xenia's 11001 / 11003) and tests the game's server port 127.0.0.1:25003; `python -B udp_join_probe.py <session folder> [minutes]`, results in `udp-join-probe.txt` there. Loopback only |
| `guestmem.py` | the running game's memory from outside Xenia (probe N0; mailbox v2 for N4, 2026-10-30): `attach` finds Xenia and the guest base and checks `MZ` at 0x82000000, the bots experiment's code and its mailbox; `bots` reads the bot code's state and tick, each bot's state record (player, soldier, team, position) and command, the capture points' records, soldier health and aim; `throttle <value> [--slot N\|all] [--hold S]` and `input [--throttle] [--strafe] [--yaw] [--pitch] [--buttons Jump,...] [--slot N\|all] [--press S] [--hold S]` drive the bots for `--hold` seconds (default 10), stamping each command with the guest's tick about 60 times a second (a command more than 10 ticks old is ignored), then zero them; `stop` zeroes every command; `watch [seconds] [--every S] [--slot N] [--json out.json]`. Only command records are written. Needs a build with `--experiment-bots`. v1 worked in game on 2026-10-30 (guest base 0x100000000); v2 is tested on a made-up guest only. navhost (project-opfor-bf1943) is the mailbox's real host |

## Traced sessions (run_xenia_probe.ps1)

```powershell
powershell -ExecutionPolicy Bypass -File run_xenia_probe.ps1 -Session <name> [-FunctionTrace] [-GpuFrames <list>]
    [-LogLevel 0-3] [-Gpu d3d12|vulkan] [-GuestCommandLine <text>] [-StopAfterSeconds N] [-NoShaderDump] [-NoNetFix]
```

It launches the Xenia kit's build (`bf1943-xenia-kit\output\kit-manifest.json`: the title folder, Xenia and its
network-fixed copy; `-NoNetFix` runs Xenia's own) with Xenia's log, GPU traces and shader dumps redirected into
`<suite>\work\xenia-probes\<time>-<name>\` (`BF1943_WORK` sets another work folder). The kit's `verify` runs before
and after: the session refuses a build that differs from its manifest. `session.json` records the flags and hashes
up front, `manifest.json` every output with its hash and the exit code. `-FunctionTrace` writes Xenia's function
trace and coverage; `bf1943-xex-tools\parse_function_trace.py` maps it onto the game's functions (it needs the
research workspace's function table).

## The run reader (xeniarun.py)

Tells what happened in the last Xenia session of `bf1943-xenia-kit`'s build. It reads Xenia's log, the bots'
reports in it, and Windows' record of a Xenia crash.

## Use

```
python xeniarun.py                          the Xenia folder the kit's last build used
python xeniarun.py --xenia <Xenia folder>   another Xenia folder (its xenia.log)
python xeniarun.py --log <xenia.log>        another log
python xeniarun.py --positions              also list every bot's team, kit and last position
python xeniarun.py --json out.json          also write the full result as JSON
```

The Xenia folder defaults to the one named in `bf1943-xenia-kit\output\kit-manifest.json`. With `--log` alone,
crashes are matched on the executables in the log's own folder.

## What it reports

- **The session:** Xenia's build, the title, the patches the patcher applied (`!>` lines), the number of warnings
  (`w>` lines), and the log's last lines (where a hang or crash left it).
- **The bots experiment** (`bf1943-xenia-kit --experiment-bots`; its `rs-bots:` lines): the host's player and
  team, each bot's team and kit, the spawns, and the last reported soldier positions of the host and every bot.
  Without those lines it says there was no bots experiment in the session.
- **A crash:** Windows' Application log (Application Error, event 1000) for the Xenia executable within 5 minutes
  of the log's last write: faulting module, offset and exception code. Xenia writes no crash report of its own.
  A normal exit ends the log with "Cheap-skate exit!", and the reader says so.

The exit code is 1 when Xenia crashed in that session, else 0. Nothing is changed: the log and the event log are
only read (the event log through PowerShell's `Get-WinEvent`).

## Tests

`python tests\test_xeniarun.py` (the `quick` tier of `run_tests.py`; no game data, no Xenia): the reader on a log
the test writes - build, title and patches, warnings, the bots' reports (host team, bots' teams and kits, spawns,
last positions), the normal exit line, and the report text.

`python tests\test_guestmem.py` (quick tier; no Xenia): `guestmem.py` on a made-up guest memory laid out as `bots.py`'s
mailbox v2: attach, the records and flags, torn records, `input` and `throttle` (fields first, the stamp last, zeroed
afterwards), `stop`, a block of another version refused, `--help`.

## Errors and logs

It keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools. A missing Xenia folder or
log is reported in words, with what to do.
