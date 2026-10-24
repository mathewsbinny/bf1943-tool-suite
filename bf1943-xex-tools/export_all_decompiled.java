// Decompile every mapped function under the isolated Xenon language. Run with -readOnly: nothing is saved.
// Output is exploratory Ghidra pseudocode, not recovered original source.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.app.decompiler.parallel.*;
import ghidra.app.util.PseudoDisassembler;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.util.DefaultLanguageService;
import ghidra.util.task.TaskMonitor;
import java.nio.file.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.security.MessageDigest;
public class export_all_decompiled extends GhidraScript {
    static final String IMAGE="cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a";
    static final String LANGUAGE="PowerPC:BE:64:XenonVMX128";
    String digest()throws Exception{byte[] d=new byte[14352384];currentProgram.getMemory().getBytes(toAddr(0x82000000L),d);return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(d));}
    String fileName(Function f){return f.getEntryPoint().toString()+"_"+f.getName().replaceAll("[^A-Za-z0-9_]","_")+".c";}
    // Per-function facts gathered single-threaded from the listing before decompilation.
    static class Facts{int words,instructions,bad,unreached,unreachedDecodable,opaque,otherUserOps;boolean terminalCall;TreeSet<String> opaqueNames=new TreeSet<>();List<String> badWords=new ArrayList<>();}
    record Outcome(Function function,boolean completed,boolean hasC,String diagnostic,boolean badInstructionWarning,int warnings,String style){}
    Outcome decompile(DecompileResults r,Path out,String style)throws Exception{
        Function f=r.getFunction();String c=r.getDecompiledFunction()==null?null:r.getDecompiledFunction().getC();
        String diagnostic=r.getErrorMessage()==null?"":r.getErrorMessage().trim().replaceAll("\\s+"," ");
        if(c!=null)Files.writeString(out.resolve(fileName(f)),"/* Ghidra 12.1.4 pseudocode; language "+LANGUAGE+"; image sha256 "+IMAGE+"; simplification "+style+(style.equals("decompile")?"":" (FALLBACK after full decompile failed)")+".\n   Not recovered original source. VMX128_* calls are opaque: unknown results, memory effects not modeled. */\n"+c);
        int warnings=c==null?0:c.split("WARNING:",-1).length-1;
        return new Outcome(f,r.decompileCompleted(),c!=null,diagnostic,c!=null&&c.contains("WARNING: Bad instruction"),warnings,style);
    }
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);int limit=getScriptArgs().length>1?Integer.parseInt(getScriptArgs()[1]):Integer.MAX_VALUE;
        Path out=root.resolve("decompiled");Files.createDirectories(out);
        Path summary=root.resolve("batch-decompile.txt");Files.writeString(summary,"RUNNING\n");
        if(!digest().equals(IMAGE))throw new Exception("Unexpected image identity before language change");
        Language xenon=DefaultLanguageService.getLanguageService().getLanguage(new LanguageID(LANGUAGE));
        currentProgram.setLanguage(xenon,new CompilerSpecID("default"),true,monitor);
        if(!currentProgram.getLanguageID().toString().equals(LANGUAGE))throw new Exception("Language switch failed");
        if(!digest().equals(IMAGE))throw new Exception("Image changed by language switch");
        // Fresh bad-instruction evidence: drop stale error bookmarks from earlier stock-language disassembly.
        currentProgram.getBookmarkManager().removeBookmarks(BookmarkType.ERROR);
        List<Function> functions=new ArrayList<>();
        for(Function f:currentProgram.getFunctionManager().getFunctions(true)){if(functions.size()>=limit)break;functions.add(f);}
        Map<Function,Facts> facts=new HashMap<>();
        PseudoDisassembler pd=new PseudoDisassembler(currentProgram);
        monitor.initialize(functions.size());monitor.setMessage("Disassembling");
        for(Function f:functions){
            monitor.checkCancelled();monitor.incrementProgress(1);
            new DisassembleCommand(f.getEntryPoint(),f.getBody(),true).applyTo(currentProgram,monitor);
            Facts x=new Facts();facts.put(f,x);
            // A final unconditional call ending the .pdata range cannot return into this function; stop decompiler fall-through there.
            Instruction last=getInstructionContaining(f.getBody().getMaxAddress());
            if(last!=null&&last.getMaxAddress().equals(f.getBody().getMaxAddress())&&last.getFlowType().isCall()&&!last.getFlowType().isConditional()){last.setFlowOverride(FlowOverride.CALL_RETURN);x.terminalCall=true;}
            for(AddressRange range:f.getBody())for(Address a=range.getMinAddress();a.compareTo(range.getMaxAddress())<=0;a=a.add(4)){
                x.words++;Instruction ins=getInstructionAt(a);
                if(ins==null){
                    if(getInstructionContaining(a)!=null)continue;
                    x.unreached++;try{pd.disassemble(a);x.unreachedDecodable++;}catch(Exception ignored){}
                    continue;
                }
                x.instructions++;
                for(PcodeOp op:ins.getPcode())if(op.getOpcode()==PcodeOp.CALLOTHER){
                    String name=currentProgram.getLanguage().getUserDefinedOpName((int)op.getInput(0).getOffset());
                    if(name.startsWith("VMX128_")){x.opaque++;x.opaqueNames.add(name);}else x.otherUserOps++;
                }
            }
        }
        for(Bookmark b:(Iterable<Bookmark>)()->currentProgram.getBookmarkManager().getBookmarksIterator(BookmarkType.ERROR)){
            Function f=getFunctionContaining(b.getAddress());
            if(f!=null&&facts.containsKey(f)){Facts x=facts.get(f);x.bad++;x.badWords.add(b.getAddress()+"="+String.format("%08x",getInt(b.getAddress()))+" "+b.getComment());}
        }
        Map<Function,Outcome> outcomes=new ConcurrentHashMap<>();
        DecompileConfigurer configure=di->{di.toggleCCode(true);di.toggleSyntaxTree(false);di.setSimplificationStyle("decompile");};
        DecompilerCallback<Outcome> callback=new DecompilerCallback<>(currentProgram,configure){
            public Outcome process(DecompileResults r,TaskMonitor m)throws Exception{return decompile(r,out,"decompile");}};
        callback.setTimeout(120);
        monitor.setMessage("Decompiling");
        try{ParallelDecompiler.decompileFunctions(callback,currentProgram,functions.iterator(),o->outcomes.put(o.function(),o),monitor);}finally{callback.dispose();}
        // Retry anything without C single-threaded: full style with a longer timeout, then lighter styles as labelled fallbacks.
        List<Function> retried=new ArrayList<>();
        for(Function f:functions){Outcome o=outcomes.get(f);if(o==null||!o.hasC())retried.add(f);}
        for(String style:new String[]{"decompile","normalize","firstpass"}){
            DecompInterface di=new DecompInterface();
            try{configure.configure(di);di.setSimplificationStyle(style);di.openProgram(currentProgram);
                for(Function f:retried){Outcome o=outcomes.get(f);if(o!=null&&o.hasC())continue;
                    Outcome n=decompile(di.decompileFunction(f,900,monitor),out,style);
                    // Keep every attempt's diagnostic so a fallback never hides the full-style failure.
                    String history=(o==null?"":o.diagnostic()+" | ")+style+": "+(n.diagnostic().isEmpty()?(n.hasC()?"ok":"no C output"):n.diagnostic());
                    outcomes.put(f,new Outcome(f,n.completed(),n.hasC(),history,n.badInstructionWarning(),n.warnings(),style));}
            }finally{di.dispose();}
        }
        int decodedAll=0,withC=0,fullStyle=0,terminalCalls=0,diagnostics=0,opaqueFunctions=0,opaqueOps=0,warned=0;
        TreeMap<String,Integer> opaqueByName=new TreeMap<>();
        try(PrintWriter index=new PrintWriter(root.resolve("decompiled-index.tsv").toFile());PrintWriter report=new PrintWriter(root.resolve("decompile-diagnostics.tsv").toFile())){
            index.println("address\tname\tfile\tdecoded\tdecompiled\topaque_vmx_ops\twords\tinstructions\tbad_instructions\tunreached_words\tunreached_decodable\tother_userops\twarnings\tterminal_call_override\tstyle\topaque_op_names\tdiagnostic");
            report.println("address\tname\tkind\tdetail");
            for(Function f:functions){
                Facts x=facts.get(f);Outcome o=outcomes.get(f);
                boolean decoded=x.bad==0&&o!=null&&!o.badInstructionWarning();boolean decompiled=o!=null&&o.completed()&&o.hasC()&&o.style().equals("decompile");
                if(decoded)decodedAll++;if(o!=null&&o.hasC())withC++;if(decompiled)fullStyle++;if(x.terminalCall)terminalCalls++;
                if(x.opaque>0){opaqueFunctions++;opaqueOps+=x.opaque;}
                for(String n:x.opaqueNames)opaqueByName.merge(n,1,Integer::sum);
                String diagnostic=o==null?"no result":o.diagnostic();
                if(!diagnostic.isEmpty()){diagnostics++;report.println(f.getEntryPoint()+"\t"+f.getName()+"\tdiagnostic\t"+diagnostic);}
                if(!decoded)report.println(f.getEntryPoint()+"\t"+f.getName()+"\tdecode\tbad_instructions="+x.bad+" decompiler_bad_instruction_warning="+(o!=null&&o.badInstructionWarning())+" "+String.join("; ",x.badWords));
                if(!decompiled)report.println(f.getEntryPoint()+"\t"+f.getName()+"\tdecompile\tcompleted="+(o!=null&&o.completed())+" has_c="+(o!=null&&o.hasC())+" style="+(o==null?"":o.style()));
                if(o!=null&&o.warnings()>0)warned++;
                index.println(f.getEntryPoint()+"\t"+f.getName()+"\t"+(o!=null&&o.hasC()?fileName(f):"")+"\t"+decoded+"\t"+decompiled+"\t"+x.opaque+"\t"+x.words+"\t"+x.instructions+"\t"+x.bad+"\t"+x.unreached+"\t"+x.unreachedDecodable+"\t"+x.otherUserOps+"\t"+(o==null?0:o.warnings())+"\t"+x.terminalCall+"\t"+(o==null?"":o.style())+"\t"+String.join(",",x.opaqueNames)+"\t"+diagnostic);
            }
        }
        try(PrintWriter tally=new PrintWriter(root.resolve("decompiled-opaque-tally.tsv").toFile())){
            tally.println("pcodeop\tfunctions");for(var e:opaqueByName.entrySet())tally.println(e.getKey()+"\t"+e.getValue());
        }
        String after=digest();if(!after.equals(IMAGE))throw new Exception("Image changed during batch");
        int total=currentProgram.getFunctionManager().getFunctionCount();
        boolean complete=functions.size()==total&&decodedAll==total&&withC==total&&fullStyle==total;
        Files.writeString(summary,(complete?"PASS COMPLETE ":"INCOMPLETE ")+"functions="+functions.size()+"/"+total+" decoded="+decodedAll+" with_c="+withC+" full_style="+fullStyle+" retried="+retried.size()+" terminal_call_overrides="+terminalCalls
            +" diagnostics="+diagnostics+" functions_with_c_warnings="+warned+" functions_with_opaque_vmx="+opaqueFunctions+" opaque_vmx_ops="+opaqueOps+" language="+LANGUAGE+" image_sha256="+after+"\n");
    }
}
