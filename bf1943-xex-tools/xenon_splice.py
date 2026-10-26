"""Splice our own p-code bodies into the inherited SLEIGH sources (used by build_vmx_language.py).

- AltiVec (altivec.sinc, original set): every instruction except the stream hints and the estimate instructions
  without a p-code primitive gets its body from xenon_vector_semantics; its header (decoding) is kept, minus the
  inherited sub-piece selector operands, and the vector-load forms that named RA as `A` use RA_OR_ZERO as the ISA says.
- Scalar FP (ppc_instructions.sinc): fctiw/fctiwz/fctid/fctidz (and their record forms) are replaced: the inherited
  bodies truncate for every form and do not saturate. Ours: fctiw/fctid round to nearest even (FPSCR RN = 0 is
  assumed), z forms truncate; out-of-range values saturate and NaN gives the most negative integer.
"""
import re

import xenon_vector_semantics as vsem

RA_AS_A = {'lvehx', 'lvewx', 'lvsl', 'lvxl'}


def constructors(lines):
    """(start, brace, end, name) for each constructor: header lines start..brace-1, body brace..end (the '}' line)."""
    i = 0
    while i < len(lines):
        if lines[i].startswith(':'):
            name = lines[i][1:].split()[0]
            j = i
            while not lines[j].lstrip().startswith('{'):
                j += 1
            k = j
            while lines[k].strip() != '}':
                k += 1
            yield i, j, k, name
            i = k
        i += 1


def splice(text, replace):
    """replace(name, header) -> (header, body_lines) or None (keep)."""
    lines = text.split('\n')
    out, last = [], 0
    done = []
    for start, brace, end, name in constructors(lines):
        header = ' '.join(l.strip() for l in lines[start:brace])
        new = replace(name, header)
        if new is None:
            continue
        h, body = new
        out += lines[last:start] + [h, '{'] + ['\t' + b for b in body] + ['}']
        last = end + 1
        done.append(name)
    out += lines[last:]
    return '\n'.join(out), done


def altivec_ops(name, header):
    display = re.split(r'\s+is\s+', header[1:], maxsplit=1)[0].split(None, 1)
    fields = display[1].split(',') if len(display) > 1 else []
    imm = next((f for f in fields if f in ('A_BITS', 'A_BITSS', 'SHB', 'UIMW', 'UIMH', 'UIMB')), None)
    store = name.startswith('stv')
    return dict(D='vrS' if store else 'vrD', A='vrA', B='vrB', C='vrC', IMM=imm, RA='RA_OR_ZERO', RB='B')


def altivec_replace(name, header):
    if name in vsem.ALTIVEC_HINTS:
        return None
    body = vsem.body(name, altivec_ops(name, header))
    if body is None:
        return None
    h = re.sub(r'\s*&\s*vr[ABCDS]_\d+_\d+', '', header)
    h = re.sub(r'\s*&\s*vaddubm_part\d', '', h)
    if name in RA_AS_A:
        h = h.replace(f':{name} vrD,A,B', f':{name} vrD,RA_OR_ZERO,B').replace('& A &', '& RA_OR_ZERO &')
    return h, body


def rewrite_altivec(text):
    text, done = splice(text, altivec_replace)
    # VSCR as a register for mfvscr/mtvscr (SAT and NJ effects of other instructions are not modelled).
    return 'define register offset=0x101000 size=4 [ vscr ];\n' + text, done


FP_CONVERT = {'fctiw', 'fctiw.', 'fctiwz', 'fctiwz.', 'fctid', 'fctid.', 'fctidz', 'fctidz.'}


def fp_convert_body(name):
    word = name.startswith('fctiw')
    nearest = 'z' not in name
    b = ['local cx:8 = fB;']
    if nearest:
        b += ['local cax:8 = abs(cx);', 'local c52:8 = int2float(0x10000000000000:8);', 'local cy:8 = (cax f+ c52) f- c52;',
              'local csm:8 = zext(cax f< c52);', 'csm = 0 - csm;', 'cy = cy | (cx & 0x8000000000000000);', 'cx = (cy & csm) | (cx & ~csm);']
    size = 4 if word else 8
    top = 0x7fffffff if word else 0x7fffffffffffffff
    low = 0x80000000 if word else 0x8000000000000000
    b += [f'local cbig:8 = int2float({"0x80000000" if word else "0x4000000000000000"}:8);']
    if not word:
        b.append('cbig = cbig f+ cbig;')
    b += [f'local chi:{size} = zext(cx f>= cbig);', 'chi = 0 - chi;',
          f'local clo:{size} = zext((cx f< (f- cbig)) || nan(cx));', 'clo = 0 - clo;',
          f'local ct:{size} = trunc(cx);',
          f'local cres:{size} = (ct & ~(chi | clo)) | ({top:#x} & chi) | ({low:#x} & clo);',
          'fD = sext(cres);' if word else 'fD = cres;']
    if name.endswith('.'):
        b.append('cr1flags();')
    return b


def fp_replace(name, header):
    if name not in FP_CONVERT:
        return None
    return header, fp_convert_body(name)


def rewrite_scalar_fp(text):
    return splice(text, fp_replace)
