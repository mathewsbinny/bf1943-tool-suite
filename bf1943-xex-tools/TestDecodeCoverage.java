// Every classified VX128 word and every reference-table encoding must decode to its expected mnemonic.
import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.app.util.PseudoInstruction;
import ghidra.program.model.pcode.PcodeOp;
import java.nio.file.*;
import java.io.*;
import java.util.*;
import java.security.MessageDigest;
public class TestDecodeCoverage extends GhidraScript {
    static final Map<Integer,String> OPCODE31=Map.of(21,"ldx",84,"ldarx",519,"lvlx",775,"lvlxl",551,"lvrx",807,"lvrxl",647,"stvlx",903,"stvlxl",679,"stvrx",935,"stvrxl");
    void check(boolean b,String why)throws Exception{if(!b)throw new Exception(why);}
    String digest()throws Exception{byte[] d=new byte[14352384];currentProgram.getMemory().getBytes(toAddr(0x82000000L),d);return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(d));}
    List<String> userOps(PseudoInstruction ins){List<String> s=new ArrayList<>();
        for(PcodeOp op:ins.getPcode())if(op.getOpcode()==PcodeOp.CALLOTHER)s.add(currentProgram.getLanguage().getUserDefinedOpName((int)op.getInput(0).getOffset()));
        return s;}
    String lastError="";
    PseudoInstruction decode(PseudoDisassembler pd,long at){try{return pd.disassemble(toAddr(at));}catch(Exception ex){lastError=ex.toString();return null;}}
    public void run()throws Exception{
        Path root=Path.of(getScriptArgs()[0]);Files.writeString(root.resolve("decode-coverage.txt"),"RUNNING\n");
        String expectedHash="cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a";
        check(digest().equals(expectedHash),"Original image identity");
        PseudoDisassembler pd=new PseudoDisassembler(currentProgram);
        Map<String,int[]> counts=new TreeMap<>();HashSet<Long> classified=new HashSet<>();
        List<String> rows=Files.readAllLines(root.resolve("classified.tsv"));
        Map<String,Integer> collisions=new TreeMap<>();
        try(PrintWriter out=new PrintWriter(root.resolve("decode-mismatches.tsv").toFile())){
            out.println("address\tfunction\tword\texpected\tdecoded");
            for(String line:rows.subList(1,rows.size())){
                String[] c=line.split("\t");long at=Long.parseLong(c[0],16);classified.add(at);
                PseudoInstruction ins=decode(pd,at);
                int[] n=counts.computeIfAbsent(c[3],k->new int[3]);n[0]++;
                String got=ins==null?"UNDECODED":ins.getMnemonicString();
                if(!got.equals(c[3])){out.println(line+"\t"+got);collisions.merge(c[3]+" -> "+got,1,Integer::sum);continue;}
                n[1]++;
                if(userOps(ins).stream().anyMatch(o->o.startsWith("VMX128_")))n[2]++;
            }
        }
        check(collisions.isEmpty(),"Classified words not decoding as classified: "+collisions);
        // Synthetic coverage of all 80 reference encodings, including those absent from this image.
        if(currentProgram.getMemory().getBlock(toAddr(0x83400000L))==null)currentProgram.getMemory().createInitializedBlock("decode_coverage_test",toAddr(0x83400000L),0x10000,(byte)0,monitor,false);
        clearListing(toAddr(0x83400000L),toAddr(0x8340ffffL));
        Map<String,String> collisionWords=new TreeMap<>();
        Random random=new Random(1943);long at=0x83400000L;int synthetic=0,entries=0,unaligned=0;
        rows=Files.readAllLines(root.resolve("vx128-table.tsv"));
        for(String line:rows.subList(1,rows.size())){
            String[] c=line.split("\t");int mask=(int)Long.parseLong(c[2],16),value=(int)Long.parseLong(c[3],16);entries++;
            for(int i=0;i<64;i++){
                int word=value|(random.nextInt()&~mask);setInt(toAddr(at),word);
                PseudoInstruction ins=decode(pd,at);
                if(ins==null || !ins.getMnemonicString().equals(c[0])){
                    collisions.merge(c[0]+" -> "+(ins==null?lastError.replaceAll(" at .*",""):ins.getMnemonicString()),1,Integer::sum);
                    collisionWords.putIfAbsent(c[0]+" -> "+(ins==null?"error":ins.getMnemonicString()),String.format("%08x",word));
                    at+=4;synthetic++;continue;
                }
                boolean opaque=userOps(ins).stream().anyMatch(o->o.startsWith("VMX128_"));
                boolean expectOpaque=c[4].equals("opaque")||(c[4].startsWith("vrlimi128")&&((word>>20)&1)==1)
                    ||(c[4].startsWith("partial:")&&!java.util.Arrays.asList(c[4].substring(8).split("\\|")).contains("xmuimm="+((word>>16)&31)));
                check(opaque==expectOpaque,"Semantics class "+c[0]+" word "+Integer.toHexString(word)+" ops "+userOps(ins));
                if(c[0].matches("(lv|stv)[lr]xl?(128)?")){
                    // Unaligned forms: memory confined to one aligned 16-byte access; the opaque op never receives a pointer.
                    int loads=0,stores=0;
                    for(PcodeOp op:ins.getPcode()){
                        if(op.getOpcode()==PcodeOp.LOAD){check(op.getOutput().getSize()==16,"Unaligned load size "+c[0]);loads++;}
                        if(op.getOpcode()==PcodeOp.STORE){check(op.getInput(2).getSize()==16,"Unaligned store size "+c[0]);stores++;}
                        if(op.getOpcode()==PcodeOp.CALLOTHER)for(int k=1;k<op.getNumInputs();k++)
                            check(op.getInput(k).getSize()==16||op.getInput(k).getSize()==1,"Pointer-sized opaque input "+c[0]+" "+op);
                    }
                    check(loads==1&&stores==(c[0].startsWith("stv")?1:0),"Unaligned memory shape "+c[0]+" loads="+loads+" stores="+stores);
                    unaligned++;
                }
                at+=4;synthetic++;
            }
        }
        check(collisions.isEmpty(),"Synthetic encodings not decoding as table entry: "+collisions+" examples "+collisionWords);
        // Informational: unclassified primary-opcode 4/5/6 words (AltiVec, data or unsupported encodings).
        int other=0,otherDecoded=0;TreeMap<String,Integer> opcode31=new TreeMap<>();
        try(PrintWriter out=new PrintWriter(root.resolve("unclassified-decode.tsv").toFile())){
            out.println("address\tword\tdecoded");
            rows=Files.readAllLines(root.resolve("../xex-analysis/functions.tsv"));
            for(String line:rows.subList(1,rows.size())){
                String[] c=line.split("\t");
                for(long a=Long.parseLong(c[0],16);a<Long.parseLong(c[1],16);a+=4){
                    int word=getInt(toAddr(a));int op=word>>>26;
                    if(op==31&&(word&1)==0&&OPCODE31.containsKey((word>>>1)&0x3ff)){
                        // Corrected inherited ldx/ldarx and the Xenon VR0-31 unaligned vector loads/stores.
                        String want=OPCODE31.get((word>>>1)&0x3ff);PseudoInstruction ins=decode(pd,a);
                        check(ins!=null&&ins.getMnemonicString().equals(want),"Opcode-31 word "+Long.toHexString(a)+" expected "+want);
                        opcode31.merge(want,1,Integer::sum);
                        continue;
                    }
                    if((op<4||op>6)||classified.contains(a))continue;
                    PseudoInstruction ins=decode(pd,a);other++;if(ins!=null)otherDecoded++;
                    out.println(Long.toHexString(a)+"\t"+String.format("%08x",word)+"\t"+(ins==null?"UNDECODED":ins.getMnemonicString()));
                }
            }
        }
        try(PrintWriter out=new PrintWriter(root.resolve("decode-coverage.tsv").toFile())){
            out.println("mnemonic\tclassified\tdecoded\twith_opaque_op");
            for(var e:counts.entrySet())out.println(e.getKey()+"\t"+e.getValue()[0]+"\t"+e.getValue()[1]+"\t"+e.getValue()[2]);
        }
        check(digest().equals(expectedHash),"Image bytes modified");
        Files.writeString(root.resolve("decode-coverage.txt"),"PASS COMPLETE classified_words="+classified.size()+" mnemonics="+counts.size()+" table_entries="+entries+" synthetic_words="+synthetic+" unaligned_memory_shape_checked="+unaligned
            +" unclassified_op4_5_6="+other+" unclassified_decoded="+otherDecoded+" opcode31="+opcode31.toString().replace(" ","")+" image_sha256="+expectedHash+"\n");
    }
}
