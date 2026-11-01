"""NavKit driver (little-endian): the project layer of navkit_le.py - the Project OPFOR folder and its game copy, the
toolkit's tools, a level's paths in the project, and the file helpers every step uses (a JSON or text file replaced
only once written whole). Moved out of navkit_le.py on 2026-10-31, verbatim; navkit_le.py imports every name back, so
navkit_le.<name> works as before (tests replace navkit_le.Project, which navkit_le's own code calls)."""
import json
import os
import shutil
import subprocess

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # no console flashes when run from the GUI
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_SUFFIX = '.opfor-orig'
NONE_SUFFIX = '.opfor-none'     # installed where the level had no path data: restore removes the file
UNPACKED = '.navkit-unpacked'   # written into an unpack folder once FbrbTool and DbxEdit both finished


class NavKitError(Exception):
    pass


def write_json(path, value, **kw):
    """value as JSON at path, replacing the file only once it is written whole (a failed or killed write leaves the
    previous file, never a truncated one)."""
    write_text(path, json.dumps(value, **kw))


def write_text(path, text):
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def unpack_complete(dest):
    """An unpack folder from before the UNPACKED marker counts as complete when every .dbx has its converted .xml."""
    dbx = xml = 0
    for _dp, _, fs in os.walk(dest):
        names = {f.lower() for f in fs}
        for f in names:
            if f.endswith('.dbx'):
                dbx += 1
                xml += f[:-4] + '.xml' in names
    return dbx > 0 and xml == dbx


def find_opfor(given=None):
    """The Project OPFOR folder: --opfor, else OPFOR_ROOT, else a "Project OPFOR" folder next to one of the folders
    this tool sits in (e.g. next to the toolkit that holds the suite)."""
    up = []
    d = HERE
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        up.append(os.path.join(d, 'Project OPFOR'))
    candidates = [given, os.environ.get('OPFOR_ROOT')] + up
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, 'game')):
            return os.path.abspath(c)
    raise NavKitError('Project OPFOR not found - pass --opfor <folder> or set OPFOR_ROOT '
                      f'(looked for a game\\ folder in: {", ".join(c for c in candidates if c)})')


def find_tool(opfor, exe, toolkit_dir):
    """A toolkit tool: built in the toolkit (..\\<toolkit_dir>\\<exe>), else OPFOR's tools\\ folder."""
    for p in (os.path.join(os.path.dirname(HERE), toolkit_dir, exe), os.path.join(opfor, 'tools', exe)):
        if os.path.isfile(p):
            return p
    raise NavKitError(f'{exe} not found - run build.bat in the toolkit\'s {toolkit_dir} folder')


class Project:
    def __init__(self, root=None):
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
    def level_dirs(self):
        base = os.path.join(self.game, 'Dist', 'win32', 'levels')
        return {d.lower(): os.path.join(base, d) for d in sorted(os.listdir(base))
                if os.path.isfile(os.path.join(base, d, 'level-00.fbrb')) and not d.lower().endswith('_common')
                and not d.lower().startswith('opfor_')}   # opfor_*: OPFOR's data-fill bundles, not levels

    def pathdata(self, lv):
        return os.path.join(self.game, 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')

    def dice_pathdata(self, lv):
        """The level's original path data (the backup if NavKit's file is installed), or None - also while a NavKit file
        is installed on a level that had none (its NONE_SUFFIX marker): that file is not an original."""
        p = self.pathdata(lv)
        if os.path.isfile(p + NONE_SUFFIX) and not os.path.isfile(p + BACKUP_SUFFIX):
            return None
        for f in (p + BACKUP_SUFFIX, p):
            if os.path.isfile(f):
                return f
        return None

    def installed(self, lv):
        # whether NavKit's file is installed (over an original, or where the level had none)
        p = self.pathdata(lv)
        return os.path.isfile(p + BACKUP_SUFFIX) or os.path.isfile(p + NONE_SUFFIX)

    def level(self, name):
        lv = name.replace('\\', '/').split('/')[-1].lower()
        dirs = self.level_dirs()
        if lv not in dirs:
            raise NavKitError(f'unknown level {name!r} (see: navkit_le.py levels)')
        return lv

    def level_work(self, lv):
        return os.path.join(self.work, 'levels', lv)

    def out_dir(self, lv):
        d = os.path.join(self.work, 'navkit', lv)
        os.makedirs(d, exist_ok=True)
        return d

    def settings_path(self, lv):
        return os.path.join(self.out_dir(lv), 'navkit.json')

    def settings(self, lv):
        p = self.settings_path(lv)
        if not os.path.isfile(p):
            raise NavKitError(f'{lv}: not prepared yet (navkit_le.py prepare {lv.upper()})')
        return read_json(p)

    def samples(self, lv, suffix=''):
        return os.path.join(self.work, 'navsample', f'{lv}_navkit{suffix}.bin')

    def samples08(self, lv):
        """The level's 0.8 m samples: <lv>_navkit.bin (navkit_le.py sample) or <lv>_full.bin (older campaign runs) - the
        first with its _hi run, as navregress.inputs (build, apply) takes them; else the first there is."""
        found = [q for q in (os.path.join(self.work, 'navsample', n + '.bin') for n in (f'{lv}_navkit', f'{lv}_full'))
                 if os.path.isfile(q)]
        return next((q for q in found if os.path.isfile(q[:-4] + '_hi.bin')), found[0] if found else None)

    def run(self, args, log):
        r = subprocess.run(args, capture_output=True, text=True, errors='replace', creationflags=NO_WINDOW)
        out = (r.stdout + r.stderr).strip().splitlines()
        if r.returncode != 0:
            raise NavKitError(f'{os.path.basename(args[0])} failed: ' + ' | '.join(out[-3:]))
        return out

    def unpack(self, bundle, dest, log):
        """Unpacks a bundle and converts its DBX to XML. A folder counts as done only when both finished (its UNPACKED
        marker, or every .dbx converted in a folder unpacked before the marker existed); an interrupted or failed
        unpack is redone from an empty folder."""
        marker = os.path.join(dest, UNPACKED)
        if os.path.isfile(marker):
            log(f'  {os.path.relpath(dest, self.root)} already unpacked')
            return
        if os.path.isdir(dest) and os.listdir(dest):
            if unpack_complete(dest):
                write_text(marker, 'FbrbTool unpack and DbxEdit xml finished (found complete)\n')
                log(f'  {os.path.relpath(dest, self.root)} already unpacked')
                return
            log(f'  {os.path.relpath(dest, self.root)} was not unpacked completely - unpacking it again')
            shutil.rmtree(dest)
        log(f'  unpacking {os.path.relpath(bundle, self.game)} ...')
        self.run([self.fbrbtool, 'unpack', bundle, dest], log)
        log('  converting binary DBX to XML ...')
        out = self.run([self.dbxedit, 'xml', dest], log)
        log('  ' + (out[-1] if out else 'converted'))
        write_text(marker, 'FbrbTool unpack and DbxEdit xml finished\n')
