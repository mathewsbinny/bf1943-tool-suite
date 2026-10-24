import java.nio.file.*;
import java.util.ArrayList;
import xexloaderwv.XEXHeader;
import xexloaderwv.Helper;
import xexloaderwv.LzxDecompression;
import java.io.*;
import java.nio.*;
import java.security.MessageDigest;
import java.util.Arrays;

/** Decode only; deliberately do not call ProcessImportLibraries (it patches thunks). */
public class DecodeXex {
    static class CheckedHeader extends XEXHeader {
        CheckedHeader(byte[] data) throws Exception { super(data, new ArrayList<>(), false); }
        @Override public void ReadPEImage(byte[] data) throws Exception {
            if (baseFileFormat.encryption != 1 || baseFileFormat.compression != 2)
                throw new IllegalArgumentException("Pilot only supports NORMAL/NORMAL");
            byte[] compressed = Helper.AESDecrypt(sessionKey, Arrays.copyOfRange(data, offsetPE, data.length));
            int size = baseFileFormat.normal.blockSize, pos = 0, blocks = 0;
            byte[] expected = baseFileFormat.normal.blockHash;
            ByteArrayOutputStream packed = new ByteArrayOutputStream();
            while (size != 0) {
                if (size < 24 || pos + size > compressed.length) throw new IOException("Block bounds");
                byte[] block = Arrays.copyOfRange(compressed, pos, pos + size);
                if (!Arrays.equals(expected, MessageDigest.getInstance("SHA-1").digest(block)))
                    throw new IOException("Compressed block SHA-1 mismatch at " + pos);
                ByteBuffer b = ByteBuffer.wrap(block).order(ByteOrder.BIG_ENDIAN);
                int next = b.getInt(); expected = Arrays.copyOfRange(block, 4, 24);
                for (int p = 24; p + 2 <= size;) {
                    int n = Short.toUnsignedInt(b.getShort(p)); p += 2;
                    if (n == 0) break;
                    if (p + n > size) throw new IOException("Chunk bounds");
                    packed.write(block, p, n); p += n;
                }
                pos += size; size = next; blocks++;
            }
            int window = baseFileFormat.normal.windowSize;
            if (Integer.bitCount(window) != 1) throw new IOException("Invalid window");
            byte[] input = packed.toByteArray();
            LzxDecompression lzx = new LzxDecompression();
            lzx.init(Integer.numberOfTrailingZeros(window), 0, input.length, loaderInfo.imageSize, false, null);
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            lzx.decompress(new ByteArrayInputStream(input), output, loaderInfo.imageSize);
            peImage = output.toByteArray();
            if (peImage.length != loaderInfo.imageSize) throw new IOException("Decoded size mismatch");
            System.out.println("Verified compressed SHA-1 blocks: " + blocks);
        }
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("input.xex output.memory.bin");
        Path output = Path.of(args[1]);
        if (Files.exists(output)) throw new IllegalArgumentException("Output already exists");
        XEXHeader header = new CheckedHeader(Files.readAllBytes(Path.of(args[0])));
        if (header.peImage[0] != 'M' || header.peImage[1] != 'Z') throw new IllegalStateException("Missing MZ");
        Files.write(output, header.peImage, StandardOpenOption.CREATE_NEW);
        System.out.printf("Decoded memory image: %d bytes; base=0x%08x entry=0x%08x%n",
            header.peImage.length, header.imageBaseAddress, header.entryPointAddress);
    }
}
