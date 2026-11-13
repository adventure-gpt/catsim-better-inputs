// P/Invoke bindings for catsim_correct (C#). Layouts follow include/catsim_correct.h exactly.
using System;
using System.Runtime.InteropServices;

namespace CatsimCorrect
{
    [StructLayout(LayoutKind.Sequential)] public struct CscVec { public float x, y, z; }
    [StructLayout(LayoutKind.Sequential)] public struct CscRotMat { public CscVec forward, right, up; }

    [StructLayout(LayoutKind.Sequential)]
    public struct CscControls
    {
        public float throttle, steer, pitch, yaw, roll;
        [MarshalAs(UnmanagedType.U1)] public bool jump;
        [MarshalAs(UnmanagedType.U1)] public bool boost;
        [MarshalAs(UnmanagedType.U1)] public bool handbrake;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct CscCarIn
    {
        public int team;
        public CscVec pos, vel, ang_vel;
        public float pitch, yaw, roll, boost;
        public CscVec hitbox, hitbox_offset;
        [MarshalAs(UnmanagedType.U1)] public bool demolished;
        public float demo_left;
        [MarshalAs(UnmanagedType.U1)] public bool supersonic;
        public sbyte has_jumped, has_double_jumped, has_dodged, on_ground;
        public int air_state;
        [MarshalAs(UnmanagedType.U1)] public bool has_dodge_clocks;
        public float dodge_timeout, dodge_elapsed, dodge_dir_x, dodge_dir_y;
        [MarshalAs(UnmanagedType.U1)] public bool has_last_input;
        public CscControls last_input;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct CscFrame
    {
        public int frame, match_phase, num_cars;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 8)] public CscCarIn[] cars;
        [MarshalAs(UnmanagedType.U1)] public bool has_ball;
        public CscVec ball_pos, ball_vel, ball_ang_vel;
        public int num_pads;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 34, ArraySubType = UnmanagedType.U1)] public bool[] pad_active;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 34)] public float[] pad_timer;

        public static CscFrame New() => new CscFrame { cars = new CscCarIn[8], pad_active = new bool[34], pad_timer = new float[34] };
    }

    [StructLayout(LayoutKind.Sequential)] public struct CscWorldContact { [MarshalAs(UnmanagedType.U1)] public bool hasContact; public CscVec contactNormal; }
    [StructLayout(LayoutKind.Sequential)] public struct CscCarContact { public uint otherCarID; public float cooldownTimer; }
    [StructLayout(LayoutKind.Sequential)]
    public struct CscBallHitInfo
    {
        [MarshalAs(UnmanagedType.U1)] public bool isValid;
        public CscVec relativePosOnBall, ballPos, extraHitVel;
        public ulong tickCountWhenHit, tickCountWhenExtraImpulseApplied;
    }

    // RocketSim's CarState, field for field, then the extras
    [StructLayout(LayoutKind.Sequential)]
    public struct CscCarState
    {
        public CscVec pos; public CscRotMat rotMat; public CscVec vel, angVel; public ulong tickCountSinceUpdate;
        [MarshalAs(UnmanagedType.U1)] public bool isOnGround;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4, ArraySubType = UnmanagedType.U1)] public bool[] wheelsWithContact;
        [MarshalAs(UnmanagedType.U1)] public bool hasJumped;
        [MarshalAs(UnmanagedType.U1)] public bool hasDoubleJumped;
        [MarshalAs(UnmanagedType.U1)] public bool hasFlipped;
        public CscVec flipRelTorque; public float jumpTime, flipTime;
        [MarshalAs(UnmanagedType.U1)] public bool isFlipping;
        [MarshalAs(UnmanagedType.U1)] public bool isJumping;
        public float airTime, airTimeSinceJump, boost, timeSinceBoosted;
        [MarshalAs(UnmanagedType.U1)] public bool isBoosting;
        public float boostingTime;
        [MarshalAs(UnmanagedType.U1)] public bool isSupersonic;
        public float supersonicTime, handbrakeVal;
        [MarshalAs(UnmanagedType.U1)] public bool isAutoFlipping;
        public float autoFlipTimer, autoFlipTorqueScale;
        public CscWorldContact worldContact; public CscCarContact carContact;
        [MarshalAs(UnmanagedType.U1)] public bool isDemoed;
        public float demoRespawnTimer; public CscBallHitInfo ballHitInfo; public CscControls lastControls;
        public int index, team; public float timeSpentBoosting, supersonicGrace;
        [MarshalAs(UnmanagedType.U1)] public bool jumpHoldBroken;
        public float boostLockLeft, pitchLockLeft;
        [MarshalAs(UnmanagedType.U1)] public bool canFlip;
        [MarshalAs(UnmanagedType.U1)] public bool hasFlipReset;
        public float dodgeTimeout;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public CscVec[] wheelNormals;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public int[] wheelGround;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public float[] wheelSuspensionLength;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public float[] wheelSuspensionRest;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public float[] wheelSuspensionTravel;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public float[] wheelRadius;
        public int otherCarIndex;
        [MarshalAs(UnmanagedType.U1)] public bool synced;
        [MarshalAs(UnmanagedType.U1)] public bool consistent;
        public long ticksSinceColdStart; public float drift; public uint assumedFacts;
        [MarshalAs(UnmanagedType.U1)] public bool inputsKnown;
    }

    [StructLayout(LayoutKind.Sequential)] public struct CscBallState { public CscVec pos; public CscRotMat rotMat; public CscVec vel, angVel; }
    [StructLayout(LayoutKind.Sequential)]
    public struct CscPadState
    {
        [MarshalAs(UnmanagedType.U1)] public bool isActive;
        public float cooldown, timer;
        [MarshalAs(UnmanagedType.U1)] public bool isBig;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct CscCorrected
    {
        public int frame, horizon, input_delay, num_cars;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 8)] public CscCarState[] cars;
        [MarshalAs(UnmanagedType.U1)] public bool has_ball;
        public CscBallState ball;
        public int num_pads;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 34)] public CscPadState[] pads;
    }

    public static class Native
    {
        const string Lib = "catsim_correct";
        [DllImport(Lib)] public static extern IntPtr csc_version();
        [DllImport(Lib, CharSet = CharSet.Ansi)]
        public static extern IntPtr csc_new(string dataDir, int ownIndex, int trainingActionDelay, int inputDelay, byte[] err, UIntPtr errLen);
        [DllImport(Lib)] public static extern void csc_free(IntPtr c);
        [DllImport(Lib)] public static extern int csc_correct(IntPtr c, ref CscFrame frame, out CscCorrected outState);
        [DllImport(Lib)] public static extern int csc_update(IntPtr c, ref CscFrame frame, out CscCorrected outState);
        [DllImport(Lib)] public static extern int csc_predict(IntPtr c, int ticks, out CscCorrected outState);
        [DllImport(Lib)] public static extern void csc_sent(IntPtr c, ref CscControls controls, int frame);
        [DllImport(Lib)] public static extern int csc_input_delay(IntPtr c);
        [DllImport(Lib)] public static extern int csc_horizon(IntPtr c);
    }

    // The same lifecycle as the C++ wrapper: one per bot, correct() before the policy, sent() after.
    public sealed class Corrector : IDisposable
    {
        IntPtr p;
        public Corrector(string dataDir, int ownIndex, int trainingActionDelay = 0, int inputDelay = 0)
        {
            var err = new byte[512];
            p = Native.csc_new(dataDir, ownIndex, trainingActionDelay, inputDelay, err, (UIntPtr)err.Length);
            if (p == IntPtr.Zero) throw new Exception("catsim_correct: " + System.Text.Encoding.ASCII.GetString(err).TrimEnd('\0'));
        }
        public CscCorrected Correct(ref CscFrame f) { if (Native.csc_correct(p, ref f, out var o) < 0) throw new Exception("csc_correct"); return o; }   // num_cars 0: nothing to correct
        public void Sent(CscControls c, int frame = -1) => Native.csc_sent(p, ref c, frame);
        public int InputDelay => Native.csc_input_delay(p);
        public int Horizon => Native.csc_horizon(p);
        public void Dispose() { if (p != IntPtr.Zero) { Native.csc_free(p); p = IntPtr.Zero; } }
    }
}
