"""NavKit driver (little-endian): the project layer of navkit_le.py - the Project OPFOR folder and its game copy, the
toolkit's tools, a level's paths in the project, and the file helpers every step uses (a JSON or text file replaced
only once written whole).

History: moved out of navkit_le.py on 2026-10-31, verbatim; navkit_le.py imports every name back, so
navkit_le.<name> works as before (tests replace navkit_le.Project, which navkit_le's own code calls)."""
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # no console flashes when run from the GUI
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_SUFFIX = '.opfor-orig'
NONE_SUFFIX = '.opfor-none'     # installed where the level had no path data: restore removes the file
UNPACKED = '.navkit-unpacked'   # written into an unpack folder once FbrbTool and DbxEdit both finished

type Log = Callable[[str], object]


class NavKitError(Exception):
    pass


def write_json(path: str, value: Any, **json_options: Any) -> None:
    """value as JSON at path, replacing the file only once it is written whole (a failed or killed write leaves the
    previous file, never a truncated one)."""
    write_text(path, json.dumps(value, **json_options))


def write_text(path: str, text: str) -> None:
    """text (UTF-8, \\n line ends) at path, replacing the file only once it is written whole."""
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path: str) -> Any:
    with open(path, encoding='utf-8') as f:
        return json.load(f)



def shown(path: str, base: str) -> str:
    """A path for a log line: relative to base, or whole when it is on another drive (2026-11-03: CI checks the suite
    out on D: while its temporary folders are on C:, and os.path.relpath raises across drives)."""
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return os.path.abspath(path)


def unpack_complete(dest: str) -> bool:
    """An unpack folder from before the UNPACKED marker counts as complete when every .dbx has its converted .xml."""
    dbx = xml = 0
    for _folder, _, files in os.walk(dest):
        names = {name.lower() for name in files}
        for name in names:
            if name.endswith('.dbx'):
                dbx += 1
                xml += name[:-4] + '.xml' in names
    return dbx > 0 and xml == dbx


def find_opfor(given: str | None = None) -> str:
    """The Project OPFOR folder: --opfor, else OPFOR_ROOT, else a "Project OPFOR" folder next to one of the folders
    this tool sits in (e.g. next to the toolkit that holds the suite)."""
    beside_parents = []
    folder = HERE
    while os.path.dirname(folder) != folder:
        folder = os.path.dirname(folder)
        beside_parents.append(os.path.join(folder, 'Project OPFOR'))
    candidates = [given, os.environ.get('OPFOR_ROOT')] + beside_parents
    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(candidate, 'game')):
            return os.path.abspath(candidate)
    raise NavKitError('Project OPFOR not found - pass --opfor <folder> or set OPFOR_ROOT '
                      f'(looked for a game\\ folder in: {", ".join(tried for tried in candidates if tried)})')


def find_tool(opfor: str, exe: str, toolkit_dir: str) -> str:
    """A toolkit tool: built in the toolkit (..\\<toolkit_dir>\\<exe>), else OPFOR's tools\\ folder."""
    for path in (os.path.join(os.path.dirname(HERE), toolkit_dir, exe), os.path.join(opfor, 'tools', exe)):
        if os.path.isfile(path):
            return path
    raise NavKitError(f'{exe} not found - run build.bat in the toolkit\'s {toolkit_dir} folder')


class Project:
    """A Project OPFOR folder: its game copy (game\\), NavKit's work folder (work\\), the toolkit's tools, and where
    each level's files are. lv: a level's folder name in lower case (e.g. 'sp_01')."""

    def __init__(self, root: str | None = None) -> None:
        self.root = find_opfor(root)
        self.game = os.path.join(self.root, 'game')
        self.work = os.path.join(self.root, 'work')
        self.ini = os.path.join(self.game, 'opfor.ini')
        self.log_path = os.path.join(self.game, 'logs', 'opfor.log')
        if not os.path.isfile(os.path.join(self.game, 'BFBC2Game.exe')):
            raise NavKitError(f'no game copy in {self.game} (run OPFOR\'s setup_game.bat first)')
        self.fbrbtool = find_tool(self.root, 'FbrbTool.exe', 'bf1943-fbrb-tool')
        self.dbxedit = find_tool(self.root, 'DbxEdit.exe', 'bf1943-dbx-tools')

    # --- levels --------------------------------------------------------------------------------------
    def level_dirs(self) -> dict[str, str]:
        """{level: its folder in the game's Dist\\win32\\levels}, every level with a level bundle."""
        base = os.path.join(self.game, 'Dist', 'win32', 'levels')
        return {name.lower(): os.path.join(base, name) for name in sorted(os.listdir(base))
                if os.path.isfile(os.path.join(base, name, 'level-00.fbrb')) and not name.lower().endswith('_common')
                and not name.lower().startswith('opfor_')}   # opfor_*: OPFOR's data-fill bundles, not levels

    def pathdata(self, lv: str) -> str:
        """Where the game reads the level's path data."""
        return os.path.join(self.game, 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')

    def dice_pathdata(self, lv: str) -> str | None:
        """The level's original path data (the backup if NavKit's file is installed), or None - also while a NavKit file
        is installed on a level that had none (its NONE_SUFFIX marker): that file is not an original."""
        path = self.pathdata(lv)
        if os.path.isfile(path + NONE_SUFFIX) and not os.path.isfile(path + BACKUP_SUFFIX):
            return None
        for candidate in (path + BACKUP_SUFFIX, path):
            if os.path.isfile(candidate):
                return candidate
        return None

    def installed(self, lv: str) -> bool:
        # whether NavKit's file is installed (over an original, or where the level had none)
        path = self.pathdata(lv)
        return os.path.isfile(path + BACKUP_SUFFIX) or os.path.isfile(path + NONE_SUFFIX)

    def level(self, name: str) -> str:
        """The level of a name or path (its last part, in lower case); NavKitError when the game has no such level."""
        lv = name.replace('\\', '/').split('/')[-1].lower()
        dirs = self.level_dirs()
        if lv not in dirs:
            raise NavKitError(f'unknown level {name!r} (see: navkit_le.py levels)')
        return lv

    def level_work(self, lv: str) -> str:
        """The level's unpacked bundle and extracted objects (work\\levels\\<lv>)."""
        return os.path.join(self.work, 'levels', lv)

    def out_dir(self, lv: str) -> str:
        """NavKit's folder for the level (work\\navkit\\<lv>), made when missing."""
        folder = os.path.join(self.work, 'navkit', lv)
        os.makedirs(folder, exist_ok=True)
        return folder

    def settings_path(self, lv: str) -> str:
        return os.path.join(self.out_dir(lv), 'navkit.json')

    def settings(self, lv: str) -> dict[str, Any]:
        """The level's settings (navkit.json, written by prepare); NavKitError when it is not prepared."""
        path = self.settings_path(lv)
        if not os.path.isfile(path):
            raise NavKitError(f'{lv}: not prepared yet (navkit_le.py prepare {lv.upper()})')
        return read_json(path)

    def samples(self, lv: str, suffix: str = '') -> str:
        """A sampling run's file (work\\navsample\\<lv>_navkit<suffix>.bin)."""
        return os.path.join(self.work, 'navsample', f'{lv}_navkit{suffix}.bin')

    def samples08(self, lv: str) -> str | None:
        """The level's 0.8 m samples: <lv>_navkit.bin (navkit_le.py sample) or <lv>_full.bin (older campaign runs) - the
        first with its _hi run, as navregress.inputs (build, apply) takes them; else the first there is."""
        names = (f'{lv}_navkit', f'{lv}_full')
        found = [path for path in (os.path.join(self.work, 'navsample', name + '.bin') for name in names)
                 if os.path.isfile(path)]
        return next((path for path in found if os.path.isfile(path[:-4] + '_hi.bin')), found[0] if found else None)

    def run(self, args: Sequence[str], log: Log) -> list[str]:
        """Runs a tool; its output lines, or NavKitError with its last lines when it fails."""
        result = subprocess.run(args, capture_output=True, text=True, errors='replace', creationflags=NO_WINDOW)
        out = (result.stdout + result.stderr).strip().splitlines()
        if result.returncode != 0:
            raise NavKitError(f'{os.path.basename(args[0])} failed: ' + ' | '.join(out[-3:]))
        return out

    def unpack(self, bundle: str, dest: str, log: Log) -> None:
        """Unpacks a bundle and converts its DBX to XML. A folder counts as done only when both finished (its UNPACKED
        marker, or every .dbx converted in a folder unpacked before the marker existed); an interrupted or failed
        unpack is redone from an empty folder."""
        marker = os.path.join(dest, UNPACKED)
        if os.path.isfile(marker):
            log(f'  {shown(dest, self.root)} already unpacked')
            return
        if os.path.isdir(dest) and os.listdir(dest):
            if unpack_complete(dest):
                write_text(marker, 'FbrbTool unpack and DbxEdit xml finished (found complete)\n')
                log(f'  {shown(dest, self.root)} already unpacked')
                return
            log(f'  {shown(dest, self.root)} was not unpacked completely - unpacking it again')
            shutil.rmtree(dest)
        log(f'  unpacking {shown(bundle, self.game)} ...')
        self.run([self.fbrbtool, 'unpack', bundle, dest], log)
        log('  converting binary DBX to XML ...')
        out = self.run([self.dbxedit, 'xml', dest], log)
        log('  ' + (out[-1] if out else 'converted'))
        write_text(marker, 'FbrbTool unpack and DbxEdit xml finished\n')
