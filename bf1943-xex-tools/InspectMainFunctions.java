// Read-only listing of selected functions under the Xenon language: instructions, user ops, out-of-body branch targets.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.util.PseudoDisassembler;
import ghidra.app.util.PseudoInstruction;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.*;
import ghidra.program.util.DefaultLanguageService;
import java.nio.file.*;
import java.io.*;
public class InspectMainFunctions extends GhidraScript {
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Path out=root.resolve("inspect");Files.createDirectories(out);
        currentProgram.setLanguage(DefaultLanguageService.getLanguageService().getLanguage(new LanguageID("PowerPC:BE:64:XenonVMX128")),new CompilerSpecID("default"),true,monitor);
        PseudoDisassembler pd=new PseudoDisassembler(currentProgram);
        for(String text:getScriptArgs()[1].split(",")){
            Function f=getFunctionAt(toAddr(Long.parseLong(text,16)));if(f==null)throw new Exception("No function at "+text);
            new DisassembleCommand(f.getEntryPoint(),f.getBody(),true).applyTo(currentProgram,monitor);
            try(PrintWriter w=new PrintWriter(out.resolve(text+".asm").toFile())){
                w.println("# "+f.getName()+" body "+f.getBody());
                for(Instruction ins:currentProgram.getListing().getInstructions(f.getBody(),true)){
                    StringBuilder ops=new StringBuilder();
                    for(PcodeOp op:ins.getPcode())if(op.getOpcode()==PcodeOp.CALLOTHER)ops.append(" userop=").append(currentProgram.getLanguage().getUserDefinedOpName((int)op.getInput(0).getOffset()));
                    for(Address target:ins.getFlows())if(!f.getBody().contains(target)){
                        Function callee=getFunctionAt(target);String decodes;
                        try{PseudoInstruction p=pd.disassemble(target);decodes=p.toString();}catch(Exception e){decodes="UNDECODABLE "+e.getMessage();}
                        ops.append(" outside_target=").append(target).append(callee!=null?" function="+callee.getName():" not_a_function").append(" first=").append(decodes);
                        for(Symbol s:currentProgram.getSymbolTable().getSymbols(target))ops.append(" label=").append(s.getName());
                    }
                    w.println(ins.getAddress()+"  "+String.format("%08x",getInt(ins.getAddress()))+"  "+ins+ops);
                }
            }
        }
    }
}
