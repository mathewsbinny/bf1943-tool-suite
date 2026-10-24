import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import java.nio.file.*;
import java.util.*;
import java.io.*;
public class ApplyHelpers extends GhidraScript {
    public void run() throws Exception {
        Path root=Path.of(getScriptArgs()[0]);
        List<String> rows=Files.readAllLines(root.resolve("helpers.tsv"));
        try(PrintWriter out=new PrintWriter(root.resolve("helper-application.txt").toFile())) {
            for(String line:rows.subList(1,rows.size())) {
                String[] c=line.split("\t"); Address start=toAddr(Long.parseLong(c[0],16)),end=toAddr(Long.parseLong(c[1],16)-1);
                AddressSet body=new AddressSet(start,end);
                Function f=currentProgram.getFunctionManager().getFunctionAt(start);
                if(f==null) f=currentProgram.getFunctionManager().createFunction(c[2],start,body,SourceType.ANALYSIS);
                else { f.setBody(body); f.setName(c[2],SourceType.ANALYSIS); }
                f.setComment("Register helper identified by complete instruction-byte pattern. Analyst-assigned conventional name. Interior suffix entry labels share this body. Raw pdata flag semantics remain unresolved; do not infer ARM/Thumb encoding.");
                new DisassembleCommand(start,body,true).applyTo(currentProgram,monitor);
                int decoded=0;
                for(Address a=start;a.compareTo(end)<=0;a=a.add(4)) if(currentProgram.getListing().getInstructionAt(a)!=null) decoded++;
                out.println(c[2]+" "+start+".."+end+" instructions="+decoded+"/"+(body.getNumAddresses()/4));
            }
            rows=Files.readAllLines(root.resolve("helper-entries.tsv"));
            for(String line:rows.subList(1,rows.size())) {
                String[] c=line.split("\t"); Address a=toAddr(Long.parseLong(c[0],16));
                createLabel(a,c[1],true);
                setEOLComment(a,"Verified suffix entry into shared register helper; see xex-helper-resolution.json.");
            }
            out.println("suffix entries="+(rows.size()-1));
            out.println("function count="+currentProgram.getFunctionManager().getFunctionCount());
        }
    }
}
