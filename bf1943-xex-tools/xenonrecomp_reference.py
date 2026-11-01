"""Hash-verified access to the supplied XenonRecomp reference, as the original archive or the extracted folder.

The archive (Tools/XenonRecomp.zip, SHA-256 e344f016...) was later extracted in place to Tools/XenonRecomp/.
Every member used as evidence is pinned below; a read fails unless the bytes match the recorded hash.
Each function takes the research workspace as `root`; by default it is the one workspace.py finds (BF1943_RESEARCH,
else the current folder), looked up when the function is called rather than when this module is imported.
"""
from pathlib import Path
from typing import Any
import hashlib
import zipfile

from workspace import research_root  # noqa: E402

ARCHIVE_SHA256 = 'e344f0162cf40983377535774a523206a0a0d42b1e7679884c867f9969d71087'
MEMBERS = {
    'XenonRecomp-main/thirdparty/disasm/ppc-dis.c': '352a4f17cdfe95fe284653c4ba343308557d4fb16b96bed3b136d6df54c16b58',
    'XenonRecomp-main/XenonRecomp/recompiler.cpp': '30e7ea5b4d8a225bc3e0ac71aebd1a0af7bcde5aaf5679517719b559c9cd777a',
    'XenonRecomp-main/XenonUtils/ppc_context.h': '369acaf639c52bb25ee8a2c6a555c7875912f0692b1e8220ea8dab0384e42263',
    'XenonRecomp-main/XenonUtils/xbox.h': 'eba4079e8379bdb138f4da77405a3432df60f8bcdf3f47cbc3ca1c8543cf5814',
    'XenonRecomp-main/README.md': 'b72d34f82f796e4c84f0f622fad8fb720b64607b842de31b70596d35157a68ff',
}


def archive_path(root: str | Path | None = None) -> Path:
    return Path(root or research_root()) / 'Tools/XenonRecomp.zip'


def folder_path(root: str | Path | None = None) -> Path:
    return Path(root or research_root()) / 'Tools/XenonRecomp'


def source(root: str | Path | None = None) -> str:
    """'archive' when the original ZIP is present and intact, else 'folder'."""
    archive = archive_path(root)
    if archive.exists():
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise ValueError('XenonRecomp.zip differs from recorded hash')
        return 'archive'
    if not folder_path(root).exists():
        raise FileNotFoundError('XenonRecomp reference missing (neither archive nor extracted folder)')
    return 'folder'


def read(suffix: str, root: str | Path | None = None) -> tuple[str, bytes]:
    """Return (member name, bytes) for the pinned member ending in suffix, verified against its recorded hash."""
    root = root or research_root()
    member = next(name for name in MEMBERS if name.endswith(suffix))
    if source(root) == 'archive':
        with zipfile.ZipFile(archive_path(root)) as archive:
            data = archive.read(member)
    else:
        data = (folder_path(root) / member).read_bytes()
    if hashlib.sha256(data).hexdigest() != MEMBERS[member]:
        raise ValueError(f'{member} differs from recorded hash')
    return member, data


def provenance(root: str | Path | None = None) -> dict[str, Any]:
    """Evidence record of where the reference bytes came from."""
    return dict(source=source(root), archive_sha256=ARCHIVE_SHA256, members_verified=sorted(MEMBERS))
