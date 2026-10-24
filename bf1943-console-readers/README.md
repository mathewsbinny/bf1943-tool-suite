# BF1943 console readers

Part of the BF1943 Tool Suite. **BF1943 status: ready** - the STFS reader extracts your own BF1943 (XBLA)
content package; `bf1943-xenia-kit` uses it (`stfs.Package`). OPFOR's disc image reader (`xiso\xiso.py`) was
removed: BF1943 is download-only, so it has no disc. It remains in OPFOR's `bfbc2-tool-suite` and in this
repository's history.

A read-only Python reader for Xbox 360 content packages, from your own console downloads. It only reads the
standard file-system structure: there is no decryption, and no keys are involved.

| Folder | Reads | Commands |
|---|---|---|
| `stfs\stfs.py` | Xbox 360 content packages (STFS: LIVE / PIRS / CON), such as BF1943's XBLA package | `info <package>`, `list <package> [filter]`, `extract <package> <outdir> [filter]` |

`filter` is a case-insensitive part of the file name. It needs only Python 3.

```
python stfs\stfs.py info    <your BF1943 package>
python stfs\stfs.py list    <your BF1943 package> levels
python stfs\stfs.py extract <your BF1943 package> out
```

## Errors and logs

The reader keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools; on an error
it prints the cause, what to do and the log's path (exit code 1). Tests: `tests\test_console_readers.py`
(the STFS reader on packages it builds, quick tier); your own BF1943 package is read by the Xenia kit's full
build test (bf1943 tier).

Format notes are in the script's header (STFS follows the public Free60 documentation).

Developed for Project OPFOR. Xbox is a trademark of Microsoft; Battlefield 1943 is © Electronic Arts / DICE.
