// Reopen saved database read-only and validate mapped ranges, labels and bytes.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import java.nio.file.*;
import java.util.*;
import java.security.MessageDigest;
public class ValidateXex extends GhidraScript {
    long hex(String s) { return Long.parseUnsignedLong(s.replace("0x",""),16); }
    public void run() throws Exception {
        Path root=Path.of(getScriptArgs()[0]); int functions=0,imports=0,thunks=0;
        List<String> rows=Files.readAllLines(root.resolve("functions.tsv"));
        for(String line:rows.subList(1,rows.size())) {
            String[] c=line.split("\t"); long start=hex(c[0]),end=hex(c[1]);
            Function f=currentProgram.getFunctionManager().getFunctionAt(toAddr(start));
            if(f==null || f.getBody().getNumAddresses()!=end-start || f.getBody().getMaxAddress().getOffset()!=end-1)
                throw new Exception("Function mismatch: "+line);
            functions++;
        }
        rows=Files.readAllLines(root.resolve("imports.tsv"));
        for(String line:rows.subList(1,rows.size())) {
            String[] c=line.split("\t",-1);
            if(currentProgram.getReferenceManager().getReferencesFrom(toAddr(hex(c[3]))).length==0)
                throw new Exception("No import reference "+line);
            if(currentProgram.getSymbolTable().getSymbols(toAddr(hex(c[3]))).length==0)
                throw new Exception("No slot label "+line);
            if(!c[4].isEmpty()) {
                if(currentProgram.getSymbolTable().getSymbols(toAddr(hex(c[4]))).length==0) throw new Exception("No thunk label");
                thunks++;
            }
            imports++;
        }
        byte[] data=new byte[14352384]; currentProgram.getMemory().getBytes(toAddr(0x82000000L),data);
        int helperLabels=0;
        if(Files.exists(root.resolve("helper-entries.tsv"))) {
            rows=Files.readAllLines(root.resolve("helper-entries.tsv"));
            for(String line:rows.subList(1,rows.size())) {
                String[] c=line.split("\t"); boolean found=false;
                for(var symbol:currentProgram.getSymbolTable().getSymbols(toAddr(hex(c[0])))) if(symbol.getName().equals(c[1])) found=true;
                if(!found) throw new Exception("Missing helper label "+line);
                helperLabels++;
            }
        }
        String hash=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
        if(!hash.equals("cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a")) throw new Exception("Memory mismatch");
        Files.writeString(root.resolve("saved-validation.txt"),"PASS functions="+functions+" imports="+imports+" thunks="+thunks+" helper_labels="+helperLabels+" sha256="+hash+"\n");
    }
}
