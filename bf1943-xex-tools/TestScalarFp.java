// Scalar floating-point p-code of the Xenon language: every scalar FP mnemonic BF1943 uses is encoded, emulated and
// compared with a Java model of the PowerPC definition over random and special operands, plus hand-computed
// literal cases (fctiw/fctid rounding and saturation, fsel with NaN and -0, fused-looking chains, loads/stores).
// Writes scalar-fp-validation.txt (last line "PASS COMPLETE ..." only when everything passed).
import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.PcodeOp;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;

public class TestScalarFp extends GhidraScript {
    void check(boolean b,String why)throws Exception{if(!b)throw new Exception(why);}
    // Write one instruction word to the program (fresh listing) and to the emulator's memory, which caches pages.
    void put(EmulatorHelper e,long at,int word)throws Exception{
        clearListing(toAddr(at),toAddr(at+3));setInt(toAddr(at),word);
        e.writeMemory(toAddr(at),new byte[]{(byte)(word>>>24),(byte)(word>>>16),(byte)(word>>>8),(byte)word});
    }
    interface Op{double f(double a,double b,double c);}
    record Spec(String name,int op,int xo,char form,Op ref,boolean lowWord){}
    static double sgl(double x){return (double)(float)x;}
    static long rne(double x){return (long)Math.rint(x);}
    static double satw(double x,boolean nearest){
        if(Double.isNaN(x))return Double.longBitsToDouble(0x80000000L);
        double r=nearest?Math.rint(x):(x<0?Math.ceil(x):Math.floor(x));
        long v=r>=2147483648.0?0x7fffffffL:r<-2147483648.0?0x80000000L:((long)r)&0xffffffffL;return Double.longBitsToDouble(v);}
    static double satd(double x,boolean nearest){
        if(Double.isNaN(x))return Double.longBitsToDouble(0x8000000000000000L);
        double r=nearest?Math.rint(x):(x<0?Math.ceil(x):Math.floor(x));
        long v=r>=9.223372036854775807E18?0x7fffffffffffffffL:r<-9.223372036854775807E18?0x8000000000000000L:(long)r;return Double.longBitsToDouble(v);}
    List<Spec> specs(){
        List<Spec> s=new ArrayList<>();
        // A-form: op 63 double, op 59 single. form: 'A' (fD,fA,fB), 'M' (fD,fA,fC), 'F' (fD,fA,fC,fB), 'B' (fD,fB), 'X' (X-form fD,fB)
        s.add(new Spec("fadd",63,21,'A',(a,b,c)->a+b,false));s.add(new Spec("fadds",59,21,'A',(a,b,c)->sgl(a+b),false));
        s.add(new Spec("fsub",63,20,'A',(a,b,c)->a-b,false));s.add(new Spec("fsubs",59,20,'A',(a,b,c)->sgl(a-b),false));
        s.add(new Spec("fmul",63,25,'M',(a,b,c)->a*c,false));s.add(new Spec("fmuls",59,25,'M',(a,b,c)->sgl(a*c),false));
        s.add(new Spec("fdiv",63,18,'A',(a,b,c)->a/b,false));s.add(new Spec("fdivs",59,18,'A',(a,b,c)->sgl(a/b),false));
        s.add(new Spec("fmadd",63,29,'F',(a,b,c)->a*c+b,false));s.add(new Spec("fmadds",59,29,'F',(a,b,c)->sgl(a*c+b),false));
        s.add(new Spec("fmsub",63,28,'F',(a,b,c)->a*c-b,false));s.add(new Spec("fmsubs",59,28,'F',(a,b,c)->sgl(a*c-b),false));
        s.add(new Spec("fnmadd",63,31,'F',(a,b,c)->-(a*c+b),false));s.add(new Spec("fnmadds",59,31,'F',(a,b,c)->-sgl(a*c+b),false));
        s.add(new Spec("fnmsub",63,30,'F',(a,b,c)->-(a*c-b),false));s.add(new Spec("fnmsubs",59,30,'F',(a,b,c)->-sgl(a*c-b),false));
        s.add(new Spec("fsel",63,23,'F',(a,b,c)->a>=0?c:b,false));
        s.add(new Spec("fsqrt",63,22,'B',(a,b,c)->Math.sqrt(b),false));s.add(new Spec("fsqrts",59,22,'B',(a,b,c)->sgl(Math.sqrt(b)),false));
        s.add(new Spec("fres",59,24,'B',(a,b,c)->sgl(1.0/b),false));s.add(new Spec("frsqrte",63,26,'B',(a,b,c)->1.0/Math.sqrt(b),false));
        s.add(new Spec("fabs",63,264,'X',(a,b,c)->Double.longBitsToDouble(Double.doubleToRawLongBits(b)&0x7fffffffffffffffL),false));
        s.add(new Spec("fnabs",63,136,'X',(a,b,c)->Double.longBitsToDouble(Double.doubleToRawLongBits(b)|0x8000000000000000L),false));
        s.add(new Spec("fneg",63,40,'X',(a,b,c)->Double.longBitsToDouble(Double.doubleToRawLongBits(b)^0x8000000000000000L),false));
        s.add(new Spec("fmr",63,72,'X',(a,b,c)->b,false));
        s.add(new Spec("frsp",63,12,'X',(a,b,c)->sgl(b),false));
        s.add(new Spec("fctiw",63,14,'X',(a,b,c)->satw(b,true),true));s.add(new Spec("fctiwz",63,15,'X',(a,b,c)->satw(b,false),true));
        s.add(new Spec("fctid",63,814,'X',(a,b,c)->satd(b,true),false));s.add(new Spec("fctidz",63,815,'X',(a,b,c)->satd(b,false),false));
        s.add(new Spec("fcfid",63,846,'X',(a,b,c)->(double)Double.doubleToRawLongBits(b),false));
        return s;
    }
    int encode(Spec s,int d,int a,int b,int c){
        int w=(s.op()<<26)|(d<<21);
        return switch(s.form()){
            case 'A'->w|(a<<16)|(b<<11)|(s.xo()<<1);
            case 'M'->w|(a<<16)|(c<<6)|(s.xo()<<1);
            case 'F'->w|(a<<16)|(b<<11)|(c<<6)|(s.xo()<<1);
            case 'B'->w|(b<<11)|(s.xo()<<1);
            default->w|(b<<11)|(s.xo()<<1);};
    }
    static final double[] SPECIAL={0.0,-0.0,1.0,-1.0,0.5,1.5,2.5,-2.5,-0.5,3.5,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NaN,
        2147483647.0,2147483647.5,2147483648.0,-2147483648.0,-2147483648.5,-2147483649.0,4294967296.0,1e300,-1e300,1e-310,Double.MIN_NORMAL,
        9.223372036854775807E18,-9.223372036854775808E18,1.0e19,-1.0e19,4503599627370496.5,4503599627370497.0,0.49999999999999994,
        (double)Float.MAX_VALUE,(double)Float.MIN_VALUE,3.4028235677973366E38,1.0000001192092896};
    double value(Random r,boolean intBits){
        int m=r.nextInt(5);
        if(m==0)return SPECIAL[r.nextInt(SPECIAL.length)];
        if(m==1&&intBits)return Double.longBitsToDouble(r.nextLong()>>r.nextInt(64));
        if(m==2)return (double)(float)((r.nextDouble()*2-1)*Math.pow(2,r.nextInt(60)-30));
        if(m==3)return Math.rint((r.nextDouble()*2-1)*Math.pow(2,r.nextInt(70)))+(r.nextInt(4)*0.25);
        return (r.nextDouble()*2-1)*Math.pow(10,r.nextInt(20)-10);
    }
    boolean same(double got,double want,boolean lowWord){
        long g=Double.doubleToRawLongBits(got),w=Double.doubleToRawLongBits(want);
        if(lowWord)return (g&0xffffffffL)==(w&0xffffffffL);
        if(Double.isNaN(want))return Double.isNaN(got);
        return g==w;
    }
    BigInteger bits(double v){return new BigInteger(Long.toUnsignedString(Double.doubleToRawLongBits(v)));}
    double dbl(BigInteger v){return Double.longBitsToDouble(v.longValue());}

    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Path out=root.resolve("scalar-fp-validation.txt");Files.writeString(out,"RUNNING\n");
        long base=0x83700000L;
        if(currentProgram.getMemory().getBlock(toAddr(base))==null)currentProgram.getMemory().createInitializedBlock("scalar_fp_test",toAddr(base),0x40000,(byte)0,monitor,false);
        clearListing(toAddr(base),toAddr(base+0x3ffff));
        EmulatorHelper e=new EmulatorHelper(currentProgram);
        Random r=new Random(1943);long at=base;int total=0,literal=0;StringBuilder rep=new StringBuilder();
        try{
            List<Spec> specs=specs();
            // Literal cases (hand-computed): {name, a, b, c, expected bits}
            Object[][] lits={
                {"fctiw",0.0,2.5,0.0,2L},{"fctiw",0.0,3.5,0.0,4L},{"fctiw",0.0,-2.5,0.0,0xfffffffeL},{"fctiw",0.0,-0.5,0.0,0L},{"fctiw",0.0,1e10,0.0,0x7fffffffL},
                {"fctiw",0.0,Double.NaN,0.0,0x80000000L},{"fctiwz",0.0,2.9,0.0,2L},{"fctiwz",0.0,-2.9,0.0,0xfffffffeL},{"fctiwz",0.0,-3e9,0.0,0x80000000L},
                {"fctiwz",0.0,2147483647.9,0.0,0x7fffffffL},{"fctiwz",0.0,-2147483648.9,0.0,0x80000000L},
                {"fctidz",0.0,-7.75,0.0,0xfffffffffffffff9L},{"fctid",0.0,-7.5,0.0,0xfffffffffffffff8L},{"fctid",0.0,1e19,0.0,0x7fffffffffffffffL},{"fctidz",0.0,Double.NaN,0.0,0x8000000000000000L},
                {"fsel",-0.0,1.0,2.0,Double.doubleToRawLongBits(2.0)},{"fsel",Double.NaN,1.0,2.0,Double.doubleToRawLongBits(1.0)},{"fsel",-1e-300,1.0,2.0,Double.doubleToRawLongBits(1.0)},
                {"fmadds",1.5,0.25,4.0,Double.doubleToRawLongBits(6.25)},{"fnmsubs",1.5,0.25,4.0,Double.doubleToRawLongBits(-5.75)},{"fnmadd",2.0,1.0,3.0,Double.doubleToRawLongBits(-7.0)},
                {"fmuls",3.0,0.0,0.1,Double.doubleToRawLongBits((double)0.3f)},{"frsp",0.0,0.1,0.0,Double.doubleToRawLongBits((double)0.1f)},
                {"fcfid",0.0,Double.longBitsToDouble(0xfffffffffffffffdL),0.0,Double.doubleToRawLongBits(-3.0)},{"fres",0.0,4.0,0.0,Double.doubleToRawLongBits(0.25)},
                {"frsqrte",0.0,16.0,0.0,Double.doubleToRawLongBits(0.25)},{"fabs",0.0,-0.0,0.0,0L},{"fnabs",0.0,3.0,0.0,Double.doubleToRawLongBits(-3.0)}};
            for(Object[] l:lits){
                Spec s=specs.stream().filter(x->x.name().equals(l[0])).findFirst().orElseThrow();
                double got=step(e,s,at,5,6,7,8,(double)l[1],(double)l[2],(double)l[3]);at+=4;
                long g=Double.doubleToRawLongBits(got),w=(long)l[4];
                check(s.lowWord()?(g&0xffffffffL)==(w&0xffffffffL):g==w,"literal "+l[0]+"("+l[1]+","+l[2]+","+l[3]+") got "+Long.toHexString(g)+" want "+Long.toHexString(w));
                check(same(s.ref().f((double)l[1],(double)l[2],(double)l[3]),Double.longBitsToDouble(w),s.lowWord()),"model "+l[0]+" disagrees with its literal");literal++;
            }
            for(Spec s:specs){
                int n=0;
                for(int k=0;k<200;k++){
                    int d=r.nextInt(32),a=r.nextInt(32),b=r.nextInt(32),c=r.nextInt(32);
                    if(k%7==1)a=d;if(k%7==2)b=d;if(k%7==3)c=a;
                    double av=value(r,false),bv=value(r,s.name().equals("fcfid")),cv=value(r,false);
                    if(s.name().endsWith("s")&&!s.name().equals("fabs")&&!s.name().equals("fres")&&!s.name().equals("fsqrts")){av=(double)(float)av;bv=(double)(float)bv;cv=(double)(float)cv;}
                    Map<Integer,Double> v=new HashMap<>();v.put(a,av);v.put(b,bv);v.put(c,cv);av=v.get(a);bv=v.get(b);cv=v.get(c);
                    double want=s.ref().f(av,bv,cv);
                    double got=step(e,s,at,d,a,b,c,av,bv,cv);at+=4;
                    check(same(got,want,s.lowWord()),s.name()+" a="+av+" b="+bv+" c="+cv+" got "+got+" ("+Long.toHexString(Double.doubleToRawLongBits(got))+") want "+want+" ("+Long.toHexString(Double.doubleToRawLongBits(want))+")");
                    n++;
                }
                total+=n;rep.append(s.name()).append('=').append(n).append(' ');
            }
            // fcmpu: CR field LT/GT/EQ/UN.
            double[] cmp={1.0,2.0,-0.0,0.0,Double.NaN,Double.NEGATIVE_INFINITY};
            for(int crf=0;crf<8;crf++)for(double x:cmp)for(double y:cmp){
                int word=(63<<26)|(crf<<23)|(6<<16)|(7<<11);
                put(e,at,word);disassemble(toAddr(at));Instruction ins=getInstructionAt(toAddr(at));check(ins!=null&&ins.getMnemonicString().equals("fcmpu"),"decode fcmpu");
                e.writeRegister("f6",bits(x));e.writeRegister("f7",bits(y));e.writeRegister("crall",0);e.writeRegister(e.getPCRegister(),at);check(e.step(monitor),"fcmpu");at+=4;
                int want=Double.isNaN(x)||Double.isNaN(y)?1:x<y?8:x>y?4:2;
                check(e.readRegister("cr"+crf).intValue()==want,"fcmpu cr"+crf+" "+x+" "+y+" got "+e.readRegister("cr"+crf));total++;
            }
            // Loads and stores: lfs widens, stfs narrows, stfiwx stores the low word, lfd/stfd move bits.
            long data=0x83780000L;
            if(currentProgram.getMemory().getBlock(toAddr(data))==null)currentProgram.getMemory().createInitializedBlock("scalar_fp_data",toAddr(data),0x100,(byte)0,monitor,false);
            Object[][] ls={{"lfs",(48<<26)|(5<<21)|(3<<16)|8},{"lfd",(50<<26)|(5<<21)|(3<<16)|8},{"stfs",(52<<26)|(5<<21)|(3<<16)|8},{"stfd",(54<<26)|(5<<21)|(3<<16)|8},
                {"stfiwx",(31<<26)|(5<<21)|(3<<16)|(4<<11)|(983<<1)},{"lfsx",(31<<26)|(5<<21)|(3<<16)|(4<<11)|(535<<1)}};
            for(Object[] l:ls){
                put(e,at,(int)l[1]);disassemble(toAddr(at));check(getInstructionAt(toAddr(at)).getMnemonicString().equals(l[0]),"decode "+l[0]);
                e.writeMemory(toAddr(data),new byte[32]);e.writeMemory(toAddr(data+8),new byte[]{0x3f,(byte)0xc0,0,0,0,0,0,0});
                e.writeRegister("r3",data);e.writeRegister("r4",8);e.writeRegister("f5",bits(-2.5));e.writeRegister(e.getPCRegister(),at);check(e.step(monitor),(String)l[0]);at+=4;
                byte[] m=e.readMemory(toAddr(data+8),8);double f5=dbl(e.readRegister("f5"));
                switch((String)l[0]){
                    case "lfs","lfsx"->check(f5==1.5,l[0]+" "+f5);
                    case "lfd"->check(Double.doubleToRawLongBits(f5)==0x3fc0000000000000L,"lfd");
                    case "stfs"->check(Arrays.equals(m,new byte[]{(byte)0xc0,0x20,0,0,0,0,0,0}),"stfs "+HexFormat.of().formatHex(m));
                    case "stfd"->check(Arrays.equals(m,new byte[]{(byte)0xc0,0x04,0,0,0,0,0,0}),"stfd "+HexFormat.of().formatHex(m));
                    case "stfiwx"->check(Arrays.equals(m,new byte[]{0,0,0,0,0,0,0,0}),"stfiwx "+HexFormat.of().formatHex(m));
                }
                total++;literal++;
            }
        }finally{e.dispose();}
        Files.writeString(out,rep.toString().trim()+"\nPASS COMPLETE scalar_fp_cases="+total+" literal_cases="+literal+"; random, special and aliased operands; fcmpu all CR fields; loads/stores; fctiw/fctiwz check the low word (the high word is architecturally undefined)\n");
    }
    double step(EmulatorHelper e,Spec s,long at,int d,int a,int b,int c,double av,double bv,double cv)throws Exception{
        int word=encode(s,d,a,b,c);put(e,at,word);disassemble(toAddr(at));
        Instruction ins=getInstructionAt(toAddr(at));
        check(ins!=null&&ins.getMnemonicString().equals(s.name()),"Decode "+s.name()+" "+Integer.toHexString(word)+" -> "+(ins==null?"none":ins.getMnemonicString()));
        for(PcodeOp op:ins.getPcode())check(op.getOpcode()!=PcodeOp.CALLOTHER,"Opaque op in "+s.name());
        // Unused operand fields first, so an alias with a used register keeps the used value.
        String used=switch(s.form()){case 'A'->"ab";case 'M'->"ac";case 'F'->"abc";default->"b";};
        for(String pass:new String[]{"unused","used"})for(char k:new char[]{'a','b','c'}){
            if((used.indexOf(k)>=0)!=pass.equals("used"))continue;
            int rn=k=='a'?a:k=='b'?b:c;double val=k=='a'?av:k=='b'?bv:cv;e.writeRegister("f"+rn,bits(val));}
        int guard=(d+1)%32;while(guard==a||guard==b||guard==c)guard=(guard+1)%32;
        e.writeRegister("f"+guard,bits(12345.678));
        e.writeRegister(e.getPCRegister(),at);check(e.step(monitor),s.name()+": "+e.getLastError());
        if(guard!=d)check(dbl(e.readRegister("f"+guard))==12345.678,s.name()+" changed f"+guard);
        return dbl(e.readRegister("f"+d));
    }
}
