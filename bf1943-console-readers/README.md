# BF1943 console readers

Part of the BF1943 Tool Suite. **BF1943 status: ready** - the STFS reader extracts your own BF1943 (XBLA)
content package; `bf1943-xenia-kit` uses it (`stfs.Package`). The disc image reader is for discs such as
BFBC2's (BF1943 is download-only) and has no tests here; the examples below use BFBC2's files.

Read-only Python readers for Xbox 360 data: content packages and game disc images, from your own console
downloads and discs. Both only read standard file-system structures: there is no decryption, and no keys
are involved.

| Folder | Reads | Commands |
|---|---|---|
| `xiso\xiso.py` | Xbox / Xbox 360 game disc images (XDVDFS) | `list <image.iso> [filter]`, `extract <image.iso> <outdir> [filter]` |
| `stfs\stfs.py` | Xbox 360 content packages (STFS: LIVE / PIRS / CON), e.g. downloaded map packs | `info <package>`, `list <package> [filter]`, `extract <package> <outdir> [filter]` |

`filter` is a case-insensitive part of the file name. Both need only Python 3.

```
python xiso\xiso.py list "D:\BFBC2.iso" levels
python stfs\stfs.py extract "D:\Update3" out levels/ons_mp_008
```

## Errors and logs

Both readers keep a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools; on an error
they print the cause, what to do and the log's path (exit code 1). Tests: `tests\test_console_readers.py`
(the STFS reader on packages it builds, quick tier); your own BF1943 package is read by the Xenia kit's full
build test (bf1943 tier).

Format notes are in each script's header (STFS follows the public Free60 documentation).

Developed for Project OPFOR. Xbox is a trademark of Microsoft; Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
