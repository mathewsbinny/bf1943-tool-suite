# BF1943 Xenia kit

Turns your own copy of Battlefield 1943 (XBLA) into a Xenia setup that plays offline:
- **Local Match** opens a browser of locally hosted maps.
- Picking a map starts a local match: the game's own server and client on your PC, with no EA servers.
- Before your first deploy, **CHOOSE YOUR SIDE** lets you pick USMC or Imperial Japan.

The kit ships no game data. Everything it makes comes from your copy and goes into `output/`, which is never committed. A build from the known package is byte-identical to the setup tested on 2026-10-22; `tests\test_xeniakit.py --game` checks this.

## What you need

- Your Battlefield 1943 content package: the file `945CD965D6B2B2584A9C06CE16F38A075A6D390558` from `5841097E\000D0000\`. Only the full release with SHA-256 `36f96e27…6550` is accepted.
- **Xenia Canary build `e6cd2e4bc` (Windows)**, with `xenia_canary.exe` SHA-256 `732cb7d5…`. Another build is refused, because the network fix is tied to this build's exact code.
- **A profile in Xenia.** Start Xenia once, create a profile and sign it in. The game needs a signed-in profile; the patch file then reports it as signed in to Xbox LIVE.
- Python 3.10 or newer. No extra libraries are needed.

## Use

```
python -B xeniakit.py build "<path to the package>" "<Xenia folder>"
output\launch.cmd
```

`build` takes about half a minute; `--out <folder>` builds somewhere other than `output\`. `verify` re-checks everything the build made, and `launch` verifies and then starts the game. Both take `--out`, and `--gpu d3d12` switches from Vulkan to DirectX 12.

The kit refuses rather than guesses: another game version, another Xenia build, a damaged package, an edited result or an existing patch file with other contents (unless you pass `--replace-patch`) all stop it with a message. Your package and `xenia_canary.exe` are only read.

### What `build` makes

| Where | What |
|---|---|
| `output\title\` | Your game, extracted (every data block's SHA-1 and every file's SHA-256 checked against `known.json`), with three files edited (below) |
| `output\title\title-manifest.json`, `output\kit-manifest.json` | Every file's hash and every change made; `verify` uses them |
| `output\launch.cmd` | Starts `xenia_canary_netfix.exe --gpu=vulkan --license_mask=1 output\title\default.xex` |
| `<Xenia>\patches\5841097E - Battlefield 1943.patch.toml` | Xenia's patch file for the game (applied in memory, by module hash `872BBE31C0FBA21B`) |
| `<Xenia>\xenia_canary_netfix.exe` | A copy of Xenia with its UDP address bug fixed; `xenia_canary.exe` stays as it was |

Xenia's settings are not changed. The two command-line options in `launch.cmd` are the only Xenia settings the kit needs.

## Every change, and why

### Xenia and the game's code

| Change | Where | Without it |
|---|---|---|
| Signed in to Xbox LIVE: sign-in state 2, all privileges | patch file, 9 code words | Online menus ask you to sign in to Xbox LIVE |
| No network (`XNET_GET_XNADDR_NONE`) | patch file, 1 code word | The menu loops on "Connecting": Xenia reports a network with no gateway |
| Full licence | `--license_mask=1` in `launch.cmd` | The game runs as the Trial version (Xenia cannot check XBLA licences) |
| UDP addresses in network order | `xenia_canary_netfix.exe`, 5 instructions (`netfix.py`) | Xenia byte-swaps the destination of every UDP packet: the client's join requests go to `1.0.0.127` on the internet instead of its own server, and the match never starts |

### The game's files (`output\title`)

| File | Change | Code |
|---|---|---|
| `menu\main\mainmenu-00.fbrb` (`main_nav`) | **Quick Match** becomes **LOCAL MATCH**. It opens a browser of the four maps (Wake Island, Guadalcanal and Iwo Jima Conquest; Coral Sea Air Superiority), and picking one hosts a local match (`hostMultiplayerLocal`). The browser is the game's own developer host list, tidied and given a heading | `menu_edits.py` |
| same | **Leaderboards** removed from the main menu | `menu_edits.py` |
| same | **No EA Online check** when the main menu opens. Join With Friends and Private Match still report that they cannot connect, by design until LAN play | `menu_edits.py` |
| same (`main_root`) | **No "the game is saving" icon notice** at startup | `menu_edits.py` |
| `menu\hud\hud_multiplayer_cq-00.fbrb` (`igm_mp_cq`) | **CHOOSE YOUR SIDE** before the first deploy of each match. Two cards, USMC and JAPAN, show the game's own soldier portraits and flags. Left/right (stick or D-pad) chooses and A confirms. The class menu and the deploy labels stay hidden until the server has switched you, then the deploy screen redraws itself for your side. You cannot enter battle before choosing | `igm_edits.py` |
| `config.zz` (`GameSettings.cfg`) | Network ports 1001/1003 become 25001/25003 | `config_edits.py` |

Port numbers below 1024 are the problem: Xenia moves the server's ports by +10000 but not the client's destination, so the client never finds its server.

The evidence behind each change is in `operation-rising-sun/research/AGENT_PROGRESS.md` (2026-10-22).

## Known limits

- **The in-game menu ("Frontline Report") still lists Leaderboards**; only the main menu's entry is removed so far.
- **Xenia occasionally crashes** in its own thread clean-up when a second match loads in one session (observed once, at `xenia_canary_netfix.exe+0x25338A`). It is Xenia's bug, not the game's or the kit's. Restart Xenia if it happens.
- **Experiment, for development only:** `--experiment-team 1|2` replaces the side choice with a fixed team request each time the deploy screen opens.

## Tests

- `python -B tests\test_xeniakit.py` checks the patch file, the port edit and the Xenia fix without game data.
- With `--game` it also runs a full build from your package into a temporary folder: every edited file must match `known.json`, `verify` must accept the build, and `verify` must notice a changed file.

Both tests are in the suite's `run_tests.py` (quick and game tiers).
