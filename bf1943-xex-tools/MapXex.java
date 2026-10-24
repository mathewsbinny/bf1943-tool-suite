// Apply evidence TSVs to the existing decoded-image project. Never patch bytes.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;
import java.util.*;
import java.io.*;
import java.security.MessageDigest;

public class MapXex extends GhidraScript {
    long hex(String s) { return Long.parseUnsignedLong(s.replace("0x", ""),16); }
    String digest() throws Exception {
        byte[] data=new byte[14352384];
        currentProgram.getMemory().getBytes(toAddr(0x82000000L),data);
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
    }
    void split(long at) throws Exception {
        Memory mem=currentProgram.getMemory(); Address a=toAddr(at);
        MemoryBlock b=mem.getBlock(a);
        if(b!=null && !b.getStart().equals(a)) mem.split(b,a);
    }
    public void run() throws Exception {
        Path root=Path.of(getScriptArgs()[0]);
        String before=digest();
        if(!before.equals("cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a")) throw new IOException("Unexpected memory identity");
        List<String> sections=Files.readAllLines(root.resolve("sections.tsv"));
        for(String line:sections.subList(1,sections.size())) {
            String[] c=line.split("\t"); long start=hex(c[1]),size=Long.parseLong(c[2]); split(start); split(start+size);
        }
        int gap=0;
        for(MemoryBlock b:currentProgram.getMemory().getBlocks()) {
            b.setName("unclassified_"+(gap++)); b.setRead(true); b.setWrite(false); b.setExecute(false);
        }
        for(String line:sections.subList(1,sections.size())) {
            String[] c=line.split("\t"); MemoryBlock b=currentProgram.getMemory().getBlock(toAddr(hex(c[1])));
            b.setName(c[0]); b.setRead(c[3].equals("1")); b.setWrite(c[4].equals("1")); b.setExecute(c[5].equals("1"));
        }
        int defined=0,conflicts=0; List<Function> samples=new ArrayList<>();
        try(PrintWriter report=new PrintWriter(root.resolve("mapping-report.txt").toFile())) {
            List<String> rows=Files.readAllLines(root.resolve("functions.tsv"));
            for(String line:rows.subList(1,rows.size())) {
                monitor.checkCancelled(); String[] c=line.split("\t");
                Address start=toAddr(hex(c[0])),end=toAddr(hex(c[1])-1);
                AddressSet body=new AddressSet(start,end);
                try {
                    Function f=currentProgram.getFunctionManager().getFunctionAt(start);
                    if(f==null) f=currentProgram.getFunctionManager().createFunction(c.length>4?c[4]:"pdata_"+c[0],start,body,c.length>4?SourceType.ANALYSIS:SourceType.IMPORTED);
                    else f.setBody(body);
                    f.setComment(c.length>4?"Full-byte-verified register helper block; split at actual blr where needed. Raw pdata bits retained in evidence; unwind flag semantics unresolved.":"BF1943 .pdata declared range; end exclusive 0x"+c[1]+"; prologue bytes="+c[2]+"; exception flag="+c[3]+". Not all bytes necessarily instructions.");
                    defined++;
                    if(samples.size()<8 || start.getOffset()==0x827d78b0L) samples.add(f);
                } catch(Exception e) { conflicts++; report.println("FUNCTION_CONFLICT "+line+" "+e); }
            }
            int imports=0;
            List<String> rowsI=Files.readAllLines(root.resolve("imports.tsv"));
            for(String line:rowsI.subList(1,rowsI.size())) {
                String[] c=line.split("\t",-1); String lib=c[0],name=c[2];
                Address slot=toAddr(hex(c[3]));
                createLabel(slot,"__imp_"+lib.replace('.','_')+"_"+name,true);
                setPlateComment(slot,"XEX import ordinal "+c[1]+" from "+lib+"; name resolved by reference table, not embedded symbol.");
                currentProgram.getExternalManager().addExtLocation(lib,name,null,SourceType.IMPORTED);
                currentProgram.getReferenceManager().addExternalReference(slot,lib,name,null,SourceType.IMPORTED,0,RefType.DATA);
                if(!c[4].isEmpty() && !c[4].equals("0") && !c[4].equals("0x0")) {
                    Address thunk=toAddr(hex(c[4]));
                    createLabel(thunk,name,true);
                    setPlateComment(thunk,"XEX unresolved import thunk for "+lib+" ordinal "+c[1]+". Original encoded bytes preserved; not treated as executable PPC before loader fixup.");
                }
                imports++;
            }
            report.println("pdata functions applied="+defined+" conflicts="+conflicts+" imports="+imports);
            DecompInterface di=new DecompInterface();
            try {
                di.openProgram(currentProgram);
                for(Function f:samples) {
                    new DisassembleCommand(f.getEntryPoint(),f.getBody(),true).applyTo(currentProgram,monitor);
                    DecompileResults r=di.decompileFunction(f,20,monitor);
                    report.println("SAMPLE "+f.getEntryPoint()+" complete="+r.decompileCompleted()+" "+r.getErrorMessage());
                    if(r.getDecompiledFunction()!=null) Files.writeString(root.resolve("mapped-"+f.getEntryPoint()+".c"),r.getDecompiledFunction().getC());
                }
            } finally { di.dispose(); }
            report.println("memory_before="+before); report.println("memory_after="+digest());
            if(!before.equals(digest())) throw new IOException("Memory changed");
            try(PrintWriter out=new PrintWriter(root.resolve("ghidra-functions.tsv").toFile())) {
                out.println("entry\tname\tbytes\tmin\tmax");
                for(Function f:currentProgram.getFunctionManager().getFunctions(true))
                    out.println(f.getEntryPoint()+"\t"+f.getName()+"\t"+f.getBody().getNumAddresses()+"\t"+f.getBody().getMinAddress()+"\t"+f.getBody().getMaxAddress());
            }
        }
    }
}
