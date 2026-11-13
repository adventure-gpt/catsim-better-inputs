"""Every offline measurement in the README, in one run (no game, CPU only).

    CATSIM_DATA=path/to/catsim_data python tests/run_all.py [--quick] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import catsim  # noqa: E402

catsim.init(silent=True)

from harness import DelayedBot, Faults, Truth, make_packet  # noqa: E402
from metrics import FIELDS, Tally, compare  # noqa: E402

from catsim_better_inputs import Corrector  # noqa: E402
from catsim_better_inputs import rsview as R  # noqa: E402


def peek(bot: DelayedBot, tick: int):
    best, bt = bot.current, -1
    for t, c in bot.queue.items():
        if bt < t <= tick:
            best, bt = c, t
    return best


def play(truth: Truth, bot: DelayedBot, fix: Corrector | None, frames: int, on_packet=None, faults=None, stateless=False,
         order="jump_first", skip=None):
    """Run the match `frames` ticks; each tick: truth steps, a packet is made, the corrector takes it, the bot sends."""
    for k in range(frames):
        rows = [bot.applied(truth.frame + 1)] + [truth.script(i) for i in range(1, truth.n)]
        truth.step(rows)
        if skip and k % skip[0] in skip[1]:
            continue                                     # a packet RLBot never delivered
        pkt = make_packet(truth, faults, order)
        if fix is not None:
            if stateless:
                fix.frame = -1
            if on_packet:
                on_packet(k, pkt)
            else:
                fix.update(pkt)
        c = truth.script(0)
        if fix is not None:
            fix.sent(c, truth.frame)
        bot.send(truth.frame, c)


# ---------------------------------------------------------------- 1. exactness

def exactness(n_per_team: int, delay: int, horizons, frames: int, seed: int = 11) -> dict:
    """Seeded with the exact state and given every car's true controls, predictions must equal the truth bit for bit."""
    truth = Truth(n_per_team, seed=seed)
    bot = DelayedBot(delay)
    fix = Corrector(index=0, input_delay=delay)
    fix.seed_exact(make_packet(truth), truth.na, truth.obs)
    out = {h: [0, 0] for h in horizons}

    def on_packet(k, pkt):
        fix.update(pkt)
        for h in horizons:
            fut = truth.copy()
            for t in range(truth.frame + 1, truth.frame + h + 1):
                rows = [peek(bot, t)] + [fut.script(i) for i in range(1, fut.n)]
                for j in range(1, fut.n):
                    fix.record_sent(rows[j], t, index=j)
                fut.step(rows)
            exact = _exact(fix, h, fut)
            out[h][0] += exact
            out[h][1] += 1
    play(truth, bot, fix, frames, on_packet=on_packet)
    return {h: {"exact": a, "of": b} for h, (a, b) in out.items()}


def _bodies(na):
    return [na.car_body(i) for i in range(na.num_cars)] + [na.ball_body()] + [na.car_hidden(i) for i in range(na.num_cars)]


def _exact(fix: Corrector, h: int, fut: Truth) -> bool:
    """predict(h)'s arena and its RocketSim readout against the truth h ticks on: every body, every hidden row, every field."""
    pred = fix.predict(h)
    truth_rs = [fut.rs(i) for i in range(fut.n)]
    rs_ok = all(all(ok is None or ok for ok, _ in compare(t, e).values()) for t, e in zip(truth_rs, pred.cars))
    return _bodies(fix.predicted_arena) == _bodies(fut.na) and rs_ok


# ---------------------------------------------------------------- 2. accuracy ladder

def ladder(n_per_team: int, mode: str, delay: int, train_delay: int, frames: int, seed: int, faults=None,
           trust_packet_flags=False, order="jump_first", skip=None) -> dict:
    truth = Truth(n_per_team, seed=seed)
    bot = DelayedBot(delay)
    fix = Corrector(index=0, input_delay=delay, training_action_delay=train_delay, trust_packet_flags=trust_packet_flags,
                    log_flag_errors=True)
    if mode == "exact seed":
        fix.seed_exact(make_packet(truth), truth.na, truth.obs)
    h = fix.horizon
    pending = {}
    own, opp, ball = Tally(), Tally(), []
    warm = 120

    def on_packet(k, pkt):
        fix.update(pkt)
        pred = fix.predict(h)
        pending[truth.frame + h] = (k, pred)
        done = pending.pop(truth.frame, None)
        if done and done[0] >= warm:
            p = done[1]
            for i in range(truth.n):
                (own if i == 0 else opp).add(compare(truth.rs(i), p.cars[i]))
            if p.ball is not None:
                b = truth.na.arena_row()[0:3]
                ball.append(sum((x - y) ** 2 for x, y in zip(b, p.ball.pos)) ** 0.5)
    stateless = mode == "stateless"
    if stateless:
        def on_packet_sl(k, pkt):
            fix.frame = -1
            on_packet(k, pkt)
        play(truth, bot, fix, frames, on_packet=on_packet_sl, faults=faults, order=order, skip=skip)
    else:
        play(truth, bot, fix, frames, on_packet=on_packet, faults=faults, order=order, skip=skip)
    return {"horizon": h, "own": own, "opp": opp, "ball": ball, "fix": fix}


def summarize(t: Tally) -> dict:
    out = {f: round(t.rate(f), 5) for f in FIELDS}
    for k in ("pos", "vel", "angVel", "rotMat"):
        out[k + "_mean"] = t.mean(k)
        out[k + "_p99"] = t.pct(k, 0.99)
    return out


# ---------------------------------------------------------------- 3. flag faults

FAULT_FIELD = {"wrong_ground": "air_state", "drop_flipped": "has_dodged", "drop_jumped": "has_jumped",
               "stale_timers": "dodge_timeout", "wrong_supersonic": "supersonic"}
PACKET_TO_INJ = {"air_state": "air_state", "has_dodged": "has_dodged", "has_jumped": "has_jumped",
                 "dodge_timeout": "dodge_timeout", "dodge_elapsed": "dodge_elapsed", "supersonic": "is_supersonic"}


def faults_run(n_per_team: int, frames: int, seed: int, rate: float) -> dict:
    res = {}
    for trust in (False, True):
        f = Faults(seed=seed, rate=rate)
        r = ladder(n_per_team, "cold start", 1, 0, frames, seed, faults=f, trust_packet_flags=trust)
        res["naive" if trust else "detect"] = r
        res["faults_" + ("naive" if trust else "detect")] = f
    det = res["detect"]["fix"]
    f = res["faults_detect"]
    injected = {(fr, car, fld) for fr, car, fld, _, _ in f.injected}
    log = {}
    for e in det.flag_log:
        log[(e["frame"], e["car"], PACKET_TO_INJ.get(e["field"], e["field"]))] = e
    per = {}
    start = min((x[0] for x in injected), default=0)
    for fld, window in [(f, w) for f in sorted({x[2] for x in injected}) for w in ("all", "after 2 s")]:
        inj = [x for x in injected if x[2] == fld and (window == "all" or x[0] > start + 240)]
        dec = [log.get(x, {}).get("decision", "agreed") for x in inj]
        reasons = [log.get(x, {}).get("reason") for x in inj]
        flagged = {x for x, e in log.items() if x[2] == fld and e["decision"] == "packet wrong" and
                   (window == "all" or x[0] > start + 240)}
        per[f"{fld} ({window})"] = {"injected": len(inj), "detected": dec.count("packet wrong"), "held": dec.count("held"),
                    "adopted": dec.count("adopted"), "adopted_while_assumed": sum(1 for d, r in zip(dec, reasons)
                                                                                  if d == "adopted" and r == "assumption"),
                    "matched_physics": dec.count("agreed"), "false_alarms": len(flagged - set(inj)),
                    "detection_rate": round(dec.count("packet wrong") / len(inj), 4) if inj else None,
                    "kept_out_rate": round((dec.count("packet wrong") + dec.count("held")) / len(inj), 4) if inj else None}
    return {"per_field": per, "detect": summarize(det_t(res["detect"])), "naive": summarize(det_t(res["naive"])),
            "packets": frames}


def det_t(r) -> Tally:
    t = Tally()                                     # own car and opponents together
    for src in (r["own"], r["opp"]):
        for k in src.n:
            t.n[k] += src.n[k]
            t.ok[k] += src.ok[k]
        for k, v in src.err.items():
            t.err[k].extend(v)
    return t


# ---------------------------------------------------------------- 4. jump on the ground

def jump_on_ground(seed: int = 5) -> dict:
    """A jump started with all four wheels down: the packet's single air state says Jumping, the wheels are still on."""
    truth = Truth(1, seed=seed, warmup=60)
    na = truth.na
    na.set_car(0, [-2000.0, -1500.0, 17.0, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 50.0])
    for _ in range(60):
        truth.step([(0.0,) * 8, truth.script(1)])
    rows_out = []
    for variant in ("tracking", "stateless"):
        t = truth.copy()
        fix = Corrector(index=0, input_delay=1)
        fix.update(make_packet(t))
        for k in range(14):
            ctl = (0.0, 0.0, 0.0, 0.0, 0.0, 1.0 if k < 10 else 0.0, 0.0, 0.0)
            fix.sent(ctl, t.frame)
            t.step([ctl, t.script(1)])
            pkt = make_packet(t)
            if variant == "stateless":
                fix.frame = -1
            st = fix.update(pkt)
            tr = t.rs(0)
            raw = int(pkt.players[0].air_state)
            rows_out.append({"variant": variant, "tick": k + 1, "truth_on_ground": tr.isOnGround, "truth_jumping": tr.isJumping,
                             "truth_wheels": sum(tr.wheelsWithContact), "packet_air_state": raw,
                             "raw_says_on_ground": raw == 0, "corrected_on_ground": st.cars[0].isOnGround,
                             "corrected_jumping": st.cars[0].isJumping, "corrected_wheels": sum(st.cars[0].wheelsWithContact),
                             "corrected_jumpTime": round(st.cars[0].jumpTime, 4), "truth_jumpTime": round(tr.jumpTime, 4)})
    return rows_out


# ---------------------------------------------------------------- 5. lag meter, 7. timing

def lag_meter(delays, frames=400) -> dict:
    out = {}
    for d in delays:
        truth = Truth(1, seed=20 + d)
        bot = DelayedBot(d)
        fix = Corrector(index=0)
        play(truth, bot, fix, frames)
        out[d] = fix.input_delay
    return out


def timing(n_per_team: int, horizons, frames: int) -> dict:
    truth = Truth(n_per_team, seed=31)
    bot = DelayedBot(3)
    fix = Corrector(index=0, input_delay=3)
    up, pr = [], {h: [] for h in horizons}

    def on_packet(k, pkt):
        t0 = time.perf_counter()
        fix.update(pkt)
        up.append(time.perf_counter() - t0)
        for h in horizons:
            t0 = time.perf_counter()
            fix.predict(h)
            pr[h].append(time.perf_counter() - t0)
    play(truth, bot, fix, frames, on_packet=on_packet)

    def q(v):
        v = sorted(v[20:])
        return {"median_ms": round(1000 * statistics.median(v), 3), "p99_ms": round(1000 * v[int(0.99 * (len(v) - 1))], 3)}
    return {"update": q(up), **{f"predict_{h}": q(v) for h, v in pr.items()}}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "results.json"))
    a = ap.parse_args()
    F = 300 if a.quick else 1500
    res = {}
    t0 = time.time()
    res["exactness"] = {f"{n}v{n}": exactness(n, 3, (2, 4, 8), 60 if a.quick else 200) for n in (1, 3)}
    print("exactness", res["exactness"], flush=True)
    lad = {}
    for n in (1, 3):
        for mode in ("exact seed", "cold start", "stateless"):
            for tad in (0, 1):
                r = ladder(n, mode, 3, tad, F, seed=40 + n)
                lad[f"{n}v{n} | {mode} | train delay {tad}"] = {"horizon": r["horizon"], "own": summarize(r["own"]),
                                                                "opponents": summarize(r["opp"]),
                                                                "ball_pos_mean": statistics.mean(r["ball"]),
                                                                "ball_pos_p99": sorted(r["ball"])[int(0.99 * (len(r["ball"]) - 1))],
                                                                "snaps": [c.snaps for c in r["fix"].cars],
                                                                "stats": {k: vars(s) for k, s in r["fix"].stats.items()}}
                print("ladder", n, mode, tad, "own pos", round(lad[f"{n}v{n} | {mode} | train delay {tad}"]["own"]["pos_mean"], 4), flush=True)
    res["ladder"] = lad
    res["faults"] = {f"{n}v{n}": faults_run(n, F, 60 + n, 0.02) for n in (1, 3)}
    print("faults", json.dumps({k: v["per_field"] for k, v in res["faults"].items()}), flush=True)
    res["air_order"] = {}
    for order in ("jump_first", "ground_first"):
        r = ladder(1, "cold start", 1, 0, F, seed=70, order=order)
        st = r["fix"].stats.get("air_state")
        res["air_order"][order] = {"learned": "ground_first" if r["fix"].order_votes["ground_first"] > r["fix"].order_votes["jump_first"] else "jump_first",
                                   "votes": dict(r["fix"].order_votes), "air_state_packet_wrong": st.packet_wrong if st else None,
                                   "own_isOnGround": r["own"].rate("isOnGround"), "own_isJumping": r["own"].rate("isJumping")}
    print("air order", res["air_order"], flush=True)
    res["skips"] = {}
    for n in (1, 3):
        r = ladder(n, "cold start", 3, 0, F, seed=80 + n, skip=(10, {3, 7}))
        res["skips"][f"{n}v{n}"] = {"own": summarize(r["own"]), "opponents": summarize(r["opp"]), "snaps": [c.snaps for c in r["fix"].cars]}
    print("skips own pos", {k: round(v["own"]["pos_mean"], 4) for k, v in res["skips"].items()}, flush=True)
    res["jump_on_ground"] = jump_on_ground()
    res["lag_meter"] = lag_meter((1, 2, 3, 4, 6))
    print("lag", res["lag_meter"], flush=True)
    res["timing"] = {f"{n}v{n}": timing(n, (2, 4, 8), 200 if a.quick else 600) for n in (1, 3)}
    print("timing", res["timing"], flush=True)
    res["seconds"] = round(time.time() - t0, 1)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("wrote", a.out, "in", res["seconds"], "s")


if __name__ == "__main__":
    main()
