// Java 22+ (the Foreign Function & Memory API; no JNI, no extra jars):
//   java --enable-native-access=ALL-UNNAMED CatsimCorrect.java <path/to/catsim_correct.dll> <catsim_data> <trace.txt>
// Same packet trace and checks as examples/correct_trace.cpp.
import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.file.*;
import java.util.*;

public class CatsimCorrect {
    // offsets in catsim_correct.h's structs (x64), generated from tests/cabi.py
    static final long FRAME_SIZE = 1412, CORRECTED_SIZE = 4416, CONTROLS_SIZE = 24;
    static final long F_FRAME = 0, F_MATCH_PHASE = 4, F_NUM_CARS = 8, F_CARS = 12, F_HAS_BALL = 1196, F_BALL_POS = 1200,
            F_BALL_VEL = 1212, F_BALL_ANG_VEL = 1224, F_NUM_PADS = 1236, F_PAD_ACTIVE = 1240, F_PAD_TIMER = 1276;
    static final long CAR_IN_SIZE = 148, CI_TEAM = 0, CI_POS = 4, CI_VEL = 16, CI_ANG_VEL = 28, CI_PITCH = 40, CI_YAW = 44,
            CI_ROLL = 48, CI_BOOST = 52, CI_HITBOX = 56, CI_HITBOX_OFFSET = 68, CI_DEMOLISHED = 80, CI_DEMO_LEFT = 84,
            CI_SUPERSONIC = 88, CI_HAS_JUMPED = 89, CI_HAS_DOUBLE_JUMPED = 90, CI_HAS_DODGED = 91, CI_ON_GROUND = 92,
            CI_AIR_STATE = 96, CI_HAS_DODGE_CLOCKS = 100, CI_DODGE_TIMEOUT = 104, CI_DODGE_ELAPSED = 108, CI_DODGE_DIR_X = 112,
            CI_DODGE_DIR_Y = 116, CI_HAS_LAST_INPUT = 120, CI_LAST_INPUT = 124;
    static final long CT_THROTTLE = 0, CT_JUMP = 20, CT_BOOST = 21, CT_HANDBRAKE = 22;
    static final long O_FRAME = 0, O_HORIZON = 4, O_INPUT_DELAY = 8, O_CARS = 16, CAR_STATE_SIZE = 472;
    static final long CS_ISONGROUND = 80, CS_HASFLIPPED = 87, CS_JUMPTIME = 100, CS_AIRTIMESINCEJUMP = 116;

    static final ValueLayout.OfFloat F32 = ValueLayout.JAVA_FLOAT;
    static final ValueLayout.OfInt I32 = ValueLayout.JAVA_INT;

    static void controls(MemorySegment m, long at, float[] c) {
        for (int k = 0; k < 5; k++) m.set(F32, at + CT_THROTTLE + 4L * k, c[k]);
        m.set(ValueLayout.JAVA_BOOLEAN, at + CT_JUMP, c[5] > 0.5f);
        m.set(ValueLayout.JAVA_BOOLEAN, at + CT_BOOST, c[6] > 0.5f);
        m.set(ValueLayout.JAVA_BOOLEAN, at + CT_HANDBRAKE, c[7] > 0.5f);
    }

    public static void main(String[] args) throws Throwable {
        Linker linker = Linker.nativeLinker();
        Arena mem = Arena.ofConfined();
        SymbolLookup lib = SymbolLookup.libraryLookup(Path.of(args[0]), mem);
        MethodHandle cscNew = linker.downcallHandle(lib.find("csc_new").orElseThrow(), FunctionDescriptor.of(ValueLayout.ADDRESS,
                ValueLayout.ADDRESS, I32, I32, I32, ValueLayout.ADDRESS, ValueLayout.JAVA_LONG));
        MethodHandle cscCorrect = linker.downcallHandle(lib.find("csc_correct").orElseThrow(),
                FunctionDescriptor.of(I32, ValueLayout.ADDRESS, ValueLayout.ADDRESS, ValueLayout.ADDRESS));
        MethodHandle cscSent = linker.downcallHandle(lib.find("csc_sent").orElseThrow(),
                FunctionDescriptor.ofVoid(ValueLayout.ADDRESS, ValueLayout.ADDRESS, I32));
        MethodHandle cscDelay = linker.downcallHandle(lib.find("csc_input_delay").orElseThrow(), FunctionDescriptor.of(I32, ValueLayout.ADDRESS));
        MethodHandle cscFree = linker.downcallHandle(lib.find("csc_free").orElseThrow(), FunctionDescriptor.ofVoid(ValueLayout.ADDRESS));

        MemorySegment err = mem.allocate(512);
        MemorySegment fix = (MemorySegment) cscNew.invoke(mem.allocateFrom(args[1]), 0, 0, 0, err, 512L);
        if (fix.equals(MemorySegment.NULL)) throw new RuntimeException("catsim_correct: " + err.getString(0));
        MemorySegment frame = mem.allocate(FRAME_SIZE, 8), out = mem.allocate(CORRECTED_SIZE, 8), ctl = mem.allocate(CONTROLS_SIZE, 8);
        Map<Integer, float[]> predicted = new HashMap<>();   // target frame -> own car (isOnGround, hasFlipped, jumpTime, airTimeSinceJump)
        int packets = 0, checked = 0, nCars = 0, curFrame = 0;
        int[] right = new int[4];
        for (String line : Files.readAllLines(Path.of(args[2]))) {
            String[] w = line.trim().split("\\s+");
            switch (w[0]) {
                case "F" -> { frame.fill((byte) 0); curFrame = Integer.parseInt(w[1]); frame.set(I32, F_FRAME, curFrame);
                              frame.set(I32, F_MATCH_PHASE, Integer.parseInt(w[2])); nCars = 0; }
                case "C" -> {
                    float[] v = new float[39];
                    for (int k = 0; k < 39; k++) v[k] = Float.parseFloat(w[1 + k]);
                    long c = F_CARS + CAR_IN_SIZE * nCars++;
                    frame.set(I32, F_NUM_CARS, nCars);
                    frame.set(I32, c + CI_TEAM, (int) v[0]);
                    long[] vecs = {CI_POS, CI_VEL, CI_ANG_VEL};
                    for (int j = 0; j < 3; j++) for (int k = 0; k < 3; k++) frame.set(F32, c + vecs[j] + 4L * k, v[1 + 3 * j + k]);
                    frame.set(F32, c + CI_PITCH, v[10]); frame.set(F32, c + CI_YAW, v[11]); frame.set(F32, c + CI_ROLL, v[12]);
                    frame.set(F32, c + CI_BOOST, v[13]);
                    for (int k = 0; k < 3; k++) { frame.set(F32, c + CI_HITBOX + 4L * k, v[14 + k]); frame.set(F32, c + CI_HITBOX_OFFSET + 4L * k, v[17 + k]); }
                    frame.set(ValueLayout.JAVA_BOOLEAN, c + CI_DEMOLISHED, v[20] != 0); frame.set(F32, c + CI_DEMO_LEFT, v[21]);
                    frame.set(ValueLayout.JAVA_BOOLEAN, c + CI_SUPERSONIC, v[22] != 0);
                    frame.set(ValueLayout.JAVA_BYTE, c + CI_HAS_JUMPED, (byte) v[23]);
                    frame.set(ValueLayout.JAVA_BYTE, c + CI_HAS_DOUBLE_JUMPED, (byte) v[24]);
                    frame.set(ValueLayout.JAVA_BYTE, c + CI_HAS_DODGED, (byte) v[25]);
                    frame.set(ValueLayout.JAVA_BYTE, c + CI_ON_GROUND, (byte) -1);
                    frame.set(I32, c + CI_AIR_STATE, (int) v[26]);
                    frame.set(ValueLayout.JAVA_BOOLEAN, c + CI_HAS_DODGE_CLOCKS, true);
                    frame.set(F32, c + CI_DODGE_TIMEOUT, v[27]); frame.set(F32, c + CI_DODGE_ELAPSED, v[28]);
                    frame.set(F32, c + CI_DODGE_DIR_X, v[29]); frame.set(F32, c + CI_DODGE_DIR_Y, v[30]);
                    frame.set(ValueLayout.JAVA_BOOLEAN, c + CI_HAS_LAST_INPUT, true);
                    controls(frame, c + CI_LAST_INPUT, Arrays.copyOfRange(v, 31, 39));
                }
                case "B" -> {
                    if (!w[1].equals("0")) {
                        frame.set(ValueLayout.JAVA_BOOLEAN, F_HAS_BALL, true);
                        for (int k = 0; k < 9; k++) frame.set(F32, F_BALL_POS + 4L * k, Float.parseFloat(w[2 + k]));
                    }
                }
                case "P" -> {
                    int n = Integer.parseInt(w[1]);
                    frame.set(I32, F_NUM_PADS, n);
                    for (int k = 0; k < n; k++) {
                        frame.set(ValueLayout.JAVA_BOOLEAN, F_PAD_ACTIVE + k, !w[2 + 2 * k].equals("0"));
                        frame.set(F32, F_PAD_TIMER + 4L * k, Float.parseFloat(w[3 + 2 * k]));
                    }
                    int r = (int) cscCorrect.invoke(fix, frame, out);           // before the policy
                    if (r < 0) throw new RuntimeException("csc_correct " + r);   // 1: nothing to correct (between a goal and the kickoff)
                    long me = O_CARS;
                    predicted.put(out.get(I32, O_FRAME), new float[]{out.get(ValueLayout.JAVA_BOOLEAN, me + CS_ISONGROUND) ? 1 : 0,
                            out.get(ValueLayout.JAVA_BOOLEAN, me + CS_HASFLIPPED) ? 1 : 0, out.get(F32, me + CS_JUMPTIME),
                            out.get(F32, me + CS_AIRTIMESINCEJUMP)});
                    packets++;
                }
                case "T" -> {
                    float[] e = predicted.remove(curFrame);
                    if (w[1].equals("0") && packets >= 120 && e != null) {
                        checked++;
                        if ((e[0] != 0) == !w[2].equals("0")) right[0]++;
                        if ((e[1] != 0) == !w[6].equals("0")) right[1]++;
                        if (Math.abs(e[2] - Float.parseFloat(w[12])) <= 1e-3) right[2]++;
                        if (Math.abs(e[3] - Float.parseFloat(w[15])) <= 1e-3) right[3]++;
                    } else if (e != null && !w[1].equals("0")) predicted.put(curFrame, e);
                }
                case "S" -> {
                    float[] c = new float[8];
                    for (int k = 0; k < 8; k++) c[k] = Float.parseFloat(w[1 + k]);
                    controls(ctl, 0, c);
                    cscSent.invoke(fix, ctl, curFrame);
                }
                default -> { }
            }
        }
        double d = Math.max(1, checked);
        System.out.printf("Java: %d packets, measured delay %d; own car on %d frames: isOnGround %.2f%%  hasFlipped %.2f%%  jumpTime %.2f%%  airTimeSinceJump %.2f%%%n",
                packets, (int) cscDelay.invoke(fix), checked, 100 * right[0] / d, 100 * right[1] / d, 100 * right[2] / d, 100 * right[3] / d);
        cscFree.invoke(fix);
    }
}
