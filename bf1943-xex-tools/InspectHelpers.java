import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.nio.file.*;
import java.io.*;
public class InspectHelpers extends GhidraScript {
    public void run() throws Exception {
        long[] starts={0x827be800L,0x827be850L,0x827beed0L,0x827bef1cL,0x827bef70L,0x827bf208L};
        int[] sizes={80,84,76,76,664,664};
        try(PrintWriter out=new PrintWriter(Path.of(getScriptArgs()[0],"helper-disassembly.tsv").toFile())) {
            out.println("group\taddress\tword\tinstruction");
            for(int i=0;i<starts.length;i++) {
                Address a=toAddr(starts[i]);
                new DisassembleCommand(a,new AddressSet(a,a.add(sizes[i]-1)),true).applyTo(currentProgram,monitor);
                for(int p=0;p<sizes[i];p+=4) {
                    Address at=a.add(p); Instruction ins=currentProgram.getListing().getInstructionAt(at);
                    out.printf("%08x\t%s\t%08x\t%s%n",starts[i],at,getInt(at),ins==null?"UNDECODED":ins.toString());
                }
            }
        }
    }
}
