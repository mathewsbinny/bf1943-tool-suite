r"""Writes BF1943's reflection schema into DbxEdit.cs (the block between "BEGIN GENERATED BF1943 SCHEMA" and "END
GENERATED BF1943 SCHEMA") for the level check's BF1943 rules: every reflected class (with its base class), value type
(struct) and enum, with its members - each member's type when it is a class, struct or enum, and an array's element
type - and each enum's value names.

  python -B schema1943.py [<bf1943-reflection-schema-full.json>]

The schema is the research workspace's research\evidence\bf1943-reflection-schema-full.json (BF1943_RESEARCH, else
operation-rising-sun next to the suite), read from the game's executable by src\xexresearch\dump_reflection.py and
completed by dump_reflection_dynamic.py (value types, array element types, the member records written at start-up).
The block records the file's SHA-256 and the executable's. Run build.bat afterwards.
"""
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))
BEGIN, END = '    // BEGIN GENERATED BF1943 SCHEMA', '    // END GENERATED BF1943 SCHEMA'
KIND = {'class': 'c', 'struct': 's', 'enum': 'e'}


def default_schema():
    root = os.environ.get('BF1943_RESEARCH') or os.path.join(os.path.dirname(SUITE), 'operation-rising-sun')
    return os.path.join(root, 'research', 'evidence', 'bf1943-reflection-schema-full.json')


def block(path):
    raw = open(path, 'rb').read()
    s = json.loads(raw)
    if 'completion' not in s:
        raise ValueError(f'{path} is not the completed schema (no "completion" record): run the research workspace\'s '
                         f'src\\xexresearch\\dump_reflection_dynamic.py and pass bf1943-reflection-schema-full.json')
    types = {t['name']: t for t in s['types'] if t.get('name')}
    by_obj = {t['type_object']: t for t in s['types']}
    lines, counts = [], {'c': 0, 's': 0, 'e': 0}
    members = 0
    for t in sorted(types.values(), key=lambda t: t['name']):
        k = KIND[t['kind']]
        counts[k] += 1
        base = by_obj.get(t.get('base_type_object')) if k == 'c' else None
        parts = [t['name'], k, base['name'] if base else '-']
        for m in t['members']:
            if k == 'e':
                parts.append(m['name'])
                continue
            members += 1
            mt, el = m.get('type'), m.get('element')
            if el is not None or mt == '82d5f000':
                parts.append(m['name'] + '[]' + (':' + el if el in types else ''))
            else:
                parts.append(m['name'] + (':' + mt if mt in types else ''))
        lines.append(' '.join(parts))
    c = s['completion']
    return '\n'.join([
        BEGIN,
        f'    // Written by schema1943.py from bf1943-reflection-schema-full.json (SHA-256 {hashlib.sha256(raw).hexdigest()}),',
        f'    // read from the executable with SHA-256 {s["image_sha256"]} ({c["filled"]} member records',
        '    // recovered from the start-up code, value types and array element types added). Do not edit by hand.',
        '    static class Bf1943Schema',
        '    {',
        f'        // "<name> <c|s|e> <base class|-> <member>..." for {counts["c"]} classes, {counts["s"]} structs and {counts["e"]} enums;',
        f'        // a member ({members} in all) is "name", "name:<type>" (a reflected class, struct or enum), "name[]" or',
        '        // "name[]:<element type>"; an enum lists its value names',
        '        public static readonly string[] Types = {',
        '\n'.join('            "' + line + '",' for line in lines),
        '        };',
        '    }',
        END]), counts, members


def main(argv):
    path = argv[1] if len(argv) > 1 else default_schema()
    if not os.path.isfile(path):
        raise FileNotFoundError(f'no reflection schema at {path}: pass bf1943-reflection-schema-full.json, or set '
                                f'BF1943_RESEARCH to the research workspace that has research\\evidence\\'
                                f'bf1943-reflection-schema-full.json')
    target = os.path.join(HERE, 'DbxEdit.cs')
    text = open(target, encoding='utf-8', newline='').read()
    new, counts, members = block(path)
    if BEGIN not in text:
        raise ValueError(f'{target} has no "{BEGIN.strip()}" block to replace')
    a = text.index(BEGIN)
    b = text.index(END) + len(END)
    text = text[:a] + new + text[b:]
    nl = '\r\n' if '\r\n' in text else '\n'
    text = text.replace('\r\n', '\n').replace('\n', nl)
    with open(target, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)
    print(f'wrote {counts["c"]} classes, {counts["s"]} structs, {counts["e"]} enums and {members} members into {target}; '
          f'run build.bat')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('schema1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
