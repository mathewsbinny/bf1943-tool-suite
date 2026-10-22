# BF1943 Xenia kit

Turns your own copy of Battlefield 1943 (XBLA) into a Xenia setup that plays offline. Quick Match opens a browser of locally hosted maps, and picking one starts a local match: the game's own server and client on your PC, with no EA servers.

The kit ships no game data. Everything it makes comes from your copy and goes into `output/`, which is never committed.

## What you need

- Your Battlefield 1943 content package: the file `945CD965D6B2B2584A9C06CE16F38A075A6D390558` from `5841097E\000D0000\`.
- Xenia Canary, build `e6cd2e4bc` (Windows), with a profile created in it.
- Python 3.10 or newer. No extra libraries are needed.

## Use

```
python -B xeniakit.py build "<path to the package>" "<Xenia folder>"
output\launch.cmd
```

`build` takes about half a minute. It runs five steps:
1. **Checks the package** against the known release.
2. **Extracts it**, checking every data block's SHA-1 and each of the 90 files against `known.json`.
3. **Edits two files** of that copy:
   - the main menu (`menu_edits.py`): Quick Match opens the map browser;
   - `GameSettings.cfg` (`config_edits.py`): network ports 1001/1003 become 25001/25003.
4. **Sets up Xenia:**
   - installs `patches\5841097E - Battlefield 1943.patch.toml` into Xenia's `patches` folder. Its two entries report a profile signed in to Xbox LIVE, offline, and no network;
   - writes `xenia_canary_netfix.exe` next to `xenia_canary.exe` (`netfix.py`).
5. **Writes** `title-manifest.json`, `kit-manifest.json` and `launch.cmd`.

`verify` re-checks everything the build made, and `launch` verifies and then starts the game. Both take `--out` if you built somewhere else, and `--gpu d3d12` to use DirectX 12 instead of Vulkan.

The kit refuses rather than guesses: another game version, another Xenia build, a damaged package or an edited output all stop it with a message. Your package and `xenia_canary.exe` are only read. An existing patch file with other contents is replaced only with `--replace-patch`.

## Why each change is needed

| Change | Without it |
|---|---|
| Patch file, sign-in entry | "Connect to Xbox LIVE" blocks the online menus |
| Patch file, no-network entry | The menu loops on "Connecting" (Xenia reports a network with no gateway) |
| `--license_mask=1` (in `launch.cmd`) | The game runs as the Trial version (Xenia cannot check XBLA licences) |
| Main menu | Quick Match searches for an online match, and fails |
| Ports above 1024 | Xenia moves the server's ports and the client never finds its server |
| `xenia_canary_netfix.exe` | Xenia byte-swaps the destination of every UDP packet. The client's join requests go to `1.0.0.127` on the internet instead of to its own server |

The evidence for each finding is in `operation-rising-sun/research/AGENT_PROGRESS.md` (2026-10-22).

## Tests

`python -B tests\test_xeniakit.py` checks the patch file, the port edit and the Xenia fix without game data. Add `--game` for a full build from your package into a temporary folder. Both tests are also in the suite's `run_tests.py`.
