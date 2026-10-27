"""SLEIGH p-code bodies for Xenon's vector instructions (AltiVec/VMX and VMX128): our own, from the instruction
definitions (AltiVec Technology Programming Environments Manual semantics; VMX128 operand roles from the encoding
table). build_vmx_language.py splices these bodies into the isolated Xenon language.

Conventions (all lanes are numbered as the guest does: lane 0 is the most significant, i.e. lowest-address, lane):
- Floating point: lane-wise IEEE single precision with p-code float ops, round to nearest even. Not modelled
  (documented): VSCR NJ (denormal flushing) and SAT, NaN payload propagation, fused rounding of multiply-add
  (the product is rounded before the add), and the reduced precision of the estimate instructions (vrefp,
  vrsqrtefp are exact reciprocals / reciprocal square roots).
- Results are assembled from lanes with shifts and ORs; selections use bit masks (no branches).
- Element loads place the element in its lane and zero the others (the architecture leaves them undefined).
  Element stores are modelled as a read-modify-write of the aligned 16-byte block that changes only the element.

- Estimates with no p-code primitive (vexptefp, vlogefp and their 128 forms) use named ops (KNOWN_OPS:
  exp2_estimate, log2_estimate). vupkd3d128 is split by its immediate: UIMM 20 (half to float) is modelled, UIMM 4
  models z and w with x/y from the named op vupkd3d128_xy_unknown (PARTIAL_OPS), other values stay opaque.
  vpkd3d128 stays opaque (OPAQUE gives the reasons).

`body(name, ops)` returns the body lines (without braces) or None when the instruction stays opaque.
`variants(name, ops)` returns the per-immediate constructors [(extra constraint, body or None)] for a split
instruction (vupkd3d128), else None.
ops maps roles to SLEIGH operand names: D, A, B, C (vector), IMM (immediate field), RA, RB (GPRs).
"""

SEMANTICS_VERSION = '1.1'

# Instructions deliberately left opaque, and why.
OPAQUE = {
    'vpkd3d128': 'pack placement (VD3D1/VD3D2) and the type-5 pack conversion are not established by the image\'s uses',
    'vupkd3d128': 'only UIMM 4 (z, w) and 20 (half to float) are established; other types stay opaque',
}

# Named lane-wise operations with defined semantics that p-code has no primitive for. They are CALLOTHERs, but their
# meaning is fixed (the hardware returns estimates within the AltiVec bound, relative error <= 1/16 for 2^x and
# absolute error <= 1/32 for log2), so the decompiler shows them by name and the batch counts them as modelled.
KNOWN_OPS = {'exp2_estimate': '2^x per float lane (estimate)', 'log2_estimate': 'log2(x) per float lane (estimate)'}
# Partially known: vupkd3d128 UIMM 4 lanes x, y (biased short format not verified from the image).
PARTIAL_OPS = {'vupkd3d128_xy_unknown': 'lanes 0-1 of vupkd3d128 with UIMM 4 (unknown value)'}
PCODEOP_DEFINES = ''.join(f'define pcodeop {n};\n' for n in list(KNOWN_OPS) + list(PARTIAL_OPS))


def half_to_float(g, h):
    """IEEE binary16 to binary32 for a 2-byte lane: normal, subnormal, zero, infinity/NaN (exponent 31 as IEEE)."""
    s = g.local(4, f'zext({h} >> 15) << 31', 's')
    e = g.local(4, f'zext(({h} >> 10) & 31)', 'x')
    m = g.local(4, f'zext({h} & 0x3ff)', 'y')
    normal = g.local(4, f'{s} | (({e} + 112) << 23) | ({m} << 13)', 'n')
    inf = g.local(4, f'{s} | 0x7f800000 | ({m} << 13)', 'i')
    tiny = g.local(4, f'int2float({m})', 'd')
    scale = g.local(4, f'{g.fconst(1)} f/ {g.fconst(1 << 24)}', 'k')
    sub = g.local(4, f'({tiny} f* {scale}) | {s}', 'u')
    r = g.blend(g.mask(f'{e} == 31', 4), inf, normal, 4)
    return g.blend(g.mask(f'{e} == 0', 4), sub, r, 4)


def variants(name, ops):
    """Constructors split by immediate: [(extra constraint, body or None for opaque)], or None for a single one."""
    if name != 'vupkd3d128':
        return None
    g = G()
    b = g.lanes(g.copy(ops['B']), 2)
    lanes = [half_to_float(g, h) for h in b[4:]]
    g.emit(f'{ops["D"]} = {g.pack(lanes, 4)};')
    half4 = g.lines
    g = G()
    xy = g.local(8, f'vupkd3d128_xy_unknown({ops["B"]})', 'p')
    one = g.fconst(1)
    r = g.local(16, f'zext({xy}) << 64', 'r')
    g.emit(f'{r} = {r} | zext({one});')
    g.emit(f'{ops["D"]} = {r};')
    return [(' & xmuimm=20', half4), (' & xmuimm=4', g.lines), ('', None)]   # a raw field: an operand cannot be constrained


class G:
    """Emits uniquely named locals for one constructor."""

    def __init__(self):
        self.lines = []
        self.n = 0

    def name(self, p):
        self.n += 1
        return f'{p}_{self.n}'   # the underscore keeps locals clear of register names (r16, f11, v40...)

    def emit(self, s):
        self.lines.append(s)

    def local(self, size, expr, p='t'):
        v = self.name(p)
        self.emit(f'local {v}:{size} = {expr};')
        return v

    def copy(self, reg, p='s'):
        return self.local(16, reg, p)

    def lanes(self, src, e, p='e'):
        n = 16 // e
        return [self.local(e, f'{src}({16 - (i + 1) * e})', p) for i in range(n)]

    def pack(self, vals, e):
        r = self.local(16, f'zext({vals[0]})', 'r')
        for v in vals[1:]:
            self.emit(f'{r} = ({r} << {8 * e}) | zext({v});')
        return r

    def mask(self, cond, e):
        m = self.local(e, f'{cond}' if e == 1 else f'zext({cond})', 'm')
        self.emit(f'{m} = 0 - {m};')
        return m

    def byte_lane(self, v, index_expr, e):
        """Element of e bytes at guest lane index_expr (a 1-byte expression) of the 16-byte value v."""
        s = self.local(16, f'{v} >> (({16 // e - 1} - ({index_expr})) * {8 * e})', 'h')
        return self.local(e, f'{s}:{e}', 'x')

    def blend(self, m, x, y, e):
        """x where mask m is all ones, else y."""
        return self.local(e, f'({x} & {m}) | ({y} & ~{m})', 'b')

    def fconst(self, value):
        """A 4-byte float constant from an integer value."""
        return self.local(4, f'int2float({value}:8)', 'k')


# --- helpers ----------------------------------------------------------------------------------------------------
def smax(e): return (1 << (8 * e - 1)) - 1
def smin_bits(e): return 1 << (8 * e - 1)
def umax(e): return (1 << (8 * e)) - 1


def sat_signed(g, t, e):
    """t is a wider signed value; saturate to e bytes signed."""
    hi = g.mask(f'{t} s> {smax(e)}', e)
    lo = g.mask(f'{t} s< -{smin_bits(e)}', e)
    return g.local(e, f'({t}:{e} & ~({hi} | {lo})) | ({smax(e):#x} & {hi}) | ({smin_bits(e):#x} & {lo})', 'q')


def sat_unsigned_from_signed(g, t, e):
    hi = g.mask(f'{t} s> {umax(e)}', e)
    lo = g.mask(f'{t} s< 0', e)
    return g.local(e, f'({t}:{e} & ~({hi} | {lo})) | ({umax(e):#x} & {hi})', 'q')


def sat_unsigned(g, t, e):
    hi = g.mask(f'{t} > {umax(e)}', e)
    return g.local(e, f'({t}:{e} & ~{hi}) | ({umax(e):#x} & {hi})', 'q')


def fnan_quiet(g, x):
    return g.local(4, f'{x} | 0x00400000', 'n')


def fmaxmin(g, a, b, is_max):
    """AltiVec vmaxfp/vminfp: NaN in either operand gives that NaN (quieted, A first); -0 < +0."""
    gt = g.mask(f'{a} f> {b}' if is_max else f'{a} f< {b}', 4)
    r = g.blend(gt, a, b, 4)
    zeros = g.mask(f'({a} & 0x7fffffff) == 0 && ({b} & 0x7fffffff) == 0', 4)
    z = g.local(4, f'{a} & {b}' if is_max else f'{a} | {b}', 'z')
    r = g.blend(zeros, z, r, 4)
    nb = g.mask(f'nan({b})', 4)
    r = g.blend(nb, fnan_quiet(g, b), r, 4)
    na = g.mask(f'nan({a})', 4)
    return g.blend(na, fnan_quiet(g, a), r, 4)


def ftoint_signed(g, x, scale_field):
    """Float to signed 32-bit, scaled by 2^IMM, truncated toward zero, saturating; NaN -> 0."""
    s = g.local(8, '1', 'p')
    g.emit(f'{s} = {s} << {scale_field};')
    k = g.local(4, f'int2float({s})', 'k')
    v = g.local(4, f'{x} f* {k}', 'v')
    big = g.fconst(0x80000000)
    neg = g.local(4, f'f- {big}', 'k')
    hi = g.mask(f'{v} f>= {big}', 4)
    lo = g.mask(f'{v} f< {neg}', 4)
    nn = g.mask(f'nan({v})', 4)
    t = g.local(4, f'trunc({v})', 'i')
    return g.local(4, f'({t} & ~({hi} | {lo} | {nn})) | (0x7fffffff & {hi}) | (0x80000000 & {lo})', 'q')


def ftoint_unsigned(g, x, scale_field):
    s = g.local(8, '1', 'p')
    g.emit(f'{s} = {s} << {scale_field};')
    k = g.local(4, f'int2float({s})', 'k')
    v = g.local(4, f'{x} f* {k}', 'v')
    big = g.fconst(0x100000000)
    zero = g.fconst(0)
    hi = g.mask(f'{v} f>= {big}', 4)
    lo = g.mask(f'{v} f<= {zero}', 4)
    nn = g.mask(f'nan({v})', 4)
    t8 = g.local(8, f'trunc({v})', 'i')
    return g.local(4, f'({t8}:4 & ~({hi} | {lo} | {nn})) | (0xffffffff & {hi})', 'q')


def int_to_float(g, x, scale_field, signed):
    wide = g.local(8, f'sext({x})' if signed else f'zext({x})', 'x')
    f = g.local(4, f'int2float({wide})', 'f')
    s = g.local(8, '1', 'p')
    g.emit(f'{s} = {s} << {scale_field};')
    k = g.local(4, f'int2float({s})', 'k')
    return g.local(4, f'{f} f/ {k}', 'q')


def round_float(g, x, mode):
    """vrfin (nearest even), vrfiz (toward zero), vrfim (floor), vrfip (ceil); NaN and |x| >= 2^23 unchanged."""
    if mode == 'm':
        r = g.local(4, f'floor({x})', 'r')
    elif mode == 'p':
        r = g.local(4, f'ceil({x})', 'r')
    elif mode == 'z':
        neg = g.mask(f'{x} f< {g.fconst(0)}', 4)
        r = g.blend(neg, g.local(4, f'ceil({x})', 'c'), g.local(4, f'floor({x})', 'f'), 4)
    else:
        two23 = g.fconst(1 << 23)
        ax = g.local(4, f'abs({x})', 'a')
        y = g.local(4, f'({ax} f+ {two23}) f- {two23}', 'y')
        r = g.local(4, f'{y} | ({x} & 0x80000000)', 'r')
    big = g.mask(f'abs({x}) f>= {g.fconst(1 << 23)} || nan({x})', 4)
    return g.blend(big, x, r, 4)


def cr6_all(g, masks, e):
    """Record form: CR6 = all-true << 3 | all-false << 1."""
    allt = ' & '.join(masks)
    anyt = ' | '.join(masks)
    t = g.local(e, allt, 'u')
    o = g.local(e, anyt, 'o')
    g.emit(f'cr6 = (({t} == {umax(e):#x}) << 3) | (({o} == 0) << 1);')


# --- the bodies -------------------------------------------------------------------------------------------------
FLOAT_BIN = {'vaddfp': 'f+', 'vsubfp': 'f-', 'vaddfp128': 'f+', 'vsubfp128': 'f-', 'vmulfp128': 'f*'}
INT_MOD = {'vaddubm': (1, '+'), 'vadduhm': (2, '+'), 'vadduwm': (4, '+'), 'vsububm': (1, '-'), 'vsubuhm': (2, '-'), 'vsubuwm': (4, '-')}
INT_SAT = {'vaddsbs': (1, '+', 's'), 'vaddshs': (2, '+', 's'), 'vaddsws': (4, '+', 's'), 'vaddubs': (1, '+', 'u'), 'vadduhs': (2, '+', 'u'), 'vadduws': (4, '+', 'u'),
           'vsubsbs': (1, '-', 's'), 'vsubshs': (2, '-', 's'), 'vsubsws': (4, '-', 's'), 'vsububs': (1, '-', 'u'), 'vsubuhs': (2, '-', 'u'), 'vsubuws': (4, '-', 'u')}
AVG = {'vavgsb': (1, 's'), 'vavgsh': (2, 's'), 'vavgsw': (4, 's'), 'vavgub': (1, 'u'), 'vavguh': (2, 'u'), 'vavguw': (4, 'u')}
MAXMIN = {f'v{op}{s}{t}': (e, s, op) for op in ('max', 'min') for s in 'su' for t, e in (('b', 1), ('h', 2), ('w', 4))}
INT_CMP = {'vcmpequb': (1, '=='), 'vcmpequh': (2, '=='), 'vcmpequw': (4, '=='), 'vcmpgtub': (1, '>'), 'vcmpgtuh': (2, '>'), 'vcmpgtuw': (4, '>'),
           'vcmpgtsb': (1, 's>'), 'vcmpgtsh': (2, 's>'), 'vcmpgtsw': (4, 's>'), 'vcmpequw128': (4, '==')}
FLT_CMP = {'vcmpeqfp': 'f==', 'vcmpgefp': 'f>=', 'vcmpgtfp': 'f>', 'vcmpeqfp128': 'f==', 'vcmpgefp128': 'f>=', 'vcmpgtfp128': 'f>'}
SHIFTS = {'vslb': (1, '<<'), 'vslh': (2, '<<'), 'vslw': (4, '<<'), 'vsrb': (1, '>>'), 'vsrh': (2, '>>'), 'vsrw': (4, '>>'),
          'vsrab': (1, 's>>'), 'vsrah': (2, 's>>'), 'vsraw': (4, 's>>'), 'vrlb': (1, 'rot'), 'vrlh': (2, 'rot'), 'vrlw': (4, 'rot'),
          'vslw128': (4, '<<'), 'vsrw128': (4, '>>'), 'vsraw128': (4, 's>>'), 'vrlw128': (4, 'rot')}
MERGE = {'vmrghb': (1, 'h'), 'vmrghh': (2, 'h'), 'vmrghw': (4, 'h'), 'vmrglb': (1, 'l'), 'vmrglh': (2, 'l'), 'vmrglw': (4, 'l'), 'vmrghw128': (4, 'h'), 'vmrglw128': (4, 'l')}
PACK = {'vpkuhum': (2, 'um'), 'vpkuwum': (4, 'um'), 'vpkuhus': (2, 'uu'), 'vpkuwus': (4, 'uu'), 'vpkshus': (2, 'su'), 'vpkswus': (4, 'su'),
        'vpkshss': (2, 'ss'), 'vpkswss': (4, 'ss')}
PACK.update({k + '128': v for k, v in list(PACK.items())})
UNPACK = {'vupkhsb': (1, 'h'), 'vupkhsh': (2, 'h'), 'vupklsb': (1, 'l'), 'vupklsh': (2, 'l'), 'vupkhsb128': (1, 'h'), 'vupklsb128': (1, 'l')}
LOGIC = {'vand': '{A} & {B}', 'vandc': '{A} & ~{B}', 'vor': '{A} | {B}', 'vxor': '{A} ^ {B}', 'vnor': '~({A} | {B})', 'vnor128': '~({A} | {B})'}


def body(name, ops):
    rc = name.endswith('.')
    base = name[:-1] if rc else name
    if base in OPAQUE:
        return None
    g = G()
    D = ops.get('D')
    if base in LOGIC:
        a, b = g.copy(ops['A']), g.copy(ops['B'])
        g.emit(f'{D} = {LOGIC[base].format(A=a, B=b)};')
        return g.lines
    if base in FLOAT_BIN:
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        r = [g.local(4, f'{x} {FLOAT_BIN[base]} {y}', 'f') for x, y in zip(a, b)]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vmaddfp', 'vnmsubfp', 'vmaddfp128', 'vmaddcfp128', 'vnmsubfp128'):
        # (multiplicand, multiplier, addend) roles; VMX128 forms reuse VD as an operand.
        roles = {'vmaddfp': ('A', 'C', 'B'), 'vnmsubfp': ('A', 'C', 'B'), 'vmaddfp128': ('A', 'B', 'D'), 'vmaddcfp128': ('A', 'D', 'B'), 'vnmsubfp128': ('A', 'B', 'D')}[base]
        x, y, z = (g.lanes(g.copy(ops[r]), 4) for r in roles)
        out = []
        for p, q, s in zip(x, y, z):
            m = g.local(4, f'{p} f* {q}', 'f')
            out.append(g.local(4, f'f- ({m} f- {s})' if 'nmsub' in base else f'{m} f+ {s}', 'f'))
        g.emit(f'{D} = {g.pack(out, 4)};')
        return g.lines
    if base in ('vmsum3fp128', 'vmsum4fp128'):
        n = 3 if '3' in base else 4
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        p = [g.local(4, f'{a[i]} f* {b[i]}', 'f') for i in range(n)]
        s = p[0]
        for x in p[1:]:
            s = g.local(4, f'{s} f+ {x}', 'f')
        g.emit(f'{D} = {g.pack([s] * 4, 4)};')
        return g.lines
    if base in FLT_CMP or base in ('vcmpbfp', 'vcmpbfp128'):
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        if base.startswith('vcmpbfp'):
            ms = []
            for x, y in zip(a, b):
                le = g.mask(f'!({x} f<= {y})', 4)
                ge = g.mask(f'!({x} f>= (f- {y}))', 4)
                ms.append(g.local(4, f'({le} & 0x80000000) | ({ge} & 0x40000000)', 'c'))
            g.emit(f'{D} = {g.pack(ms, 4)};')
            if rc:
                o = g.local(4, ' | '.join(ms), 'o')
                g.emit(f'cr6 = ({o} == 0) << 1;')
            return g.lines
        ms = [g.mask(f'{x} {FLT_CMP[base]} {y}', 4) for x, y in zip(a, b)]
        g.emit(f'{D} = {g.pack(ms, 4)};')
        if rc:
            cr6_all(g, ms, 4)
        return g.lines
    if base in INT_CMP:
        e, op = INT_CMP[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        ms = [g.mask(f'{x} {op} {y}', e) for x, y in zip(a, b)]
        g.emit(f'{D} = {g.pack(ms, e)};')
        if rc:
            cr6_all(g, ms, e)
        return g.lines
    if base in ('vmaxfp', 'vminfp', 'vmaxfp128', 'vminfp128'):
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        r = [fmaxmin(g, x, y, 'max' in base) for x, y in zip(a, b)]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vexptefp', 'vlogefp', 'vexptefp128', 'vlogefp128'):
        op = 'exp2_estimate' if 'expte' in base else 'log2_estimate'
        b = g.lanes(g.copy(ops['B']), 4)
        g.emit(f'{D} = {g.pack([g.local(4, f"{op}({x})", "f") for x in b], 4)};')
        return g.lines
    if base in ('vrefp', 'vrsqrtefp', 'vrefp128', 'vrsqrtefp128'):
        b = g.lanes(g.copy(ops['B']), 4)
        one = g.fconst(1)
        r = [g.local(4, f'{one} f/ sqrt({x})' if 'sqrt' in base else f'{one} f/ {x}', 'f') for x in b]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base.rstrip('128') in ('vrfin', 'vrfiz', 'vrfim', 'vrfip') or base in ('vrfin128', 'vrfiz128', 'vrfim128', 'vrfip128'):
        mode = base[4]
        b = g.lanes(g.copy(ops['B']), 4)
        r = [round_float(g, x, mode) for x in b]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vcfsx', 'vcfux', 'vcsxwfp128', 'vcuxwfp128'):
        b = g.lanes(g.copy(ops['B']), 4)
        r = [int_to_float(g, x, ops['IMM'], base in ('vcfsx', 'vcsxwfp128')) for x in b]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vctsxs', 'vctuxs', 'vcfpsxws128', 'vcfpuxws128'):
        b = g.lanes(g.copy(ops['B']), 4)
        f = ftoint_signed if base in ('vctsxs', 'vcfpsxws128') else ftoint_unsigned
        r = [f(g, x, ops['IMM']) for x in b]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in INT_MOD:
        e, op = INT_MOD[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        g.emit(f'{D} = {g.pack([g.local(e, f"{x} {op} {y}", "i") for x, y in zip(a, b)], e)};')
        return g.lines
    if base in INT_SAT:
        e, op, s = INT_SAT[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        ext = 'sext' if s == 's' else 'zext'
        r = []
        for x, y in zip(a, b):
            t = g.local(8, f'{ext}({x}) {op} {ext}({y})', 'w')
            r.append(sat_signed(g, t, e) if s == 's' else sat_unsigned_from_signed(g, t, e))
        g.emit(f'{D} = {g.pack(r, e)};')
        return g.lines
    if base in ('vaddcuw', 'vsubcuw'):
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        r = [g.local(4, f'zext(carry({x}, {y}))' if base == 'vaddcuw' else f'zext({x} >= {y})', 'c') for x, y in zip(a, b)]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in AVG:
        e, s = AVG[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        ext, sh = ('sext', 's>>') if s == 's' else ('zext', '>>')
        r = []
        for x, y in zip(a, b):
            s = g.local(8, f'{ext}({x})', 'x')
            g.emit(f'{s} = ({s} + {ext}({y}) + 1) {sh} 1;')
            r.append(g.local(e, f'{s}:{e}', 'v'))
        g.emit(f'{D} = {g.pack(r, e)};')
        return g.lines
    if base in MAXMIN:
        e, s, op = MAXMIN[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        cmp = ('s>' if s == 's' else '>') if op == 'max' else ('s<' if s == 's' else '<')
        r = [g.blend(g.mask(f'{x} {cmp} {y}', e), x, y, e) for x, y in zip(a, b)]
        g.emit(f'{D} = {g.pack(r, e)};')
        return g.lines
    if base in ('vmulesb', 'vmulesh', 'vmuleub', 'vmuleuh', 'vmulosb', 'vmulosh', 'vmuloub', 'vmulouh'):
        e = 1 if base[-1] == 'b' else 2
        ext = 'sext' if base[5] == 's' else 'zext'
        odd = base[4] == 'o'
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        r = []
        for i in range(1 if odd else 0, len(a), 2):
            x = g.local(2 * e, f'{ext}({a[i]})', 'x')
            y = g.local(2 * e, f'{ext}({b[i]})', 'y')
            r.append(g.local(2 * e, f'{x} * {y}', 'p'))
        g.emit(f'{D} = {g.pack(r, 2 * e)};')
        return g.lines
    if base in ('vmsumubm', 'vmsummbm', 'vmsumuhm', 'vmsumuhs', 'vmsumshm', 'vmsumshs'):
        e = 1 if base in ('vmsumubm', 'vmsummbm') else 2
        a, b, c = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e), g.lanes(g.copy(ops['C']), 4)
        ea, eb = {'vmsumubm': ('zext', 'zext'), 'vmsummbm': ('sext', 'zext'), 'vmsumuhm': ('zext', 'zext'), 'vmsumuhs': ('zext', 'zext'),
                  'vmsumshm': ('sext', 'sext'), 'vmsumshs': ('sext', 'sext')}[base]
        per = 4 // e
        r = []
        for w in range(4):
            cext = 'sext' if base in ('vmsumshs', 'vmsummbm', 'vmsumshm') else 'zext'
            t = g.local(8, '0', 'w')
            for j in range(per):
                pa = g.local(8, f'{ea}({a[w * per + j]})', 'x')
                pb = g.local(8, f'{eb}({b[w * per + j]})', 'y')
                g.emit(f'{t} = {t} + ({pa} * {pb});')
            g.emit(f'{t} = {t} + {cext}({c[w]});')
            if base.endswith('s'):
                r.append(sat_signed(g, t, 4) if base == 'vmsumshs' else sat_unsigned(g, t, 4))
            else:
                r.append(g.local(4, f'{t}:4', 'v'))
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vmhaddshs', 'vmhraddshs'):
        a, b, c = g.lanes(g.copy(ops['A']), 2), g.lanes(g.copy(ops['B']), 2), g.lanes(g.copy(ops['C']), 2)
        r = []
        for x, y, z in zip(a, b, c):
            p = g.local(8, f'sext({x}) * sext({y})', 'p')
            if base == 'vmhraddshs':
                g.emit(f'{p} = {p} + 0x4000;')
            t = g.local(8, f'({p} s>> 15) + sext({z})', 'w')
            r.append(sat_signed(g, t, 2))
        g.emit(f'{D} = {g.pack(r, 2)};')
        return g.lines
    if base == 'vmladduhm':
        a, b, c = g.lanes(g.copy(ops['A']), 2), g.lanes(g.copy(ops['B']), 2), g.lanes(g.copy(ops['C']), 2)
        g.emit(f'{D} = {g.pack([g.local(2, f"({x} * {y}) + {z}", "v") for x, y, z in zip(a, b, c)], 2)};')
        return g.lines
    if base in ('vsum4ubs', 'vsum4sbs', 'vsum4shs'):
        e = 2 if base == 'vsum4shs' else 1
        ext = 'zext' if base == 'vsum4ubs' else 'sext'
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), 4)
        per = 4 // e
        r = []
        for w in range(4):
            t = g.local(8, f'{ext}({b[w]})', 'w')
            for j in range(per):
                g.emit(f'{t} = {t} + {ext}({a[w * per + j]});')
            r.append(sat_unsigned(g, t, 4) if ext == 'zext' else sat_signed(g, t, 4))
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vsum2sws', 'vsumsws'):
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        zero = g.local(4, '0', 'z')
        if base == 'vsum2sws':
            r = []
            for k in (0, 2):
                t = g.local(8, f'sext({a[k]}) + sext({a[k + 1]}) + sext({b[k + 1]})', 'w')
                r += [zero, sat_signed(g, t, 4)]
        else:
            t = g.local(8, f'sext({a[0]}) + sext({a[1]}) + sext({a[2]}) + sext({a[3]}) + sext({b[3]})', 'w')
            r = [zero, zero, zero, sat_signed(g, t, 4)]
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base in ('vsel', 'vsel128'):
        a, b = g.copy(ops['A']), g.copy(ops['B'])
        c = g.copy(ops['C'] if base == 'vsel' else ops['D'])
        g.emit(f'{D} = ({a} & ~{c}) | ({b} & {c});')
        return g.lines
    if base in ('vperm', 'vperm128'):
        a, b, c = g.copy(ops['A']), g.copy(ops['B']), g.copy(ops['C'])
        idx = g.lanes(c, 1)
        out = []
        for i in range(16):
            j = g.local(1, f'{idx[i]} & 31', 'j')
            # byte j of A||B (j < 16 from A); byte k of a 16-byte value v is (v >> ((15-k)*8)):1
            fa = g.byte_lane(a, f'{j} & 15', 1)
            fb = g.byte_lane(b, f'{j} & 15', 1)
            m = g.mask(f'{j} < 16', 1)
            out.append(g.blend(m, fa, fb, 1))
        g.emit(f'{D} = {g.pack(out, 1)};')
        return g.lines
    if base in ('vsldoi', 'vsldoi128'):
        a, b = g.copy(ops['A']), g.copy(ops['B'])
        t = g.local(32, f'(zext({a}) << 128) | zext({b})', 'c')
        g.emit(f'{t} = {t} << ({ops["IMM"]} * 8);')
        g.emit(f'{D} = {t}(16);')
        return g.lines
    if base in MERGE:
        e, half = MERGE[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        n = 16 // e
        start = 0 if half == 'h' else n // 2
        out = []
        for i in range(start, start + n // 2):
            out += [a[i], b[i]]
        g.emit(f'{D} = {g.pack(out, e)};')
        return g.lines
    if base in ('vspltb', 'vsplth', 'vspltw', 'vspltw128'):
        e = {'vspltb': 1, 'vsplth': 2}.get(base, 4)
        n = 16 // e
        src = g.copy(ops['B'])
        lane = g.local(1, f'{ops["IMM"]} & {n - 1}', 'l')
        v = g.byte_lane(src, lane, e)
        g.emit(f'{D} = {g.pack([v] * n, e)};')
        return g.lines
    if base in ('vspltisb', 'vspltish', 'vspltisw', 'vspltisw128'):
        e = {'vspltisb': 1, 'vspltish': 2}.get(base, 4)
        v = g.local(e, f'sext({ops["IMM"]}:1)', 'v')
        g.emit(f'{D} = {g.pack([v] * (16 // e), e)};')
        return g.lines
    if base in SHIFTS:
        e, op = SHIFTS[base]
        bits = 8 * e
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        r = []
        for x, y in zip(a, b):
            s = g.local(e, f'{y} & {bits - 1}', 'n')
            if op == 'rot':
                r.append(g.local(e, f'({x} << {s}) | ({x} >> (({bits} - {s}) & {bits - 1}))', 'v'))
            else:
                r.append(g.local(e, f'{x} {op} {s}', 'v'))
        g.emit(f'{D} = {g.pack(r, e)};')
        return g.lines
    if base in ('vsl', 'vsr', 'vslo', 'vsro', 'vslo128', 'vsro128'):
        a, b = g.copy(ops['A']), g.copy(ops['B'])
        last = g.local(1, f'{b}:1', 'n')
        amount = g.local(1, f'{last} & 7' if base in ('vsl', 'vsr') else f'(({last} >> 3) & 15) * 8', 'n')
        g.emit(f'{D} = {a} {"<<" if "sl" in base else ">>"} {amount};')
        return g.lines
    if base in PACK:
        e, kind = PACK[base]
        a, b = g.lanes(g.copy(ops['A']), e), g.lanes(g.copy(ops['B']), e)
        h = e // 2
        r = []
        for x in a + b:
            if kind == 'um':
                r.append(g.local(h, f'{x}:{h}', 'v'))
            elif kind == 'uu':
                r.append(sat_unsigned(g, g.local(8, f'zext({x})', 'w'), h))
            elif kind == 'su':
                r.append(sat_unsigned_from_signed(g, g.local(8, f'sext({x})', 'w'), h))
            else:
                r.append(sat_signed(g, g.local(8, f'sext({x})', 'w'), h))
        g.emit(f'{D} = {g.pack(r, h)};')
        return g.lines
    if base in UNPACK:
        e, half = UNPACK[base]
        b = g.lanes(g.copy(ops['B']), e)
        part = b[:len(b) // 2] if half == 'h' else b[len(b) // 2:]
        r = [g.local(2 * e, f'sext({x})', 'v') for x in part]
        g.emit(f'{D} = {g.pack(r, 2 * e)};')
        return g.lines
    if base in ('vupkhpx', 'vupklpx'):
        b = g.lanes(g.copy(ops['B']), 2)
        part = b[:4] if base == 'vupkhpx' else b[4:]
        r = []
        for x in part:
            r.append(g.local(4, f'(zext(0 - ({x} >> 15)) << 24) | (zext(({x} >> 10) & 31) << 16) | (zext(({x} >> 5) & 31) << 8) | zext({x} & 31)', 'v'))
        g.emit(f'{D} = {g.pack(r, 4)};')
        return g.lines
    if base == 'vpkpx':
        a, b = g.lanes(g.copy(ops['A']), 4), g.lanes(g.copy(ops['B']), 4)
        r = []
        for x in a + b:
            w = g.local(4, f'((({x} >> 24) & 1) << 15) | ((({x} >> 19) & 31) << 10) | ((({x} >> 11) & 31) << 5) | (({x} >> 3) & 31)', 'w')
            r.append(g.local(2, f'{w}:2', 'v'))
        g.emit(f'{D} = {g.pack(r, 2)};')
        return g.lines
    # --- loads and stores ---------------------------------------------------------------------------------------
    if base in ('lvx', 'lvxl', 'lvx128', 'lvxl128', 'stvx', 'stvxl', 'stvx128', 'stvxl128'):
        ea = g.local(8, f'({ops["RA"]} + {ops["RB"]}) & 0x00000000fffffff0', 'a')
        if base.startswith('lv'):
            g.emit(f'{D} = *[ram]:16 {ea};')
        else:
            g.emit(f'*[ram]:16 {ea} = {D};')
        return g.lines
    if base in ('lvebx', 'lvehx', 'lvewx', 'lvewx128'):
        e = {'lvebx': 1, 'lvehx': 2}.get(base, 4)
        ea = g.local(8, f'({ops["RA"]} + {ops["RB"]}) & 0x00000000{(0xffffffff & ~(e - 1)):08x}', 'a')
        v = g.local(e, f'*[ram]:{e} {ea}', 'v')
        sh = g.local(1, f'(15 - ({ea}:1 & 15)) - {e - 1}', 'n')
        g.emit(f'{D} = zext({v}) << ({sh} * 8);')
        return g.lines
    if base in ('stvebx', 'stvehx', 'stvewx', 'stvewx128'):
        e = {'stvebx': 1, 'stvehx': 2}.get(base, 4)
        # Read-modify-write of the aligned 16-byte block: the element keeps its byte position, so no lane shift of
        # the value is needed. (An e-byte store of a shifted register made the decompiler fail with "Duplicate
        # VariablePiece" on 0x82740ac8; the memory effect is the same.)
        ea = g.local(8, f'{ops["RA"]} + {ops["RB"]}', 'a')
        block = g.local(8, f'{ea} & 0x00000000fffffff0', 'a')
        pos = g.local(1, f'{ea}:1 & {16 - e}', 'n')
        m = g.local(16, f'{(1 << (8 * e)) - 1:#x}', 'm')
        g.emit(f'{m} = {m} << (({16 - e} - {pos}) * 8);')
        old = g.local(16, f'*[ram]:16 {block}', 'o')
        g.emit(f'*[ram]:16 {block} = ({old} & ~{m}) | ({D} & {m});')
        return g.lines
    if base in ('lvsl', 'lvsr', 'lvsl128', 'lvsr128'):
        ea = g.local(8, f'{ops["RA"]} + {ops["RB"]}', 'a')
        sh = g.local(1, f'{ea}:1 & 15', 'n')
        start = sh if base.startswith('lvsl') else g.local(1, f'16 - {sh}', 'n')
        bytes_ = [g.local(1, f'{start} + {i}', 'v') for i in range(16)]
        g.emit(f'{D} = {g.pack(bytes_, 1)};')
        return g.lines
    if base == 'mfvscr':
        g.emit(f'{D} = zext(vscr);')
        return g.lines
    if base == 'mtvscr':
        g.emit(f'vscr = {ops["B"]}:4;')
        return g.lines
    raise KeyError(name)


# AltiVec mnemonics (the original set kept in the language) whose bodies come from here; stream hints stay as they are.
ALTIVEC_HINTS = {'dss', 'dssall', 'dst', 'dstt', 'dstst', 'dststt'}
