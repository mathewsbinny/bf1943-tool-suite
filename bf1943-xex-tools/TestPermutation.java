import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;
public class TestPermutation extends GhidraScript {
    String reg(int n){return n<32?"vs"+(n+32):"v"+n;}
    void check(boolean b,String why)throws Exception{if(!b)throw new Exception(why);}
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Files.writeString(root.resolve("permutation-validation.txt"),"RUNNING\n");
        if(currentProgram.getMemory().getBlock(toAddr(0x83200000L))==null)currentProgram.getMemory().createInitializedBlock("permutation_test",toAddr(0x83200000L),0x10000,(byte)0,monitor,false);
        clearListing(toAddr(0x83200000L),toAddr(0x8320ffffL));
        ArrayList<int[]> cases=new ArrayList<>();
        for(int bank=0;bank<4;bank++)for(int immediate=0;immediate<256;immediate++)for(int alias=0;alias<2;alias++){
            int d=bank*32+(immediate&31),b=alias==1?d:(d+33)%128;
            int word=0x18000210|((d&31)<<21)|((d&96)>>3)|((b&31)<<11)|((b&96)>>5)|((immediate&31)<<16)|((immediate&224)<<1);
            setInt(toAddr(0x83200000L+cases.size()*4),word);cases.add(new int[]{d,b,immediate});
        }
        long[] words={0x01234567L,0x89abcdefL,0xff00aa55L,0x76543210L};
        BigInteger input=BigInteger.ZERO;for(long w:words)input=input.shiftLeft(32).or(BigInteger.valueOf(w));
        EmulatorHelper e=new EmulatorHelper(currentProgram);
        try{int index=0;for(int[] c:cases){
            long at=0x83200000L+index*4;disassemble(toAddr(at));
            check(getInstructionAt(toAddr(at))!=null && getInstructionAt(toAddr(at)).getMnemonicString().equals("vpermwi128"),"Decode "+Long.toHexString(at));
            e.writeRegister(reg(c[0]),BigInteger.ZERO);e.writeRegister(reg(c[1]),input);
            e.writeRegister("crall",0x12345678L);e.writeRegister(e.getPCRegister(),at);
            check(e.step(monitor),e.getLastError());
            BigInteger expected=BigInteger.ZERO;for(int lane=0;lane<4;lane++)expected=expected.shiftLeft(32).or(BigInteger.valueOf(words[(c[2]>>(6-2*lane))&3]));
            check(e.readRegister(reg(c[0])).equals(expected),"Lane/alias mismatch "+Arrays.toString(c));
            if(c[0]!=c[1])check(e.readRegister(reg(c[1])).equals(input),"Source modified");
            check(e.readRegister("crall").equals(BigInteger.valueOf(0x12345678L)),"CR changed");index++;
        }}finally{e.dispose();}
        Files.writeString(root.resolve("permutation-validation.txt"),"PASS COMPLETE permutation_cases="+cases.size()+" all 256 immediates; all 128 register numbers; four register banks; distinct and aliased operands; known guest lane order; source/CR preservation\n");
    }
}
