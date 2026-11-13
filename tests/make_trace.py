"""Write a packet trace for the C++ example: the packets exactly as a bot receives them, the controls the bot sent, and
the truth (catSim's own state in RocketSim's meanings) at every frame.

    CATSIM_DATA=... python tests/make_trace.py out.txt [--cars-per-team 1] [--delay 3] [--frames 900] [--faults 0.02]
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import catsim  # noqa: E402

catsim.init(silent=True)

from harness import DelayedBot, Faults, Truth, make_packet  # noqa: E402

from catsim_better_inputs import as_frame  # noqa: E402

PHASE = {"inactive": 0, "countdown": 1, "kickoff": 2, "active": 3, "goal": 4, "replay": 5, "paused": 6, "ended": 7}


def num(x) -> str:
    if isinstance(x, bool):
        return "1" if x else "0"
    if x is None:
        return "-1"
    return repr(float(x)) if isinstance(x, float) else str(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--cars-per-team", type=int, default=1)
    ap.add_argument("--delay", type=int, default=3)
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--faults", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    truth = Truth(a.cars_per_team, seed=a.seed)
    bot = DelayedBot(a.delay)
    faults = Faults(seed=a.seed, rate=a.faults) if a.faults else None
    with open(a.out, "w") as f:
        f.write(f"csc-trace 1 cars {truth.n} frames {a.frames} delay {a.delay}\n")
        for k in range(a.frames):
            rows = [bot.applied(truth.frame + 1)] + [truth.script(i) for i in range(1, truth.n)]
            truth.step(rows)
            fr = as_frame(make_packet(truth, faults))
            f.write(f"F {fr.frame} {PHASE[fr.phase]}\n")
            for p in fr.cars:
                v = [p.team, *p.pos, *p.vel, *p.ang_vel, *p.rot, p.boost, *p.hitbox, *p.hitbox_offset, p.demolished, p.demo_left,
                     p.supersonic, p.has_jumped, p.has_double_jumped, p.has_dodged, p.air_state, p.dodge_timeout,
                     p.dodge_elapsed, *(p.dodge_dir or (0.0, 0.0)), *(p.last_input or (0.0,) * 8)]
                f.write("C " + " ".join(num(x) for x in v) + "\n")
            b = fr.ball
            f.write("B " + ("1 " + " ".join(num(x) for x in (*b.pos, *b.vel, *b.ang_vel)) if b else "0") + "\n")
            f.write(f"P {len(fr.pads)} " + " ".join(f"{num(x)} {num(t)}" for x, t in fr.pads) + "\n")
            for i in range(truth.n):
                t = truth.rs(i)
                v = [i, t.isOnGround, t.isJumping, t.hasJumped, t.hasDoubleJumped, t.hasFlipped, t.isFlipping, *t.wheelsWithContact,
                     t.jumpTime, t.flipTime, t.airTime, t.airTimeSinceJump, t.boost, *t.pos]
                f.write("T " + " ".join(num(x) for x in v) + "\n")
            c = truth.script(0)
            bot.send(truth.frame, c)
            f.write("S " + " ".join(num(float(x)) for x in c) + "\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
