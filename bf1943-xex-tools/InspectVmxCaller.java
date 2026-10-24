import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import java.nio.file.*;
import java.io.*;
import java.util.*;
import java.security.MessageDigest;
public class InspectVmxCaller extends GhidraScript {
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);
        for(long[] range:new long[][]{{0x82348ec8L,0x82349003L},{0x82220460L,0x822204a3L}}){
        Address start=toAddr(range[0]),end=toAddr(range[1]);AddressSet body=new AddressSet(start,end);
        String stem="caller-"+start;
        clearListing(start,end);
        new DisassembleCommand(start,body,true).applyTo(currentProgram,monitor);
        Function f=getFunctionAt(start);if(f==null)f=currentProgram.getFunctionManager().createFunction("pdata_"+start,start,body,SourceType.ANALYSIS);
        int decoded=0,unknown=0,logical=0;
        try(PrintWriter out=new PrintWriter(root.resolve(stem+".asm").toFile())){
            for(Address at=start;at.compareTo(end)<=0;at=at.add(4)){
                Instruction ins=getInstructionAt(at);
                if(ins!=null){decoded++;if(ins.getMnemonicString().equals("vor128"))logical++;}else unknown++;
                out.println(at+" "+(ins==null?"UNDECODED":ins));
            }
        }
        DecompInterface di=new DecompInterface();String error;boolean complete;
        try{di.openProgram(currentProgram);DecompileResults r=di.decompileFunction(f,60,monitor);complete=r.decompileCompleted();error=r.getErrorMessage();
            if(r.getDecompiledFunction()!=null)Files.writeString(root.resolve(stem+".c"),r.getDecompiledFunction().getC());
        }finally{di.dispose();}
        byte[] data=new byte[14352384];currentProgram.getMemory().getBytes(toAddr(0x82000000L),data);
        String hash=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
        if(!hash.equals("cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a"))throw new Exception("Image changed");
        Files.writeString(root.resolve(range[0]==0x82348ec8L?"caller-validation.txt":"permutation-caller-validation.txt"),"decoded="+decoded+" unknown_words="+unknown+" vor128="+logical+" decompile_completed="+complete+" diagnostic="+error+"\nimage_sha256="+hash+"\n");
        }
    }
}
