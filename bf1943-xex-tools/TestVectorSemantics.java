// Vector p-code semantics of the Xenon language (AltiVec/VMX and VMX128): every modelled mnemonic is encoded,
// emulated and compared with an independent Java model of the instruction definition, over random and special
// operands, every register bank and aliased operands; hand-computed literal vectors check the models themselves.
// Writes vector-validation.txt (last line "PASS COMPLETE ..." only when everything passed).
import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.pcode.memstate.MemoryState;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;
import java.io.*;

public class TestVectorSemantics extends GhidraScript {
    // ---- vector helpers: a vector is byte[16], byte 0 = guest lane 0 (most significant) ----
    static long lane(byte[] v,int e,int i,boolean s){long x=0;for(int k=0;k<e;k++)x=(x<<8)|(v[i*e+k]&0xff);if(s&&e<8){long sign=1L<<(8*e-1);x=(x^sign)-sign;}return x;}
    static byte[] lanes(long[] l,int e){byte[] v=new byte[16];for(int i=0;i<l.length;i++)for(int k=0;k<e;k++)v[i*e+k]=(byte)(l[i]>>>(8*(e-1-k)));return v;}
    static float fl(byte[] v,int i){return Float.intBitsToFloat((int)lane(v,4,i,false));}
    static byte[] fv(float... f){long[] l=new long[4];for(int i=0;i<4;i++)l[i]=Float.floatToRawIntBits(f[i])&0xffffffffL;return lanes(l,4);}
    static long satS(long x,int e){long max=(1L<<(8*e-1))-1,min=-(1L<<(8*e-1));return Math.max(min,Math.min(max,x));}
    static long satU(long x,int e){long max=e==8?-1:(1L<<(8*e))-1;return x<0?0:Math.min(max,x);}
    static boolean nan(float f){return Float.isNaN(f);}
    interface Ref{byte[] f(byte[] d,byte[] a,byte[] b,byte[] c,int imm);}
    interface L2{long f(long x,long y);}
    static Ref ib(int e,boolean s,L2 op){return (d,a,b,c,imm)->{int n=16/e;long[] r=new long[n];for(int i=0;i<n;i++)r[i]=op.f(lane(a,e,i,s),lane(b,e,i,s));return lanes(r,e);};}
    interface F2{float f(float x,float y);}
    static Ref fb(F2 op){return (d,a,b,c,imm)->{float[] r=new float[4];for(int i=0;i<4;i++)r[i]=op.f(fl(a,i),fl(b,i));return fv(r);};}
    interface F1{float f(float x);}
    static Ref f1(F1 op){return (d,a,b,c,imm)->{float[] r=new float[4];for(int i=0;i<4;i++)r[i]=op.f(fl(b,i));return fv(r);};}
    static float maxf(float x,float y,boolean max){
        if(nan(x))return Float.intBitsToFloat(Float.floatToRawIntBits(x)|0x400000);if(nan(y))return Float.intBitsToFloat(Float.floatToRawIntBits(y)|0x400000);
        if(x==0&&y==0)return Float.intBitsToFloat(max?Float.floatToRawIntBits(x)&Float.floatToRawIntBits(y):Float.floatToRawIntBits(x)|Float.floatToRawIntBits(y));
        return max?(x>y?x:y):(x<y?x:y);}
    static float rnd(float x,char m){
        if(nan(x)||Math.abs(x)>=8388608f)return x;
        double r=switch(m){case 'n'->Math.rint(x);case 'z'->x<0?Math.ceil(x):Math.floor(x);case 'm'->Math.floor(x);default->Math.ceil(x);};
        float f=(float)r;if(f==0)f=Math.copySign(0f,x);return f;}
    static long ctsx(float x,int imm){float v=x*(float)(1L<<imm);if(nan(v))return 0;if(v>=2147483648f)return 0x7fffffff;if(v<-2147483648f)return 0x80000000L;return ((int)v)&0xffffffffL;}
    static long ctux(float x,int imm){float v=x*(float)(1L<<imm);if(nan(v)||v<=0)return 0;if(v>=4294967296f)return 0xffffffffL;return ((long)v)&0xffffffffL;}
    static Ref cmpf(F2 cmpAsFloat){return null;}
    interface FC{boolean f(float x,float y);}
    static Ref fcmp(FC c){return (d,a,b,cc,imm)->{long[] r=new long[4];for(int i=0;i<4;i++)r[i]=c.f(fl(a,i),fl(b,i))?0xffffffffL:0;return lanes(r,4);};}
    interface LC{boolean f(long x,long y);}
    static Ref icmp(int e,boolean s,LC c){return (d,a,b,cc,imm)->{int n=16/e;long[] r=new long[n];for(int i=0;i<n;i++)r[i]=c.f(lane(a,e,i,s),lane(b,e,i,s))?-1L:0;return lanes(r,e);};}
    static Ref merge(int e,boolean high){return (d,a,b,c,imm)->{int n=16/e;long[] r=new long[n];for(int i=0;i<n/2;i++){int k=high?i:i+n/2;r[2*i]=lane(a,e,k,false);r[2*i+1]=lane(b,e,k,false);}return lanes(r,e);};}
    static Ref shift(int e,int kind){return (d,a,b,c,imm)->{int n=16/e,bits=8*e;long[] r=new long[n];long m=(1L<<bits)-1;
        for(int i=0;i<n;i++){long x=lane(a,e,i,false);int s=(int)(lane(b,e,i,false)&(bits-1));
            r[i]=switch(kind){case 0->x<<s;case 1->x>>>s;case 2->lane(a,e,i,true)>>s;default->((x<<s)|(x>>>(bits-s)))&m;};}return lanes(r,e);};}
    static Ref pack(int e,int kind){return (d,a,b,c,imm)->{int n=16/e,h=e/2;long[] r=new long[2*n];
        for(int i=0;i<2*n;i++){byte[] src=i<n?a:b;int k=i%n;
            r[i]=switch(kind){case 0->lane(src,e,k,false);case 1->satU(lane(src,e,k,false),h);case 2->satU(lane(src,e,k,true),h);default->satS(lane(src,e,k,true),h);};}return lanes(r,h);};}
    static Ref unpack(int e,boolean high){return (d,a,b,c,imm)->{int n=8/e;long[] r=new long[n];for(int i=0;i<n;i++)r[i]=lane(b,e,high?i:i+n,true);return lanes(r,2*e);};}
    static Ref mul(int e,boolean s,boolean odd){return (d,a,b,c,imm)->{int n=16/(2*e);long[] r=new long[n];for(int i=0;i<n;i++){int k=2*i+(odd?1:0);r[i]=lane(a,e,k,s)*lane(b,e,k,s);}return lanes(r,2*e);};}
    static Ref splat(int e){return (d,a,b,c,imm)->{int n=16/e;long v=lane(b,e,imm&(n-1),false);long[] r=new long[n];Arrays.fill(r,v);return lanes(r,e);};}
    static Ref splatImm(int e){return (d,a,b,c,imm)->{int n=16/e;long v=(imm<<27)>>27;long[] r=new long[n];Arrays.fill(r,v);return lanes(r,e);};}
    static Ref avg(int e,boolean s){return ib(e,s,(x,y)->(x+y+1)>>1);}
    static Ref msum(int e,boolean sa,boolean sb,int sat){return (d,a,b,c,imm)->{long[] r=new long[4];int per=4/e;
        for(int w=0;w<4;w++){long t=sat==2?lane(c,4,w,true):(sat==1?lane(c,4,w,false):lane(c,4,w,sa));for(int j=0;j<per;j++)t+=lane(a,e,w*per+j,sa)*lane(b,e,w*per+j,sb);
            r[w]=sat==1?satU(t,4):sat==2?satS(t,4):t;}return lanes(r,4);};}

    record Spec(String name,int base,String form,Ref ref,char kind,int crMode){}
    // forms: VX (D,A,B), VA (D,A,B,C), VB (D,B), VI (D,B,imm in A), VS (D,simm in A), SH (D,A,B,sh in 6-9),
    // X128 (D,A,B), X128C (D,A,B,C), X128B (D,B,uimm), X128S (D,simm), X128H (D,A,B,shb), X128U (D,B[,A ignored])
    // kind: 'f' float lanes (NaN-tolerant compare), 'i' exact. crMode: 0 none, 1 all-true/all-false, 2 bounds.
    List<Spec> specs(){
        List<Spec> s=new ArrayList<>();
        Ref vadd=fb((x,y)->x+y),vsub=fb((x,y)->x-y),vmul=fb((x,y)->x*y);
        s.add(new Spec("vaddfp",0x1000000a,"VX",vadd,'f',0));s.add(new Spec("vsubfp",0x1000004a,"VX",vsub,'f',0));
        s.add(new Spec("vaddfp128",0x14000010,"X128",vadd,'f',0));s.add(new Spec("vsubfp128",0x14000050,"X128",vsub,'f',0));s.add(new Spec("vmulfp128",0x14000090,"X128",vmul,'f',0));
        Ref madd=(d,a,b,c,i)->{float[] r=new float[4];for(int k=0;k<4;k++){float m=fl(a,k)*fl(c,k);r[k]=m+fl(b,k);}return fv(r);};
        Ref nmsub=(d,a,b,c,i)->{float[] r=new float[4];for(int k=0;k<4;k++){float m=fl(a,k)*fl(c,k);r[k]=-(m-fl(b,k));}return fv(r);};
        s.add(new Spec("vmaddfp",0x1000002e,"VA",madd,'f',0));s.add(new Spec("vnmsubfp",0x1000002f,"VA",nmsub,'f',0));
        s.add(new Spec("vmaddfp128",0x140000d0,"X128",(d,a,b,c,i)->madd.f(null,a,d,b,0),'f',0));
        s.add(new Spec("vmaddcfp128",0x14000110,"X128",(d,a,b,c,i)->madd.f(null,a,b,d,0),'f',0));
        s.add(new Spec("vnmsubfp128",0x14000150,"X128",(d,a,b,c,i)->nmsub.f(null,a,d,b,0),'f',0));
        for(int n=3;n<=4;n++){final int N=n;s.add(new Spec("vmsum"+n+"fp128",n==3?0x14000190:0x140001d0,"X128",(d,a,b,c,i)->{float t=fl(a,0)*fl(b,0);for(int k=1;k<N;k++)t=t+fl(a,k)*fl(b,k);return fv(t,t,t,t);},'f',0));}
        Ref eq=fcmp((x,y)->x==y),ge=fcmp((x,y)->x>=y),gt=fcmp((x,y)->x>y);
        Ref bounds=(d,a,b,c,i)->{long[] r=new long[4];for(int k=0;k<4;k++){float x=fl(a,k),y=fl(b,k);r[k]=(!(x<=y)?0x80000000L:0)|(!(x>=-y)?0x40000000L:0);}return lanes(r,4);};
        int[][] fc={{198},{454},{710},{966}};Ref[] fr={eq,ge,gt,bounds};String[] fn={"vcmpeqfp","vcmpgefp","vcmpgtfp","vcmpbfp"};
        for(int k=0;k<4;k++){s.add(new Spec(fn[k],0x10000000|fc[k][0],"VX",fr[k],'i',0));s.add(new Spec(fn[k]+".",0x10000400|fc[k][0],"VX",fr[k],'i',k==3?2:1));}
        int[] f128={0x18000000,0x18000080,0x18000100,0x18000180};
        for(int k=0;k<4;k++){s.add(new Spec(fn[k]+"128",f128[k],"X128",fr[k],'i',0));s.add(new Spec(fn[k]+"128.",f128[k]|0x40,"X128",fr[k],'i',k==3?2:1));}
        s.add(new Spec("vcmpequw128",0x18000200,"X128",icmp(4,false,(x,y)->x==y),'i',0));s.add(new Spec("vcmpequw128.",0x18000240,"X128",icmp(4,false,(x,y)->x==y),'i',1));
        Object[][] ic={{"vcmpequb",6,1,false,0},{"vcmpequh",70,2,false,0},{"vcmpequw",134,4,false,0},{"vcmpgtub",518,1,false,1},{"vcmpgtuh",582,2,false,1},{"vcmpgtuw",646,4,false,1},
            {"vcmpgtsb",774,1,true,1},{"vcmpgtsh",838,2,true,1},{"vcmpgtsw",902,4,true,1}};
        for(Object[] c:ic){Ref r=icmp((int)c[2],(boolean)c[3],(int)c[4]==0?(x,y)->x==y:(x,y)->x>y);s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",r,'i',0));s.add(new Spec(c[0]+".",0x10000400|(int)c[1],"VX",r,'i',1));}
        Ref mx=fb((x,y)->maxf(x,y,true)),mn=fb((x,y)->maxf(x,y,false));
        s.add(new Spec("vmaxfp",0x1000040a,"VX",mx,'f',0));s.add(new Spec("vminfp",0x1000044a,"VX",mn,'f',0));
        s.add(new Spec("vmaxfp128",0x18000280,"X128",mx,'f',0));s.add(new Spec("vminfp128",0x180002c0,"X128",mn,'f',0));
        Ref re=f1(x->1f/x),rs=f1(x->1f/(float)Math.sqrt(x));
        s.add(new Spec("vrefp",0x1000010a,"VB",re,'f',0));s.add(new Spec("vrsqrtefp",0x1000014a,"VB",rs,'f',0));
        s.add(new Spec("vrefp128",0x18000630,"X128U",re,'f',0));s.add(new Spec("vrsqrtefp128",0x18000670,"X128U",rs,'f',0));
        char[] modes={'n','z','m','p'};int[] rf={522,586,714,650};int[] rf128={0x18000370,0x180003f0,0x18000330,0x180003b0};
        for(int k=0;k<4;k++){final char m=modes[k];s.add(new Spec("vrfi"+m,0x10000000|rf[k],"VB",f1(x->rnd(x,m)),'f',0));s.add(new Spec("vrfi"+m+"128",rf128[k],"X128U",f1(x->rnd(x,m)),'f',0));}
        Ref cfs=(d,a,b,c,i)->{float[] r=new float[4];for(int k=0;k<4;k++)r[k]=(float)(int)lane(b,4,k,true)/(float)(1L<<i);return fv(r);};
        Ref cfu=(d,a,b,c,i)->{float[] r=new float[4];for(int k=0;k<4;k++)r[k]=(float)lane(b,4,k,false)/(float)(1L<<i);return fv(r);};
        Ref cts=(d,a,b,c,i)->{long[] r=new long[4];for(int k=0;k<4;k++)r[k]=ctsx(fl(b,k),i);return lanes(r,4);};
        Ref ctu=(d,a,b,c,i)->{long[] r=new long[4];for(int k=0;k<4;k++)r[k]=ctux(fl(b,k),i);return lanes(r,4);};
        s.add(new Spec("vcfsx",0x1000034a,"VI",cfs,'f',0));s.add(new Spec("vcfux",0x1000030a,"VI",cfu,'f',0));
        s.add(new Spec("vctsxs",0x100003ca,"VI",cts,'i',0));s.add(new Spec("vctuxs",0x1000038a,"VI",ctu,'i',0));
        s.add(new Spec("vcsxwfp128",0x180002b0,"X128B",cfs,'f',0));s.add(new Spec("vcuxwfp128",0x180002f0,"X128B",cfu,'f',0));
        s.add(new Spec("vcfpsxws128",0x18000230,"X128B",cts,'i',0));s.add(new Spec("vcfpuxws128",0x18000270,"X128B",ctu,'i',0));
        Object[][] mod={{"vaddubm",0,1,0},{"vadduhm",64,2,0},{"vadduwm",128,4,0},{"vsububm",1024,1,1},{"vsubuhm",1088,2,1},{"vsubuwm",1152,4,1}};
        for(Object[] c:mod)s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",ib((int)c[2],false,(int)c[3]==0?(x,y)->x+y:(x,y)->x-y),'i',0));
        Object[][] sat={{"vaddsbs",768,1,true,0},{"vaddshs",832,2,true,0},{"vaddsws",896,4,true,0},{"vaddubs",512,1,false,0},{"vadduhs",576,2,false,0},{"vadduws",640,4,false,0},
            {"vsubsbs",1792,1,true,1},{"vsubshs",1856,2,true,1},{"vsubsws",1920,4,true,1},{"vsububs",1536,1,false,1},{"vsubuhs",1600,2,false,1},{"vsubuws",1664,4,false,1}};
        for(Object[] c:sat){int e=(int)c[2];boolean sg=(boolean)c[3];boolean sub=(int)c[4]==1;
            s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",ib(e,sg,(x,y)->{long t=sub?x-y:x+y;return sg?satS(t,e):satU(t,e);}),'i',0));}
        s.add(new Spec("vaddcuw",0x10000180,"VX",ib(4,false,(x,y)->(x+y)>>>32),'i',0));s.add(new Spec("vsubcuw",0x10000580,"VX",ib(4,false,(x,y)->x>=y?1:0),'i',0));
        Object[][] av={{"vavgsb",1282,1,true},{"vavgsh",1346,2,true},{"vavgsw",1410,4,true},{"vavgub",1026,1,false},{"vavguh",1090,2,false},{"vavguw",1154,4,false}};
        for(Object[] c:av)s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",avg((int)c[2],(boolean)c[3]),'i',0));
        Object[][] mm={{"vmaxsb",258,1,true,1},{"vmaxsh",322,2,true,1},{"vmaxsw",386,4,true,1},{"vmaxub",2,1,false,1},{"vmaxuh",66,2,false,1},{"vmaxuw",130,4,false,1},
            {"vminsb",770,1,true,0},{"vminsh",834,2,true,0},{"vminsw",898,4,true,0},{"vminub",514,1,false,0},{"vminuh",578,2,false,0},{"vminuw",642,4,false,0}};
        for(Object[] c:mm){boolean max=(int)c[4]==1;s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",ib((int)c[2],(boolean)c[3],max?Math::max:Math::min),'i',0));}
        Object[][] mu={{"vmulesb",776,1,true,false},{"vmulesh",840,2,true,false},{"vmuleub",520,1,false,false},{"vmuleuh",584,2,false,false},
            {"vmulosb",264,1,true,true},{"vmulosh",328,2,true,true},{"vmuloub",8,1,false,true},{"vmulouh",72,2,false,true}};
        for(Object[] c:mu)s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",mul((int)c[2],(boolean)c[3],(boolean)c[4]),'i',0));
        s.add(new Spec("vmsumubm",0x10000024,"VA",msum(1,false,false,0),'i',0));s.add(new Spec("vmsummbm",0x10000025,"VA",msum(1,true,false,0),'i',0));
        s.add(new Spec("vmsumuhm",0x10000026,"VA",msum(2,false,false,0),'i',0));s.add(new Spec("vmsumuhs",0x10000027,"VA",msum(2,false,false,1),'i',0));
        s.add(new Spec("vmsumshm",0x10000028,"VA",msum(2,true,true,0),'i',0));s.add(new Spec("vmsumshs",0x10000029,"VA",msum(2,true,true,2),'i',0));
        for(int r=0;r<2;r++){final int R=r;s.add(new Spec(r==0?"vmhaddshs":"vmhraddshs",r==0?0x10000020:0x10000021,"VA",(d,a,b,c,i)->{long[] o=new long[8];
            for(int k=0;k<8;k++)o[k]=satS(((lane(a,2,k,true)*lane(b,2,k,true)+(R==1?0x4000:0))>>15)+lane(c,2,k,true),2);return lanes(o,2);},'i',0));}
        s.add(new Spec("vmladduhm",0x10000022,"VA",(d,a,b,c,i)->{long[] o=new long[8];for(int k=0;k<8;k++)o[k]=lane(a,2,k,false)*lane(b,2,k,false)+lane(c,2,k,false);return lanes(o,2);},'i',0));
        s.add(new Spec("vsum4ubs",0x10000608,"VX",(d,a,b,c,i)->{long[] o=new long[4];for(int w=0;w<4;w++){long t=lane(b,4,w,false);for(int j=0;j<4;j++)t+=lane(a,1,4*w+j,false);o[w]=satU(t,4);}return lanes(o,4);},'i',0));
        s.add(new Spec("vsum4sbs",0x10000708,"VX",(d,a,b,c,i)->{long[] o=new long[4];for(int w=0;w<4;w++){long t=lane(b,4,w,true);for(int j=0;j<4;j++)t+=lane(a,1,4*w+j,true);o[w]=satS(t,4);}return lanes(o,4);},'i',0));
        s.add(new Spec("vsum4shs",0x10000648,"VX",(d,a,b,c,i)->{long[] o=new long[4];for(int w=0;w<4;w++){long t=lane(b,4,w,true);for(int j=0;j<2;j++)t+=lane(a,2,2*w+j,true);o[w]=satS(t,4);}return lanes(o,4);},'i',0));
        s.add(new Spec("vsum2sws",0x10000688,"VX",(d,a,b,c,i)->{long[] o=new long[4];for(int k=0;k<4;k+=2)o[k+1]=satS(lane(a,4,k,true)+lane(a,4,k+1,true)+lane(b,4,k+1,true),4);return lanes(o,4);},'i',0));
        s.add(new Spec("vsumsws",0x10000788,"VX",(d,a,b,c,i)->{long[] o=new long[4];long t=lane(b,4,3,true);for(int k=0;k<4;k++)t+=lane(a,4,k,true);o[3]=satS(t,4);return lanes(o,4);},'i',0));
        Ref and=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++)r[k]=(byte)(a[k]&b[k]);return r;};
        Ref andc=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++)r[k]=(byte)(a[k]&~b[k]);return r;};
        Ref or=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++)r[k]=(byte)(a[k]|b[k]);return r;};
        Ref xor=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++)r[k]=(byte)(a[k]^b[k]);return r;};
        Ref nor=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++)r[k]=(byte)~(a[k]|b[k]);return r;};
        s.add(new Spec("vand",0x10000404,"VX",and,'i',0));s.add(new Spec("vandc",0x10000444,"VX",andc,'i',0));s.add(new Spec("vor",0x10000484,"VX",or,'i',0));
        s.add(new Spec("vxor",0x100004c4,"VX",xor,'i',0));s.add(new Spec("vnor",0x10000504,"VX",nor,'i',0));s.add(new Spec("vnor128",0x14000290,"X128",nor,'i',0));
        Ref sel=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++)r[k]=(byte)((a[k]&~c[k])|(b[k]&c[k]));return r;};
        s.add(new Spec("vsel",0x1000002a,"VA",sel,'i',0));s.add(new Spec("vsel128",0x14000350,"X128",(d,a,b,c,i)->sel.f(null,a,b,d,0),'i',0));
        Ref perm=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++){int j=c[k]&31;r[k]=j<16?a[j]:b[j-16];}return r;};
        s.add(new Spec("vperm",0x1000002b,"VA",perm,'i',0));s.add(new Spec("vperm128",0x14000000,"X128C",perm,'i',0));
        Ref sldoi=(d,a,b,c,i)->{byte[] r=new byte[16];for(int k=0;k<16;k++){int j=k+i;r[k]=j<16?a[j]:b[j-16];}return r;};
        s.add(new Spec("vsldoi",0x1000002c,"SH",sldoi,'i',0));s.add(new Spec("vsldoi128",0x10000010,"X128H",sldoi,'i',0));
        Object[][] mg={{"vmrghb",12,1,true},{"vmrghh",76,2,true},{"vmrghw",140,4,true},{"vmrglb",268,1,false},{"vmrglh",332,2,false},{"vmrglw",396,4,false}};
        for(Object[] c:mg)s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",merge((int)c[2],(boolean)c[3]),'i',0));
        s.add(new Spec("vmrghw128",0x18000300,"X128",merge(4,true),'i',0));s.add(new Spec("vmrglw128",0x18000340,"X128",merge(4,false),'i',0));
        s.add(new Spec("vspltb",0x1000020c,"VI",splat(1),'i',0));s.add(new Spec("vsplth",0x1000024c,"VI",splat(2),'i',0));s.add(new Spec("vspltw",0x1000028c,"VI",splat(4),'i',0));
        s.add(new Spec("vspltw128",0x18000730,"X128B",splat(4),'i',0));
        s.add(new Spec("vspltisb",0x1000030c,"VS",splatImm(1),'i',0));s.add(new Spec("vspltish",0x1000034c,"VS",splatImm(2),'i',0));s.add(new Spec("vspltisw",0x1000038c,"VS",splatImm(4),'i',0));
        s.add(new Spec("vspltisw128",0x18000770,"X128S",splatImm(4),'i',0));
        Object[][] sh={{"vslb",260,1,0},{"vslh",324,2,0},{"vslw",388,4,0},{"vsrb",516,1,1},{"vsrh",580,2,1},{"vsrw",644,4,1},{"vsrab",772,1,2},{"vsrah",836,2,2},{"vsraw",900,4,2},
            {"vrlb",4,1,3},{"vrlh",68,2,3},{"vrlw",132,4,3}};
        for(Object[] c:sh)s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",shift((int)c[2],(int)c[3]),'i',0));
        s.add(new Spec("vslw128",0x180000d0,"X128",shift(4,0),'i',0));s.add(new Spec("vsrw128",0x180001d0,"X128",shift(4,1),'i',0));
        s.add(new Spec("vsraw128",0x18000150,"X128",shift(4,2),'i',0));s.add(new Spec("vrlw128",0x18000050,"X128",shift(4,3),'i',0));
        Ref vsl=(d,a,b,c,i)->{BigInteger v=new BigInteger(1,a).shiftLeft(b[15]&7);return fit(v);};
        Ref vsr=(d,a,b,c,i)->{BigInteger v=new BigInteger(1,a).shiftRight(b[15]&7);return fit(v);};
        Ref vslo=(d,a,b,c,i)->{BigInteger v=new BigInteger(1,a).shiftLeft(((b[15]>>3)&15)*8);return fit(v);};
        Ref vsro=(d,a,b,c,i)->{BigInteger v=new BigInteger(1,a).shiftRight(((b[15]>>3)&15)*8);return fit(v);};
        s.add(new Spec("vsl",0x100001c4,"VX",vsl,'i',0));s.add(new Spec("vsr",0x100002c4,"VX",vsr,'i',0));s.add(new Spec("vslo",0x1000040c,"VX",vslo,'i',0));s.add(new Spec("vsro",0x1000044c,"VX",vsro,'i',0));
        s.add(new Spec("vslo128",0x14000390,"X128",vslo,'i',0));s.add(new Spec("vsro128",0x140003d0,"X128",vsro,'i',0));
        Object[][] pk={{"vpkuhum",14,2,0},{"vpkuwum",78,4,0},{"vpkuhus",142,2,1},{"vpkuwus",206,4,1},{"vpkshus",270,2,2},{"vpkswus",334,4,2},{"vpkshss",398,2,3},{"vpkswss",462,4,3}};
        for(Object[] c:pk)s.add(new Spec((String)c[0],0x10000000|(int)c[1],"VX",pack((int)c[2],(int)c[3]),'i',0));
        Object[][] pk128={{"vpkshss128",0x14000200,2,3},{"vpkshus128",0x14000240,2,2},{"vpkswss128",0x14000280,4,3},{"vpkswus128",0x140002c0,4,2},{"vpkuhum128",0x14000300,2,0},
            {"vpkuhus128",0x14000340,2,1},{"vpkuwum128",0x14000380,4,0},{"vpkuwus128",0x140003c0,4,1}};
        for(Object[] c:pk128)s.add(new Spec((String)c[0],(int)c[1],"X128",pack((int)c[2],(int)c[3]),'i',0));
        s.add(new Spec("vupkhsb",0x1000020e,"VB",unpack(1,true),'i',0));s.add(new Spec("vupklsb",0x1000028e,"VB",unpack(1,false),'i',0));
        s.add(new Spec("vupkhsh",0x1000024e,"VB",unpack(2,true),'i',0));s.add(new Spec("vupklsh",0x100002ce,"VB",unpack(2,false),'i',0));
        s.add(new Spec("vupkhsb128",0x18000380,"X128",unpack(1,true),'i',0));s.add(new Spec("vupklsb128",0x180003c0,"X128",unpack(1,false),'i',0));
        Ref upx=(d,a,b,c,i)->null;
        for(int h=0;h<2;h++){final int H=h;s.add(new Spec(h==0?"vupkhpx":"vupklpx",h==0?0x1000034e:0x100003ce,"VB",(d,a,b,c,i)->{long[] o=new long[4];
            for(int k=0;k<4;k++){long x=lane(b,2,k+4*H,false);o[k]=((x>>15)!=0?0xff000000L:0)|(((x>>10)&31)<<16)|(((x>>5)&31)<<8)|(x&31);}return lanes(o,4);},'i',0));}
        // Named estimate ops: the emulator callbacks below compute exact 2^x / log2(x); the test checks lane plumbing.
        Ref exp2=f1(x->(float)Math.pow(2,x)),log2=f1(x->(float)(Math.log(x)/Math.log(2)));
        s.add(new Spec("vexptefp",0x1000018a,"VB",exp2,'f',0));s.add(new Spec("vlogefp",0x100001ca,"VB",log2,'f',0));
        s.add(new Spec("vexptefp128",0x180006b0,"X128U",exp2,'f',0));s.add(new Spec("vlogefp128",0x180006f0,"X128U",log2,'f',0));
        // vupkd3d128 UIMM 20 (encoded in the base word): the four halves of bytes 8-15 to floats (Java's IEEE binary16).
        s.add(new Spec("vupkd3d128",0x180007f0|(20<<16),"X128U",(d,a,b,c,i)->{float[] r=new float[4];for(int k=0;k<4;k++)r[k]=Float.float16ToFloat((short)lane(b,2,4+k,false));return fv(r);},'f',0));
        // vupkd3d128 UIMM 4: z = 0.0, w = 1.0; x, y come from the unknown op (callback value 0x11223344 0x55667788).
        s.add(new Spec("vupkd3d128",0x180007f0|(4<<16),"X128U",(d,a,b,c,i)->lanes(new long[]{0x11223344L,0x55667788L,0,0x3f800000L},4),'i',0));
        s.add(new Spec("vpkpx",0x1000030e,"VX",(d,a,b,c,i)->{long[] o=new long[8];for(int k=0;k<8;k++){long x=lane(k<4?a:b,4,k%4,false);
            o[k]=(((x>>24)&1)<<15)|(((x>>19)&31)<<10)|(((x>>11)&31)<<5)|((x>>3)&31);}return lanes(o,2);},'i',0));
        return s;
    }
    static byte[] fit(BigInteger v){byte[] r=new byte[16];byte[] x=v.toByteArray();for(int k=0;k<16;k++){int j=x.length-16+k;r[k]=j>=0?x[j]:0;}return r;}

    // ---- encoding ----
    static int vmxD(int d){return ((d&31)<<21)|((d>>5)<<2);}
    static int vmxA(int a){return ((a&31)<<16)|(((a>>5)&1)<<5)|((a>>6)<<10);}
    static int vmxB(int b){return ((b&31)<<11)|(b>>5);}
    int encode(Spec s,int d,int a,int b,int c,int imm){
        return switch(s.form()){
            case "VX"->s.base()|(d<<21)|(a<<16)|(b<<11);
            case "VA"->s.base()|(d<<21)|(a<<16)|(b<<11)|(c<<6);
            case "VB"->s.base()|(d<<21)|(b<<11);
            case "VI"->s.base()|(d<<21)|(imm<<16)|(b<<11);
            case "VS"->s.base()|(d<<21)|((imm&31)<<16);
            case "SH"->s.base()|(d<<21)|(a<<16)|(b<<11)|(imm<<6);
            case "X128"->s.base()|vmxD(d)|vmxA(a)|vmxB(b);
            case "X128U"->s.base()|vmxD(d)|vmxB(b);
            case "X128C"->s.base()|vmxD(d)|vmxA(a)|vmxB(b)|((c&7)<<6);
            case "X128B"->s.base()|vmxD(d)|vmxB(b)|(imm<<16);
            case "X128S"->s.base()|vmxD(d)|((imm&31)<<16);
            case "X128H"->s.base()|vmxD(d)|vmxA(a)|vmxB(b)|(imm<<6);
            default->throw new IllegalArgumentException(s.form());};
    }
    String reg(int n){return n<32?"vs"+(n+32):"v"+n;}
    void check(boolean b,String why)throws Exception{if(!b)throw new Exception(why);}
    // Write one instruction word to the program (fresh listing) and to the emulator's memory, which caches pages.
    void put(EmulatorHelper e,long at,int word)throws Exception{
        clearListing(toAddr(at),toAddr(at+3));setInt(toAddr(at),word);
        e.writeMemory(toAddr(at),new byte[]{(byte)(word>>>24),(byte)(word>>>16),(byte)(word>>>8),(byte)word});
    }

    // Operand values: random words, floats of mixed magnitude and the special values.
    static final int[] SPECIAL={0,0x80000000,0x7f800000,0xff800000,0x7fc00000,0x7f800001,0x00000001,0x807fffff,0x3f800000,0xbf800000,0x3f000000,0x4b000000,0x4effffff,0x4f000000,0xcf000000,0x4f800000,
        0x3fc00000,0x40200000,0xbfc00000,0x7f7fffff,0x00800000,0xffffffff,0x7fffffff,0x00000080,0x00008000,0x0000ffff,0x80008000,0x40000000,0xc0000000,0x3e800000,0x47000000,0x2f800000};
    byte[] value(Random r,char kind){
        long[] l=new long[4];
        for(int k=0;k<4;k++){int mode=r.nextInt(6);int w;
            if(mode==0)w=SPECIAL[r.nextInt(SPECIAL.length)];
            else if(mode<=2&&kind=='f'){float f=(float)((r.nextDouble()*2-1)*Math.pow(2,r.nextInt(40)-20));w=Float.floatToRawIntBits(r.nextInt(8)==0?(float)Math.rint(f):f);}
            else if(mode==3){w=(r.nextInt(64)-32);}
            else w=r.nextInt();
            l[k]=w&0xffffffffL;}
        return lanes(l,4);
    }
    boolean same(byte[] got,byte[] want,char kind){
        if(kind=='i')return Arrays.equals(got,want);
        for(int k=0;k<4;k++){float g=fl(got,k),w=fl(want,k);
            // Subnormal results: the emulator's rounding of tiny results differs from IEEE, and Xenon's VMX flushes them
            // (VSCR NJ, not modelled); a zero or subnormal result is accepted for a zero or subnormal expectation.
            if(Math.abs(w)<Float.MIN_NORMAL&&Math.abs(g)<Float.MIN_NORMAL){subnormalTolerated++;continue;}
            if(nan(w)?!nan(g):Float.floatToRawIntBits(g)!=Float.floatToRawIntBits(w))return false;}
        return true;
    }
    static byte[] bytes(BigInteger v){return fit(v);}
    int subnormalTolerated=0;
    static final Set<String> KNOWN=Set.of("exp2_estimate","log2_estimate","vupkd3d128_xy_unknown");
    // Callbacks for the named ops: exact values for the estimates, a fixed marker for the unknown x/y lanes.
    void registerKnownOps(EmulatorHelper e){
        e.registerCallOtherCallback("exp2_estimate",new BreakCallBack(){public boolean pcodeCallback(PcodeOpRaw op){
            MemoryState m=emulate.getMemoryState();float x=Float.intBitsToFloat((int)m.getValue(op.getInput(1)));
            m.setValue(op.getOutput(),Float.floatToRawIntBits((float)Math.pow(2,x))&0xffffffffL);return true;}});
        e.registerCallOtherCallback("log2_estimate",new BreakCallBack(){public boolean pcodeCallback(PcodeOpRaw op){
            MemoryState m=emulate.getMemoryState();float x=Float.intBitsToFloat((int)m.getValue(op.getInput(1)));
            m.setValue(op.getOutput(),Float.floatToRawIntBits((float)(Math.log(x)/Math.log(2)))&0xffffffffL);return true;}});
        e.registerCallOtherCallback("vupkd3d128_xy_unknown",new BreakCallBack(){public boolean pcodeCallback(PcodeOpRaw op){
            emulate.getMemoryState().setValue(op.getOutput(),0x1122334455667788L);return true;}});
    }
    static boolean floatInputs(Spec s){String n=s.name();return n.contains("fp")||n.startsWith("vct")||n.startsWith("vrfi")||n.startsWith("vref")||n.startsWith("vrsq");}

    // Loads, stores, lvsl/lvsr and VSCR moves: every byte offset, RA=0 and RA!=0, guard bytes around the block.
    int testMemory(EmulatorHelper e,long at)throws Exception{
        long data=0x83b00000L;
        if(currentProgram.getMemory().getBlock(toAddr(data))==null)currentProgram.getMemory().createInitializedBlock("vector_memory_test",toAddr(data),0x1000,(byte)0,monitor,false);
        Object[][] ops={{"lvsl",0x7c00000c,'x',0},{"lvsr",0x7c00004c,'x',0},{"lvebx",0x7c00000e,'x',1},{"lvehx",0x7c00004e,'x',2},{"lvewx",0x7c00008e,'x',4},{"lvx",0x7c0000ce,'x',16},{"lvxl",0x7c0002ce,'x',16},
            {"stvebx",0x7c00010e,'x',-1},{"stvehx",0x7c00014e,'x',-2},{"stvewx",0x7c00018e,'x',-4},{"stvx",0x7c0001ce,'x',-16},{"stvxl",0x7c0003ce,'x',-16},
            {"lvsl128",0x10000003,'v',0},{"lvsr128",0x10000043,'v',0},{"lvewx128",0x10000083,'v',4},{"stvewx128",0x10000183,'v',-4},{"lvxl128",0x100002c3,'v',16},{"stvxl128",0x100003c3,'v',-16}};
        Random r=new Random(360);int n=0;
        byte[] block=new byte[64];
        for(Object[] o:ops){
            String name=(String)o[0];boolean v128=(char)o[2]=='v';int kind=(int)o[3];
            for(int off=0;off<16;off++)for(int raZero=0;raZero<2;raZero++){
                int d=v128?r.nextInt(128):r.nextInt(32);int ra=raZero==1?0:3,rb=4;
                long base=data+0x100;long ea=base+off;
                int word=(int)o[1]|(v128?vmxD(d):(d<<21))|(ra<<16)|(rb<<11);
                put(e,at,word);disassemble(toAddr(at));Instruction ins=getInstructionAt(toAddr(at));
                check(ins!=null&&ins.getMnemonicString().equals(name),"Decode "+name+" "+Integer.toHexString(word)+" -> "+(ins==null?"none":ins.getMnemonicString()));
                for(PcodeOp op:ins.getPcode())check(op.getOpcode()!=PcodeOp.CALLOTHER,"Opaque op in "+name);
                r.nextBytes(block);e.writeMemory(toAddr(base-16),block);
                byte[] vec=new byte[16];r.nextBytes(vec);e.writeRegister(reg(d),new BigInteger(1,vec));
                if(raZero==1){e.writeRegister("r4",ea);}else{e.writeRegister("r3",base);e.writeRegister("r4",off);}
                e.writeRegister("r0",0x5555);
                e.writeRegister(e.getPCRegister(),at);check(e.step(monitor),name+": "+e.getLastError());at+=4;
                byte[] got=bytes(e.readRegister(reg(d)));byte[] mem=e.readMemory(toAddr(base-16),64);
                int sh=(int)(ea&15);byte[] want=new byte[16];byte[] wantMem=block.clone();
                if(name.startsWith("lvsl"))for(int k=0;k<16;k++)want[k]=(byte)(sh+k);
                else if(name.startsWith("lvsr"))for(int k=0;k<16;k++)want[k]=(byte)(16-sh+k);
                else if(kind>0&&kind<16){long a=ea&~(kind-1);int lanePos=(int)(a&15);for(int k=0;k<kind;k++)want[lanePos+k]=block[(int)(a-(base-16))+k];}
                else if(kind==16){long a=ea&~15L;for(int k=0;k<16;k++)want[k]=block[(int)(a-(base-16))+k];}
                else{int sz=-kind;long a=ea&~(long)(sz-1);int lanePos=(int)(a&15);if(sz==16){a=ea&~15L;lanePos=0;}
                    for(int k=0;k<sz;k++)wantMem[(int)(a-(base-16))+k]=vec[lanePos+k];want=vec;}
                check(Arrays.equals(got,want),name+" off="+off+" ra0="+raZero+" got "+hex(got)+" want "+hex(want));
                check(Arrays.equals(mem,wantMem),name+" off="+off+" memory "+hex(mem)+" want "+hex(wantMem));
                n++;
            }
        }
        // mtvscr then mfvscr: the low word moves through VSCR.
        for(int k=0;k<32;k++){
            int b=r.nextInt(32),d=r.nextInt(32);byte[] vec=new byte[16];r.nextBytes(vec);
            put(e,at,0x10000644|(b<<11));put(e,at+4,0x10000604|(d<<21));disassemble(toAddr(at));disassemble(toAddr(at+4));
            check("mtvscr".equals(getInstructionAt(toAddr(at)).getMnemonicString())&&"mfvscr".equals(getInstructionAt(toAddr(at+4)).getMnemonicString()),"Decode VSCR moves");
            e.writeRegister(reg(b),new BigInteger(1,vec));e.writeRegister(e.getPCRegister(),at);check(e.step(monitor)&&e.step(monitor),"VSCR moves");at+=8;
            byte[] want=new byte[16];System.arraycopy(vec,12,want,12,4);
            check(Arrays.equals(bytes(e.readRegister(reg(d))),want),"mfvscr after mtvscr");n++;
        }
        return n;
    }
    static String hex(byte[] v){return HexFormat.of().formatHex(v);}

    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Path result=root.resolve("vector-validation.txt");Files.writeString(result,"RUNNING\n");
        long codeBase=0x83a00000L;
        if(currentProgram.getMemory().getBlock(toAddr(codeBase))==null)currentProgram.getMemory().createInitializedBlock("vector_semantics_test",toAddr(codeBase),0x80000,(byte)0,monitor,false);
        clearListing(toAddr(codeBase),toAddr(codeBase+0x7ffff));
        List<Spec> specs=specs();
        Random random=new Random(1943);
        StringBuilder report=new StringBuilder();
        int total=0,literal=0,memoryCases=0;long at=codeBase;List<String> failures=new ArrayList<>();
        EmulatorHelper e=new EmulatorHelper(currentProgram);
        registerKnownOps(e);
        try{
            // Literal vectors, hand-computed (independent of the models above).
            Object[][] lits={
                // half to float, hand-computed: 0x3c00 = 1.0, 0xc000 = -2.0, 0x7bff = 65504, 0x0001 = 2^-24
                {"vupkd3d128",null,HexFormat.of().parseHex("ffffffffffffffff3c00c0007bff0001"),null,null,0,fv(1f,-2f,65504f,5.9604645e-8f)},
                {"vmulfp128",fv(1.5f,-2f,0.25f,3f),fv(2f,2f,8f,-0.5f),null,null,0,fv(3f,-4f,2f,-1.5f)},
                {"vmsum3fp128",fv(1f,2f,3f,100f),fv(4f,5f,6f,1000f),null,null,0,fv(32f,32f,32f,32f)},
                {"vmsum4fp128",fv(1f,2f,3f,4f),fv(4f,5f,6f,0.5f),null,null,0,fv(34f,34f,34f,34f)},
                {"vmaddfp",fv(2f,3f,-1f,0.5f),fv(1f,1f,1f,1f),fv(3f,-2f,4f,8f),null,0,fv(7f,-5f,-3f,5f)},
                {"vmaddcfp128",fv(2f,3f,-1f,0.5f),fv(1f,1f,1f,1f),null,fv(3f,-2f,4f,8f),0,fv(7f,-5f,-3f,5f)},
                {"vmaddfp128",fv(2f,3f,-1f,0.5f),fv(3f,-2f,4f,8f),null,fv(1f,1f,1f,1f),0,fv(7f,-5f,-3f,5f)},
                {"vnmsubfp128",fv(2f,3f,-1f,0.5f),fv(3f,-2f,4f,8f),null,fv(1f,1f,1f,1f),0,fv(-5f,7f,5f,-3f)},
                {"vcsxwfp128",null,lanes(new long[]{256,0xffffff00L,3,0x7fffffff},4),null,null,8,fv(1f,-1f,0.01171875f,8388608f)},
                {"vcfpsxws128",null,fv(1.75f,-1.75f,3e9f,Float.NaN),null,null,1,lanes(new long[]{3,0xfffffffdL,0x7fffffff,0},4)},
                {"vcfpuxws128",null,fv(1.75f,-1.75f,5e9f,4e9f),null,null,0,lanes(new long[]{1,0,0xffffffffL,4000000000L},4)},
                {"vspltw128",null,lanes(new long[]{0x11111111L,0x22222222L,0x33333333L,0x44444444L},4),null,null,2,lanes(new long[]{0x33333333L,0x33333333L,0x33333333L,0x33333333L},4)},
                {"vspltisw128",null,null,null,null,0x1d,lanes(new long[]{0xfffffffdL,0xfffffffdL,0xfffffffdL,0xfffffffdL},4)},
                {"vupkhsb128",null,HexFormat.of().parseHex("017f80ff0203040500000000000000ff"),null,null,0,HexFormat.of().parseHex("0001007fff80ffff0002000300040005")},
                {"vupklsb128",null,HexFormat.of().parseHex("00000000000000000102fe8000000000"),null,null,0,HexFormat.of().parseHex("00010002fffeff800000000000000000")},
                {"vpkswss128",lanes(new long[]{1,0x7fffffffL,0xffff0000L,0xffffffffL},4),lanes(new long[]{0x8000,0x7fff,0xffff8000L,0x12345678L},4),null,null,0,HexFormat.of().parseHex("00017fff8000ffff7fff7fff80007fff")},
                {"vperm128",HexFormat.of().parseHex("000102030405060708090a0b0c0d0e0f"),HexFormat.of().parseHex("101112131415161718191a1b1c1d1e1f"),HexFormat.of().parseHex("1f00110e0303e1ff0000000000000000"),null,0,HexFormat.of().parseHex("1f00110e0303011f0000000000000000")},
                {"vsldoi128",HexFormat.of().parseHex("000102030405060708090a0b0c0d0e0f"),HexFormat.of().parseHex("101112131415161718191a1b1c1d1e1f"),null,null,4,HexFormat.of().parseHex("0405060708090a0b0c0d0e0f10111213")},
                {"vsel128",HexFormat.of().parseHex("ffffffff00000000ffffffff00000000"),HexFormat.of().parseHex("0000000011111111aaaaaaaa55555555"),null,HexFormat.of().parseHex("0f0f0f0fffffffff00000000f0f0f0f0"),0,HexFormat.of().parseHex("f0f0f0f011111111ffffffff50505050")},
                {"vmrghw128",lanes(new long[]{1,2,3,4},4),lanes(new long[]{5,6,7,8},4),null,null,0,lanes(new long[]{1,5,2,6},4)},
                {"vmrglw128",lanes(new long[]{1,2,3,4},4),lanes(new long[]{5,6,7,8},4),null,null,0,lanes(new long[]{3,7,4,8},4)},
                {"vslw128",lanes(new long[]{1,0x80000001L,0xf0f0f0f0L,5},4),lanes(new long[]{4,33,0,31},4),null,null,0,lanes(new long[]{16,2,0xf0f0f0f0L,0x80000000L},4)},
                {"vsraw128",lanes(new long[]{0x80000000L,0x40000000L,0xfffffff0L,7},4),lanes(new long[]{31,30,2,1},4),null,null,0,lanes(new long[]{0xffffffffL,1,0xfffffffcL,3},4)},
                {"vcmpgtfp128",fv(2f,1f,Float.NaN,-0f),fv(1f,1f,0f,0f),null,null,0,lanes(new long[]{0xffffffffL,0,0,0},4)},
                {"vcmpbfp128",fv(0.5f,2f,-3f,Float.NaN),fv(1f,1f,2f,1f),null,null,0,lanes(new long[]{0,0x80000000L,0x40000000L,0xc0000000L},4)},
                {"vrfin128",null,fv(2.5f,-0.5f,1.5f,-2.75f),null,null,0,fv(2f,-0f,2f,-3f)},
                {"vrfiz128",null,fv(2.5f,-0.5f,1.75f,-2.75f),null,null,0,fv(2f,-0f,1f,-2f)},
                {"vmaxfp128",fv(-0f,1f,Float.NaN,3f),fv(0f,2f,1f,-4f),null,null,0,fv(0f,2f,Float.NaN,3f)},
                {"vminfp128",fv(-0f,1f,Float.NaN,3f),fv(0f,2f,1f,-4f),null,null,0,fv(-0f,1f,Float.NaN,-4f)},
                {"vsubfp128",fv(1f,0.5f,-2f,10f),fv(0.25f,0.5f,-2f,-10f),null,null,0,fv(0.75f,0f,0f,20f)},
                {"vaddubm",HexFormat.of().parseHex("ff01800000000000000000000000007f"),HexFormat.of().parseHex("01ff800000000000000000000000007f"),null,null,0,HexFormat.of().parseHex("000000000000000000000000000000fe")},
                {"vmsumshs",lanes(new long[]{0x7fff7fffL,0x00020003L,0,0},4),lanes(new long[]{0x7fff7fffL,0xfffe0004L,0,0},4),lanes(new long[]{0x7fffffffL,0x10,0xffffffffL,0},4),null,0,lanes(new long[]{0x7fffffffL,0x18,0xffffffffL,0},4)},
                {"vctsxs",null,fv(-1.5f,2.5f,1e10f,Float.NaN),null,null,2,lanes(new long[]{0xfffffffaL,10,0x7fffffff,0},4)},
                {"vaddsws",lanes(new long[]{0x7fffffffL,0x80000000L,5,0xffffffffL},4),lanes(new long[]{1,0xffffffffL,6,1},4),null,null,0,lanes(new long[]{0x7fffffffL,0x80000000L,11,0},4)},
                {"vpkpx",lanes(new long[]{0x01f80408L,0,0,0},4),lanes(new long[]{0,0,0,0x00ffffffL},4),null,null,0,HexFormat.of().parseHex("fc010000000000000000000000007fff")},
            };
            for(Object[] l:lits){
                Spec s=specs.stream().filter(x->x.name().equals(l[0])).findFirst().orElseThrow();
                int d=s.form().startsWith("X128")?70:5,a=s.form().startsWith("X128")?101:6,b=s.form().startsWith("X128")?33:7,c=s.form().equals("X128C")?3:8;
                byte[] av=l[1]==null?new byte[16]:(byte[])l[1],bv=l[2]==null?new byte[16]:(byte[])l[2],cv=l[3]==null?new byte[16]:(byte[])l[3],dv=l[4]==null?new byte[16]:(byte[])l[4];
                byte[] got=step(e,s,at,d,a,b,c,(int)l[5],dv,av,bv,cv,null);at+=4;
                check(same(got,(byte[])l[6],s.kind()=='f'?'f':'i'),"literal "+l[0]+" got "+hex(got)+" want "+hex((byte[])l[6]));
                check(same(s.ref().f(dv,av,bv,cv,(int)l[5]),(byte[])l[6],s.kind()=='f'?'f':'i'),"model "+l[0]+" disagrees with its literal vector");
                literal++;
            }
            for(Spec s:specs){
                int cases=0;try{boolean x128=s.form().startsWith("X128");int regs=x128?128:32;
                for(int k=0;k<96;k++){
                    int d=random.nextInt(regs),a=random.nextInt(regs),b=random.nextInt(regs),c=s.form().equals("X128C")?random.nextInt(8):random.nextInt(32);
                    if(k%8==1)a=d;if(k%8==2)b=d;if(k%8==3)b=a;if(k%8==4&&!s.form().equals("X128C"))c=d;
                    if(k<4&&x128){d=k*32+(d&31);}
                    int imm=random.nextInt(32);
                    if(s.name().equals("vspltb"))imm&=15;if(s.name().equals("vsplth"))imm&=7;if(s.name().equals("vspltw"))imm&=3;
                    if(s.form().equals("SH")||s.form().equals("X128H"))imm&=15;
                    if(s.name().startsWith("vcfpsx")||s.name().startsWith("vcfpux")||s.name().startsWith("vcsx")||s.name().startsWith("vcux"))imm=random.nextInt(32);
                    char inKind=floatInputs(s)?'f':'i';
                    byte[] dv=value(random,inKind),av=value(random,inKind),bv=value(random,inKind),cv=value(random,inKind);
                    if(s.name().startsWith("vperm"))for(int q=0;q<16;q++)cv[q]=(byte)random.nextInt(256);
                    // Aliased registers carry one value.
                    Map<Integer,byte[]> regVal=new HashMap<>();
                    boolean usesA=!s.form().equals("VB")&&!s.form().equals("VI")&&!s.form().equals("VS")&&!s.form().equals("X128B")&&!s.form().equals("X128S")&&!s.form().equals("X128U");
                    boolean usesC=s.form().equals("VA")||s.form().equals("X128C");
                    regVal.put(d,dv);if(usesA)regVal.put(a,av);regVal.put(b,bv);if(usesC)regVal.put(x128?c:c,cv);
                    dv=regVal.get(d);if(usesA)av=regVal.get(a);bv=regVal.get(b);if(usesC)cv=regVal.get(c);
                    byte[] want=s.ref().f(dv,av,bv,cv,imm);
                    int[] crWant=new int[1];
                    byte[] got=step(e,s,at,d,a,b,c,imm,dv,av,bv,cv,crWant);at+=4;
                    check(same(got,want,s.kind()),s.name()+" d="+d+" a="+a+" b="+b+" c="+c+" imm="+imm+" a="+hex(av)+" b="+hex(bv)+" c="+hex(cv)+" d0="+hex(dv)+" got "+hex(got)+" want "+hex(want));
                    if(s.crMode()>0){
                        int cr=crWant[0],expect;
                        if(s.crMode()==1){boolean all=true,none=true;for(byte q:want){if(q!=(byte)0xff)all=false;if(q!=0)none=false;}expect=(all?8:0)|(none?2:0);}
                        else{boolean in=true;for(byte q:want)if(q!=0)in=false;expect=in?2:0;}
                        check(cr==expect,s.name()+" cr6="+cr+" expected "+expect+" for "+hex(want));
                    }
                    cases++;
                }
                }catch(Exception ex){failures.add(s.name()+": "+ex.getMessage());}
                total+=cases;report.append(s.name()).append('=').append(cases).append(' ');
            }
            try{memoryCases=testMemory(e,at+0x20000);}catch(Exception ex){failures.add("memory: "+ex.getMessage());}
        }finally{e.dispose();}
        if(!failures.isEmpty()){Files.writeString(result,"FAIL "+failures.size()+"\n"+String.join("\n",failures)+"\n");return;}
        Files.writeString(result,"literal_vectors="+literal+"\n"+report.toString().trim()+"\nPASS COMPLETE vector_mnemonics="+specs.size()+" emulated_cases="+total+" literal_vectors="+literal+" memory_cases="+memoryCases+" subnormal_lanes_tolerated="+subnormalTolerated
            +"; no CALLOTHER in any tested instruction other than the named ops exp2_estimate, log2_estimate and vupkd3d128_xy_unknown (emulated by callbacks); destination, guard register and CR checked (CR6 for record forms); register banks 0-127 for VMX128\n");
    }

    // Write operands, step one instruction, return the destination; crOut[0] receives CR6 when asked.
    byte[] step(EmulatorHelper e,Spec s,long at,int d,int a,int b,int c,int imm,byte[] dv,byte[] av,byte[] bv,byte[] cv,int[] crOut)throws Exception{
        int word=encode(s,d,a,b,c,imm);
        put(e,at,word);disassemble(toAddr(at));
        Instruction ins=getInstructionAt(toAddr(at));
        check(ins!=null&&ins.getMnemonicString().equals(s.name()),"Decode "+s.name()+" "+Integer.toHexString(word)+" -> "+(ins==null?"none":ins.getMnemonicString()));
        for(PcodeOp op:ins.getPcode())if(op.getOpcode()==PcodeOp.CALLOTHER&&!KNOWN.contains(currentProgram.getLanguage().getUserDefinedOpName((int)op.getInput(0).getOffset())))throw new Exception("Opaque op in "+s.name()+": "+currentProgram.getLanguage().getUserDefinedOpName((int)op.getInput(0).getOffset()));
        boolean x128=s.form().startsWith("X128");
        String cr=x128&&s.form().equals("X128C")?reg(c):reg(c);
        // Sources first, destination last only if not aliased with a source (aliases already share a value).
        if(s.form().equals("VA"))e.writeRegister(reg(c),new BigInteger(1,cv));
        if(s.form().equals("X128C"))e.writeRegister(reg(c),new BigInteger(1,cv));
        e.writeRegister(reg(b),new BigInteger(1,bv));
        e.writeRegister(reg(a),new BigInteger(1,av));
        e.writeRegister(reg(d),new BigInteger(1,dv));
        e.writeRegister(reg(b),new BigInteger(1,bv));
        if(!s.form().equals("VB")&&!s.form().equals("VI")&&!s.form().equals("VS")&&!s.form().startsWith("X128B")&&!s.form().equals("X128S")&&!s.form().equals("X128U"))e.writeRegister(reg(a),new BigInteger(1,av));
        if(s.form().equals("VA")||s.form().equals("X128C"))e.writeRegister(reg(c),new BigInteger(1,cv));
        int guard=(d+1)%(x128?128:32);while(guard==a||guard==b||guard==c&&(s.form().equals("VA")||s.form().equals("X128C")))guard=(guard+1)%(x128?128:32);
        BigInteger gv=new BigInteger("123456789abcdef0fedcba9876543210",16);
        if(guard!=d)e.writeRegister(reg(guard),gv);
        e.writeRegister("crall",0x1234567812345678L);
        e.writeRegister(e.getPCRegister(),at);
        check(e.step(monitor),"Emulation "+s.name()+": "+e.getLastError());
        if(guard!=d)check(e.readRegister(reg(guard)).equals(gv),s.name()+" changed register "+guard);
        BigInteger crall=e.readRegister("crall");
        long expectAll=0x1234567812345678L;
        if(s.crMode()>0){
            int cr6=e.readRegister("cr6").intValue();if(crOut!=null)crOut[0]=cr6;
            // cr6 is byte 6 of crall (offset 0x906); everything else unchanged.
            long mask=~(0xffL<<8);check((crall.longValue()&mask)==(expectAll&mask),s.name()+" changed CR beyond CR6");
        }else check(crall.longValue()==expectAll,s.name()+" changed CR");
        return bytes(e.readRegister(reg(d)));
    }
}
