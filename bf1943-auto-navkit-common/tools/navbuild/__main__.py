"""navbuild's command line: python -B -m navbuild <samples.bin> <forbid_tris.npy> <XOffset> <ZOffset> <out.hpd> [...]
(from bf1943-auto-navkit-common\tools; or python -B <that folder>\navbuild ...). The options are in navbuild's
docstring (navbuild/__init__.py), which a run without its five arguments prints."""
import sys

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
import navbuild  # noqa: E402


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith('--')]
    opts = dict(zip(argv[1:], argv[2:]))
    if any(arg in ('-h', '--help') for arg in argv[1:]):
        print(navbuild.__doc__)
        return 0
    if len(args) < 5:
        print(navbuild.__doc__)
        return 1
    navbuild.build(args[0], args[1] if args[1] != '-' else None, int(args[2]), int(args[3]), args[4],
                   float(opts.get('--step', 0.28)), dynamic_path=opts.get('--dynamic'), high_path=opts.get('--high'),
                   narrow_path=opts.get('--narrow'), stairs_path=opts.get('--stairs'),
                   exclude_path=opts.get('--exclude'), exclude_foot_path=opts.get('--exclude-foot'))
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navbuild', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
