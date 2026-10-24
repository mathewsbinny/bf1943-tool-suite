// Read-only experiments on one function that fails decompilation: annotation-only variants, never semantic changes.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.program.model.data.*;
import ghidra.program.model.lang.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.util.DefaultLanguageService;
import java.nio.file.*;
import java.io.*;
import java.util.*;
public class ExperimentDecompile extends GhidraScript {
    PrintWriter log;
    boolean respectReadOnly=true;
    String attempt(Function f,String label,Path out)throws Exception{
        DecompInterface di=new DecompInterface();
        try{di.toggleCCode(true);di.setSimplificationStyle("decompile");
            DecompileOptions options=new DecompileOptions();options.grabFromProgram(currentProgram);options.setRespectReadOnly(respectReadOnly);di.setOptions(options);
            di.openProgram(currentProgram);
            DecompileResults r=di.decompileFunction(f,600,monitor);
            String c=r.getDecompiledFunction()==null?null:r.getDecompiledFunction().getC();
            log.println(label+": completed="+r.decompileCompleted()+" has_c="+(c!=null)+" error="+(r.getErrorMessage()==null?"":r.getErrorMessage().trim()));log.flush();
            if(c!=null)Files.writeString(out,"/* variant: "+label+" */\n"+c);
            return c;
        }finally{di.dispose();}
    }
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Path dir=root.resolve("experiment");Files.createDirectories(dir);
        String[] args=getScriptArgs()[1].split("[ ,]+");
        long entry=Long.parseLong(args[0],16);
        log=new PrintWriter(dir.resolve(args[0]+".log").toFile());
        currentProgram.setLanguage(DefaultLanguageService.getLanguageService().getLanguage(new LanguageID("PowerPC:BE:64:XenonVMX128")),new CompilerSpecID("default"),true,monitor);
        Function f=getFunctionAt(toAddr(entry));
        new DisassembleCommand(f.getEntryPoint(),f.getBody(),true).applyTo(currentProgram,monitor);
        Path out=dir.resolve(args[0]+".c");
        // Raw p-code: which instructions touch vector-register storage with a size other than 16 bytes.
        for(Instruction ins:currentProgram.getListing().getInstructions(f.getBody(),true))
            for(ghidra.program.model.pcode.PcodeOp op:ins.getPcode()){
                List<ghidra.program.model.pcode.Varnode> vs=new ArrayList<>(Arrays.asList(op.getInputs()));if(op.getOutput()!=null)vs.add(op.getOutput());
                for(var v:vs)if(v.isRegister()&&v.getOffset()>=0x4200&&v.getOffset()<0x4400&&v.getSize()!=16)
                    log.println("  partial "+ins.getAddress()+" "+ins+" :: "+op);
            }
        if(attempt(f,"baseline",out)!=null){log.close();return;}
        // Hypothesis: read-only constants loaded at 4 and 16 bytes from the same .rdata address become overlapping RAM inputs.
        TreeMap<Long,TreeSet<Integer>> constantReads=new TreeMap<>();Map<Integer,Long> high=new HashMap<>();
        for(Instruction ins:currentProgram.getListing().getInstructions(f.getBody(),true)){
            int w=getInt(ins.getAddress()),op=w>>>26,rd=(w>>21)&31,ra=(w>>16)&31;short imm=(short)(w&0xffff);
            if(op==15&&ra==0){high.put(rd,(long)(imm<<16)&0xffffffffL);continue;}
            if((op==48||op==50||op==32)&&high.containsKey(ra))constantReads.computeIfAbsent((high.get(ra)+imm)&0xffffffffL,k->new TreeSet<>()).add(op==50?8:4);
            if(op==14&&high.containsKey(ra))high.put(rd,(high.get(ra)+imm)&0xffffffffL);
            if(ins.getMnemonicString().startsWith("lvx128")||ins.getMnemonicString().equals("lvx")){
                int a=(w>>16)&31,b=(w>>11)&31;Long base=high.get(a)!=null?high.get(a):high.get(b);
                if(base!=null)constantReads.computeIfAbsent(base&~15L,k->new TreeSet<>()).add(16);
            }
        }
        for(var e:constantReads.entrySet())log.println("  constant read 0x"+Long.toHexString(e.getKey())+" sizes="+e.getValue()+" block="+(currentProgram.getMemory().getBlock(toAddr(e.getKey()))==null?"none":currentProgram.getMemory().getBlock(toAddr(e.getKey())).getName()));
        respectReadOnly=false;
        if(attempt(f,"respectReadOnly=false",out)!=null){log.close();return;}
        respectReadOnly=true;
        for(var e:constantReads.entrySet())if(e.getValue().contains(16)){
            try{createData(toAddr(e.getKey()),new ArrayDataType(FloatDataType.dataType,4,4));}catch(Exception ex){log.println("  data "+Long.toHexString(e.getKey())+" "+ex.getMessage());}
        }
        if(attempt(f,"float[4] data at vector constants",out)!=null){log.close();return;}
        // Diagnose: in each lighter style that completes, list input varnodes and report any overlapping pair.
        for(String style:new String[]{"register","paramid","jumptable","firstpass"}){
            DecompInterface di=new DecompInterface();
            try{di.toggleSyntaxTree(true);di.setSimplificationStyle(style);di.openProgram(currentProgram);
                DecompileResults r=di.decompileFunction(f,600,monitor);
                ghidra.program.model.pcode.HighFunction h=r.getHighFunction();
                log.println("style "+style+": completed="+r.decompileCompleted()+" high="+(h!=null)+" error="+(r.getErrorMessage()==null?"":r.getErrorMessage().trim()));
                if(h==null)continue;
                List<ghidra.program.model.pcode.Varnode> inputs=new ArrayList<>();
                for(Iterator<ghidra.program.model.pcode.VarnodeAST> it=h.locRange();it.hasNext();){var v=it.next();if(v.isInput())inputs.add(v);}
                for(var v:inputs)log.println("  input "+v.getAddress()+" size="+v.getSize()+" reg="+currentProgram.getRegister(v.getAddress(),v.getSize()));
                for(int i=0;i<inputs.size();i++)for(int j=i+1;j<inputs.size();j++){var a=inputs.get(i);var b=inputs.get(j);
                    if(a.getSpace()==b.getSpace()&&a.getOffset()<b.getOffset()+b.getSize()&&b.getOffset()<a.getOffset()+a.getSize())log.println("  OVERLAP "+a.getAddress()+":"+a.getSize()+" with "+b.getAddress()+":"+b.getSize());}
            }finally{di.dispose();}
        }
        log.flush();
        // Frame size from the stwu r1,-N(r1) prologue; r1-relative bases become entry-SP-relative frame offsets.
        int frame=0;TreeSet<Integer> slots=new TreeSet<>();
        for(Instruction ins:currentProgram.getListing().getInstructions(f.getBody(),true)){
            int w=getInt(ins.getAddress()),op=w>>>26,rd=(w>>21)&31,ra=(w>>16)&31;short imm=(short)(w&0xffff);
            if(op==37&&rd==1&&ra==1)frame=-imm;
            else if(frame>0&&op==14&&ra==1&&(imm&15)==0)slots.add(imm-frame);
        }
        log.println("frame=0x"+Integer.toHexString(frame)+" vector slots (entry-relative)="+slots);
        StackFrame sf=f.getStackFrame();
        DataType vec=new ArrayDataType(FloatDataType.dataType,4,4);
        for(int s:slots){try{sf.createVariable("vec_"+Integer.toHexString(-s),s,vec,SourceType.USER_DEFINED);}catch(Exception e){log.println("slot "+s+" "+e);}}
        if(attempt(f,"float[4] stack slots",out)!=null){log.close();return;}
        for(int s:slots){Variable v=sf.getVariableContaining(s);if(v!=null)sf.clearVariable(v.getStackOffset());}
        DataType raw=new ArrayDataType(ByteDataType.dataType,16,1);
        for(int s:slots){try{sf.createVariable("raw_"+Integer.toHexString(-s),s,raw,SourceType.USER_DEFINED);}catch(Exception e){log.println("slot "+s+" "+e);}}
        if(attempt(f,"byte[16] stack slots",out)!=null){log.close();return;}
        for(int s:slots){Variable v=sf.getVariableContaining(s);if(v!=null)sf.clearVariable(v.getStackOffset());}
        // Parameter recovery is where the overlap is raised (paramid fails, jumptable succeeds); lock the prototype so it is skipped.
        f.updateFunction(null,new ReturnParameterImpl(VoidDataType.dataType,currentProgram),new ArrayList<Variable>(),
            Function.FunctionUpdateType.DYNAMIC_STORAGE_ALL_PARAMS,true,SourceType.USER_DEFINED);
        if(attempt(f,"prototype locked void(void)",out)!=null){log.close();return;}
        // Diagnosis only: cut one path at a time and report which cut avoids the error. Never used as output.
        Path scratch=dir.resolve(args[0]+".diagnostic.c");
        for(Instruction ins:currentProgram.getListing().getInstructions(f.getBody(),true)){
            var flow=ins.getFlowType();FlowOverride cut;
            if(flow.isConditional()&&flow.isJump())cut=FlowOverride.RETURN;
            else if(flow.isCall())cut=FlowOverride.CALL_RETURN;
            else continue;
            ins.setFlowOverride(cut);
            String c=attempt(f,"diagnostic cut "+cut+" at "+ins.getAddress()+" "+ins,scratch);
            ins.setFlowOverride(FlowOverride.NONE);
        }
        // Diagnosis only, in the read-only session: nop one instruction at a time past the formerly undecodable lvlx, then restore it.
        if(args.length>2){
            long lo=Long.parseLong(args[1],16),hi=Long.parseLong(args[2],16);
            for(long a=lo;a<hi;a+=4){
                int original=getInt(toAddr(a));Instruction was=getInstructionAt(toAddr(a));String text=was==null?"?":was.toString();
                clearListing(toAddr(a));setInt(toAddr(a),0x60000000);disassemble(toAddr(a));
                attempt(f,"diagnostic nop at "+Long.toHexString(a)+" ("+text+")",scratch);
                clearListing(toAddr(a));setInt(toAddr(a),original);disassemble(toAddr(a));
                if(getInt(toAddr(a))!=original)throw new Exception("restore failed at "+Long.toHexString(a));
            }
        }
        log.println("no variant produced C");log.close();
    }
}
