# Xenia runs (BF1943 Tool Suite, test harness)

**BF1943 status: ready** (2026-10-23) - new for BF1943; `xeniarun.py` replaces OPFOR's BFBC2 crash reader. The
probes below came from operation-rising-sun's `src/xeniaprobe` on 2026-10-23.

| File | What it does |
|---|---|
| `xeniarun.py` | what happened in the last Xenia session (below) |
| `run_xenia_probe.ps1` | a traced session of the Xenia kit's build (below) |
| `capture_xenia_window.ps1` | screenshots of Xenia's own window only, a frame kept when the picture changed: `-OutDir <folder> [-Minutes 10] [-IntervalMs 1500]` (generic: any title in Xenia) |
| `udp_join_probe.py` | watch a local multiplayer join from the host side: listens at the ports a client may send to by mistake (1001 / 1003, Xenia's 11001 / 11003) and, once the game's server port 25003 is bound (read from Windows' UDP table, 2026-10-30; the probe's own test bind before reported a server on 0.0.0.0 as unbound and, while it held the bind, received the server's datagrams), sends test datagrams to 127.0.0.1:25003; `python -B udp_join_probe.py <session folder> [minutes]`, results in `udp-join-probe.txt` there. Loopback only; Windows only |
| `guestmem.py` | the running game's memory from outside Xenia (probe N0; mailbox v2 for N4, 2026-10-30): `attach` finds Xenia and the guest base and checks `MZ` at 0x82000000, the bots experiment's code, the hook that branches to it (code without it is "unhooked", not applied) and its mailbox; `bots` reads the bot code's state and tick, each bot's state record (player, soldier, team, position) and command, the capture points' records (a record older than the newest one read is left from a tick with more capture points: marked stale), soldier health and aim; `throttle <value> [--slot N\|all] [--hold S]` and `input [--throttle] [--strafe] [--yaw] [--pitch] [--buttons Jump,...] [--slot N\|all] [--press S] [--hold S]` drive the bots for `--hold` seconds (default 10), stamping each command with the guest's tick about 60 times a second (a command more than 10 ticks old is ignored) under the mailbox's sequence lock, then zero them; a hold stops writing (exit 1) once the match's mailbox is cleared or replaced; `stop` zeroes every command; `watch [seconds] [--every S] [--slot N] [--json out.json]`. Only command records are written. Needs a build with `--experiment-bots`. v1 worked in game on 2026-10-30 (guest base 0x100000000); v2 is tested on a made-up guest only. navhost (project-opfor-bf1943) is the mailbox's real host |

## Traced sessions (run_xenia_probe.ps1)

```powershell
powershell -ExecutionPolicy Bypass -File run_xenia_probe.ps1 -Session <name> [-FunctionTrace] [-GpuFrames <list>]
    [-LogLevel 0-3] [-Gpu d3d12|vulkan] [-GuestCommandLine <text>] [-StopAfterSeconds N] [-NoShaderDump] [-NoNetFix]
    [-KitOut <kit output folder>]
```

It launches the Xenia kit's build (`bf1943-xenia-kit\output\kit-manifest.json`, or `-KitOut`'s: the title folder, Xenia and its
network-fixed copy; `-NoNetFix` runs Xenia's own) with Xenia's log, GPU traces and shader dumps redirected into
`<suite>\work\xenia-probes\<time>-<name>\` (`BF1943_WORK` sets another work folder). The kit's `verify` runs before
and after: the session refuses a build that differs from its manifest. `session.json` records the flags and hashes
up front, `manifest.json` every output with its hash and the exit code. `-FunctionTrace` writes Xenia's function
trace and coverage; `bf1943-xex-tools\parse_function_trace.py` maps it onto the game's functions (it needs the
research workspace's function table). Every argument reaches Xenia quoted, so a path with a space (`BF1943_WORK`, the
suite's folder) stays one argument (before 2026-10-30 it was split); `-StopAfterSeconds` waits for the stopped Xenia
to exit before reading its exit code.

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
  Without those lines it says there was no bots experiment in the session. A report cut short or garbled (a log
  read while Xenia still writes it, or one a crash ended mid-line) is skipped and kept in `malformed`; the report
  counts them and shows the last.
- **A crash:** Windows' Application log (Application Error, event 1000) for the Xenia executable within 5 minutes
  of the log's last write: faulting module, offset and exception code. Xenia writes no crash report of its own.
  A normal exit ends the log with "Cheap-skate exit!", and the reader says so. A crash record is not this session's when
  the log ends with that normal exit, or when it is older than the session's start (`session.json` beside the log, as
  `run_xenia_probe.ps1` writes it): it is listed as another session's and not counted (before 2026-10-30 a crash of the
  previous session within 5 minutes was reported as this one's).

The exit code is 1 when Xenia crashed in that session, 2 when the event log could not be read (crash status unknown;
before 2026-10-30 such a failure read as "no crash", exit 0), else 0. Nothing is changed: the log and the event log are
only read (the event log through PowerShell's `Get-WinEvent`).

## Tests

`python tests\test_xeniarun.py` (the `quick` tier of `run_tests.py`; no game data, no Xenia): the reader on a log
the test writes - build, title and patches, warnings, the bots' reports (host team, bots' teams and kits, spawns,
last positions), the normal exit line, and the report text; and the same log cut off inside each kind of report
(recorded as malformed, the rest kept); which crash records are the session's (a normal exit, `session.json`'s start) and
an event log that cannot be read (exit 2), with PowerShell stood in for.

`python tests\test_udp_join_probe.py` (no Xenia; Windows): a stand-in server in another process bound to 0.0.0.0 reads
as bound, and its datagram reaches it. `python tests\test_run_xenia_probe.py` (no Xenia; Windows): `run_xenia_probe.ps1`
on a made-up kit build and a stand-in Xenia in folders with a space: each path argument arrives whole, and
`-StopAfterSeconds` writes `manifest.json` with the exit code. These two are not in `run_tests.py` yet.

`python tests\test_guestmem.py` (quick tier; no Xenia): `guestmem.py` on a made-up guest memory laid out as `bots.py`'s
mailbox v2: attach, the records and flags, torn records, `input` and `throttle` (under the sequence lock: the
sequence odd, the stamp and fields, the sequence even; zeroed afterwards), `stop`, a block of another version
refused, `--help`; a command write against the bot code's read (`bots.guest_command`) wherever its writes land
between the guest's loads (a whole command or none, never a mix); the state and flag record reads against the bot
code's writes (`bots.guest_record`, its order) landing between guestmem's loads, at steady paces and in two bursts
(a reader preempted mid-read): the old record or the new one, or marked torn, never an unmarked mix; the bot code
without its hook (not applied); a hold that outlives its match (nothing more written).

guestmem reads each state and flag record under its two-tick lock (`record_words`, as `bots.host_record` reads it):
the closing tick, the record, then the opening tick, each read finished before the next; whole when the two ticks are
the same, else tried again (4 tries in all, then `torn`). Before 2026-10-30 it compared the two ticks of one copy,
which can pass the old opening tick, new fields and the old closing tick as whole.

## Errors and logs

It keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools. A missing Xenia folder or
log is reported in words, with what to do.
