"""The Xenon language's vector p-code generator (no game data, no Ghidra): every AltiVec and VMX128 mnemonic it
models produces a body; locals are unique, sized and clear of register names; the documented opaque set stays opaque;
the scalar-FP conversion bodies cover all eight forms; and the splicer keeps decoding headers.
(The emulated tests of the semantics themselves are TestVectorSemantics.java / TestScalarFp.java, run by run_vmx.ps1.)

  python -B test_vector_semantics.py
"""
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import xenon_splice  # noqa: E402
import xenon_vector_semantics as vsem  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


ALTIVEC = ('vaddfp vsubfp vmaddfp vnmsubfp vmaxfp vminfp vrefp vrsqrtefp vrfin vrfiz vrfim vrfip vcfsx vcfux vctsxs vctuxs '
           'vcmpbfp vcmpbfp. vcmpeqfp vcmpeqfp. vcmpgefp vcmpgefp. vcmpgtfp vcmpgtfp. vcmpequb vcmpequb. vcmpequh vcmpequw vcmpgtub vcmpgtuh '
           'vcmpgtuw vcmpgtsb vcmpgtsh vcmpgtsw vaddubm vadduhm vadduwm vsububm vsubuhm vsubuwm vaddsbs vaddshs vaddsws vaddubs vadduhs vadduws '
           'vsubsbs vsubshs vsubsws vsububs vsubuhs vsubuws vaddcuw vsubcuw vavgsb vavgsh vavgsw vavgub vavguh vavguw vmaxsb vmaxsh vmaxsw vmaxub '
           'vmaxuh vmaxuw vminsb vminsh vminsw vminub vminuh vminuw vmulesb vmulesh vmuleub vmuleuh vmulosb vmulosh vmuloub vmulouh vmsumubm vmsummbm '
           'vmsumuhm vmsumuhs vmsumshm vmsumshs vmhaddshs vmhraddshs vmladduhm vsum4ubs vsum4sbs vsum4shs vsum2sws vsumsws vand vandc vor vxor vnor '
           'vsel vperm vsldoi vmrghb vmrghh vmrghw vmrglb vmrglh vmrglw vspltb vsplth vspltw vspltisb vspltish vspltisw vslb vslh vslw vsrb vsrh vsrw '
           'vsrab vsrah vsraw vrlb vrlh vrlw vsl vsr vslo vsro vpkuhum vpkuwum vpkuhus vpkuwus vpkshus vpkswus vpkshss vpkswss vpkpx vupkhsb vupkhsh '
           'vupklsb vupklsh vupkhpx vupklpx lvebx lvehx lvewx lvsl lvsr lvx lvxl stvebx stvehx stvewx stvx stvxl mfvscr mtvscr').split()
VMX128 = ('vsldoi128 lvsl128 lvsr128 lvewx128 stvewx128 lvxl128 stvxl128 vperm128 vaddfp128 vsubfp128 vmulfp128 vmaddfp128 vmaddcfp128 vnmsubfp128 '
          'vmsum3fp128 vmsum4fp128 vpkshss128 vpkshus128 vpkswss128 vnor128 vpkswus128 vpkuhum128 vpkuhus128 vsel128 vpkuwum128 vslo128 vpkuwus128 '
          'vsro128 vcfpsxws128 vcfpuxws128 vcsxwfp128 vcuxwfp128 vrfim128 vrfin128 vrfip128 vrfiz128 vrefp128 vrsqrtefp128 vspltw128 vspltisw128 '
          'vcmpeqfp128 vcmpeqfp128. vrlw128 vcmpgefp128 vcmpgefp128. vslw128 vcmpgtfp128 vcmpgtfp128. vsraw128 vcmpbfp128 vcmpbfp128. vsrw128 '
          'vcmpequw128 vcmpequw128. vmaxfp128 vminfp128 vmrghw128 vmrglw128 vupkhsb128 vupklsb128').split()
REGISTER_LIKE = re.compile(r'^(r\d+|f\d+|v\d+|vs\d+|cr\d|ctr|lr|xer|pc)$')

if __name__ == '__main__':
    altivec_ops = dict(D='vrD', A='vrA', B='vrB', C='vrC', IMM='A_BITS', RA='RA_OR_ZERO', RB='B')
    vmx_ops = dict(D='XV', A='XA', B='XB', C='XC', IMM='A_BITS', RA='RA_OR_ZERO', RB='B')
    bad = []
    for names, ops in ((ALTIVEC, altivec_ops), (VMX128, vmx_ops)):
        for n in names:
            body = vsem.body(n, ops)
            if not body:
                bad.append(f'{n}: no body')
                continue
            text = '\n'.join(body)
            declared = re.findall(r'local (\w+):(\d+)', text)
            names_ = [d for d, _ in declared]
            if len(names_) != len(set(names_)):
                bad.append(f'{n}: duplicate local')
            if any(REGISTER_LIKE.match(d) for d in names_):
                bad.append(f'{n}: local named like a register')
            if 'None' in text or 'CALLOTHER' in text:
                bad.append(f'{n}: unresolved operand')
            dest = 'cr6' if False else ops['D']
            if not n.startswith('stv') and not n == 'mtvscr' and not any(l.startswith(dest + ' =') for l in body):
                bad.append(f'{n}: never writes {dest}')
            if n.endswith('.') and not any(l.startswith('cr6 =') for l in body):
                bad.append(f'{n}: record form does not write cr6')
    check(not bad, f'{len(ALTIVEC)} AltiVec and {len(VMX128)} VMX128 bodies: unique sized locals, destination written, CR6 for record forms ({"; ".join(bad[:5])})')
    check(all(vsem.body(n, vmx_ops) is None for n in ('vexptefp128', 'vlogefp128', 'vpkd3d128', 'vupkd3d128', 'vexptefp', 'vlogefp')),
          'estimate (2^x, log2) and D3D pack/unpack instructions stay opaque')
    fp = {n: xenon_splice.fp_convert_body(n) for n in xenon_splice.FP_CONVERT}
    check(len(fp) == 8 and all(any(l.startswith('fD = ') for l in b) for b in fp.values()) and all(('cr1flags();' in b) == n.endswith('.') for n, b in fp.items()),
          'fctiw/fctiwz/fctid/fctidz bodies: all eight forms write fD; record forms update CR1')
    check(any('c52' in l for l in fp['fctiw']) and not any('c52' in l for l in fp['fctiwz']), 'fctiw rounds to nearest even, fctiwz truncates')
    sample = (':vadduwm vrD,vrA,vrB\tis OP=4 & vrD & vrA & vrB & XOP_0_10=128\n    & vrA_32_0 & vrA_32_1\n{\n    vrD_32_0 = vrA_32_0;\n}\n'
              ':dss STRM\tis $(NOTVLE) & OP=31 & STRM & XOP_1_10=822 & Rc=0\n{\n\tdataStreamStop(STRM:1);\n}\n'
              ':lvewx vrD,A,B\tis OP=31 & vrD & A & B & XOP_1_10=71 & Rc=0\n{\n\tvrD = loadVectorElementWordIndexed(A,B);\n}\n')
    out, done = xenon_splice.rewrite_altivec(sample)
    check(done == ['vadduwm', 'lvewx'] and 'dataStreamStop(STRM:1);' in out and 'vrA_32_0' not in out and ':lvewx vrD,RA_OR_ZERO,B' in out
          and '& RA_OR_ZERO &' in out and out.startswith('define register offset=0x101000 size=4 [ vscr ];'),
          'splicer: stream hints kept; sub-piece operands dropped; vector loads take RA_OR_ZERO; VSCR defined')
    print('PASS' if not failures else f'FAIL {len(failures)}')
    sys.exit(1 if failures else 0)
