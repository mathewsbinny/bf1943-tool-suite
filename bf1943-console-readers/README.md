# Console readers

Read-only Python readers for Xbox 360 data. Use them to take Battlefield: Bad Company 2 content from your
own console discs and downloads. Both only read standard file-system structures: there is no
decryption, and no keys are involved.

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

Both readers keep a log in `%LOCALAPPDATA%\bfbc2-tool-suite\logs\` like the suite's other tools; on an error
they print the cause, what to do and the log's path (exit code 1). Tests: `tests\test_console_readers.py`
(built images in the quick tier; with your Xbox 360 files in the game tier).

Format notes are in each script's header (STFS follows the public Free60 documentation).

Developed for Project OPFOR. Xbox is a trademark of Microsoft; Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
