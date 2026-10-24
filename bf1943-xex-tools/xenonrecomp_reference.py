"""Hash-verified access to the supplied XenonRecomp reference, as the original archive or the extracted folder.

The archive (Tools/XenonRecomp.zip, SHA-256 e344f016...) was later extracted in place to Tools/XenonRecomp/.
Every member used as evidence is pinned below; a read fails unless the bytes match the recorded hash.
"""
from pathlib import Path
import hashlib,zipfile
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import research_root  # noqa: E402
ROOT = research_root()                  # the research workspace (workspace.py)
ARCHIVE=ROOT/'Tools/XenonRecomp.zip'
ARCHIVE_SHA256='e344f0162cf40983377535774a523206a0a0d42b1e7679884c867f9969d71087'
FOLDER=ROOT/'Tools/XenonRecomp'
MEMBERS={
    'XenonRecomp-main/thirdparty/disasm/ppc-dis.c':'352a4f17cdfe95fe284653c4ba343308557d4fb16b96bed3b136d6df54c16b58',
    'XenonRecomp-main/XenonRecomp/recompiler.cpp':'30e7ea5b4d8a225bc3e0ac71aebd1a0af7bcde5aaf5679517719b559c9cd777a',
    'XenonRecomp-main/XenonUtils/ppc_context.h':'369acaf639c52bb25ee8a2c6a555c7875912f0692b1e8220ea8dab0384e42263',
    'XenonRecomp-main/XenonUtils/xbox.h':'eba4079e8379bdb138f4da77405a3432df60f8bcdf3f47cbc3ca1c8543cf5814',
    'XenonRecomp-main/README.md':'b72d34f82f796e4c84f0f622fad8fb720b64607b842de31b70596d35157a68ff',
}
def source():
    """'archive' when the original ZIP is present and intact, else 'folder'."""
    if ARCHIVE.exists():
        if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()!=ARCHIVE_SHA256:raise ValueError('XenonRecomp.zip differs from recorded hash')
        return 'archive'
    if not FOLDER.exists():raise FileNotFoundError('XenonRecomp reference missing (neither archive nor extracted folder)')
    return 'folder'
def read(suffix):
    """Return (member name, bytes) for the pinned member ending in suffix, verified against its recorded hash."""
    member=next(m for m in MEMBERS if m.endswith(suffix))
    if source()=='archive':
        with zipfile.ZipFile(ARCHIVE) as z:data=z.read(member)
    else:data=(FOLDER/member).read_bytes()
    if hashlib.sha256(data).hexdigest()!=MEMBERS[member]:raise ValueError(f'{member} differs from recorded hash')
    return member,data
def provenance():
    """Evidence record of where the reference bytes came from."""
    return dict(source=source(),archive_sha256=ARCHIVE_SHA256,members_verified=sorted(MEMBERS))
