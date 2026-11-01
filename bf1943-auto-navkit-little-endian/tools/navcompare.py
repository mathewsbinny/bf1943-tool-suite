"""NavKit: compare two pathdata.hpd files of a level on the positions where its AI is ordered to go.

Usage: navcompare.py <level.xml> <reference.hpd> <candidate.hpd> [--json]

For the level's AI order and patrol positions (ordercheck.order_points) it reports:
  coverage       of the positions the reference has on its walkable areas, how many the candidate has too
                 (NavKit's headline accuracy figure), plus positions only the candidate covers
  main network   positions in a route group at least 25 % the size of the largest group, per file
  pairwise       of all position pairs the reference's route network connects, the share the candidate also
                 connects (a guide: the reference has gaps of its own)
  size           sectors, route nodes and edges per file

The reference is normally the level's original (DICE) file, the candidate NavKit's build; any two files work.
"""
import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
import hpdtool  # noqa: E402
import ordercheck  # noqa: E402

if TYPE_CHECKING:
    from ordercheck import OrderPoint

type Report = dict[str, Any]     # compare()'s result (its keys are listed in compare)


def main_network(data: ordercheck.PathData, points: 'list[OrderPoint]') -> int:
    """How many of the points are near a route group at least 25 % the size of the file's largest."""
    largest = max(data.group_size.values()) if data.group_size else 0
    count = 0
    for _, _, position in points:
        _, size = data.nearest_group(position)
        if size and size >= 0.25 * largest:
            count += 1
    return count


def compare(level_xml: str, reference: str, candidate: str) -> Report:
    """The comparison of the two files on the level's AI positions (the module docstring's measures)."""
    points = ordercheck.order_points(level_xml)
    reference_data, candidate_data = ordercheck.PathData(reference), ordercheck.PathData(candidate)
    on_reference = [reference_data.on_area(position) for _, _, position in points]
    on_candidate = [candidate_data.on_area(position) for _, _, position in points]
    reference_group = [reference_data.nearest_group(position)[0] if on_reference[i] else None
                       for i, (_, _, position) in enumerate(points)]
    candidate_group = [candidate_data.nearest_group(position)[0] if on_candidate[i] else None
                       for i, (_, _, position) in enumerate(points)]
    both = sum(1 for in_reference, in_candidate in zip(on_reference, on_candidate) if in_reference and in_candidate)
    only_candidate = sum(1 for in_reference, in_candidate in zip(on_reference, on_candidate)
                         if in_candidate and not in_reference)
    grouped = [i for i in range(len(points)) if reference_group[i] is not None]
    pairs = connected = 0
    for first in range(len(grouped)):
        for second in range(first + 1, len(grouped)):
            x, y = grouped[first], grouped[second]
            if reference_group[x] == reference_group[y]:
                pairs += 1
                if candidate_group[x] is not None and candidate_group[x] == candidate_group[y]:
                    connected += 1
    files = {}
    for name, path in (('reference', reference), ('candidate', candidate)):
        hpd = hpdtool.parse(open(path, 'rb').read())
        files[name] = {'file': path, 'sectors': len(hpd.sectors),
                       'nodes': sum(len(sector.nodes) for sector in hpd.sectors),
                       'edges': sum(len(sector.edges) for sector in hpd.sectors)}
    return {
        'positions': len(points),
        'reference_covers': sum(on_reference),
        'candidate_covers_of_those': both,
        'coverage_percent': round(both / max(sum(on_reference), 1) * 100, 1),
        'candidate_only': only_candidate,
        'main_network': {'reference': main_network(reference_data, points),
                         'candidate': main_network(candidate_data, points)},
        'pairwise': {'connected': connected, 'pairs': pairs, 'percent': round(connected / max(pairs, 1) * 100, 1)},
        'files': files,
    }


def report(result: Report, log: Callable[[str], object] = print) -> None:
    """compare()'s result in three lines."""
    log(f"{result['positions']} order positions; the reference covers {result['reference_covers']}, "
        f"the candidate {result['candidate_covers_of_those']} of those ({result['coverage_percent']} %) "
        f"+ {result['candidate_only']} more")
    main, pairwise = result['main_network'], result['pairwise']
    log(f"main route network: reference {main['reference']}, candidate {main['candidate']}; "
        f"pairwise {pairwise['connected']}/{pairwise['pairs']} ({pairwise['percent']} %)")
    reference_file, candidate_file = result['files']['reference'], result['files']['candidate']
    log(f"sectors {reference_file['sectors']} / {candidate_file['sectors']}; "
        f"nodes {reference_file['nodes']} / {candidate_file['nodes']}; "
        f"edges {reference_file['edges']} / {candidate_file['edges']}")


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith('--')]
    if len(args) != 3:
        print(__doc__)
        return 1
    result = compare(*args)
    if '--json' in argv:
        print(json.dumps(result, indent=1))
    else:
        report(result)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navcompare', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
