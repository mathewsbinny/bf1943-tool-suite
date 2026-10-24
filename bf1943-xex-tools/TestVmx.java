import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;
import java.io.*;
import java.security.MessageDigest;
public class TestVmx extends GhidraScript {
    String reg(int r){return r<32?"vs"+(r+32):"v"+r;}
    void check(boolean yes,String why) throws Exception {if(!yes)throw new Exception(why);}
    public void run() throws Exception {
        Path root=Path.of(getScriptArgs()[0]);
        Files.writeString(root.resolve("validation.txt"),"RUNNING\n");
        byte[] original=new byte[14352384];currentProgram.getMemory().getBytes(toAddr(0x82000000L),original);
        String expectedHash="cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a";
        check(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(original)).equals(expectedHash),"Original image identity");
        if(currentProgram.getMemory().getBlock(toAddr(0x83000000L))==null)
            currentProgram.getMemory().createInitializedBlock("synthetic_test_code",toAddr(0x83000000L),0x10000,(byte)0,monitor,false);
        clearListing(toAddr(0x83000000L),toAddr(0x8300ffffL));
        long[][] cases={{0,11,0x7777,0,0x1007,0x1000},{1,0,0x17,0x1000,0,0x1010},{1,11,0,0xfffffff8L,0x29,0x20},{1,11,0,0x1003,0xc,0x1000},{0,11,0,0,0x10000100fL,0x1000}};
        byte[] pattern=new byte[16];for(int i=0;i<16;i++)pattern[i]=(byte)(i*13+7);
        int count=0;
        for(int v=0;v<128;v++)for(int store=0;store<2;store++)for(long[] c:cases){
            long addr=0x83000000L+count*4;
            int word=0x100000c3|(store<<8)|((v&31)<<21)|((v&96)>>3)|((int)c[0]<<16)|((int)c[1]<<11);
            setInt(toAddr(addr),word);count++;
        }
        EmulatorHelper emu=new EmulatorHelper(currentProgram);count=0;
        try {
            for(int v=0;v<128;v++)for(int store=0;store<2;store++)for(long[] c:cases){
                long addr=0x83000000L+count*4;
                disassemble(toAddr(addr));
                Instruction ins=getInstructionAt(toAddr(addr));
                check(ins!=null && ins.getMnemonicString().equals(store==1?"stvx128":"lvx128"),"Decode "+Long.toHexString(addr));
                emu.writeRegister("r0",c[2]);emu.writeRegister("r1",c[3]);emu.writeRegister("r11",c[4]);
                byte[] guard=new byte[48];Arrays.fill(guard,(byte)0x55);emu.writeMemory(toAddr(c[5]-16),guard);
                emu.writeRegister(reg(v),store==1?new BigInteger(1,pattern):BigInteger.ZERO);
                emu.writeRegister(reg((v+1)%128),new BigInteger("123456789abcdef",16));
                if(store==0)emu.writeMemory(toAddr(c[5]),pattern);
                emu.writeRegister(emu.getPCRegister(),addr);
                check(emu.step(monitor),"Emulation failed: "+emu.getLastError());
                check(store==1?Arrays.equals(emu.readMemory(toAddr(c[5]),16),pattern):emu.readRegister(reg(v)).equals(new BigInteger(1,pattern)),"Byte order/register/address mismatch v"+v);
                check(emu.readRegister(reg((v+1)%128)).equals(new BigInteger("123456789abcdef",16)),"Register alias corruption");
                check(emu.readMemoryByte(toAddr(c[5]-1))==0x55 && emu.readMemoryByte(toAddr(c[5]+16))==0x55,"Store overrun");count++;
            }
        }finally{emu.dispose();}
        try(PrintWriter out=new PrintWriter(root.resolve("validation.txt").toFile())){
            out.println("PASS synthetic p-code cases="+count+"; all 128 vector registers; load/store; RA0, RB0, alignment, wrap, high bits, known byte order, adjacent register and memory guards");
            DecompInterface di=new DecompInterface();
            try{di.openProgram(currentProgram);
                for(long start:new long[]{0x827bf004L,0x827bf29cL}){
                    Address a=toAddr(start);AddressSet body=new AddressSet(a,a.add(515));
                    new DisassembleCommand(a,body,true).applyTo(currentProgram,monitor);
                    int decoded=0;for(int i=0;i<516;i+=4)if(getInstructionAt(a.add(i))!=null)decoded++;
                    check(decoded==129,"Helper decoding incomplete");
                    Function f=getFunctionAt(a);
                    if(f==null)f=currentProgram.getFunctionManager().createFunction(start==0x827bf004L?"__savevmx_64":"__restvmx_64",a,body,SourceType.ANALYSIS);
                    DecompileResults result=di.decompileFunction(f,60,monitor);
                    check(result.decompileCompleted(),"Helper decompile: "+result.getErrorMessage());
                    Files.writeString(root.resolve(f.getName()+".c"),result.getDecompiledFunction().getC());
                    EmulatorHelper helper=new EmulatorHelper(currentProgram);
                    try{
                        helper.writeRegister("r12",0x5000);helper.writeRegister("LR",0x8300fff0L);
                        helper.writeRegister(reg(63),new BigInteger("abcdef",16));
                        for(int v=64;v<128;v++){
                            byte[] value=new byte[16];for(int j=0;j<16;j++)value[j]=(byte)(v+j*3);
                            helper.writeRegister(reg(v),start==0x827bf004L?new BigInteger(1,value):BigInteger.ZERO);
                            helper.writeMemory(toAddr(0x4c00+(v-64)*16),start==0x827bf29cL?value:new byte[16]);
                        }
                        helper.writeRegister(helper.getPCRegister(),start);
                        for(int i=0;i<129;i++)check(helper.step(monitor),"Full helper emulation");
                        for(int v=64;v<128;v++){
                            byte[] value=new byte[16];for(int j=0;j<16;j++)value[j]=(byte)(v+j*3);
                            check(start==0x827bf004L?Arrays.equals(helper.readMemory(toAddr(0x4c00+(v-64)*16),16),value):helper.readRegister(reg(v)).equals(new BigInteger(1,value)),"Full helper effect v"+v);
                        }
                        check(helper.getExecutionAddress().getOffset()==0x8300fff0L,"blr destination");
                        check(helper.readRegister(reg(63)).equals(new BigInteger("abcdef",16)),"Helper changed v63");
                    }finally{helper.dispose();}
                    out.println("PASS "+f.getName()+" instructions="+decoded+"/129 decompiled=true full_helper_effects=64_vectors return=verified");
                }
            }finally{di.dispose();}
            byte[] after=new byte[original.length];currentProgram.getMemory().getBytes(toAddr(0x82000000L),after);
            check(Arrays.equals(original,after),"Image bytes modified");
            out.println("PASS COMPLETE image_sha256="+expectedHash);
        }
    }
}
