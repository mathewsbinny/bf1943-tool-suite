# BF1943 map and bot test harness (bf1943-automated-map-and-bot-test-harness)

Part of the BF1943 Tool Suite. **BF1943 status: partly** (2026-10-23) - the Xenia run reader works on BF1943
sessions in Xenia; the in-game map and bot tests are planned. OPFOR's BFBC2 crash reader (`crashreader.py`,
`symbols_795745.txt`: BFBC2 PC's `crashreport.xml` and `BFBC2Game.exe`) was removed; BF1943 runs in Xenia, which
writes no `crashreport.xml`. It remains in OPFOR's `bfbc2-tool-suite` and in this repository's history.

Repeatable tests of maps and bots, with results compared between builds (OPFOR's
[DESIGN.md](../docs/opfor-bfbc2/DESIGN.md) section 4.5).

**Ready**

- [`xenia-run/`](xenia-run/README.md): `xeniarun.py` reads the last Xenia session of `bf1943-xenia-kit`'s build:
  Xenia's build, the title, the patches applied, warnings, the bots experiment's reports (host team, bots' teams
  and kits, spawns, last positions), and a Xenia crash from Windows' Application log (exit code 1 on a crash).
  `run_xenia_probe.ps1` runs a traced session of the kit's build (Xenia's log, GPU traces, shader dumps, function
  trace) into the suite's work folder; `capture_xenia_window.ps1` takes screenshots of Xenia's window;
  `udp_join_probe.py` watches a local multiplayer join. The probes came from operation-rising-sun on 2026-10-23.
- The suite's own tests: `python -B ../run_tests.py quick | bf1943` (see the suite README). They check the tools
  themselves (format readers, bundle round trips, the level loader) before map and bot tests rely on them.
- Bot tests in Xenia are, for now, `bf1943-xenia-kit`'s (`tests/test_bots.py`) and its `rs-bots:` log lines, which
  `xenia-run` reads.
- Offline navigation checks: `bf1943-auto-navkit-big-endian`'s `navkit.py check` (every spawn and flag on the route
  graph, a route between every pair of flags), `tools/navaudit1943.py` (a build audited against its level: every
  required position, reachability, islands, consistency; 2026-10-24), and `tools/enclosure_probe1943.py` for a spot
  that is cut off.

**Planned (milestone M5)**

- Offline: unreachable regions and path-test batteries, saved per named run.
- In game: bot traversal, stuck detection, squad and vehicle use.
