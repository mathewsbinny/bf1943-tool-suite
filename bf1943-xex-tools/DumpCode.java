// Read-only pseudo-disassembly around addresses that lie outside any .pdata function.
import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.app.util.PseudoInstruction;
import ghidra.program.model.lang.*;
import ghidra.program.model.listing.Function;
import ghidra.program.util.DefaultLanguageService;
import java.nio.file.*;
import java.io.*;
public class DumpCode extends GhidraScript {
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Files.createDirectories(root.resolve("inspect"));
        currentProgram.setLanguage(DefaultLanguageService.getLanguageService().getLanguage(new LanguageID("PowerPC:BE:64:XenonVMX128")),new CompilerSpecID("default"),true,monitor);
        PseudoDisassembler pd=new PseudoDisassembler(currentProgram);
        try(PrintWriter out=new PrintWriter(root.resolve("inspect/dump-code.txt").toFile())){
            for(String text:getScriptArgs()[1].split("[ ,]+")){
                long at=Long.parseLong(text,16);out.println("== "+text);
                for(long a=at-0x20;a<=at+0x10;a+=4){
                    Function f=getFunctionContaining(toAddr(a));String ins;
                    try{PseudoInstruction p=pd.disassemble(toAddr(a));ins=p==null?"UNDECODED":p.toString();}catch(Exception e){ins="UNDECODED";}
                    out.println((a==at?"> ":"  ")+Long.toHexString(a)+" "+String.format("%08x",getInt(toAddr(a)))+" "+ins+(f!=null?"   ["+f.getName()+"]":""));
                }
            }
        }
    }
}
