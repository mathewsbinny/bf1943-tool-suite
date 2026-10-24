import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import java.nio.file.*;
import java.math.BigInteger;
import java.util.*;
public class TestUnaligned extends GhidraScript {
    String reg(int n){return n<32?"vs"+(n+32):"v"+n;}
    void check(boolean b,String why)throws Exception{if(!b)throw new Exception(why);}
    static final long BLOCK=0x6000L;
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Files.writeString(root.resolve("unaligned-validation.txt"),"RUNNING\n");
        Map<String,Integer> value=new LinkedHashMap<>();
        for(String line:Files.readAllLines(root.resolve("vx128-table.tsv")).subList(1,Files.readAllLines(root.resolve("vx128-table.tsv")).size())){
            String[] c=line.split("\t");if(c[0].matches("(lv|stv)[lr]xl?(128)?"))value.put(c[0],(int)Long.parseLong(c[3],16));
        }
        check(value.size()==16,"Expected 16 unaligned forms, found "+value.keySet());
        // Case: form, register, offset, RA (0 = literal zero, else r3 holds the block and r11 the offset).
        ArrayList<Object[]> cases=new ArrayList<>();ArrayList<Integer> words=new ArrayList<>();
        for(var e:value.entrySet()){
            boolean wide=e.getKey().endsWith("128");
            for(int v=0;v<(wide?128:32);v++)for(int off=0;off<16;off++){
                int ra=(v==5)?3:0;
                int word=e.getValue()|((v&31)<<21)|(wide?((v&96)>>3):0)|(ra<<16)|(11<<11);
                words.add(word);cases.add(new Object[]{e.getKey(),v,off,ra});
                if(v==5){words.add(e.getValue()|((v&31)<<21)|(11<<11));cases.add(new Object[]{e.getKey(),v,off,0});}
            }
        }
        if(currentProgram.getMemory().getBlock(toAddr(0x83500000L))==null)currentProgram.getMemory().createInitializedBlock("unaligned_test",toAddr(0x83500000L),0x20000,(byte)0,monitor,false);
        clearListing(toAddr(0x83500000L),toAddr(0x8351ffffL));
        for(int i=0;i<words.size();i++)setInt(toAddr(0x83500000L+4L*i),words.get(i));
        for(int i=0;i<words.size();i++){
            Address a=toAddr(0x83500000L+4L*i);disassemble(a);Instruction ins=getInstructionAt(a);
            check(ins!=null&&ins.getMnemonicString().equals(cases.get(i)[0]),"Decode "+a+" "+cases.get(i)[0]);
        }
        byte[] block=new byte[16],source=new byte[16];
        for(int i=0;i<16;i++){block[i]=(byte)(0x10+i*7);source[i]=(byte)(0xc0+i*3);}
        EmulatorHelper e=new EmulatorHelper(currentProgram);int count=0;
        try{for(int i=0;i<cases.size();i++){
            String name=(String)cases.get(i)[0];int v=(int)cases.get(i)[1],off=(int)cases.get(i)[2],ra=(int)cases.get(i)[3];
            boolean load=name.startsWith("lv"),right=name.charAt(load?2:3)=='r';
            byte[] memory=new byte[48];Arrays.fill(memory,(byte)0xa5);System.arraycopy(block,0,memory,16,16);
            e.writeMemory(toAddr(BLOCK-16),memory);
            if(ra==0)e.writeRegister("r11",BLOCK+off);else{e.writeRegister("r3",BLOCK);e.writeRegister("r11",off);}
            e.writeRegister(reg(v),load?new BigInteger("5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",16):new BigInteger(1,source));
            int guard=(v+1)%(name.endsWith("128")?128:32);e.writeRegister(reg(guard),BigInteger.valueOf(0x1234));
            e.writeRegister("crall",0x12345678L);e.writeRegister(e.getPCRegister(),0x83500000L+4L*i);
            check(e.step(monitor),e.getLastError());
            // Independent byte model of the Cell definitions; guest byte 0 is most significant.
            byte[] expectedMemory=memory.clone();byte[] expectedRegister=new byte[16];
            if(load&&!right)for(int k=0;k<16-off;k++)expectedRegister[k]=block[off+k];
            if(load&&right&&off!=0)for(int k=0;k<off;k++)expectedRegister[16-off+k]=block[k];
            if(!load&&!right)for(int k=0;k<16-off;k++)expectedMemory[16+off+k]=source[k];
            if(!load&&right)for(int k=0;k<off;k++)expectedMemory[16+k]=source[16-off+k];
            String label=name+" v"+v+" offset="+off+" ra="+ra;
            check(Arrays.equals(e.readMemory(toAddr(BLOCK-16),48),expectedMemory),"Memory "+label);
            if(load)check(e.readRegister(reg(v)).equals(new BigInteger(1,expectedRegister)),"Register "+label+" got "+e.readRegister(reg(v)).toString(16));
            else check(e.readRegister(reg(v)).equals(new BigInteger(1,source)),"Source modified "+label);
            if(guard!=v)check(e.readRegister(reg(guard)).equals(BigInteger.valueOf(0x1234)),"Adjacent register "+label);
            check(e.readRegister("crall").equals(BigInteger.valueOf(0x12345678L)),"CR changed "+label);
            count++;
        }}finally{e.dispose();}
        Files.writeString(root.resolve("unaligned-validation.txt"),"PASS COMPLETE unaligned_cases="+count+"; 16 forms; all 16 offsets; all register numbers per form; RA=0 and RA+RB addressing; guard bytes outside the aligned block; source/adjacent/CR preservation\n");
    }
}
