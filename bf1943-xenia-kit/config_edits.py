"""Network ports above 1024 in BF1943's GameSettings.cfg (inside dist/xenon/config.zz, a ZIP).

The game sets Network.ClientPort 1001 and Network.ServerPort 1003. A local match binds both, and its client
sends to 127.0.0.1:<ServerPort>. Xenia remaps guest binds below 1024 by +10000 but not the destinations of
sendto, so the client would never reach its own server. Only these two lines change; every other entry keeps
its bytes and ZIP header fields.
"""
import copy
import io
import re
import sys
import zipfile

sys.dont_write_bytecode = True
ARCHIVE = 'dist/xenon/config.zz'
PORTS = {'Network.ClientPort': (1001, 25001), 'Network.ServerPort': (1003, 25003)}


def build(archive_bytes):
    """(new config.zz bytes, new GameSettings.cfg bytes, change log)."""
    src = zipfile.ZipFile(io.BytesIO(archive_bytes))
    entries = [(i, src.read(i)) for i in src.infolist()]  # read first: writestr updates the ZipInfo it is given
    changes, settings = [], None
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as dst:
        for info, data in entries:
            if info.filename == 'GameSettings.cfg':
                text = data.decode('latin1')
                for key, (old, new) in PORTS.items():
                    pattern = re.compile(rf'^({re.escape(key)}\s+){old}(\s*)$', re.M)
                    if len(pattern.findall(text)) != 1:
                        raise ValueError(f'{key} {old} not found exactly once in GameSettings.cfg')
                    text = pattern.sub(rf'\g<1>{new}\g<2>', text)
                    changes.append(dict(setting=key, before=old, after=new))
                data = settings = text.encode('latin1')
            dst.writestr(copy.copy(info), data, compress_type=info.compress_type)
    if settings is None:
        raise ValueError('GameSettings.cfg is not in config.zz')
    result = out.getvalue()
    check = zipfile.ZipFile(io.BytesIO(result))
    if [i.filename for i in check.infolist()] != [i.filename for i, _ in entries] or check.testzip() is not None:
        raise ValueError('the rewritten archive differs in entries or fails its CRC check')
    for i, data in entries:
        if i.filename != 'GameSettings.cfg' and check.read(i.filename) != data:
            raise ValueError(f'entry changed: {i.filename}')
    return result, settings, changes
