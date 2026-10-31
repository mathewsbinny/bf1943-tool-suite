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

`filter` is a case-insensitive part of the file name. It needs only Python 3. `extract` checks every name in the
package before it writes anything: a name that would leave `<outdir>` (a `..`, `.` or empty part, a drive, a
backslash or colon, leading or trailing spaces) is refused with nothing written, since package names are untrusted.
So are two files that would be one file on Windows (names equal but for case) and a file where a folder goes. An
unknown command or a missing argument prints the usage and exits 1.

```
python stfs\stfs.py info    <your BF1943 package>
python stfs\stfs.py list    <your BF1943 package> levels
python stfs\stfs.py extract <your BF1943 package> out
```

## Errors and logs

The reader keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools; on an error
it prints the cause, what to do and the log's path (exit code 1). A damaged package is refused with a
`ValueError`: a folder entry that is (through its parents) its own parent, or a file whose blocks hold fewer bytes
than its declared size (a package cut short), or a fragmented file whose block chain comes back to a block it
already read, or a name longer than its 40-byte field, or a parent index that is no folder entry - never a hang, a
short file, repeated content or a file moved to the root. A package whose volume descriptor says its hash tables'
second copies are current (block separation bit 1) is refused unread: the reader follows only the first copies.
So is a package with two copies of each hash table (bit 0 clear) and more than 0xAA allocated blocks: there each
level-1 entry chooses its level-0 table's current copy, which the reader does not follow (BF1943's own package is
read-only, bit 0 set, so it is read). A header or file table cut short is a damaged package. A file without a
LIVE, PIRS or CON signature, a 0x24-byte volume descriptor, a plausible header size and a file table of at least
one block is refused as no STFS package. `extract` reads and checks each file before writing
it, through a temporary file beside it, so a damaged file leaves a copy extracted earlier as it was. Tests: `tests\test_console_readers.py`
(the STFS reader on packages it builds, including names that try to leave the output folder, parent loops and
files cut short, block chains that loop and a damaged file over an earlier copy; quick tier); your own BF1943 package is read by the Xenia kit's full
build test (bf1943 tier).

Format notes are in the script's header (STFS follows the public Free60 documentation).

Developed for Project OPFOR. Xbox is a trademark of Microsoft; Battlefield 1943 is © Electronic Arts / DICE.
