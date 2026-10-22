# bfbc2-automated-map-and-bot-test-harness

Repeatable tests of maps and bots, with results compared between builds (`../DESIGN.md` section 4.5).

**Ready**

- `crash-reader/`: turns the game's `crashreport.xml` into function names, code, null pointers and hints.
- The suite's own tests: `python ../run_tests.py quick | game | full` (see the suite README). They check the
  tools themselves (format readers, bundle round trips, the level loader) before map and bot tests rely on
  them.
- In-game level runs are done by Project OPFOR's `testrun.bat` (start a level offline, watch its log,
  report: in game, crashed, timed out), which uses the crash reader. Campaign levels often stall on a black
  screen through its developer trigger; play-test those from the campaign menu, with OPFOR's probes
  logging (Project OPFOR `DEVELOPMENT_NOTES.md`, "NavKit play tests of sp_004_b").

**Planned (milestone M5)**

- Offline: spawn-to-objective reachability for every team, unreachable regions, path-test batteries,
  NavKit parity (`navparity`) saved per named run.
- In game: bot traversal, stuck detection, squad and vehicle use, through OPFOR's telemetry.
