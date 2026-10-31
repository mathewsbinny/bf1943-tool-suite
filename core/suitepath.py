r"""How the suite's scripts find each other: the one place that puts the suite's folders on sys.path.

The tools run straight from a clone (`python -B <tool>.py`, no install step), and the suite's folders are not
packages (their names have hyphens). So a script that imports from another suite folder starts with the suite's
bootstrap, the same lines in every one:

    if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
        import pathlib
        import sys
        sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
        import suitepath
        suitepath.use()             # or use(suitepath.NAVKIT_BE), use(suitepath.NAVKIT_LE), ...

(inside an existing `if __name__ == '__main__':` block, the same lines without the `if`). It finds the suite root
above the script and puts `core` on sys.path; `use` puts the folders it is given at the front, in that order, and
the shared folders (SHARED) after them. The guard means that a module that is also imported as a library never
changes sys.path: libraries (core, the tools' importable modules) import plainly and rely on the script that was
run. Tests are scripts too and use the same lines. A script that imports only from its own folder needs none (Python
puts the script's folder first). tests/test_repo.py fails on any other sys.path edit.

Installed (`pip install -e .`), `core` and the shared folders are already on sys.path (the install's .pth file,
pyproject.toml dev-mode-dirs), and `use` adds none of them; it still puts the NavKit variants a script names in front.

The NavKit's modules both variants use (the builder navbuild, the .hpd reader and writer hpdtool and kymesh, the
Havok reader, the samples, the walkability rules, ...) are one copy in bf1943-auto-navkit-common/tools, a shared
folder (2026-10-31; until then each variant had its own copy of 14 of them and a script's order of NAVKIT_BE and
NAVKIT_LE chose between the copies). The variants keep only their own modules, and no module name is in two of the
folders (tests/test_repo.py checks every folder here, the variants too), so the order of the groups a script names
no longer matters; a script names the variants whose own modules it imports:
- NAVKIT_BE, the BF1943 tools (navkit.py, soldier1943, levelobjects1943, navexport1943, ...): its scripts, the
  Navigation Studio and Map Inspector, and core's level1943.load (levelobjects1943);
- NAVKIT_LE, the BFBC2 tools (navkit_le.py, navregress, navparity, ...): its scripts, and core's level.py (BFBC2);
- neither for the shared ones alone: core's navdata (hpdtool, kymesh, navdiff) needs only the shared folders.
"""
import os
import sys

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# folders with no module name in common with any other (tests/test_repo.py checks it), so their order does not matter
SHARED = ['core', 'bf1943-fbrb-tool', 'bf1943-apt-tools', 'bf1943-spawn-map', 'bf1943-console-readers/stfs',
          'bf1943-xenia-kit', 'bf1943-automated-map-and-bot-test-harness/xenia-run', 'bf1943-xex-tools',
          'bf1943-auto-navkit-common/tools']
NAVKIT_BE = ['bf1943-auto-navkit-big-endian/tools', 'bf1943-auto-navkit-big-endian']
NAVKIT_LE = ['bf1943-auto-navkit-little-endian/tools', 'bf1943-auto-navkit-little-endian']


def folder(rel):
    return os.path.normpath(os.path.join(SUITE, rel))


def _key(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path or os.curdir)))


def use(*groups):
    """Puts the given folder groups (NAVKIT_BE, NAVKIT_LE, or other suite-relative folders) at the front of sys.path
    in the order given, the first first (moved there if already on it), and the shared folders after them unless
    already on sys.path (an install puts them there). No module name is in two of these folders, so the order changes
    nothing a script imports."""
    wanted = [rel for g in groups for rel in ([g] if isinstance(g, str) else g)]
    front = [folder(rel) for rel in wanted]
    missing = [p for p in front if not os.path.isdir(p)]
    if missing:
        raise FileNotFoundError(f'suitepath.use: no such suite folder: {", ".join(missing)}')
    keys = {_key(p) for p in front}
    present = {_key(p) for p in sys.path}
    shared = [folder(rel) for rel in SHARED if _key(folder(rel)) not in keys | present]
    sys.path[:] = front + shared + [p for p in sys.path if _key(p) not in keys]
