import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.PcodeOp;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;
public class TestRlimi extends GhidraScript {
    String reg(int n){return n<32?"vs"+(n+32):"v"+n;}
    void check(boolean b,String why)throws Exception{if(!b)throw new Exception(why);}
    int encode(int d,int b,int imm,int z){return 0x18000710|((d&31)<<21)|((d&96)>>3)|((b&31)<<11)|((b&96)>>5)|(imm<<16)|(z<<6);}
    BigInteger pack(long[] w){BigInteger v=BigInteger.ZERO;for(long x:w)v=v.shiftLeft(32).or(BigInteger.valueOf(x));return v;}
    String userOps(Instruction ins){StringBuilder s=new StringBuilder();
        for(PcodeOp op:ins.getPcode())if(op.getOpcode()==PcodeOp.CALLOTHER)s.append(currentProgram.getLanguage().getUserDefinedOpName((int)op.getInput(0).getOffset())).append(' ');
        return s.toString().trim();}
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Files.writeString(root.resolve("rlimi-validation.txt"),"RUNNING\n");
        if(currentProgram.getMemory().getBlock(toAddr(0x83300000L))==null)currentProgram.getMemory().createInitializedBlock("rlimi_test",toAddr(0x83300000L),0x20000,(byte)0,monitor,false);
        clearListing(toAddr(0x83300000L),toAddr(0x8331ffffL));
        long[] bw={0x01234567L,0x89abcdefL,0xff00aa55L,0x76543210L},dw={0xd0d0d0d0L,0xd1d1d1d1L,0xd2d2d2d2L,0xd3d3d3d3L};
        BigInteger bv=pack(bw),dv=pack(dw);
        // Each case: destination, source, initial destination value, expected result, label.
        ArrayList<Object[]> cases=new ArrayList<>();ArrayList<Integer> words=new ArrayList<>();
        // Hand-computed vectors, independent of the model below. Lane order is most-significant word first.
        Object[][] fixed={{8,1,new long[]{bw[1],dw[1],dw[2],dw[3]}},{15,2,new long[]{bw[2],bw[3],bw[0],bw[1]}},{1,3,new long[]{dw[0],dw[1],dw[2],bw[2]}},
            {0,2,dw},{15,0,bw},{6,1,new long[]{dw[0],bw[2],bw[3],dw[3]}}};
        for(Object[] f:fixed){words.add(encode(5,70,(int)f[0],(int)f[1]));cases.add(new Object[]{5,70,dv,pack((long[])f[2]),"literal mask="+f[0]+" z="+f[1]});}
        int literals=cases.size();
        for(int imm=0;imm<16;imm++)for(int z=0;z<4;z++)for(int d=0;d<128;d++)for(int alias=0;alias<2;alias++){
            int b=alias==1?d:(d+37+imm)%128;
            long[] dl=alias==1?bw:dw;long[] expected=new long[4];
            for(int g=0;g<4;g++)expected[g]=((imm>>(3-g))&1)==1?bw[(g+z)%4]:dl[g];
            words.add(encode(d,b,imm,z));cases.add(new Object[]{d,b,alias==1?bv:dv,pack(expected),"imm="+imm+" z="+z+" d="+d+" b="+b+" alias="+alias});
        }
        // UIMM bit 4 is not interpreted by either reference; it must decode but stay explicitly opaque.
        int firstOpaque=words.size();
        for(int imm=16;imm<32;imm++)words.add(encode(9,99,imm,imm&3));
        // Write every word before the emulator caches memory pages.
        for(int i=0;i<words.size();i++)setInt(toAddr(0x83300000L+i*4L),words.get(i));
        for(int i=0;i<words.size();i++){
            Address a=toAddr(0x83300000L+i*4L);disassemble(a);Instruction ins=getInstructionAt(a);
            check(ins!=null && ins.getMnemonicString().equals("vrlimi128"),"Decode "+a);
            check(i<firstOpaque?userOps(ins).isEmpty():userOps(ins).equals("VMX128_unresolved_vrlimi128_imm4"),"Semantics class at "+a+" ops "+userOps(ins));
        }
        EmulatorHelper e=new EmulatorHelper(currentProgram);
        try{for(int i=0;i<cases.size();i++){
            Object[] c=cases.get(i);int d=(int)c[0],b=(int)c[1];
            e.writeRegister(reg(d),(BigInteger)c[2]);e.writeRegister(reg(b),bv);e.writeRegister("crall",0x12345678L);
            e.writeRegister(e.getPCRegister(),0x83300000L+i*4L);check(e.step(monitor),e.getLastError());
            check(e.readRegister(reg(d)).equals(c[3]),"Mismatch "+c[4]+" got "+e.readRegister(reg(d)).toString(16));
            if(d!=b)check(e.readRegister(reg(b)).equals(bv),"Source modified "+c[4]);
            check(e.readRegister("crall").equals(BigInteger.valueOf(0x12345678L)),"CR changed "+c[4]);
        }}finally{e.dispose();}
        Files.writeString(root.resolve("rlimi-validation.txt"),"PASS COMPLETE rlimi_cases="+(cases.size()-literals)+" literal_vectors="+literals+" opaque_imm4_cases="+(words.size()-firstOpaque)+"; all 16 masks; all 4 rotations; all 128 destination registers; distinct and aliased source; guest lane order; source/CR preservation\n");
    }
}
