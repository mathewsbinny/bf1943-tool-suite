"""NavKit: route-graph statistics of pathdata.hpd files, one line per file.

  graphstats.py <pathdata.hpd> [...]

For each file: its route nodes (unique by id; a border node shared by two sectors counts once), how many have no
outgoing edge, the connected components of the route graph (edges taken both ways) with the largest one's size and
share, and the next five sizes. A file under a game's Output folder is named by its level folder.
"""
import collections
import sys

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
import hpdtool  # noqa: E402


def file_stats(path: str) -> str:
    """The statistics line of one path-data file."""
    with open(path, 'rb') as stream:
        hpd = hpdtool.parse(stream.read())
    parent: dict[int, int] = {}

    def find(node_id: int) -> int:
        """The node's component root (union-find with path halving)."""
        while parent.setdefault(node_id, node_id) != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    out_degree: collections.Counter[int] = collections.Counter()
    ids: set[int] = set()
    for sector in hpd.sectors:
        for edge in sector.edges:
            src, dst = sector.nodes[edge.src].id, sector.nodes[edge.dst].id
            out_degree[src] += 1
            parent[find(src)] = find(dst)
        for node in sector.nodes:
            ids.add(node.id)
            find(node.id)
    components = collections.Counter(find(node_id) for node_id in ids)
    sizes = sorted(components.values(), reverse=True)
    isolated = sum(1 for node_id in ids if out_degree[node_id] == 0)
    name = path.split('/')[-3] if 'Output' in path else path
    return (f'{name} nodes {len(ids)} isolated {isolated} components {len(sizes)} largest {sizes[0]} '
            f'({sizes[0] / len(ids) * 100:.1f}%) next {sizes[1:6]}')


def main(argv: list[str]) -> int:
    for path in argv[1:]:
        print(file_stats(path))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
