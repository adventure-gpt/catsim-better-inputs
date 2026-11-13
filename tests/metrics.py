"""Field-by-field comparison of a corrected CarState with the truth's."""
from __future__ import annotations

import math
from collections import defaultdict

BOOLS = ("isOnGround", "isJumping", "hasJumped", "hasDoubleJumped", "hasFlipped", "isFlipping", "isBoosting",
         "isSupersonic", "isAutoFlipping", "isDemoed", "jumpHoldBroken", "canFlip", "hasFlipReset")
TIMERS = ("jumpTime", "flipTime", "airTime", "airTimeSinceJump", "boostingTime", "timeSinceBoosted", "supersonicTime",
          "supersonicGrace", "handbrakeVal", "autoFlipTimer", "boostLockLeft", "pitchLockLeft", "demoRespawnTimer",
          "dodgeTimeout", "boost")
TIMER_OK = 1e-3            # within a millisecond (an eighth of a tick) counts as right
FIELDS = BOOLS + ("wheelsWithContact", "worldContact", "flipRelTorque", "lastControls", "carContact") + TIMERS


def _d3(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _angle(ra, rb) -> float:
    """Angle between two rotations given as (forward, right, up), degrees."""
    f = math.sqrt(sum((x - y) ** 2 for k in range(3) for x, y in zip(ra[k], rb[k])))   # |Ra - Rb| (Frobenius)
    return math.degrees(2.0 * math.asin(min(1.0, f / (2.0 * math.sqrt(2.0)))))


def compare(t, e) -> dict:
    """{field: (ok, error)} for one car."""
    out = {}
    for k in BOOLS:
        out[k] = (bool(getattr(t, k)) == bool(getattr(e, k)), 0.0)
    out["wheelsWithContact"] = (tuple(t.wheelsWithContact) == tuple(e.wheelsWithContact), 0.0)
    out["worldContact"] = (t.worldContact.hasContact == e.worldContact.hasContact, 0.0)
    out["flipRelTorque"] = (_d3(t.flipRelTorque, e.flipRelTorque) < 1e-3, _d3(t.flipRelTorque, e.flipRelTorque))
    out["lastControls"] = (max(abs(x - y) for x, y in zip(t.lastControls, e.lastControls)) < 1e-3, 0.0)
    cc = (t.carContact.otherCarID == e.carContact.otherCarID and abs(t.carContact.cooldownTimer - e.carContact.cooldownTimer) < TIMER_OK)
    out["carContact"] = (cc, abs(t.carContact.cooldownTimer - e.carContact.cooldownTimer))
    for k in TIMERS:
        d = abs(float(getattr(t, k)) - float(getattr(e, k)))
        out[k] = (d <= TIMER_OK, d)
    if not t.isDemoed and not e.isDemoed:
        out["pos"] = (None, _d3(t.pos, e.pos))
        out["vel"] = (None, _d3(t.vel, e.vel))
        out["angVel"] = (None, _d3(t.angVel, e.angVel))
        out["rotMat"] = (None, _angle(t.rotMat, e.rotMat))
    return out


class Tally:
    def __init__(self):
        self.ok = defaultdict(int)
        self.n = defaultdict(int)
        self.err = defaultdict(list)

    def add(self, cmp: dict) -> None:
        for k, (ok, err) in cmp.items():
            if ok is not None:
                self.n[k] += 1
                self.ok[k] += bool(ok)
            self.err[k].append(err)

    def rate(self, k) -> float:
        return self.ok[k] / self.n[k] if self.n[k] else float("nan")

    def pct(self, k, q) -> float:
        v = sorted(self.err[k])
        return v[min(len(v) - 1, int(q * len(v)))] if v else float("nan")

    def mean(self, k) -> float:
        v = self.err[k]
        return sum(v) / len(v) if v else float("nan")
