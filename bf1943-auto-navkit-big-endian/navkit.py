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

Options: --game <dist\\xenon folder> (default: BF1943_GAME, else bf1943-xenia-kit's output).

Each step records what it was made from, and the next refuses another generation's work (2026-10-30: a build used
the samples of an earlier preparation, from another game copy). prepare stores the game folder, the SHA-256 of
every objects file and their digest ('preparation'), and drops every later stamp; each sampling pass stores that
digest and the game ('samples': '0.8', '0.2' with its sectors) and drops the stamps after it (a new 0.8 m pass also
the 0.2 m one, whose sectors it chooses); build needs the 0.8 m pass of the current preparation (and uses the 0.2 m
samples only when theirs too) and records both ('build_from'); check needs that build. A step run with another
--game than the preparation's is refused.

The game copy is only read and no game is started: everything is computed from the level's data. Files, under
the suite's work folder (BF1943_WORK overrides): work\\levels\\<level>\\objects\\ (levelobjects1943.py's files,
heightfield.res, water_mesh.res), samples\\ (samples*.bin and their logs), navkit\\ (navkit.json settings,
<level>.hpd, <level>.cells.npz, build.log, check.txt / check.json, jump_links.json). A Wake Island run takes about 45 minutes on 8
cores, most of it the 0.2 m sampling. The build is the input of our own navigation runtime; BF1943 has no Kynapse
runtime, so there is nothing to install in the game.
"""
import hashlib
import json
import math
import os
import subprocess
import sys
import time

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # no console flashes when run from the window
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, 'tools')
sys.path[:0] = [TOOLS, os.path.join(HERE, '..', 'core')]
from bfbc2core import game1943  # noqa: E402

PROGRESS_EVERY = 15.0          # s between progress lines of a running sampler
PITCHES = (0.8, 0.2)           # the sampling passes (--pitch); detailed1943 and the build know no other
STAGES = ('prepared', 'sampled', 'built', 'checked')


class NavKitError(Exception):
    pass


def paths(lv):
    w = game1943.level_work(lv)
    return dict(work=w, objects=os.path.join(w, 'objects'), samples=os.path.join(w, 'samples'),
                navkit=os.path.join(w, 'navkit'), settings=os.path.join(w, 'navkit', 'navkit.json'),
                hpd=os.path.join(w, 'navkit', f'{lv}.hpd'))


def settings(lv):
    p = paths(lv)['settings']
    if not os.path.isfile(p):
        raise NavKitError(f'{lv}: not prepared yet (navkit.py prepare {lv})')
    return json.load(open(p, encoding='utf-8'))


def save_settings(lv, s):
    with open(paths(lv)['settings'], 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def preparation_id(s):
    """The digest of what a preparation was made from and made: level, game folder, sea level, sector offsets and
    every objects file's hash. The later steps' stamps carry it."""
    keys = ('level', 'game', 'sea_level', 'sector_offsets', 'objects_sha256')
    return hashlib.sha256(json.dumps({k: s.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def same_game(s, game, lv):
    """Refuse a step with another game copy than the preparation's."""
    if os.path.normcase(os.path.abspath(s.get('game') or '')) != os.path.normcase(os.path.abspath(game)):
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
        s.pop('build_from', None)


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
    if os.path.isfile(p['settings']):                  # the objects change from here: a failed run leaves no stamps
        os.remove(p['settings'])
    t0 = time.time()
    log(f'{lv}: objects, collision, flags and ladders from the bundles')
    if levelobjects1943.main(['levelobjects1943.py', game, lv, p['objects']]):
        raise NavKitError(f'{lv}: levelobjects1943 failed')
    g = game1943.Game(game, lv)
    hf_data = g.data(f'terrains/{lv}/{lv}.heightfield-000.res')
    if not hf_data:
        raise NavKitError(f'{lv}: no height field (terrains/{lv}/{lv}.heightfield-000.res) in its bundles')
    open(os.path.join(p['objects'], 'heightfield.res'), 'wb').write(hf_data)
    hf = terrain.read(hf_data)
    sea = None
    mesh = g.data(f'terrains/{lv}/{lv}.water.mesh.res')
    if mesh:
        wpath = os.path.join(p['objects'], 'water_mesh.res')
        open(wpath, 'wb').write(mesh)
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
            fh = open(path, 'w', encoding='utf-8')
            procs.append((label, subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                                                  cwd=TOOLS), path, fh))
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
    if pitch is not None and pitch not in PITCHES:      # 2026-10-30: --pitch 0.4 ran nothing and stamped 'sampled'
        raise NavKitError(f'--pitch {pitch}: not a sampling pass (0.8 or 0.2; without --pitch, both)')
    s = settings(lv)
    same_game(s, game, lv)
    prep = preparation(s, lv)
    p = paths(lv)
    o, sm = p['objects'], p['samples']
    base = [sys.executable, '-B', '-u', 'navsample1943.py', o, os.path.join(o, 'heightfield.res')]
    sea = ['--sea-level', str(s['sea_level'])] if s.get('sea_level') is not None else []
    xoff, zoff = s['sector_offsets']
    t0 = time.time()
    passes = s.setdefault('samples', {})
    if pitch in (None, 0.8):
        # stamps dropped before the run: an interrupted run leaves none (the 0.2 m pass's sectors come from this one)
        invalidate(s, 'sampled', 'built', 'checked', 'detailed_sectors', 'detailed_counts')
        passes.pop('0.8', None)
        passes.pop('0.2', None)
        save_settings(lv, s)
        log(f'{lv}: sampling at 0.8 m (BF1943\'s soldier, and a 0.6 m step)')
        run_parallel([('0.8 m', base + [os.path.join(sm, 'samples.bin')] + sea, os.path.join(sm, 'sample.log')),
                      ('0.8 m, 0.6 m step', base + [os.path.join(sm, 'samples_high.bin'), '--step', '0.6'] + sea,
                       os.path.join(sm, 'sample_high.log'))], log)
        chosen, counts = detailed1943.choose(o, os.path.join(sm, 'samples.bin'), xoff, zoff)
        s['detailed_sectors'] = [f'{x:02x}{z:02x}' for x, z in chosen]
        s['detailed_counts'] = counts
        passes['0.8'] = dict(preparation=prep, game=s['game'], time=time.strftime('%Y-%m-%d %H:%M'))
        s['sampled'] = passes['0.8']['time']
        save_settings(lv, s)
        log(f'{lv}: {len(chosen)} detailed sectors ({counts})')
    if pitch in (None, 0.2):
        sectors = s.get('detailed_sectors')
        if sectors is None or (passes.get('0.8') or {}).get('preparation') != prep:
            raise NavKitError(f'{lv}: the 0.8 m pass first (navkit.py sample {lv} --pitch 0.8)')
        invalidate(s, 'built', 'checked')
        passes.pop('0.2', None)
        save_settings(lv, s)
        if sectors:
            arg = f'--sectors={xoff},{zoff}:' + ','.join(sectors)
            log(f'{lv}: sampling the {len(sectors)} detailed sectors at 0.2 m')
            run_parallel([('0.2 m', base + [os.path.join(sm, 'samples_02.bin'), '--pitch', '0.2', arg] + sea,
                           os.path.join(sm, 'sample_02.log')),
                          ('0.2 m, 0.6 m step', base + [os.path.join(sm, 'samples_02_high.bin'), '--pitch', '0.2', '--step',
                                                         '0.6', arg] + sea, os.path.join(sm, 'sample_02_high.log'))], log)
        else:
            log(f'{lv}: no detailed sectors')
        passes['0.2'] = dict(preparation=prep, game=s['game'], sectors=list(sectors), time=time.strftime('%Y-%m-%d %H:%M'))
        s['sampled'] = passes['0.2']['time']
        save_settings(lv, s)
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
    detailed = None
    current02 = s2 is not None and s2.get('preparation') == prep and s2.get('sectors') == s.get('detailed_sectors')
    if sectors and current02 and os.path.isfile(os.path.join(sm, 'samples_02.bin')):
        detailed = {'samples': os.path.join(sm, 'samples_02.bin'), 'high': os.path.join(sm, 'samples_02_high.bin'),
                    'sectors': sectors}
    elif sectors:
        log(f'{lv}: no 0.2 m samples of this preparation: the detailed sectors are built at 0.8 m (navkit.py sample '
            f'{lv} --pitch 0.2)')
    invalidate(s, 'built', 'checked')
    save_settings(lv, s)
    wpath = os.path.join(o, 'water_mesh.res')
    mesh = water.read_mesh(wpath) if os.path.isfile(wpath) else None
    lines = []

    def blog(m):
        lines.append(m)
        log(m)
    t0 = time.time()
    # jump links: the AA gun platforms of ships, under their decks (jumplinks1943); built as ladders of kind 'jump'
    import jumplinks1943
    jumps = jumplinks1943.level_links(lv, blog, game=game)
    with open(os.path.join(p['navkit'], 'jump_links.json'), 'w', encoding='utf-8') as f:
        json.dump(jumps, f, indent=1)
    ladders = json.load(open(os.path.join(o, 'ladders.json'))) + jumps
    navbuild.build(os.path.join(sm, 'samples.bin'), os.path.join(o, 'forbid_tris.npy'), xoff, zoff, p['hpd'],
                   step=soldier1943.STEP, dynamic_path=os.path.join(o, 'dynamic_tris.npy'),
                   exclude_path=os.path.join(o, 'exclude_tris.npy'), exclude_foot_path=os.path.join(o, 'exclude_footprint_tris.npy'),
                   seeds=json.load(open(os.path.join(o, 'soldier_spawns.json'))), water=mesh,
                   max_water_depth=soldier1943.WATER_DEPTH, high_path=os.path.join(sm, 'samples_high.bin'),
                   detailed=detailed, ladders=ladders, log=blog)
    open(os.path.join(p['navkit'], 'build.log'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    s['built'] = time.strftime('%Y-%m-%d %H:%M')
    s['build_from'] = dict(preparation=prep, game=s['game'], samples_08=s8['time'],
                           samples_02=s2['time'] if detailed else None)
    save_settings(lv, s)
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
    if (s.get('build_from') or {}).get('preparation') != prep:
        raise NavKitError(f'{lv}: the build is from another preparation: build again (navkit.py build {lv})')
    invalidate(s, 'checked')
    save_settings(lv, s)
    structure = hpdcheck.check(p['hpd'])
    bad = {k: v for k, v in structure.items() if v}
    r = routecheck1943.check(p['hpd'], lv, game)
    text = ['structure: ' + ('every check passes' if not bad else '; '.join(f'{k}: {len(v)} (e.g. {v[0]})' for k, v in bad.items())),
            routecheck1943.report(r)]
    open(os.path.join(p['navkit'], 'check.txt'), 'w', encoding='utf-8').write('\n'.join(text) + '\n')
    with open(os.path.join(p['navkit'], 'check.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(structure=structure, routes=r), f, indent=1)
    for line in text:
        log(line)
    ok = not bad and r['ok']
    s['checked'] =time.strftime('%Y-%m-%d %H:%M') + (' ok' if ok else ' FAILED')
    save_settings(lv, s)
    return ok


def status(game, lv, log=print):
    p = paths(lv)
    s = json.load(open(p['settings'], encoding='utf-8')) if os.path.isfile(p['settings']) else {}
    for step in STAGES:
        log(f'  {step:9s} {s.get(step, "-")}')
    if s.get('detailed_sectors') is not None:
        log(f'  detailed sectors: {len(s["detailed_sectors"])}')
    log(f'  files: {p["work"]}')
    return s


def levels(game, log=print):
    for lv in game1943.levels(game):
        s = paths(lv)['settings']
        st = json.load(open(s, encoding='utf-8')) if os.path.isfile(s) else {}
        done = [k for k in STAGES if k in st]
        log(f'  {lv:22s} {", ".join(done) if done else "-"}' + (f' ({st["checked"]})' if 'checked' in st else ''))


def main(argv):
    args = list(argv[1:])
    opts = {}
    for k in ('--game', '--pitch'):
        if k in args:
            i = args.index(k)
            opts[k] = args[i + 1]
            del args[i:i + 2]
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    try:
        try:
            game = game1943.find_game(opts.get('--game'))
        except FileNotFoundError as e:
            raise NavKitError(str(e))
        cmd = args[0]
        if cmd == 'levels':
            levels(game)
            return 0
        if len(args) < 2:
            raise NavKitError(f'{cmd}: which level? (e.g. navkit.py {cmd} wake_island_s)')
        lv = level_of(game, args[1])
        if cmd == 'prepare':
            prepare(game, lv)
        elif cmd == 'sample':
            try:
                pitch = float(opts['--pitch']) if '--pitch' in opts else None
            except ValueError:
                raise NavKitError(f'--pitch {opts["--pitch"]}: not a sampling pass (0.8 or 0.2)')
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
        else:
            raise NavKitError(f'unknown command {cmd!r} (navkit.py help)')
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
