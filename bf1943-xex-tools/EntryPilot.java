// Ghidra script: bounded entry-point disassembly and pseudocode, no target execution.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import java.io.PrintWriter;

public class EntryPilot extends GhidraScript {
    public void run() throws Exception {
        Address entry = toAddr(0x827d78b0L);
        disassemble(entry);
        Function f = createFunction(entry, "xex_entry");
        if (f == null) f = getFunctionAt(entry);
        try (PrintWriter out = new PrintWriter(getScriptArgs()[0])) {
            out.println("Exploratory decoded image; integrity validation unresolved. Pseudocode is not original source.");
            out.println("Language: " + currentProgram.getLanguageID());
            InstructionIterator it = currentProgram.getListing().getInstructions(entry, true);
            for (int i=0; i<80 && it.hasNext(); i++) {
                Instruction ins = it.next();
                out.println(ins.getAddress() + "  " + ins);
            }
            if (f != null) {
                DecompInterface di = new DecompInterface();
                try {
                    di.openProgram(currentProgram);
                    DecompileResults r = di.decompileFunction(f, 60, monitor);
                    out.println("Decompiler complete: " + r.decompileCompleted());
                    out.println(r.getErrorMessage());
                    if (r.getDecompiledFunction() != null) out.println(r.getDecompiledFunction().getC());
                } finally { di.dispose(); }
            }
        }
    }
}
