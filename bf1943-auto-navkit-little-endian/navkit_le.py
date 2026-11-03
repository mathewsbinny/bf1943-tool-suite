"""NavKit driver: one command per step, from a level name to an installed pathdata.hpd.

  python navkit_le.py levels                     list the game's levels (and which can be sampled)
  python navkit_le.py prepare SP_01              unpack + convert the level, extract its objects,
                                              choose the sampling box and sector offsets
  python navkit_le.py sample  SP_01 [--yes]      in-game collision sampling (launches the game): two runs at 0.8 m,
                                              then two at 0.2 m over the level's detailed zones (if it has any)
  python navkit_le.py build   SP_01              generate pathdata.hpd
  python navkit_le.py check   SP_01              score it (against DICE's file if the level has one)
  python navkit_le.py apply   SP_01 [--navtune file] [--out file] [--install]
                                              build with the edits of the level's navigation side file
                                              (work\\navkit\\<level>\\<level>.navtune.json, made in the
                                              Navigation Studio) into work\\navkit\\<level>\\pathdata.hpd,
                                              traversals into <level>.traversal.json, a report next to it;
                                              --install: then install it
  python navkit_le.py install SP_01 [--from file]  copy it (or another built file) into the game, with its
                                              traversal annotations (DICE's file is backed up once)
  python navkit_le.py restore SP_01              put the original file back
  python navkit_le.py all     SP_01 [--yes]      prepare, sample, build, check
  python navkit_le.py status  SP_01              what has been done for a level
  python navkit_le.py compare SP_01 [--reference a.hpd] [--candidate b.hpd]
                                              two path-data files on the level's AI positions
                                              (default: the original against NavKit's build)

Options: --box minX,minZ,maxX,maxZ and --y bottom,top (prepare / sample: override the sampling box),
         --pitch 0.8 / 0.2 (sample: that pass only; 0.2 = a detailed-zone run over the level's detailed sectors;
         writes <level>_navkit_p02.bin / _hi_p02.bin, the level's settings and its 0.8 m samples untouched),
         --budget <ms> (sample / all: milliseconds of sampling per frame for every run, e.g. 800 when a run reports
         that the level ended first; default: opfor.ini's for 0.8 m runs, 400 for 0.2 m runs),
         --opfor <Project OPFOR folder> (default: OPFOR_ROOT, else "Project OPFOR" next to the toolkit).

NavKit works on Project OPFOR's game copy and uses its in-game sampler. FbrbTool / DbxEdit come from the
toolkit (..\\bf1943-fbrb-tool, ..\\bf1943-dbx-tools, built with their build.bat) or from OPFOR's tools\\ folder.

Files (in the OPFOR folder): work\\levels\\<level>\\ (unpacked level, objects), work\\navkit\\<level>\\
(settings, pathdata.hpd, check report), work\\navsample\\<level>_navkit*.bin (samples, written by OPFOR's
[dev] navsample). See QUICKSTART.md.

In the code, `project` is the Project OPFOR folder (navkit_le_project.Project) and `lv` a level's folder name in lower
case ('sp_01'); a level's settings (navkit.json) are {'level': its partition name, 'box', 'y', 'xoff', 'zoff', ...}.
"""
import glob
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)

DETAILED_BUDGET_MS = 400   # pitch-0.2 runs: ms of sampling per frame - 10x the columns of a normal run, so it must finish
                           # in less game time than a round or a campaign level allows an idle player (150: ONS_MP_002 at
                           # 95 %, SP_006 and SP_007 back at the menu after ~11 min; 400: all 15 levels, 2026-10-22)
PITCHES = (0.8, 0.2)       # the sampling pitches a build reads: the normal runs and the detailed zones
AGENTS = [('normal', '0.4,0.28,1.9', ''), ('high capsule', '0.4,0.6,1.9', '_hi')]   # radius,stepMax,height
SAMPLE_DONE = re.compile(r'navsample: (done|no physics|cannot create|empty box|needs \[debug\])')

# the project layer (navkit_le_project.py since 2026-10-31), every name of it navkit_le.<name> as before
from navkit_le_project import (  # noqa: E402,F401
    BACKUP_SUFFIX, HERE, NO_WINDOW, NONE_SUFFIX, UNPACKED, NavKitError, Project, find_opfor, find_tool, read_json,
    shown, unpack_complete, write_json, write_text)

type Log = Callable[[str], object]
type Confirm = Callable[[str], bool]          # asks the user a yes/no question
type Settings = dict[str, Any]                # a level's navkit.json


def _is_detailed(pitch: float) -> bool:
    """Whether a sampling pitch is the detailed zones' (anything but the normal 0.8 m)."""
    return abs(pitch - 0.8) > 1e-6


# --- steps -----------------------------------------------------------------------------------------------
def levels(project: Project, log: Log = print) -> None:
    """Lists the game's levels: whether each has AI path data (can be sampled) and what NavKit has done for it."""
    log('level        AI path data   NavKit')
    for lv in project.level_dirs():
        has_ai = 'yes (samplable)' if project.dice_pathdata(lv) else 'no'
        done = []
        if os.path.isfile(os.path.join(project.work, 'navkit', lv, 'navkit.json')):
            done.append('prepared')
        if os.path.isfile(os.path.join(project.work, 'navkit', lv, 'pathdata.hpd')):
            done.append('built')
        if project.installed(lv):
            done.append('INSTALLED')
        log(f'{lv:12s} {has_ai:14s} {", ".join(done)}')


def level_names(project: Project) -> list[tuple[str, bool]]:
    """[(level, samplable)] for the GUI."""
    return [(lv, project.dice_pathdata(lv) is not None) for lv in project.level_dirs()]


def prepare(project: Project, lv: str, log: Log = print, box: Sequence[float] | None = None,
            y: Sequence[float] | None = None) -> None:
    """Unpacks the level (and the common bundles), extracts its objects, collision, spawns and ladders, and writes its
    settings: the sampling box and heights (given, else sampling_box) and the sector offsets."""
    import levelobjects
    log(f'== prepare {lv}')
    common = []
    for name in ('sp_common', 'mp_common'):
        dest = os.path.join(project.work, 'navkit', 'common', name)
        project.unpack(os.path.join(project.game, 'Dist', 'win32', 'levels', name, 'level-00.fbrb'), dest, log)
        common.append(dest)
    level_dir = project.level_work(lv)
    project.unpack(os.path.join(project.level_dirs()[lv], 'level-00.fbrb'), os.path.join(level_dir, 'dist'), log)
    package = os.path.join(project.game, 'Package', 'levels', os.path.basename(project.level_dirs()[lv]),
                           'level-00.fbrb')
    if os.path.isfile(package):
        project.unpack(package, os.path.join(level_dir, 'pkg'), log)
    else:
        os.makedirs(os.path.join(level_dir, 'pkg'), exist_ok=True)
    partition = level_partition_name(level_dir, lv)
    log('  extracting objects, collision and spawn points ...')
    levelobjects.main(['levelobjects.py', level_dir, partition] + common)
    import ladders
    found = ladders.extract(level_dir, common)
    write_json(os.path.join(level_dir, 'ladders.json'), found, indent=1)
    log(f'  ladders: {len(found)} (ladder links: their far ends seeded and linked in the build)')

    xoff = zoff = None
    offsets_files = glob.glob(os.path.join(level_dir, 'dist', 'levels', lv, 'Precomputed', 'Infantry',
                                           'SectorOffsets.xml'))
    if offsets_files:
        xoff, zoff = sector_offsets(offsets_files[0])
    auto_box, auto_y, source = sampling_box(project, lv, level_dir)
    settings: Settings = {'level': partition, 'box': box or auto_box, 'y': y or auto_y,
                          'box_source': 'given' if box else source}
    if xoff is None:
        # No SectorOffsets asset (Onslaught and other MP levels): DICE's rule, one sector below the path data's
        # lowest extent, so the first sector id is 1,1 - it holds for all 15 shipped SectorOffsets (13 campaign,
        # ons_mp_005, ons_mp_008). A new map has no path data; its box (which holds the ground) stands in.
        import navregress
        extent = navregress.reference_bounds(project.root, lv)
        low = extent[:2] if extent else settings['box'][:2]
        xoff, zoff = int(math.floor(low[0] / 100.0)) - 1, int(math.floor(low[1] / 100.0)) - 1
        settings['offsets_source'] = 'rule: path data extent' if extent else 'rule: box'
    else:
        settings['offsets_source'] = 'SectorOffsets'
    settings['xoff'], settings['zoff'] = xoff, zoff
    write_json(project.settings_path(lv), settings, indent=1)
    sampled_box = settings['box']
    columns = (sampled_box[2] - sampled_box[0]) / 0.8 * (sampled_box[3] - sampled_box[1]) / 0.8
    log(f'  box {fmt(sampled_box)} ({settings["box_source"]}), y {fmt(settings["y"])}, '
        f'sector offsets {settings["xoff"]}, {settings["zoff"]} ({settings["offsets_source"]}); '
        f'{columns / 1e6:.1f} M columns per sampling run')
    log(f'  settings: {shown(project.settings_path(lv), project.root)}')


def sector_offsets(path: str) -> tuple[int, int]:
    """(XOffset, ZOffset) of a SectorOffsets.xml; a NavKitError naming the file when it does not hold them."""
    import navregress
    try:
        return navregress.sector_offsets(path)
    except ValueError as e:
        raise NavKitError(str(e)) from e


def level_partition_name(level_dir: str, lv: str) -> str:
    """The level partition's name as the bundle spells it (levels/<name>.xml); NavKitError when it is missing."""
    for path in glob.glob(os.path.join(level_dir, 'dist', 'levels', '*.xml')):
        name = os.path.splitext(os.path.basename(path))[0]
        if name.lower() == lv:
            return name
    raise NavKitError(f'{lv}: level partition levels/{lv}.xml not found in the bundle')


def sampling_box(project: Project, lv: str, level_dir: str) -> tuple[list[int], list[int], str]:
    """Box (minX, minZ, maxX, maxZ) and y (bottom, top): the original path data's extent plus 20 m if the
    level has path data, else the extent of its placed objects and spawn points plus 50 m."""
    points: list[tuple[float, float, float]] = []
    reference = project.dice_pathdata(lv)
    margin, source = 0.0, ''
    if reference:
        import hpdtool
        import kymesh
        hpd = hpdtool.parse(open(reference, 'rb').read())
        for sector in hpd.sectors:
            for area in kymesh.parse_mesh(sector.mesh).areas:
                for line in area.outline:
                    points.extend((point[0], point[1], point[2]) for point in line)
        margin, source = 20.0, 'extent of the level\'s path data + 20 m'
    if not points:
        for name in ('objects.json', 'group_instances.json'):
            for placed in json.load(open(os.path.join(level_dir, name))):
                points.append((placed['m'][12], placed['m'][13], placed['m'][14]))
        points += [tuple(spawn) for spawn in json.load(open(os.path.join(level_dir, 'soldier_spawns.json')))]
        margin, source = 50.0, 'extent of the level\'s objects + 50 m'
    if not points:
        raise NavKitError(f'{lv}: no objects to size the sampling box - pass --box and --y')
    x_values, y_values, z_values = zip(*points)
    box = [math.floor((min(x_values) - margin) / 10) * 10, math.floor((min(z_values) - margin) / 10) * 10,
           math.ceil((max(x_values) + margin) / 10) * 10, math.ceil((max(z_values) + margin) / 10) * 10]
    y_range = [math.floor((min(y_values) - 30) / 10) * 10, math.ceil((max(y_values) + 150) / 10) * 10]
    return box, y_range, source


def fmt(values: Sequence[float]) -> str:
    """Numbers as the sampler's ini and the log write them: comma-separated, in their shortest form."""
    return ','.join(f'{value:g}' for value in values)


def game_running() -> bool:
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq BFBC2Game.exe', '/NH'], capture_output=True, text=True,
                            creationflags=NO_WINDOW)
    return 'BFBC2Game.exe' in result.stdout


def ini_set(text: str, section: str, key: str, value: str) -> str:
    """Sets key=value in [section] of an ini text (adds the key if missing). A leading byte-order mark is kept, and
    not taken for part of the first section's header (that made a second [dev] section)."""
    if text.startswith('﻿'):
        return '﻿' + _ini_set(text[1:], section, key, value)
    return _ini_set(text, section, key, value)


def _ini_set(text: str, section: str, key: str, value: str) -> str:
    lines = text.split('\n')
    current, start, end = None, None, len(lines)
    for i, line in enumerate(lines):
        header = re.match(r'\s*\[([^\]]+)\]', line)
        if header:
            if current == section.lower():
                end = i
                break
            current = header.group(1).strip().lower()
            if current == section.lower():
                start = i
    cr = '\r' if '\r\n' in text else ''   # keep the file's line endings
    if start is None:
        return text.rstrip('\r\n') + f'{cr}\n{cr}\n[{section}]{cr}\n{key}={value}{cr}\n'
    for i in range(start + 1, end):
        if re.match(rf'\s*{re.escape(key)}\s*=', lines[i]):
            lines[i] = f'{key}={value}{cr}'
            return '\n'.join(lines)
    lines.insert(start + 1, f'{key}={value}{cr}')
    return '\n'.join(lines)


def ini_backup(project: Project) -> str:
    """Where a sampling run keeps opfor.ini as it was."""
    return os.path.join(project.work, 'navkit', 'opfor.ini.backup')


def recover_ini(project: Project, log: Log = print) -> None:
    """A sampling run that was killed leaves opfor.ini changed: put the saved copy back."""
    backup = ini_backup(project)
    if os.path.isfile(backup):
        shutil.copyfile(backup, project.ini)
        os.remove(backup)
        log('  restored opfor.ini from the copy an interrupted sampling run left behind')


def ensure_settings(project: Project, lv: str) -> Settings:
    """The level's settings (navkit.json); a level sampled before navkit.json existed gets one from what is there: its
    name (the converted level partition), box and heights (its 0.8 m samples), sector offsets (its SectorOffsets)."""
    if os.path.isfile(project.settings_path(lv)):
        return project.settings(lv)
    import navsample
    level_dir = project.level_work(lv)
    samples_path = project.samples08(lv)
    offsets_files = glob.glob(os.path.join(level_dir, 'dist', 'levels', '*', 'Precomputed', 'Infantry',
                                           'SectorOffsets.xml'))
    if not samples_path or not offsets_files:
        raise NavKitError(f'{lv}: not prepared yet (navkit_le.py prepare {lv.upper()})')
    xoff, zoff = sector_offsets(offsets_files[0])
    samples, _ = navsample.load(samples_path)
    name = level_partition_name(level_dir, lv)
    max_x = samples.min_x + (samples.nx - 1) * samples.pitch
    max_z = samples.min_z + (samples.nz - 1) * samples.pitch
    settings = {'level': name,
                'box': [round(samples.min_x, 3), round(samples.min_z, 3), round(max_x, 3), round(max_z, 3)],
                'y': [round(samples.y_bottom, 3), round(samples.y_top, 3)], 'box_source': 'its 0.8 m samples',
                'offsets_source': 'its SectorOffsets', 'xoff': xoff, 'zoff': zoff}
    write_json(project.settings_path(lv), settings, indent=1)
    return settings


def detailed_sectors(project: Project, lv: str) -> list[tuple[int, int]]:
    """The sectors to build as detailed zones (pitch 0.2), as (grid x, grid z) - chosen from the level alone
    (tools\\detailed.py: flagged objects, stacked floors in the 0.8 m samples); cached in work\\navkit\\<lv>\\
    detailed.json while the samples are the same."""
    import detailed
    settings = ensure_settings(project, lv)
    samples_path = project.samples08(lv)
    cache = os.path.join(project.out_dir(lv), 'detailed.json')
    objects = os.path.join(project.level_work(lv), 'objects.json')
    # the choice depends on the samples, the placed objects and the sector offsets (its ids are relative to them)
    samples_name = os.path.basename(samples_path) if samples_path else None
    samples_time = os.path.getmtime(samples_path) if samples_path else 0
    objects_time = os.path.getmtime(objects) if os.path.isfile(objects) else 0
    key = (f'{samples_name}|{samples_time}|{detailed.MARGIN}|{detailed.STACKED_MIN}'
           f'|offsets {settings["xoff"]},{settings["zoff"]}|objects {objects_time}'
           # the rule's version: a choice by the old one is redone
           f'|flag NeedsDetailedPathfinding|sectors in whole cm')
    if os.path.isfile(cache):
        try:
            cached = read_json(cache)
        except ValueError:
            cached = {}                                   # a damaged cache is made again
        if cached.get('key') == key:
            return [tuple(sector) for sector in cached['sectors']]
    common = [os.path.join(project.work, 'navkit', 'common', name) for name in ('mp_common', 'sp_common')]
    try:
        chosen, why = detailed.choose(project.level_work(lv), common, samples_path, settings['xoff'], settings['zoff'])
    except ValueError as e:                      # e.g. objects.json from before NeedsDetailedPathfinding was extracted
        raise NavKitError(f'{lv}: {e}') from e
    write_json(cache, {'key': key, 'sectors': chosen, 'why': why})
    return chosen


def detailed_rects(project: Project, lv: str, margin: float = 1.0) -> list[list[float]]:
    """The detailed sectors' squares plus a margin (the sampling window's overlap), for navsample_rects."""
    settings = ensure_settings(project, lv)
    rects = []
    for i, j in detailed_sectors(project, lv):
        x, z = (i + settings['xoff']) * 100, (j + settings['zoff']) * 100
        rects.append([x - margin, z - margin, x + 100 + margin, z + 100 + margin])
    return rects


def dice_detailed_sectors(project: Project, lv: str) -> list[tuple[int, int]]:
    """The sectors DICE's path data builds as detailed zones (pitch 0.2), as (grid x, grid z) ids; empty without it.
    For scoring detailed_sectors only - NavKit does not choose from it."""
    import hpdtool
    import kymesh
    path = project.dice_pathdata(lv)
    if not path:
        return []
    return sorted((sector.id >> 8, sector.id & 0xff) for sector in hpdtool.parse(open(path, 'rb').read()).sectors
                  if abs(kymesh.parse_mesh(sector.mesh).pitch - 0.2) < 1e-3)


def detailed_box(project: Project, lv: str, margin: float = 1.0) -> list[float]:
    """The box around the detailed sectors (their 100 m squares plus a margin), for a pitch-0.2 sampling run."""
    settings = ensure_settings(project, lv)
    ids = detailed_sectors(project, lv)
    if not ids:
        raise NavKitError(f'{lv}: no detailed sectors (no flagged objects or stacked floors) '
                          '- nothing to sample at 0.2')
    x_values = [(i + settings['xoff']) * 100 for i, _ in ids]
    z_values = [(j + settings['zoff']) * 100 for _, j in ids]
    return [min(x_values) - margin, min(z_values) - margin, max(x_values) + 100 + margin, max(z_values) + 100 + margin]


def pitch_suffix(pitch: float) -> str:
    """Sample-file suffix of a pitch: '' for the normal 0.8, '_p02' for 0.2."""
    return '' if abs(pitch - 0.8) < 1e-6 else f'_p{int(round(pitch * 10)):02d}'


def sample(project: Project, lv: str, log: Log = print, confirm: Confirm | None = None,
           box: Sequence[float] | None = None, y: Sequence[float] | None = None, timeout: float = 3600,
           pitch: float = 0.8, budget_ms: int | None = None) -> bool:
    """One sampling pass at pitch (the normal and the high-capsule run); False when the user declined. At 0.2 m the box
    is the detailed sectors' (only their squares are sampled) and the level's settings stay as they are; at 0.8 m a
    box or y given is saved in them."""
    settings = ensure_settings(project, lv)
    detailed_run = _is_detailed(pitch)
    if detailed_run:
        # a detailed-zone run: its own box (the detailed sectors by default) and only their squares sampled
        # (navsample_rects); the level's settings stay as they are
        settings = dict(settings, box=box or detailed_box(project, lv), y=y or settings['y'],
                        rects=None if box else detailed_rects(project, lv))
    elif box or y:
        settings['box'], settings['y'] = box or settings['box'], y or settings['y']
        write_json(project.settings_path(lv), settings, indent=1)
    log(f'== sample {lv}: box {fmt(settings["box"])}, y {fmt(settings["y"])}, pitch {pitch}')
    if not project.dice_pathdata(lv):
        log('  warning: this level has no AI path data, so its AI (and the sampler) may not start - see QUICKSTART.md')
    if game_running():
        raise NavKitError('the game is running - close it first')
    if confirm and not confirm(f'NavKit will start the game twice to sample {lv.upper()} '
                               '(about 1-5 minutes each; the screen may stay black). Continue?'):
        log('  cancelled')
        return False
    recover_ini(project, log)
    os.makedirs(os.path.join(project.work, 'navsample'), exist_ok=True)
    suffix_of_pitch = pitch_suffix(pitch)
    import detailed
    if detailed_run:
        # until both runs finish the record says "pending": a run that fails part-way leaves its samples, and without
        # a record those would pass for samples from before the record existed (detailed.sampled_problem)
        detailed.write_sampled_record(project.samples(lv, suffix_of_pitch), [], settings['xoff'], settings['zoff'],
                                      pending=True)
    for label, agent, suffix in AGENTS:
        log(f'  run {label} ({agent}) ...')
        out = project.samples(lv, suffix + suffix_of_pitch)
        if os.path.isfile(out):
            os.remove(out)
        result = sample_run(project, settings, agent, os.path.basename(out)[:-4], log, timeout, pitch, budget_ms)
        if not os.path.isfile(out) or 'done' not in result:
            raise NavKitError(f'sampling did not finish: {result or "no result in the log"}')
    if detailed_run:
        # which detailed sectors these samples cover, at which offsets: build checks them against its own choice
        detailed.write_sampled_record(project.samples(lv, suffix_of_pitch),
                                      sectors_in_box(project, lv, settings['box']), settings['xoff'], settings['zoff'])
    log('  samples: ' + ', '.join(shown(project.samples(lv, suffix + suffix_of_pitch), project.root)
                                  for _, _, suffix in AGENTS))
    return True


def sectors_in_box(project: Project, lv: str, box: Sequence[float]) -> list[tuple[int, int]]:
    """The level's detailed sectors whose 100 m squares lie inside a sampling box."""
    settings = ensure_settings(project, lv)
    inside = []
    for i, j in detailed_sectors(project, lv):
        x, z = (i + settings['xoff']) * 100, (j + settings['zoff']) * 100
        if box[0] <= x and x + 100 <= box[2] and box[1] <= z and z + 100 <= box[3]:
            inside.append((i, j))
    return inside


def sample_level(project: Project, lv: str, log: Log = print, confirm: Confirm | None = None,
                 box: Sequence[float] | None = None, y: Sequence[float] | None = None,
                 budget_ms: int | None = None) -> bool:
    """The level's sampling: the normal and high-capsule runs at 0.8 m, then - when the level has detailed sectors
    (detailed.py: flagged objects, stacked floors in the 0.8 m samples) - the same two at 0.2 m over those sectors.
    One confirmation for all game launches. budget_ms (--budget): the sampling time per frame of every run."""
    if confirm and not confirm(f'NavKit will start the game up to four times to sample {lv.upper()} '
                               '(two 0.8 m runs, then two 0.2 m runs over its detailed zones; about 1-15 minutes '
                               'each; the screen may stay black). Continue?'):
        log('  cancelled')
        return False
    if not sample(project, lv, log=log, box=box, y=y, budget_ms=budget_ms):
        return False
    ids = detailed_sectors(project, lv)
    if not ids:
        log(f'  {lv}: no detailed sectors - no 0.2 m runs')
        return True
    log(f'  {lv}: {len(ids)} detailed sectors - 0.2 m runs')
    return sample(project, lv, log=log, pitch=0.2, budget_ms=budget_ms)


def sample_run(project: Project, settings: Settings, agent: str, name: str, log: Log, timeout: float,
               pitch: float = 0.8, budget_ms: int | None = None) -> str:
    """One sampling run: opfor.ini set for it (and put back afterwards, whatever happens), the game started, its log
    followed until the sampler reports an end, the game exits, the level ends or the timeout passes. Returns how it
    ended ('done' among the sampler's results, else the reason)."""
    original = open(project.ini, 'rb').read()
    shutil.copyfile(project.ini, ini_backup(project))
    log_start = log_mark(project)
    ini_text = original.decode('utf-8', errors='replace')
    changes = [('dev', 'level', 'Levels/' + settings['level']), ('dev', 'autostart', '1'), ('dev', 'navsample', '1'),
               ('dev', 'navsample_name', name), ('dev', 'navsample_box', fmt(settings['box'])),
               ('dev', 'navsample_y', fmt(settings['y'])), ('dev', 'navsample_pitch', fmt([pitch])),
               ('dev', 'navsample_offset', '0'), ('dev', 'navsample_agent', agent), ('debug', 'probes', '1'),
               ('dev', 'navsample_rects', ';'.join(fmt(rect) for rect in (settings.get('rects') or ())))]
    # a pitch-0.2 run is 10x the columns: a larger time budget per frame, so it finishes before a round with
    # working bots (or a campaign level's idle player: SP_006 back at the menu at 60 %, 2026-10-22) can end;
    # budget_ms overrides it (a retry at a larger budget: less game time for the same columns)
    # working bots can end (ONS_MP_002 at 40 ms: back to the menu at 95 %, 2026-10-21); normal runs keep the ini's
    if budget_ms:
        changes.append(('dev', 'navsample_budget_ms', str(budget_ms)))
    elif _is_detailed(pitch):
        changes.append(('dev', 'navsample_budget_ms', str(DETAILED_BUDGET_MS)))
    for section, key, value in changes:
        ini_text = ini_set(ini_text, section, key, value)
    result = ''
    try:
        open(project.ini, 'wb').write(ini_text.encode('utf-8'))
        subprocess.Popen([os.path.join(project.root, 'OPFOR.exe')], cwd=project.root)
        started = time.time()
        last = ''
        time.sleep(15)
        while time.time() - started < timeout:
            time.sleep(5)
            new = read_log(project, log_start)
            progress = [line for line in new if 'navsample:' in line]
            if progress and progress[-1] != last:
                last = progress[-1]
                log('    ' + last.split('navsample:', 1)[1].strip())
            ends = [match for match in (SAMPLE_DONE.search(line) for line in new) if match]
            if ends:
                result = ends[-1].group(1)
                break
            if not game_running():
                result = 'the game exited'
                break
            # back at the main menu after sampling began: the level ended (a round over, a campaign level failed);
            # sampling began at 'sampling', not 'armed' (that comes before the menu)
            first = next((i for i, line in enumerate(new) if 'navsample: sampling' in line), None)
            if first is not None and any('UI cmd [main menu]' in line for line in new[first:]):
                used = budget_ms or (DETAILED_BUDGET_MS if _is_detailed(pitch) else "opfor.ini's")
                result = (f'the level ended (back at the main menu) - retry with a larger --budget <ms of sampling '
                          f'per frame> (this run: {used})')
                break
        else:
            result = 'timed out'
    finally:
        close_game()
        open(project.ini, 'wb').write(original)
        os.remove(ini_backup(project))
    return result


LOG_HEAD = 256     # bytes of the log's start that tell a new log from the one that was there


def log_mark(project: Project) -> tuple[int, bytes]:
    """(size, first bytes) of the game log before a run, for read_log; (0, b'') without a log."""
    try:
        with open(project.log_path, 'rb') as f:
            head = f.read(LOG_HEAD)
            f.seek(0, os.SEEK_END)
            return f.tell(), head
    except FileNotFoundError:
        return 0, b''


def read_log(project: Project, mark: tuple[int, bytes] | int) -> list[str]:
    """The game log's lines written since mark (log_mark): none while the game has not made its log yet, and the whole
    log when the game began a new one (shorter than before, or starting differently)."""
    start, head = mark if isinstance(mark, tuple) else (mark, None)
    try:
        with open(project.log_path, 'rb') as f:
            now = f.read(LOG_HEAD)
            f.seek(0, os.SEEK_END)
            new_log = f.tell() < start or (head is not None and now[:len(head)] != head)
            f.seek(0 if new_log else start)
            return f.read().decode('utf-8', errors='replace').splitlines()
    except FileNotFoundError:
        return []


def close_game() -> None:
    """Ends the game (politely, then by force after 10 s)."""
    if not game_running():
        return
    subprocess.run(['taskkill', '/IM', 'BFBC2Game.exe'], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(10):
        time.sleep(1)
        if not game_running():
            return
    subprocess.run(['taskkill', '/F', '/IM', 'BFBC2Game.exe'], capture_output=True, creationflags=NO_WINDOW)
    time.sleep(2)


def build(project: Project, lv: str, log: Log = print) -> None:
    """Builds the level's pathdata.hpd (work\\navkit\\<lv>) from its samples and prepared files (navbuild)."""
    import navbuild
    import navregress
    settings = project.settings(lv)
    level_dir = project.level_work(lv)
    samples08 = project.samples08(lv)                      # <lv>_navkit.bin, or <lv>_full.bin of older campaign runs
    if not samples08 or not os.path.isfile(samples08[:-4] + '_hi.bin'):
        raise NavKitError(f'{lv}: samples missing ({os.path.basename(project.samples(lv))} and its _hi run) '
                          '- run sample first')
    out = os.path.join(project.out_dir(lv), 'pathdata.hpd')
    log(f'== build {lv} -> {shown(out, project.root)}')
    started = time.time()
    try:
        navbuild.build(samples08, os.path.join(level_dir, 'forbid_tris.npy'), settings['xoff'], settings['zoff'], out,
                       detailed=detailed_pass(project, lv, samples08, settings['xoff'], settings['zoff'], log),
                       dynamic_path=os.path.join(level_dir, 'dynamic_tris.npy'), high_path=samples08[:-4] + '_hi.bin',
                       stairs_path=os.path.join(level_dir, 'stair_tris.npy'),
                       seeds=read_json(os.path.join(level_dir, 'soldier_spawns.json')),
                       water=navregress.level_water(project.root, lv),
                       bounds=navregress.reference_bounds(project.root, lv),
                       order_points=navregress.order_points_of(project.root, lv),
                       ladders=navregress.ladders_of(project.root, lv),
                       log=lambda *parts: log('  ' + ' '.join(str(part) for part in parts)))
    except navbuild.BuildError as e:
        raise NavKitError(f'{lv}: {e}') from e          # a clear error (exit code 1), not an unexpected one
    log(f'  built in {time.time() - started:.0f} s ({os.path.getsize(out) / 1e6:.1f} MB)')


def detailed_pass(project: Project, lv: str, samples: str, xoff: int, zoff: int,
                  log: Log = print) -> dict[str, Any] | None:
    """The detailed-zone pass of a build (build and apply alike): the level's pitch-0.2 samples (sample --pitch 0.2) and
    its detailed sectors, or None without 0.2 m samples; an error when those samples do not cover the sectors chosen
    now (sampled before the 0.8 m samples or the sector offsets changed)."""
    import navregress
    chosen = detailed_sectors(project, lv) if os.path.isfile(project.settings_path(lv)) else None
    try:
        zones = navregress.detailed_of(project.root, lv, samples, xoff, zoff, chosen=chosen)
    except RuntimeError as e:
        raise NavKitError(str(e)) from e
    if zones is not None:
        log(f'  detailed zones: {len(zones["sectors"])} sectors at pitch 0.2'
            + ('' if zones['high'] else ' (no high-capsule run at 0.2 - stairs use the normal capsule there)'))
    return zones


def check(project: Project, lv: str, log: Log = print) -> None:
    """Scores the built file on the level's AI positions (ordercheck.py, against DICE's file when there is one; the
    whole report in check.txt) and, against DICE's file, navcompare's summary."""
    settings = project.settings(lv)
    ours = os.path.join(project.out_dir(lv), 'pathdata.hpd')
    if not os.path.isfile(ours):
        raise NavKitError(f'{lv}: not built yet')
    xml = os.path.join(project.level_work(lv), 'dist', 'levels', settings['level'] + '.xml')
    reference = project.dice_pathdata(lv)
    args = [sys.executable, '-B', os.path.join(HERE, 'tools', 'ordercheck.py'), xml]
    args += ([reference] if reference else []) + [ours]
    log(f'== check {lv}' + (' against the original path data' if reference else ' (self-check: no original path data)'))
    out = project.run(args, log)
    report = os.path.join(project.out_dir(lv), 'check.txt')
    write_text(report, '\n'.join(out) + '\n')
    for line in out:
        if re.search(r'positions:|on an area|route check|self-check', line) and not line.startswith('  '):
            log('  ' + line)
    if reference:
        import navcompare
        log('  summary against the original:')
        navcompare.report(navcompare.compare(xml, reference, ours), lambda message: log('    ' + message))
    log(f'  full report: {shown(report, project.root)}')


def compare(project: Project, lv: str, log: Log = print, reference: str | None = None,
            candidate: str | None = None) -> None:
    """Two path-data files of a level on its AI positions (default: the original against NavKit's build)."""
    import navcompare
    settings = project.settings(lv)
    reference = reference or project.dice_pathdata(lv)
    candidate = candidate or os.path.join(project.out_dir(lv), 'pathdata.hpd')
    if not reference or not os.path.isfile(reference):
        raise NavKitError(f'{lv}: no reference file (the level has no original path data; pass --reference)')
    if not os.path.isfile(candidate):
        raise NavKitError(f'{lv}: no candidate file {candidate} (build first, or pass --candidate)')
    xml = os.path.join(project.level_work(lv), 'dist', 'levels', settings['level'] + '.xml')
    log(f'== compare {lv}: reference {shown(reference, project.root)}, '
        f'candidate {shown(candidate, project.root)}')
    navcompare.report(navcompare.compare(xml, reference, candidate), lambda message: log('  ' + message))


def traversal_path(folder: str, lv: str) -> str:
    """The traversal annotations written next to a level's path data (navtraversal.py)."""
    return os.path.join(folder, f'{lv}.traversal.json')


def install(project: Project, lv: str, log: Log = print, src: str | None = None) -> None:
    """Copies a built file into the game (DICE's original is kept once, as <file>.opfor-orig), with the level's
    traversal annotations when it has them (next to the installed path data, for OPFOR's bot layer)."""
    ours = src or os.path.join(project.out_dir(lv), 'pathdata.hpd')
    if not os.path.isfile(ours):
        raise NavKitError(f'{lv}: not built yet')
    if game_running():
        raise NavKitError('the game is running - close it first')
    dest = project.pathdata(lv)
    if os.path.isfile(dest) and same_file(ours, dest):
        raise NavKitError(f'{lv}: {ours} is the installed file itself')
    data = read_back(ours)                       # never put a file the game cannot read into it
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest + BACKUP_SUFFIX) or os.path.isfile(dest + NONE_SUFFIX):
        pass                                     # already installed: the original (or its absence) is recorded
    elif os.path.isfile(dest):
        shutil.copyfile(dest, dest + BACKUP_SUFFIX)
        log(f'  original kept as {os.path.basename(dest)}{BACKUP_SUFFIX}')
    else:
        # no original: a marker says so, so restore removes NavKit's file and it is never taken for DICE's
        write_text(dest + NONE_SUFFIX, 'NavKit path data installed where the level had none; restore removes it\n')
        log(f'  the level had no path data ({os.path.basename(dest)}{NONE_SUFFIX} records that)')
    tmp = dest + '.tmp'                          # in place whole: an interrupted copy never leaves half a file
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    log(f'== installed {shown(ours, project.root)} as {shown(dest, project.root)}')
    traversal = traversal_path(os.path.dirname(ours), lv)
    traversal_dest = traversal_path(os.path.dirname(dest), lv)
    if os.path.isfile(traversal):
        shutil.copyfile(traversal, traversal_dest)
        log(f'  traversal annotations installed as {shown(traversal_dest, project.root)}')
    elif os.path.isfile(traversal_dest):
        os.remove(traversal_dest)                # none with this file: no stale annotations from an earlier one


def restore(project: Project, lv: str, log: Log = print) -> None:
    """Puts the level's original path data back (or removes NavKit's file where the level had none)."""
    dest = project.pathdata(lv)
    backup, none = os.path.isfile(dest + BACKUP_SUFFIX), os.path.isfile(dest + NONE_SUFFIX)
    if not backup and not none:
        log(f'{lv}: nothing to restore (no NavKit file installed)')
        return
    if game_running():
        raise NavKitError('the game is running - close it first')
    if backup:
        shutil.move(dest + BACKUP_SUFFIX, dest)
    elif os.path.isfile(dest):
        os.remove(dest)                          # the level had no path data: back to none
    if none:
        os.remove(dest + NONE_SUFFIX)
    traversal = traversal_path(os.path.dirname(dest), lv)
    if os.path.isfile(traversal):
        os.remove(traversal)
    log(f'== restored the original {shown(dest, project.root)}' if backup else
        f'== removed NavKit\'s {shown(dest, project.root)} (the level had no path data)')


def read_back(path: str) -> bytes:
    """A path-data file's bytes, when it is one that hpdtool reads back exactly; a NavKitError otherwise."""
    import hpdtool
    with open(path, 'rb') as f:
        data = f.read()
    try:
        reads_back = hpdtool.write(hpdtool.parse(data)) == data
    except (ValueError, struct.error, IndexError) as e:
        raise NavKitError(f'{path} is not a usable path-data file ({e})') from e
    if not reads_back:
        raise NavKitError(f'{path} does not read back exactly - not a usable path-data file')
    return data


def game_path_data(project: Project, lv: str, path: str) -> bool:
    """Whether path names the game's own path data: the installed file or the kept original of this level, or any
    file in the game's AI folder (Output\\win32\\ai) - an output there would replace the game's data unbacked."""
    installed = project.pathdata(lv)
    for game_file in (installed, installed + BACKUP_SUFFIX):
        if os.path.isfile(path) and os.path.isfile(game_file) and same_file(path, game_file):
            return True
    ai_folder = os.path.normcase(os.path.realpath(os.path.join(project.game, 'Output', 'win32', 'ai')))
    return os.path.normcase(os.path.realpath(path)).startswith(ai_folder + os.sep)


def same_file(first: str, second: str) -> bool:
    # whether two paths name one file (case, short names, junctions and other spellings included)
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def apply(project: Project, lv: str, navtune_path: str | None = None, out: str | None = None, log: Log = print,
          install_after: bool = False) -> str:
    """Builds the level with the edits of its navigation side file (DESIGN.md sections 5.1-5.2): walkability
    edits (regions, seeds, unseeds) go into the build, then links and route edges into its route graph
    (navlinks.py); on a DICE base only the graph edits, on a copy of DICE's file. Traversal annotations go to
    <level>.traversal.json next to it (navtraversal.py). The result is read back to check it, the whole log is
    kept as <output>.report.txt, and with install_after the result is installed. Returns the output path."""
    lines = []
    if out and game_path_data(project, lv, out):
        # checked first, for both bases: the build would replace the game's file (DICE's original unbacked)
        raise NavKitError(f'{lv}: --out {out} is the game\'s own path data; '
                          'write the result elsewhere (and install it)')
    where: dict[str, str | None] = {'out': out}

    def say(message: str) -> None:
        lines.append(message)
        log(message)
    try:
        out = _apply(project, lv, navtune_path, out, say, where)
    except Exception as e:                       # noqa: BLE001 - any failure: the report says so, never an old success
        if where['out']:
            write_text(where['out'] + '.report.txt', '\n'.join(lines + [f'error: {e}']) + '\n')
        raise
    write_text(out + '.report.txt', '\n'.join(lines) + '\n')
    if install_after:
        install(project, lv, log=log, src=out)
    return out


def _apply(project: Project, lv: str, navtune_path: str | None, out: str | None, log: Log,
           where: dict[str, str | None] | None = None) -> str:
    where = {} if where is None else where       # where['out']: the output, once known (for the report)
    from bfbc2core import navtune
    import navbuild
    import navregress
    import hpdtool
    navtune_path = navtune_path or navtune.default_path(project.root, lv)
    if not os.path.isfile(navtune_path):
        raise NavKitError(f'{lv}: no side file {navtune_path} (make edits in the Navigation Studio first)')
    try:
        tune = navtune.load(navtune_path)
    except ValueError as e:
        raise NavKitError(str(e)) from e
    import navlinks
    walk = [operation for operation in tune.operations
            if operation['op'] in ('walkable', 'unwalkable', 'seed', 'unseed')]
    graph = [operation for operation in tune.operations if operation['op'] in navlinks.GRAPH_OPS]
    traversals = [operation for operation in tune.operations if operation['op'] == 'traversal']
    started = time.time()
    if tune.kind == 'navkit':
        out = where['out'] = out or os.path.join(project.out_dir(lv), 'pathdata.hpd')
        try:
            samples, xoff, zoff, files = navregress.inputs(project.root, lv)
        except RuntimeError as e:
            raise NavKitError(str(e)) from e
        log(f'== apply {lv}: {len(tune.operations)} edit(s) from {shown(navtune_path, project.root)} '
            f'-> {shown(out, project.root)}')
        # the same inputs as build (detailed zones and ladders included): without edits the result is the build's
        try:
            navbuild.build(samples, files['forbid_tris.npy'], xoff, zoff, out, dynamic_path=files['dynamic_tris.npy'],
                           high_path=samples[:-4] + '_hi.bin', stairs_path=files['stair_tris.npy'],
                           seeds=read_json(files['soldier_spawns.json']), edits=walk,
                           water=navregress.level_water(project.root, lv),
                           bounds=navregress.reference_bounds(project.root, lv),
                           order_points=navregress.order_points_of(project.root, lv),
                           detailed=detailed_pass(project, lv, samples, xoff, zoff, log),
                           ladders=navregress.ladders_of(project.root, lv),
                           log=lambda *parts: log('  ' + ' '.join(str(part) for part in parts)))
        except navbuild.BuildError as e:
            raise NavKitError(f'{lv}: {e}') from e
        log(f'  built in {time.time() - started:.0f} s; {len(walk)} walkability edit(s) applied')
    else:
        # a DICE base: no rebuild - the graph edits go into a copy of DICE's file, for this PC only (never
        # shared: it contains DICE's data; the side file is what can be shared)
        if walk:
            raise NavKitError(f'{lv}: region edits need a NavKit base (DESIGN.md 5.1)')
        src = project.dice_pathdata(lv)
        if not src:
            raise NavKitError(f'{lv}: the side file\'s base is DICE\'s data, but the level has none')
        out = out or os.path.join(project.out_dir(lv), 'pathdata_dice_edited.hpd')
        if (os.path.isfile(out) and same_file(out, src)) or game_path_data(project, lv, out):
            raise NavKitError(f'{lv}: --out {out} is the game\'s own path data; write the edited copy elsewhere')
        where['out'] = out
        log(f'== apply {lv}: {len(tune.operations)} edit(s) on a copy of DICE\'s file '
            f'-> {shown(out, project.root)} (for this PC only)')
        if tune.base_changed(src):
            log('  note: DICE\'s file is not the one the edits were made on (another game version?); the edits are '
                'by position and are applied, but check them')
        shutil.copyfile(src, out)
    if graph:
        hpd = hpdtool.parse(open(out, 'rb').read())
        results = navlinks.apply(hpd, graph, log=log)
        with open(out + '.tmp', 'wb') as stream:
            stream.write(hpdtool.write(hpd))
        os.replace(out + '.tmp', out)
        log(f'  {sum(applied for _, applied, _ in results)} of {len(graph)} link / route-edge edit(s) applied')
    data = open(out, 'rb').read()
    hpd = hpdtool.parse(data)
    if hpdtool.write(hpd) != data:
        raise NavKitError(f'{out}: does not read back exactly - not usable')
    traversal = traversal_path(os.path.dirname(out), lv)
    if traversals:
        import navtraversal
        navtraversal.write(hpd, out, traversals, tune.level, traversal, log=log)
        log(f'  {len(traversals)} traversal annotation(s) written to {shown(traversal, project.root)} '
            f'(for OPFOR\'s bot layer; not route edges - the game\'s AI cannot climb or jump them)')
    elif os.path.isfile(traversal):
        os.remove(traversal)                          # an earlier run's annotations no longer apply
    notes = sum(operation['op'] == 'note' for operation in tune.operations)
    if notes:
        log(f'  {notes} note(s): kept in the side file only')
    log(f'  read back exactly ({time.time() - started:.0f} s in all)')
    return out


def status(project: Project, lv: str, log: Log = print) -> None:
    """What has been done for a level: its original path data, settings, samples, build and installation."""
    log(f'{lv}:')
    log(f'  original path data : {project.dice_pathdata(lv) or "none"}')
    settings_file = os.path.join(project.work, 'navkit', lv, 'navkit.json')
    log(f'  prepared           : {"yes" if os.path.isfile(settings_file) else "no"}')
    if os.path.isfile(settings_file):
        settings = project.settings(lv)
        log(f'    box {fmt(settings["box"])}, y {fmt(settings["y"])}, offsets {settings["xoff"]},{settings["zoff"]}')
    for _, _, suffix in AGENTS:
        path = project.samples(lv, suffix)
        log(f'  samples{suffix or "":12s}: {time.ctime(os.path.getmtime(path)) if os.path.isfile(path) else "no"}')
    path = os.path.join(project.work, 'navkit', lv, 'pathdata.hpd')
    log(f'  built              : {time.ctime(os.path.getmtime(path)) if os.path.isfile(path) else "no"}')
    log(f'  installed in game  : {"yes" if project.installed(lv) else "no"}')


def numbers(opts: dict[str, str], key: str, count: int, what: str) -> list[float] | None:
    """An option's comma-separated numbers (None when not given); ValueError unless there are exactly count of them
    (and, for --box, --y, --pitch and --budget, unless they make sense)."""
    if key not in opts:
        return None
    try:
        values = [float(text) for text in opts[key].split(',')]
    except ValueError:
        values = []
    if len(values) != count or not all(math.isfinite(value) for value in values):
        raise ValueError(f'{key} {opts[key]!r}: give {what} ({count} number{"s" if count > 1 else ""})')
    if key == '--box' and not (values[0] < values[2] and values[1] < values[3]):
        raise ValueError(f'--box {opts[key]!r}: min X and min Z must be below max X and max Z')
    if key == '--y' and not values[0] < values[1]:
        raise ValueError(f'--y {opts[key]!r}: the bottom must be below the top')
    if key == '--pitch' and not any(abs(values[0] - pitch) < 1e-6 for pitch in PITCHES):
        raise ValueError(f'--pitch {opts[key]!r}: give 0.8 (the normal runs) or 0.2 (the detailed zones)')
    if key == '--budget' and not (values[0] == int(values[0]) and 1 <= values[0] <= 10000):
        raise ValueError(f'--budget {opts[key]!r}: give whole milliseconds of sampling per frame (1 - 10000)')
    return values


def ask(question: str) -> bool:
    return input(question + ' [y/N] ').strip().lower() in ('y', 'yes')


VALUE_OPTIONS = ('--box', '--y', '--opfor', '--root', '--reference', '--candidate', '--navtune', '--out', '--from',
                 '--pitch', '--budget')   # --root: older name of --opfor


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:]]
    opts = {}
    for key in VALUE_OPTIONS:
        if key in args:
            index = args.index(key)
            if index + 1 >= len(args) or args[index + 1].startswith('--'):
                print(f'error: {key} needs a value')
                return 2
            opts[key] = args[index + 1]
            del args[index:index + 2]
    yes = '--yes' in args
    install_flag = '--install' in args
    args = [arg for arg in args if arg not in ('--yes', '--install')]
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    try:
        box = numbers(opts, '--box', 4, 'minX,minZ,maxX,maxZ')
        y = numbers(opts, '--y', 2, 'bottom,top')
        pitch = numbers(opts, '--pitch', 1, '0.8 or 0.2')
        budget_values = numbers(opts, '--budget', 1, 'milliseconds of sampling per frame')
        budget = int(budget_values[0]) if budget_values else None
    except ValueError as e:
        print('error:', e)
        return 2
    confirm = None if yes else ask
    try:
        project = Project(opts.get('--opfor') or opts.get('--root'))
        cmd = args[0]
        if cmd == 'levels':
            levels(project)
            return 0
        if len(args) < 2:
            raise NavKitError(f'{cmd}: which level? (e.g. navkit_le.py {cmd} SP_01)')
        lv = project.level(args[1])
        if cmd == 'prepare':
            prepare(project, lv, box=box, y=y)
        elif cmd == 'sample':
            if pitch:
                done = sample(project, lv, confirm=confirm, box=box, y=y, pitch=pitch[0], budget_ms=budget)
            else:
                done = sample_level(project, lv, confirm=confirm, box=box, y=y, budget_ms=budget)
            if not done:
                raise NavKitError('cancelled - nothing was sampled')
        elif cmd == 'build':
            build(project, lv)
        elif cmd == 'compare':
            compare(project, lv, reference=opts.get('--reference'), candidate=opts.get('--candidate'))
        elif cmd == 'check':
            check(project, lv)
        elif cmd == 'apply':
            apply(project, lv, opts.get('--navtune'), opts.get('--out'), install_after=install_flag)
        elif cmd == 'install':
            install(project, lv, src=opts.get('--from'))
        elif cmd == 'restore':
            restore(project, lv)
        elif cmd == 'status':
            status(project, lv)
        elif cmd == 'all':
            prepare(project, lv, box=box, y=y)
            if not sample_level(project, lv, confirm=confirm, budget_ms=budget):
                raise NavKitError('cancelled - prepared, but not sampled, built or checked')
            build(project, lv)
            check(project, lv)
        else:
            raise NavKitError(f'unknown command {cmd!r}')
    except NavKitError as e:
        print('error:', e)
        import logging
        logging.getLogger('bfbc2').error(f'{" ".join(argv[1:])}: {e}')
        return 1
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navkit_le', gui=False)          # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
