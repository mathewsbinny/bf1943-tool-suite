import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;
public class TestLogical extends GhidraScript {
    String reg(int n){return n<32?"vs"+(n+32):"v"+n;}
    void check(boolean b,String message)throws Exception{if(!b)throw new Exception(message);}
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Files.writeString(root.resolve("logical-validation.txt"),"RUNNING\n");
        if(currentProgram.getMemory().getBlock(toAddr(0x83100000L))==null)
            currentProgram.getMemory().createInitializedBlock("logical_test_code",toAddr(0x83100000L),0x10000,(byte)0,monitor,false);
        clearListing(toAddr(0x83100000L),toAddr(0x8310ffffL));
        int[] codes={528,592,720,784};String[] names={"vand128","vandc128","vor128","vxor128"};
        ArrayList<int[]> cases=new ArrayList<>();
        for(int op=0;op<4;op++)for(int d=0;d<128;d++)for(int mode=0;mode<5;mode++)for(int pattern=0;pattern<2;pattern++){
            int a=(d+31)%128,b=(d+65)%128;
            if(mode==1)a=d;if(mode==2)b=d;if(mode==3)b=a;if(mode==4){a=d;b=d;}
            int word=0x14000000|codes[op]|((d&31)<<21)|((d&96)>>3)|((a&31)<<16)|(a&32)|((a&64)<<4)|((b&31)<<11)|((b&96)>>5);
            setInt(toAddr(0x83100000L+cases.size()*4),word);cases.add(new int[]{op,d,a,b,pattern});
        }
        BigInteger mask=BigInteger.ONE.shiftLeft(128).subtract(BigInteger.ONE);
        EmulatorHelper e=new EmulatorHelper(currentProgram);
        try{int index=0;
            for(int[] c:cases){
                long at=0x83100000L+index*4;disassemble(toAddr(at));
                check(getInstructionAt(toAddr(at))!=null && getInstructionAt(toAddr(at)).getMnemonicString().equals(names[c[0]]),"Decode "+Long.toHexString(at));
                BigInteger x=c[4]==0?new BigInteger("0123456789abcdeffedcba9876543210",16):mask;
                BigInteger y=c[4]==0?new BigInteger("a55a00ff33cc96966996cc33ff005aa5",16):BigInteger.ZERO;
                e.writeRegister(reg(c[1]),mask);e.writeRegister(reg(c[2]),x);e.writeRegister(reg(c[3]),y);
                x=e.readRegister(reg(c[2]));y=e.readRegister(reg(c[3]));
                BigInteger expected=switch(c[0]){case 0->x.and(y);case 1->x.and(y.xor(mask));case 2->x.or(y);default->x.xor(y);};
                int guard=(c[1]+1)%128;while(guard==c[2]||guard==c[3])guard=(guard+1)%128;
                e.writeRegister(reg(guard),new BigInteger("123456789abcdef",16));e.writeRegister("crall",0x12345678L);
                e.writeRegister(e.getPCRegister(),at);check(e.step(monitor),e.getLastError());
                check(e.readRegister(reg(c[1])).equals(expected),"Result "+Arrays.toString(c));
                check(e.readRegister(reg(guard)).equals(new BigInteger("123456789abcdef",16)),"Adjacent register changed");
                check(e.readRegister("crall").equals(BigInteger.valueOf(0x12345678L)),"CR changed");
                index++;
            }
        }finally{e.dispose();}
        Files.writeString(root.resolve("logical-validation.txt"),"PASS COMPLETE logical_cases="+cases.size()+"; four operations; all destination registers; all source register numbers; five alias patterns; known bit patterns and ones/zero; register and CR preservation\n");
    }
}
