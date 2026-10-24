"""The research workspace the executable tools read and write.

The tools work on a workspace laid out as operation-rising-sun is: work\\extracted\\default.xex (your extracted
executable), work\\xex-analysis\\ (the decoded image, the Ghidra project, tables), work\\vmx128\\ (the Xenon
language module), work\\tools\\ (XEXLoaderWV and Xenia's reference sources, at pinned commits) and
research\\evidence\\ (the JSON evidence they write). It is BF1943_RESEARCH, else the folder they are run from.
"""
import os
from pathlib import Path


def research_root():
    """The workspace root (Path); exits with what to do when the folder is not one."""
    p = Path(os.environ.get('BF1943_RESEARCH') or os.getcwd()).resolve()
    if not ((p / 'work').is_dir() and (p / 'research').is_dir()):
        raise SystemExit(f'{p} is not a research workspace (it needs work\\ and research\\): run the tool from one, such '
                         f'as operation-rising-sun, or set BF1943_RESEARCH to it')
    return p
