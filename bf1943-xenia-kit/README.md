# BF1943 Xenia kit

Part of the BF1943 Tool Suite. **BF1943 status: ready** - a new BF1943 tool; it reads your own BF1943 (XBLA) package and Xenia Canary.

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

`build` takes about half a minute; `--out <folder>` builds somewhere other than `output\`. `verify` re-checks everything the build made, and `launch` verifies and then starts the game. All three take `--out`. `--gpu d3d12` switches from Vulkan to DirectX 12: on `build` it goes into `launch.cmd`, on `launch` it applies to that start.

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
| No automatic team on joining | patch file, 1 code word | "Start" puts you on the smaller team (USMC) before CHOOSE YOUR SIDE; now you have no team until you choose |
| Full licence | `--license_mask=1` in `launch.cmd` | The game runs as the Trial version (Xenia cannot check XBLA licences) |
| UDP addresses in network order | `xenia_canary_netfix.exe`, 5 instructions (`netfix.py`) | Xenia byte-swaps the destination of every UDP packet: the client's join requests go to `1.0.0.127` on the internet instead of its own server, and the match never starts |

### The game's files (`output\title`)

| File | Change | Code |
|---|---|---|
| `menu\main\mainmenu-00.fbrb` (`main_nav`) | **Quick Match** becomes **LOCAL MATCH**. It opens a browser of the four maps (Wake Island, Guadalcanal and Iwo Jima Conquest; Coral Sea Air Superiority), and picking one hosts a local match (`hostMultiplayerLocal`). The browser is the game's own developer host list, tidied and given a heading | `menu_edits.py` |
| same | **Leaderboards** and **Return to Game Library** removed from the main menu | `menu_edits.py` |
| same | **No EA Online check** when the main menu opens. Join With Friends and Private Match still report that they cannot connect, by design until LAN play | `menu_edits.py` |
| same (`main_root`) | **No "the game is saving" icon notice** at startup | `menu_edits.py` |
| `menu\hud\hud_multiplayer_cq-00.fbrb` (`igm_mp_cq`) | **CHOOSE YOUR SIDE** before the first deploy of each match. Two cards, USMC and JAPAN, show the game's own soldier portraits and flags. Left/right (stick or D-pad) chooses and A confirms. The class menu and the deploy labels stay hidden until the server has switched you, then the deploy screen redraws itself for your side: each class card with its own soldier and icon. The HUD's spawn banner and the scoreboard headers name your new side. You cannot enter battle before choosing | `igm_edits.py` |
| same | **FRONTLINE REPORT** (the in-game menu) without Leaderboards: My Squad, Scoreboard, Help & Options, Achievements, Exit Game, Respawn/Suicide | `igm_edits.py` |
| same | **No See Gamer Card** button on My Squad, the scoreboard or the friends list (an Xbox LIVE overlay, empty offline) | `igm_edits.py` |
| `config.zz` (`GameSettings.cfg`) | Network ports 1001/1003 become 25001/25003 | `config_edits.py` |

Port numbers below 1024 are the problem: Xenia moves the server's ports by +10000 but not the client's destination, so the client never finds its server.

The evidence behind each change is in `operation-rising-sun/research/AGENT_PROGRESS.md` (2026-10-22).

## Known limits

- **Xenia crashes when a second match loads in one session** (seen twice, at `xenia_canary_netfix.exe+0x25338A` in a job thread). It is Xenia's bug, not the game's or the kit's. Restart Xenia between matches.
- **Experiment, for development only:** `--experiment-team 1|2` replaces the side choice with a fixed team request each time the deploy screen opens.

## Experiment: offline bots (`--experiment-bots`)

`build --experiment-bots` also writes a second patch file, `5841097E - Battlefield 1943 - bots experiment.patch.toml`, generated by `bots.py` from its PowerPC source (assembled by `ppcasm.py`). A normal build removes every bots patch the kit made from Xenia's patches folder (under any name: before 2026-10-30 only the kit's own file name, so a renamed copy passed the build and failed its `verify`), and a bots build leaves exactly its own; a file under the kit's name that the kit did not make is refused before anything is written. `verify` (and so `launch`) fails while a bots patch is in Xenia's patches folder that the build did not make, as another build against the same Xenia leaves it.

Once you have a soldier in a match, the bot code adds 23 bots for a full 24-player match: 11 on your side and 12 against, Rifleman, Infantry and Scout in turn (`MAX_BOTS` and `DEFAULT_BOTS` in `bots.py`). They are server-side players with no network connection, created the way the game's own leftover AI spawner creates players.

| Working (2026-10-23) | Not yet |
|---|---|
| The bots join (the game announces "Bot 1 USMC Rifleman connected."), take their team's kit and appear on the scoreboard | No decisions in the guest: each bot's input is its command record in the host mailbox (below), so they stand until a host program writes it (navhost, or `guestmem.py input` in the test harness) |
| Each server tick they get an input frame and their soldier is stepped with it, as a human's is when that player's input arrives | No aiming, navigation or objectives yet: nothing in the build decides what to send |
| A host mailbox written from outside Xenia (2026-10-30; v1 in game: walk, strafe, facing, sprint (button 22), jump and crouch work). Mailbox v2 (N4, 2026-10-30): a command record per bot, each bot's state, the capture points' owners | v2 is tested offline only, not yet in game |
| Each spawns at its own team's base and respawns after dying | A fixed 23 bots; a chosen count comes later |
| They are friendly or hostile to you according to their side, and stand on the deck | |

The bot code reports to Xenia's log (`DbgPrint`, lines starting `rs-bots:`): the mailbox's address, each bot created, each spawn (with the spawn point's ID) and, every 64 ticks (about 2 s), each soldier's position. The research behind each step is in `operation-rising-sun/research/AGENT_PROGRESS.md` (2026-10-23, 2026-10-30).

### Host mailbox v2 (milestone N4)

v1 (probe N0) was one command for every bot, in the bot code's small data area. v2 gives every bot its own:

- **The block.** Once per match, when the bots are made, the bot code allocates one block of `BLOCK_SIZE` (0x780) bytes with the game's allocator (`82627d10`, as for the bots' input states), clears it and writes its header (magic `RSM2`, version 2, the record counts and sizes, the match's player manager). Then it stores the block's address (`MBOX`) and the magic (`MBOX_MAGIC`) in its data area. A new match clears both. Without a block no bots are made; the next tick tries again.
- **Which match the bots belong to** (2026-10-30). When it makes the bots the code also records the match's first player, the host's (`HOST_PLAYER`, in its data area after `MBOX`; not part of the mailbox). The bots, the block's address and magic are forgotten when the player manager is gone (between matches), its player list is empty, the manager differs from the recorded one, or its first player is not the recorded host. Before, only the manager was compared and a missing manager left everything in place: a new match whose manager reused the old address would have taken the old match's freed bot players as its own. The tests run the code's entry on the interpreter for each case.
- **Per bot slot** (`BOT0 + 0x40 * slot`):
  - the guest writes a state record every tick: the tick, then player, soldier (0 while dead), team and position, then the tick again, with `lwsync` after the opening tick and before the closing one. The two ticks are a lock: while the record is written the opening tick is new and the closing one old. A reader reads the closing tick, then the fields, then the opening tick, each read finished before the next, and takes the fields only when the two ticks are the same (no later write had begun: the ticks only grow); otherwise it tries again (`bots.host_record` is that read in Python; guestmem and navhost make it, up to 4 tries). Comparing the two ticks of one copy is not enough: a copy can take the old opening tick, new fields and the old closing tick (a review found it, 2026-10-30; the tests reproduce it on the old readers). The guest already wrote in this order, so the layout, the tick semantics and the version (2) are unchanged: the fix is the readers, and the two `lwsync`s keep the order on hardware that would reorder the stores;
  - the host writes a command record: the stamp (the tick it answered), throttle, strafe, absolute yaw and pitch, the two button words, and a sequence word (`C_SEQ`, the record's last word). The sequence is a lock: the host makes it odd (`(seq + 1) | 1`), writes the stamp and fields, then makes it even and new (`+ 1`). The bot code reads the sequence, the whole record into registers (with `lwsync` between), then the sequence again, and takes the record only when both reads are the same even value; otherwise it tries again, `TRIES` (4) times in all, and then leaves the frame neutral for that tick. It then uses the command only while its stamp is not 0 and `tick - stamp` (unsigned) is at most `STALE` (10) ticks (a stamp of 0, a new or zeroed record, was taken while the tick was still at most 10 before 2026-10-30, as the tick counts from 1 in a session's first match); otherwise the frame stays neutral, so the bots stand when the host stops. A host stopped mid-write leaves the sequence odd, and the bots stand until a host writes again.
  - Before 2026-10-30 the stamp was written last and nothing else guarded the fields, so a read during a write could take the new throttle with the old facing or buttons (a review found it; the tests reproduce it on the old code). The word was spare and is zero in a new block, an even sequence, so a host that does not lock is read as before, without the protection: the version stays 2, since nothing a reader already used changed meaning. `bots.guest_command` is the read in Python, for the hosts' tests.
- **Per capture point** (`FLAG0 + 0x30 * k`, up to 8, in the game's own iteration order: `825c5470` / `825c1290` over type `82d57850`): the tick, the entity, owner (`+0x80`), control (`+0xa0`), enabled (`+0xad`), radius (data `+0x114`), position, the tick again, under the same two-tick lock as the state records. A capture point without data gets radius 0 (before 2026-10-30 its record kept an earlier tick's radius). The header's flag count and then its tick are written after all of them (`lwsync` before each). The count and the tick are not a locked pair: a reader takes each flag record by its own two ticks, and a record whose tick is older than the newest one read is left from a tick that found more capture points (guestmem marks it stale).

The code, table and names take 0xc68 of the cave's 0xe40 bytes (0x1d8 free); the data area uses 0x8c of its 0x1f0 bytes. The mailbox layout and its version (2) are unchanged by the 2026-10-30 fixes. The readers are navhost (project-opfor-bf1943 `TnT/Code/Engine/AI/Bots/navhost`; its `rsmbox.h` is a copy of this layout that its tests check against `bots.py`) and `guestmem.py`.

## Tests

- `python -B tests\test_xeniakit.py` checks the patch file, the port edit and the Xenia fix without game data, and that `verify` enforces the build's bot mode on a made-up build: a normal build fails while Xenia's patches folder holds a bots experiment patch (enabled or not, under its own name or another: another build against the same Xenia may have left it), and a bots build fails with a second one. It also checks the build's bots patch step (`install_bots_patch`): a normal build removes every kit-made bots patch, a renamed copy too, and keeps other patch files; a bots build leaves exactly its own; a foreign file under the kit's name is refused with nothing changed.
- With `--game` it also runs a full build from your package into a temporary folder, against a scratch Xenia folder holding only a copy of `xenia_canary.exe` (so your Xenia folder is never written): every edited file must match `known.json`, the patch file must reach the scratch folder, `verify` must accept the build and must notice a changed file. Afterwards it checks that your Xenia folder's patch files and `xenia_canary_netfix.exe` are unchanged. Without your package or Xenia it skips.

- `python -B tests\test_ppcasm.py` checks the PowerPC assembler against capstone (an independent decoder; skipped with a note when capstone is not installed). `la` into r0 is `lis` + `ori` (an `addi` with base r0 adds to 0, so `lis` + `addi` lost the high half before 2026-10-30), and the test runs each `la`/`li32` pair as capstone decodes it for the value it leaves; `addi`/`addis` with base r0 and loads or stores with base r0 are refused, since the instruction reads 0 there. Operand counts are checked: an extra operand (which was dropped, so a typo assembled as something else) and a missing one (an `IndexError` without a line number) are an `AsmError` naming the line since 2026-10-30.
- `python -B tests\test_bots.py` checks that the bots code assembles, fits its code cave, and that the hook branches to it, and the mailbox: the layout fits, and capstone decodes the code's mailbox reads and writes as the layout names them (state and command records, header, flag records, the stale check, the allocation, the capture-point iteration). It also runs the code's command read on a small interpreter of capstone's decoding while the host's writes land between its loads at every offset and pace: it takes the old command, the new one or none, never a mix; an odd sequence, a stale or future stamp and a sequence that never settles give the neutral frame; `bots.guest_command` agrees in every case. It runs the code's state and flag record writes on the same kind of interpreter (the stores in `bots.guest_record`'s order, `lwsync` after the opening tick and before the closing one) while `bots.host_record` reads between its steps at many offsets and paces: the read takes the old record, the new one or none, never a mix, and a read of one copy comparing its two ticks takes a mix there. It runs the code's entry on the interpreter for each match case (the same match; no player manager; the manager's address reused by a new match with another first player; an empty list; another manager; no bots yet) and checks which path it takes, what forgetting clears, and that making the bots records the host's player; a stamp of 0 at tick 5 gives the neutral frame; a capture point without data gets radius 0; the header's count comes before its tick with `lwsync` between. `--image` also checks it against the decoded BF1943 image, first its SHA-256 (and that the game's own code uses `lwsync`).

All of them are in the suite's `run_tests.py`: the three without game data in the quick tier, `test_xeniakit.py --game` and `test_bots.py --image` in the bf1943 tier. `--image` first checks the image's SHA-256 against operation-rising-sun's `research/evidence/xex-image-integrity.json` (or the hash recorded in the test without it) and skips the other image checks on a mismatch.
