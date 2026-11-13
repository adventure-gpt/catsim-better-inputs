// dotnet run -c Release -- <catsim_data> <trace.txt>   (catsim_correct.dll next to the program or on PATH)
// Same packet trace and checks as examples/correct_trace.cpp.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using CatsimCorrect;

class Program
{
    static void Main(string[] args)
    {
        Console.WriteLine("catsim_correct " + Marshal.PtrToStringAnsi(Native.csc_version()) + ", C# "
                          + Marshal.SizeOf<CscFrame>() + "/" + Marshal.SizeOf<CscCorrected>() + " bytes");
        using var fix = new Corrector(args[0], 0, 0);
        var frame = CscFrame.New();
        var predicted = new Dictionary<int, CscCarState>();
        int packets = 0, checkedN = 0;
        var right = new int[4];
        float F(string s) => float.Parse(s, CultureInfo.InvariantCulture);
        foreach (var line in File.ReadLines(args[1]))
        {
            var w = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            switch (w[0])
            {
                case "F":
                    frame = CscFrame.New();
                    frame.frame = int.Parse(w[1]); frame.match_phase = int.Parse(w[2]);
                    break;
                case "C":
                {
                    var v = new float[39];
                    for (int k = 0; k < 39; k++) v[k] = F(w[1 + k]);
                    CscVec V(int k) => new CscVec { x = v[k], y = v[k + 1], z = v[k + 2] };
                    var c = new CscCarIn
                    {
                        team = (int)v[0], pos = V(1), vel = V(4), ang_vel = V(7), pitch = v[10], yaw = v[11], roll = v[12], boost = v[13],
                        hitbox = V(14), hitbox_offset = V(17), demolished = v[20] != 0, demo_left = v[21], supersonic = v[22] != 0,
                        has_jumped = (sbyte)v[23], has_double_jumped = (sbyte)v[24], has_dodged = (sbyte)v[25], on_ground = -1,
                        air_state = (int)v[26], has_dodge_clocks = true, dodge_timeout = v[27], dodge_elapsed = v[28],
                        dodge_dir_x = v[29], dodge_dir_y = v[30], has_last_input = true,
                        last_input = new CscControls { throttle = v[31], steer = v[32], pitch = v[33], yaw = v[34], roll = v[35],
                                                       jump = v[36] > 0.5f, boost = v[37] > 0.5f, handbrake = v[38] > 0.5f },
                    };
                    frame.cars[frame.num_cars++] = c;
                    break;
                }
                case "B":
                    if (w[1] != "0")
                    {
                        frame.has_ball = true;
                        frame.ball_pos = new CscVec { x = F(w[2]), y = F(w[3]), z = F(w[4]) };
                        frame.ball_vel = new CscVec { x = F(w[5]), y = F(w[6]), z = F(w[7]) };
                        frame.ball_ang_vel = new CscVec { x = F(w[8]), y = F(w[9]), z = F(w[10]) };
                    }
                    break;
                case "P":
                {
                    frame.num_pads = int.Parse(w[1]);
                    for (int k = 0; k < frame.num_pads; k++) { frame.pad_active[k] = w[2 + 2 * k] != "0"; frame.pad_timer[k] = F(w[3 + 2 * k]); }
                    var s = fix.Correct(ref frame);          // before the policy
                    predicted[s.frame] = s.cars[0];
                    packets++;
                    break;
                }
                case "T":
                    if (w[1] == "0" && packets >= 120 && predicted.TryGetValue(frame.frame, out var e))
                    {
                        checkedN++;
                        right[0] += e.isOnGround == (w[2] != "0") ? 1 : 0;
                        right[1] += e.hasFlipped == (w[6] != "0") ? 1 : 0;
                        right[2] += Math.Abs(e.jumpTime - F(w[12])) <= 1e-3 ? 1 : 0;
                        right[3] += Math.Abs(e.airTimeSinceJump - F(w[15])) <= 1e-3 ? 1 : 0;
                        predicted.Remove(frame.frame);
                    }
                    break;
                case "S":
                    fix.Sent(new CscControls { throttle = F(w[1]), steer = F(w[2]), pitch = F(w[3]), yaw = F(w[4]), roll = F(w[5]),
                                               jump = F(w[6]) > 0.5f, boost = F(w[7]) > 0.5f, handbrake = F(w[8]) > 0.5f }, frame.frame);
                    break;
            }
        }
        double P(int n) => 100.0 * n / Math.Max(1, checkedN);
        Console.WriteLine($"C#: {packets} packets, measured delay {fix.InputDelay}, horizon {fix.Horizon}; own car on {checkedN} frames: " +
                          $"isOnGround {P(right[0]):F2}%  hasFlipped {P(right[1]):F2}%  jumpTime {P(right[2]):F2}%  airTimeSinceJump {P(right[3]):F2}%");
    }
}
