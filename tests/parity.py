"""The C library against the Python reference on the same packets: every output field must be equal (Python's f64
values rounded to the C library's f32).

    CATSIM_DATA=... python tests/parity.py [--lib path/to/catsim_correct.dll]
"""
from __future__ import annotations

import argparse
import ctypes as C
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import catsim  # noqa: E402

catsim.init(silent=True)

import cabi  # noqa: E402
from harness import DelayedBot, Faults, Truth, make_packet  # noqa: E402
from rlbot import flat  # noqa: E402

from catsim_better_inputs import Corrector, as_frame  # noqa: E402

FIELDS = ("isOnGround", "hasJumped", "hasDoubleJumped", "hasFlipped", "jumpTime", "flipTime", "isFlipping", "isJumping", "airTime",
          "airTimeSinceJump", "boost", "timeSinceBoosted", "isBoosting", "boostingTime", "isSupersonic", "supersonicTime",
          "handbrakeVal", "isAutoFlipping", "autoFlipTimer", "autoFlipTorqueScale", "isDemoed", "demoRespawnTimer",
          "supersonicGrace", "jumpHoldBroken", "boostLockLeft", "pitchLockLeft", "canFlip", "hasFlipReset", "dodgeTimeout")
STATS = ("air_state", "has_jumped", "has_double_jumped", "has_dodged", "dodge_timeout", "dodge_elapsed", "supersonic")


def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def vec(v) -> tuple:
    return (v.x, v.y, v.z)


def diff_car(p, c) -> list:
    bad = []
    for k in FIELDS:
        a, b = getattr(p, k), getattr(c, k)
        if isinstance(a, bool):
            if bool(a) != bool(b):
                bad.append((k, a, b))
        elif f32(a) != b:
            bad.append((k, a, b))
    for k, a, b in (("pos", p.pos, vec(c.pos)), ("vel", p.vel, vec(c.vel)), ("angVel", p.angVel, vec(c.angVel)),
                    ("forward", p.rotMat[0], vec(c.rotMat.forward)), ("up", p.rotMat[2], vec(c.rotMat.up)),
                    ("flipRelTorque", p.flipRelTorque, vec(c.flipRelTorque)),
                    ("worldNormal", p.worldContact.contactNormal, vec(c.worldContact.contactNormal))):
        if tuple(f32(x) for x in a) != tuple(b):
            bad.append((k, a, b))
    if tuple(p.wheelsWithContact) != tuple(c.wheelsWithContact):
        bad.append(("wheelsWithContact", p.wheelsWithContact, tuple(c.wheelsWithContact)))
    if p.worldContact.hasContact != c.worldContact.hasContact:
        bad.append(("worldContact", p.worldContact.hasContact, c.worldContact.hasContact))
    if (p.carContact.otherCarID, f32(p.carContact.cooldownTimer)) != (c.carContact.otherCarID, c.carContact.cooldownTimer):
        bad.append(("carContact", (p.carContact.otherCarID, p.carContact.cooldownTimer), (c.carContact.otherCarID, c.carContact.cooldownTimer)))
    pc = tuple(f32(x) for x in p.lastControls[:5])
    cc = (c.lastControls.throttle, c.lastControls.steer, c.lastControls.pitch, c.lastControls.yaw, c.lastControls.roll)
    if pc != cc:
        bad.append(("lastControls", pc, cc))
    if (p.confidence.get("synced"), p.confidence.get("consistent")) != (c.synced, c.consistent):
        bad.append(("confidence", (p.confidence.get("synced"), p.confidence.get("consistent")), (c.synced, c.consistent)))
    return bad


def run(lib, n_per_team, delay, train_delay, frames, seed, faults_rate=0.0, skip=None, cold_every=0, countdown=0):
    truth = Truth(n_per_team, seed=seed)
    bot = DelayedBot(delay)
    py = Corrector(index=0, training_action_delay=train_delay)
    err = C.create_string_buffer(512)
    c = lib.csc_new(catsim._data_dir.__str__().encode(), 0, train_delay, 0, err, 512)
    assert c, err.value
    out = cabi.CorrectedC()
    faults = Faults(seed=seed, rate=faults_rate) if faults_rate else None
    mismatches, compared, first = 0, 0, None
    if countdown:
        truth.hold(True)
    for k in range(frames):
        if countdown and k == countdown:
            truth.hold(False)                                 # the release: every car drives from this tick
        rows = [bot.applied(truth.frame + 1)] + [truth.script(i) for i in range(1, truth.n)]
        truth.step(rows)
        if skip and k % skip[0] in skip[1]:
            continue
        pkt = make_packet(truth, faults, phase=flat.MatchPhase.Countdown if k < countdown else flat.MatchPhase.Active)
        fr = as_frame(pkt)
        if cold_every and k % cold_every == cold_every - 1:   # a goal packet: both start again on the next one
            import copy
            g = copy.copy(fr)
            g.phase = "goal"
            py.update(g)
            lib.csc_update(c, C.byref(cabi.frame_to_c(g)), None)
        ps = py.correct(fr)
        r = lib.csc_correct(c, C.byref(cabi.frame_to_c(fr)), C.byref(out))
        assert r == 0, lib.csc_last_error(c)
        assert out.frame == ps.frame and out.horizon == ps.horizon, (out.frame, ps.frame, out.horizon, ps.horizon)
        for i in range(truth.n):
            bad = diff_car(ps.cars[i], out.cars[i])
            compared += 1
            if bad:
                mismatches += 1
                if first is None:
                    first = (k, i, bad[:6])
        if ps.ball is not None:
            compared += 1
            if tuple(f32(x) for x in ps.ball.pos) != vec(out.ball.pos):
                mismatches += 1
                first = first or (k, "ball", ps.ball.pos, vec(out.ball.pos))
        for j, pp in enumerate(ps.pads):
            cp = out.pads[j]
            if (pp.isActive, f32(pp.cooldown)) != (cp.isActive, cp.cooldown):
                mismatches += 1
                first = first or (k, "pad", j, (pp.isActive, pp.cooldown), (cp.isActive, cp.cooldown))
        ctl = truth.script(0)
        py.sent(ctl, truth.frame)
        lib.csc_sent(c, C.byref(cabi.controls(ctl)), truth.frame)
        bot.send(truth.frame, ctl)
    stats_equal = True
    for name in STATS:
        s = cabi.FieldStatsC()
        lib.csc_field_stats(c, name.encode(), C.byref(s))
        ps_ = py.stats.get(name)
        pv = (ps_.compared, ps_.agreed, ps_.packet_wrong, ps_.adopted) if ps_ else (0, 0, 0, 0)
        if pv != (s.compared, s.agreed, s.packet_wrong, s.adopted):
            stats_equal = False
            first = first or ("stats", name, pv, (s.compared, s.agreed, s.packet_wrong, s.adopted))
    d_py, d_c = py.input_delay, lib.csc_input_delay(c)
    lib.csc_free(c)
    return {"compared": compared, "mismatches": mismatches, "stats_equal": stats_equal, "delay": (d_py, d_c), "first": first}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib")
    ap.add_argument("--frames", type=int, default=900)
    a = ap.parse_args()
    lib = cabi.load(a.lib)
    print("library", lib.csc_version().decode())
    cases = [("1v1 delay 1", dict(n_per_team=1, delay=1, train_delay=0)),
             ("1v1 delay 3", dict(n_per_team=1, delay=3, train_delay=0)),
             ("1v1 delay 3, trained 1", dict(n_per_team=1, delay=3, train_delay=1)),
             ("3v3 delay 2", dict(n_per_team=3, delay=2, train_delay=0)),
             ("1v1 flag faults", dict(n_per_team=1, delay=1, train_delay=0, faults_rate=0.02)),
             ("3v3 flag faults", dict(n_per_team=3, delay=1, train_delay=0, faults_rate=0.02)),
             ("2v2 dropped packets", dict(n_per_team=2, delay=3, train_delay=0, skip=(10, {3, 7}))),
             ("2v2 restarts", dict(n_per_team=2, delay=2, train_delay=0, cold_every=200)),
             ("2v2 kickoff countdown", dict(n_per_team=2, delay=2, train_delay=0, countdown=150))]
    ok = True
    for k, (name, kw) in enumerate(cases):
        r = run(lib, frames=a.frames, seed=90 + k, **kw)
        ok &= r["mismatches"] == 0 and r["stats_equal"]
        print(f"{name:<26} {r['compared']:>6} car/ball/pad states compared, {r['mismatches']} differ, flag stats equal: "
              f"{r['stats_equal']}, delay {r['delay']}" + (f"  first: {r['first']}" if r["first"] else ""))
    print("PARITY", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
