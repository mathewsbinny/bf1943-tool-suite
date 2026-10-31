"""NavKit (BF1943): one command per step, from a level of your BF1943 copy to a checked navigation build.

  python navkit.py levels                     the copy's levels, and what has been done for each
  python navkit.py prepare wake_island_s      objects, collision, flags, ladders, terrain and water from the
                                              bundles (levelobjects1943.py); sea level and sector offsets
  python navkit.py sample  wake_island_s      the collision samples, computed offline (navsample1943.py): two
                                              runs at 0.8 m (BF1943's soldier, and a 0.6 m step for stairs),
                                              the detailed sectors (detailed1943.py), then two runs at 0.2 m
                                              over them; --pitch 0.8 or 0.2 runs that pass only
  python navkit.py build   wake_island_s      the navigation build with BF1943's soldier (soldier1943.py):
                                              0.8 m everywhere, the detailed sectors at 0.2 m, water, spawn
                                              seeds, ladder links, jump links to ships' AA gun platforms
                                              (jumplinks1943.py), seam gap nodes (navbuild.SEAM_GAP_NODES)
  python navkit.py check   wake_island_s      structure (hpdcheck.py) and routes (routecheck1943.py): every
                                              spawn on the network, a route between every pair of flags
  python navkit.py all     wake_island_s      prepare, sample, build, check
  python navkit.py status  wake_island_s      what has been done for a level

Options: --game <dist\\xenon folder> (default: BF1943_GAME, else bf1943-xenia-kit's output); --pitch 0.8 | 0.2 (sample
only; --pitch=0.2 works too). -h, --help or help: this text.

Each step records what it was made from, and the next refuses another generation's work (2026-10-30: a build used
the samples of an earlier preparation, from another game copy). prepare stores the game folder, the SHA-256 of
every objects file and their digest ('preparation'), and drops every later stamp; each sampling pass stores that
digest, the game and an id of its own ('samples': '0.8', '0.2' with its sectors; 'id': the pass's identity, which
the minute in 'time' is not) and drops the stamps after it (a new 0.8 m pass also
the 0.2 m one, whose sectors it chooses); build needs the 0.8 m pass of the current preparation (and uses the 0.2 m
samples only when theirs too) and records both (their ids), the SHA-256 of the .hpd it wrote ('build_from',
'hpd_sha256': the build's identity; 'built' keeps only the minute) and of its jump_links.json ('jump_links_sha256':
the export and the audit refuse the build when that file changed); check needs that build, that file. A step run
with another --game than the preparation's (another folder, not another spelling of it) is refused. After them: navexport1943.py exports
only a build of the current preparation that was checked, and stamps the export at <level>.rsnv ('exported', with the
build's hpd_sha256); the vehicle steps stamp theirs under 'vehicles' (vehicleclasses1943.py: collision, samples per
class, worlds per class). A new preparation drops every stamp; a new sampling or build drops the export's and the
vehicle worlds'. Every step writes navkit.json by a fresh read-modify-write of its own keys (navkitjson1943.update,
under navkit.json.lock, replacing the file whole), so the stamps another step wrote while it ran stay (those it
invalidates go again when it finishes); a step whose inputs changed while it ran (prepared again, a sampling pass it
read made again) is not stamped. prepare empties objects\\ first, so no file of an earlier preparation is read as this
one's.

The game copy is only read and no game is started: everything is computed from the level's data. Files, under
the suite's work folder (BF1943_WORK overrides): work\\levels\\<level>\\objects\\ (levelobjects1943.py's files,
heightfield.res, water_mesh.res), samples\\ (samples*.bin and their logs), navkit\\ (navkit.json settings,
<level>.hpd, <level>.cells.npz, build.log, check.txt / check.json, jump_links.json). A Wake Island run takes about 45 minutes on 8
cores, most of it the 0.2 m sampling. The build is the input of our own navigation runtime; BF1943 has no Kynapse
runtime, so there is nothing to install in the game.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # no console flashes when run from the window
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, 'tools')
sys.path[:0] = [TOOLS, os.path.join(HERE, '..', 'core')]
from bfbc2core import game1943  # noqa: E402
import navkitjson1943 as nj  # noqa: E402

PROGRESS_EVERY = 15.0          # s between progress lines of a running sampler
PITCHES = (0.8, 0.2)           # the sampling passes (--pitch); detailed1943 and the build know no other
STAGES = ('prepared', 'sampled', 'built', 'checked')


NavKitError = nj.NavKitError      # a step refused, the reason in words (navkit.json's own errors are ones too)


paths = nj.paths                  # navkit.json and its helpers are shared with the tools (navkitjson1943)
save_settings = nj.write
sha256_file = nj.sha256


def settings(lv):
    try:
        return nj.read(lv)
    except nj.NotPrepared:
        raise NavKitError(f'{lv}: not prepared yet (navkit.py prepare {lv})') from None


def stamp(lv, prep, what, change):
    """A step's stamps, written with a fresh read-modify-write of navkit.json (navkitjson1943.update): only the keys
    change(st) touches are replaced, so what the vehicle steps stamped while this step ran stays (2026-10-30: the long
    steps wrote back the copy read at their start). Refused, and nothing written, when the level was prepared again
    meanwhile."""
    def guarded(st):
        if st.get('preparation') != prep:
            raise NavKitError(f'{lv}: prepared again while {what} ran: its result is not stamped (navkit.py {what} {lv})')
        change(st)
    try:
        return nj.update(lv, guarded)
    except nj.NotPrepared:                  # navkit.py prepare is running (it removes navkit.json first)
        raise NavKitError(f'{lv}: navkit.json went while {what} ran (prepared again?): its result is not stamped '
                          f'(navkit.py {what} {lv})') from None


def preparation_id(s):
    """The digest of what a preparation was made from and made: level, game folder, sea level, sector offsets and
    every objects file's hash. The later steps' stamps carry it."""
    keys = ('level', 'game', 'sea_level', 'sector_offsets', 'objects_sha256')
    return hashlib.sha256(json.dumps({k: s.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def same_game(s, game, lv):
    """Refuse a step with another game copy than the preparation's (the same folder by identity: navkitjson1943)."""
    if not nj.same_file(s.get('game'), game):
        raise NavKitError(f'{lv}: prepared from {s.get("game")}, not {game}: pass --game {s.get("game")}, or prepare '
                          f'again (navkit.py prepare {lv})')


def preparation(s, lv):
    """The current preparation's digest; a level prepared before the stamps were kept has none."""
    if not s.get('preparation'):
        raise NavKitError(f'{lv}: prepared without a preparation stamp (an older NavKit): prepare again (navkit.py prepare {lv})')
    return s['preparation']


def invalidate(s, *stages):
    for k in stages:
        s.pop(k, None)
    if 'built' in stages:
        # the export of the build, and the vehicle worlds built on it, go with it (vehicleclasses1943's stamps)
        s.pop('build_from', None)
        s.pop('exported', None)
        (s.get('vehicles') or {}).pop('worlds', None)


def level_of(game, name):
    lvs = game1943.levels(game)
    lv = name.lower()
    if lv not in lvs:
        raise NavKitError(f'{name}: not a level of {game} (levels: {", ".join(lvs)})')
    return lv


# --- steps -----------------------------------------------------------------------------------------------------

def prepare(game, lv, log=print):
    import levelobjects1943
    from bfbc2core import terrain, water
    p = paths(lv)
    for k in ('objects', 'samples', 'navkit'):
        os.makedirs(p[k], exist_ok=True)
    nj.remove(lv)                                      # the objects change from here: a failed run leaves no stamps
    # every objects file goes: levelobjects1943 writes only what this copy has, and a file of an earlier preparation
    # left there was hashed into this one and read by the build (2026-10-30 review: copy A's water_mesh.res stayed
    # after preparing copy B, which has none: sea_level None, and the build left out "deep water" ground by A's mesh)
    for n in os.listdir(p['objects']):
        if os.path.isfile(os.path.join(p['objects'], n)):
            os.remove(os.path.join(p['objects'], n))
    t0 = time.time()
    log(f'{lv}: objects, collision, flags and ladders from the bundles')
    if levelobjects1943.main(['levelobjects1943.py', game, lv, p['objects']]):
        raise NavKitError(f'{lv}: levelobjects1943 failed')
    g = game1943.Game(game, lv)
    hf_data = g.data(f'terrains/{lv}/{lv}.heightfield-000.res')
    if not hf_data:
        raise NavKitError(f'{lv}: no height field (terrains/{lv}/{lv}.heightfield-000.res) in its bundles')
    with open(os.path.join(p['objects'], 'heightfield.res'), 'wb') as f:
        f.write(hf_data)
    hf = terrain.read(hf_data)
    sea = None
    mesh = g.data(f'terrains/{lv}/{lv}.water.mesh.res')
    if mesh:
        wpath = os.path.join(p['objects'], 'water_mesh.res')
        with open(wpath, 'wb') as f:
            f.write(mesh)
        v, t = water.read_mesh(wpath)
        if len(t):
            sea = round(float(v[:, 1].mean()), 2)          # the sea is flat on every BF1943 level (test_bf1943_data)
    offsets = [int(math.floor(hf.min[0] / 100.0)), int(math.floor(hf.min[2] / 100.0))]
    files = {n: sha256_file(os.path.join(p['objects'], n)) for n in sorted(os.listdir(p['objects']))
             if os.path.isfile(os.path.join(p['objects'], n))}
    # a new settings file: the samples, build and check of an earlier preparation lose their stamps (their files
    # stay, and the steps refuse them: their stamps carry another preparation)
    s = dict(level=lv, game=game, prepared=time.strftime('%Y-%m-%d %H:%M'), sea_level=sea, sector_offsets=offsets,
             objects_sha256=files)
    s['preparation'] = preparation_id(s)
    save_settings(lv, s)
    log(f'{lv}: prepared in {time.time() - t0:.0f} s: sea level {sea} m, sector offsets {offsets[0]} / {offsets[1]}')
    return s


def run_parallel(jobs, log=print):
    """Run [(label, argv, log path)] at once; a progress line every PROGRESS_EVERY s. If one fails, or on Ctrl+C,
    the others are ended too (with their child processes)."""
    procs = []
    try:
        for label, argv, path in jobs:
            fh = open(path, 'w', encoding='utf-8', newline='\n')
            try:
                pr = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, creationflags=NO_WINDOW, cwd=TOOLS)
            except BaseException:
                fh.close()
                raise
            procs.append((label, pr, path, fh))
        last = 0.0
        while any(pr.poll() is None for _, pr, _, _ in procs):
            time.sleep(1.0)
            bad = [(label, pr) for label, pr, _, _ in procs if pr.poll() not in (None, 0)]
            if bad:
                raise NavKitError(f'{bad[0][0]} failed (exit code {bad[0][1].returncode}); see its log')
            if time.time() - last >= PROGRESS_EVERY:
                last = time.time()
                log('  ' + '; '.join(f'{label}: {tail(path)}' for label, pr, path, _ in procs))
        for label, pr, path, _ in procs:
            if pr.returncode:
                raise NavKitError(f'{label} failed (exit code {pr.returncode}); see {path}')
            log(f'  {label}: {tail(path)}')
    finally:
        for _, pr, _, fh in procs:
            if pr.poll() is None:
                end(pr)
            fh.close()


def end(proc):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def tail(path):
    try:
        lines = [l.strip() for l in open(path, encoding='utf-8', errors='replace') if l.strip()]
    except OSError:
        return ''
    return lines[-1][:110] if lines else ''


def sample(game, lv, pitch=None, log=print):
    import detailed1943
    import soldier1943
    if pitch is not None and pitch not in PITCHES:      # 2026-10-30: --pitch 0.4 ran nothing and stamped 'sampled'
        raise NavKitError(f'--pitch {pitch}: not a sampling pass (0.8 or 0.2; without --pitch, both)')
    s = settings(lv)
    same_game(s, game, lv)
    prep = preparation(s, lv)
    p = paths(lv)
    o, sm = p['objects'], p['samples']
    base = [sys.executable, '-B', '-u', 'navsample1943.py', o, os.path.join(o, 'heightfield.res')]
    sea = soldier1943.sea_level_args(s)
    xoff, zoff = s['sector_offsets']
    t0 = time.time()
    if pitch in (None, 0.8):
        def start(st):
            # stamps dropped before the run: an interrupted run leaves none (the 0.2 m pass's sectors come from this one)
            invalidate(st, 'sampled', 'built', 'checked', 'detailed_sectors', 'detailed_counts')
            for k in ('0.8', '0.2'):
                (st.get('samples') or {}).pop(k, None)
        stamp(lv, prep, 'sample', start)
        log(f'{lv}: sampling at 0.8 m (BF1943\'s soldier, and a 0.6 m step)')
        run_parallel([('0.8 m', base + [os.path.join(sm, 'samples.bin')] + sea, os.path.join(sm, 'sample.log')),
                      ('0.8 m, 0.6 m step', base + [os.path.join(sm, 'samples_high.bin'), '--step', '0.6'] + sea,
                       os.path.join(sm, 'sample_high.log'))], log)
        chosen, counts = detailed1943.choose(o, os.path.join(sm, 'samples.bin'), xoff, zoff)

        def done(st):
            # a build or check stamped meanwhile read the samples as they were rewritten: theirs go again
            invalidate(st, 'sampled', 'built', 'checked')
            st['detailed_sectors'] = [f'{x:02x}{z:02x}' for x, z in chosen]
            st['detailed_counts'] = counts
            passes = st.setdefault('samples', {})
            passes.pop('0.2', None)
            passes['0.8'] = dict(preparation=prep, game=st['game'], id=uuid.uuid4().hex,
                                 time=time.strftime('%Y-%m-%d %H:%M'))
            st['sampled'] = passes['0.8']['time']
        s = stamp(lv, prep, 'sample', done)
        log(f'{lv}: {len(chosen)} detailed sectors ({counts})')
    if pitch in (None, 0.2):
        sectors = s.get('detailed_sectors')
        s8 = (s.get('samples') or {}).get('0.8')
        if sectors is None or (s8 or {}).get('preparation') != prep:
            raise NavKitError(f'{lv}: the 0.8 m pass first (navkit.py sample {lv} --pitch 0.8)')

        def start(st):
            invalidate(st, 'built', 'checked')
            (st.get('samples') or {}).pop('0.2', None)
        stamp(lv, prep, 'sample', start)
        if sectors:
            arg = f'--sectors={xoff},{zoff}:' + ','.join(sectors)
            log(f'{lv}: sampling the {len(sectors)} detailed sectors at 0.2 m')
            run_parallel([('0.2 m', base + [os.path.join(sm, 'samples_02.bin'), '--pitch', '0.2', arg] + sea,
                           os.path.join(sm, 'sample_02.log')),
                          ('0.2 m, 0.6 m step', base + [os.path.join(sm, 'samples_02_high.bin'), '--pitch', '0.2', '--step',
                                                         '0.6', arg] + sea, os.path.join(sm, 'sample_02_high.log'))], log)
        else:
            log(f'{lv}: no detailed sectors')

        def done(st):
            if (st.get('samples') or {}).get('0.8') != s8 or st.get('detailed_sectors') != sectors:
                raise NavKitError(f'{lv}: sampled at 0.8 m again while the 0.2 m pass ran: its samples are not stamped '
                                  f'(navkit.py sample {lv} --pitch 0.2)')
            invalidate(st, 'built', 'checked')
            passes = st.setdefault('samples', {})
            passes['0.2'] = dict(preparation=prep, game=st['game'], sectors=list(sectors), id=uuid.uuid4().hex,
                                 time=time.strftime('%Y-%m-%d %H:%M'))
            st['sampled'] = passes['0.2']['time']
        stamp(lv, prep, 'sample', done)
    log(f'{lv}: sampled in {(time.time() - t0) / 60:.0f} min')


def build(game, lv, log=print):
    import navbuild
    import soldier1943
    from bfbc2core import water
    s = settings(lv)
    same_game(s, game, lv)
    prep = preparation(s, lv)
    p = paths(lv)
    o, sm = p['objects'], p['samples']
    passes = s.get('samples') or {}
    s8, s2 = passes.get('0.8'), passes.get('0.2')
    if s8 is None or not os.path.isfile(os.path.join(sm, 'samples.bin')):
        raise NavKitError(f'{lv}: not sampled yet (navkit.py sample {lv})')
    if s8.get('preparation') != prep or s8.get('game') != s['game']:
        raise NavKitError(f'{lv}: the samples are from another preparation: sample again (navkit.py sample {lv})')
    soldier1943.configure()
    xoff, zoff = s['sector_offsets']
    sectors = {(int(k[:2], 16), int(k[2:], 16)) for k in s.get('detailed_sectors') or ()}
    sectors_key = s.get('detailed_sectors')
    detailed = None
    current02 = s2 is not None and s2.get('preparation') == prep and s2.get('sectors') == s.get('detailed_sectors')
    if sectors and current02 and os.path.isfile(os.path.join(sm, 'samples_02.bin')):
        detailed = {'samples': os.path.join(sm, 'samples_02.bin'), 'high': os.path.join(sm, 'samples_02_high.bin'),
                    'sectors': sectors}
    elif sectors:
        log(f'{lv}: no 0.2 m samples of this preparation: the detailed sectors are built at 0.8 m (navkit.py sample '
            f'{lv} --pitch 0.2)')
    stamp(lv, prep, 'build', lambda st: invalidate(st, 'built', 'checked'))
    wpath = os.path.join(o, 'water_mesh.res')
    # the water of this preparation only: none when it found no water (sea_level None)
    mesh = water.read_mesh(wpath) if soldier1943.has_water(s) and os.path.isfile(wpath) else None
    lines = []

    def blog(m):
        lines.append(m)
        log(m)
    t0 = time.time()
    # jump links: the AA gun platforms of ships, under their decks (jumplinks1943); built as ladders of kind 'jump'
    import jumplinks1943
    # on the samples this build uses: the 0.2 m ones only when it builds the detailed sectors from them (2026-10-30
    # review: level_links read samples_02.bin whenever it was on disk, a pass the build had refused as not current)
    jumps = jumplinks1943.level_links(lv, blog, game=game, detailed=detailed is not None)
    jp = os.path.join(p['navkit'], 'jump_links.json')
    with open(jp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(jumps, f, indent=1)
    # the export and the audit read jump_links.json beside the build: it is stamped with it ('jump_links_sha256';
    # 2026-10-30 second review: jumplinks1943.py run alone, with another --game, replaced the build's links unseen)
    jump_sha256 = nj.sha256(jp)
    with open(os.path.join(o, 'ladders.json'), encoding='utf-8') as f:
        ladders = json.load(f) + jumps
    with open(os.path.join(o, 'soldier_spawns.json'), encoding='utf-8') as f:
        seeds = json.load(f)
    navbuild.build(os.path.join(sm, 'samples.bin'), os.path.join(o, 'forbid_tris.npy'), xoff, zoff, p['hpd'],
                   step=soldier1943.STEP, dynamic_path=os.path.join(o, 'dynamic_tris.npy'),
                   exclude_path=os.path.join(o, 'exclude_tris.npy'), exclude_foot_path=os.path.join(o, 'exclude_footprint_tris.npy'),
                   seeds=seeds, water=mesh,
                   max_water_depth=soldier1943.WATER_DEPTH, high_path=os.path.join(sm, 'samples_high.bin'),
                   detailed=detailed, ladders=ladders, log=blog)
    with open(os.path.join(p['navkit'], 'build.log'), 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(lines) + '\n')
    # the build is told apart by its .hpd's SHA-256 ('hpd_sha256'): 'built' keeps only the minute, so a quick
    # rebuild looked like the build before it to the export and the vehicle worlds (2026-10-30)
    hpd_sha256 = nj.sha256(p['hpd'])

    def done(st):
        # the passes it read are told apart by their ids (the minute in 'time' repeats); a 0.2 m pass made again, or
        # started, meanwhile rewrote files it read too (2026-10-30 review: only the 0.8 m pass was compared)
        now = st.get('samples') or {}
        if now.get('0.8') != s8 or (detailed is not None and (now.get('0.2') != s2
                                                               or st.get('detailed_sectors') != sectors_key)):
            raise NavKitError(f'{lv}: sampled again while the build ran: it is not stamped (navkit.py build {lv})')
        invalidate(st, 'built', 'checked')          # a check, export or vehicle world stamped meanwhile is of the old build
        st['built'] = time.strftime('%Y-%m-%d %H:%M')
        st['build_from'] = dict(preparation=prep, game=st['game'], samples_08=s8['time'], samples_08_id=s8.get('id'),
                                samples_02=s2['time'] if detailed else None,
                                samples_02_id=s2.get('id') if detailed else None, hpd_sha256=hpd_sha256,
                                jump_links_sha256=jump_sha256)
    stamp(lv, prep, 'build', done)
    log(f'{lv}: built {p["hpd"]} in {(time.time() - t0) / 60:.0f} min')


def check(game, lv, log=print):
    import hpdcheck
    import routecheck1943
    p = paths(lv)
    s = settings(lv)
    same_game(s, game, lv)
    prep = preparation(s, lv)
    if not os.path.isfile(p['hpd']) or 'built' not in s:
        raise NavKitError(f'{lv}: not built yet (navkit.py build {lv})')
    built = s.get('build_from') or {}
    if built.get('preparation') != prep:
        raise NavKitError(f'{lv}: the build is from another preparation: build again (navkit.py build {lv})')
    if not built.get('hpd_sha256') or nj.sha256(p['hpd']) != built['hpd_sha256']:
        raise NavKitError(f'{lv}: {os.path.basename(p["hpd"])} is not the build stamped in navkit.json (built by an older '
                          f'NavKit, or replaced): build again (navkit.py build {lv})')
    stamp(lv, prep, 'check', lambda st: invalidate(st, 'checked'))
    structure = hpdcheck.check(p['hpd'])
    bad = {k: v for k, v in structure.items() if v}
    r = routecheck1943.check(p['hpd'], lv, game)
    text = ['structure: ' + ('every check passes' if not bad else '; '.join(f'{k}: {len(v)} (e.g. {v[0]})' for k, v in bad.items())),
            routecheck1943.report(r)]
    with open(os.path.join(p['navkit'], 'check.txt'), 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(text) + '\n')
    with open(os.path.join(p['navkit'], 'check.json'), 'w', encoding='utf-8', newline='') as f:
        json.dump(dict(structure=structure, routes=r), f, indent=1)
    for line in text:
        log(line)
    ok = not bad and r['ok']

    def done(st):
        if 'built' not in st or (st.get('build_from') or {}).get('hpd_sha256') != built['hpd_sha256']:
            raise NavKitError(f'{lv}: built again while the check ran: it is not stamped (navkit.py check {lv})')
        st['checked'] = time.strftime('%Y-%m-%d %H:%M') + (' ok' if ok else ' FAILED')
    stamp(lv, prep, 'check', done)
    return ok


def stamps_of(lv):
    """navkit.json for status / levels: {} when the level is not prepared, {'unreadable': why} when it is damaged."""
    try:
        return nj.read(lv)
    except nj.NotPrepared:
        return {}
    except nj.Unreadable as e:
        return {'unreadable': str(e)}


def status(game, lv, log=print):
    p = paths(lv)
    s = stamps_of(lv)
    if 'unreadable' in s:
        log(f'  {s["unreadable"]}')
    for step in STAGES:
        log(f'  {step:9s} {s.get(step, "-")}')
    if s.get('detailed_sectors') is not None:
        log(f'  detailed sectors: {len(s["detailed_sectors"])}')
    log(f'  files: {p["work"]}')
    return s


def levels(game, log=print):
    for lv in game1943.levels(game):
        st = stamps_of(lv)
        done = [k for k in STAGES if k in st] or (['navkit.json damaged'] if 'unreadable' in st else [])
        log(f'  {lv:22s} {", ".join(done) if done else "-"}' + (f' ({st["checked"]})' if 'checked' in st else ''))


COMMANDS = ('levels', 'prepare', 'sample', 'build', 'check', 'all', 'status')


class _Parser(argparse.ArgumentParser):
    """argparse that raises NavKitError (the message printed, exit code 1, logged) instead of exiting."""

    def error(self, message):
        raise NavKitError(f'{message} (navkit.py help)')


def parse(argv):
    """(command, level or None, game or None, pitch or None) of navkit.py's arguments; None for help. 2026-10-30
    review: --pitch=0.2 was ignored (sample ran both passes and dropped the build), a trailing --pitch raised
    IndexError, and unknown arguments were ignored."""
    args = list(argv[1:])
    if not args or any(a in ('-h', '--help') for a in args) or args[0] == 'help':
        return None
    ap = _Parser(prog='navkit.py', add_help=False)
    ap.add_argument('command', choices=COMMANDS)
    ap.add_argument('level', nargs='?')
    ap.add_argument('--game')
    ap.add_argument('--pitch')
    a = ap.parse_args(args)
    if a.command != 'levels' and not a.level:
        raise NavKitError(f'{a.command}: which level? (e.g. navkit.py {a.command} wake_island_s)')
    if a.command == 'levels' and a.level:
        raise NavKitError(f'levels takes no level ({a.level!r})')
    pitch = None
    if a.pitch is not None:
        if a.command != 'sample':
            raise NavKitError(f'--pitch is an option of sample, not of {a.command}')
        try:
            pitch = float(a.pitch)
        except ValueError:
            raise NavKitError(f'--pitch {a.pitch}: not a sampling pass (0.8 or 0.2)') from None
        if pitch not in PITCHES:
            raise NavKitError(f'--pitch {a.pitch}: not a sampling pass (0.8 or 0.2; without --pitch, both)')
    return a.command, a.level, a.game, pitch


def main(argv):
    try:
        parsed = parse(argv)
        if parsed is None:
            print(__doc__)
            return 0
        cmd, name, given, pitch = parsed
        try:
            game = game1943.find_game(given)
        except FileNotFoundError as e:
            raise NavKitError(str(e)) from None
        if cmd == 'levels':
            levels(game)
            return 0
        lv = level_of(game, name)
        if cmd == 'prepare':
            prepare(game, lv)
        elif cmd == 'sample':
            sample(game, lv, pitch=pitch)
        elif cmd == 'build':
            build(game, lv)
        elif cmd == 'check':
            return 0 if check(game, lv) else 1
        elif cmd == 'status':
            status(game, lv)
        elif cmd == 'all':
            prepare(game, lv)
            sample(game, lv)
            build(game, lv)
            return 0 if check(game, lv) else 1
    except NavKitError as e:
        print('error:', e)
        import logging
        logging.getLogger('bfbc2').error(f'{" ".join(argv[1:])}: {e}')
        return 1
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navkit', gui=False)          # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
