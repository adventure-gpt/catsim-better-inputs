"""The corrector: catSim follows the match packet by packet and hands the bot a corrected state.

Per packet (`update`):
  1. step a tracking arena from the last packet's frame to this one with the inputs that were applied (the packet's
     `last_input` for every car on its own frame, your own sent controls in between);
  2. compare every body with the packet: within the packet's rounding the tracked state is kept (it is exact; the
     packet is not), beyond it the body is snapped to the packet (drift correction);
  3. compare every flag and timer the packet reports with what physics says it should report. When the tracked car is
     in step with the packet and the flag came from physics, the packet's flag is counted as wrong and the physics
     value is kept; when the flag is still an assumption (right after a cold start, a demolition, a snap) the packet's
     value is adopted;
  4. after a cold start or a snap, a one-tick probe finds the wheels' contacts at the packet's pose.
`predict(ticks)` copies the tracking arena and steps it ahead with your buffered controls and an estimate of everyone
else's, then reads every car out in RocketSim's CarState meanings.
"""
from __future__ import annotations

import math
import os
import struct
from collections import deque
from dataclasses import dataclass, field

from . import rsview as R
from .packets import (DODGING, DOUBLE_JUMPING, IN_AIR, JUMPING, ON_GROUND, Frame, as_frame)
from .state import BallState, CarState, Corrected, PadState, read_car

DT = R.DT
ROT = 32768.0 / math.pi

STANDARD_PRESETS = ("Octane", "MuscleCar", "Darkcar", "LongCar", "Hybrid", "Merc")


@dataclass
class Tolerances:
    """Two levels per quantity. Within `snap_*` the tracked body is in step with the packet (the packet's own
    rounding) and is kept; beyond it the body is snapped to the packet. Within `trust_*` the tracked car still moves as
    physics says it should (a wrong jump, dodge or ground flag moves a body far more than this in one tick), so the
    flags physics gives are trusted over the packet's; beyond it the car's flags are taken again from the packet."""
    snap_pos: float = 0.02         # uu (packets round positions to 0.01)
    snap_vel: float = 0.05         # uu/s
    snap_ang_vel: float = 0.003    # rad/s
    snap_rot: float = 1.5          # rotator units (65536 a turn; packets carry the game's integer rotator)
    trust_pos: float = 5.0
    trust_vel: float = 40.0
    trust_ang_vel: float = 1.0
    trust_rot: float = 1200.0
    boost: float = 1.0             # 0..100
    teleport: float = 400.0        # uu: a jump this big is a reset, respawn or state set: start that car again
    timer: float = 2.5 * DT        # s, for the packet's dodge clocks


@dataclass
class FieldStats:
    compared: int = 0
    agreed: int = 0
    packet_wrong: int = 0      # the packet contradicted physics; the physics value was kept
    adopted: int = 0           # the packet's value was taken (the physics value was still an assumption)

    @property
    def error_rate(self) -> float:
        return self.packet_wrong / self.compared if self.compared else 0.0


class HoldLastInput:
    """Opponent estimator: each car keeps the input its last packet showed. Replace with your own object that has the
    same `controls(corrector, car_index, tick) -> 8 floats` method (e.g. your own policy run on their view)."""

    def controls(self, corrector, i: int, tick: int):
        return corrector.observed_input(i)


class Neutral:
    def controls(self, corrector, i: int, tick: int):
        return (0.0,) * 8


def game_axis(x: float) -> float:
    """An analog input as the game applies it: one byte, k/127 at or above zero and k/128 below, k rounded to nearest
    (real RLBot v5 games: every applied `last_input` axis is one of these). Rounded to f32 first, as the C API's floats."""
    x = max(-1.0, min(1.0, _f32(x)))
    return math.floor(x * 127.0 + 0.5) / 127.0 if x >= 0.0 else -math.floor(-x * 128.0 + 0.5) / 128.0


def game_controls(c) -> tuple:
    """Controls as the game applies them (axes quantized, buttons 0 / 1). Your sent controls go through this, so the
    predicted own car is stepped with what the game will really apply."""
    r = _row(c)
    return (game_axis(r[0]), game_axis(r[1]), game_axis(r[2]), game_axis(r[3]), game_axis(r[4]),
            1.0 if r[5] > 0.5 else 0.0, 1.0 if r[6] > 0.5 else 0.0, 1.0 if r[7] > 0.5 else 0.0)


def _f32(x) -> float:
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def same_controls(a, b) -> bool:
    """Equal as the game applies them (axes one byte, buttons on/off)."""
    if a is None or b is None:
        return False
    return all(game_axis(a[k]) == game_axis(b[k]) for k in range(5)) and all((a[k] > 0.5) == (b[k] > 0.5) for k in range(5, 8))


class LagMeter:
    """Measures the input delay D: controls the bot sends after the packet of frame S are first applied on frame S + D
    (D = 1: no lag). It watches for a change in your sent controls, then for the first packet whose `last_input` for
    your car shows it."""

    def __init__(self, keep: int = 64):
        self.pending: deque = deque()
        self.samples: deque = deque(maxlen=keep)
        self.last_sent = None

    def sent(self, frame: int, controls) -> None:
        if self.last_sent is not None and not same_controls(controls, self.last_sent):
            self.pending.append((frame, tuple(controls), tuple(self.last_sent)))
            while len(self.pending) > 16:
                self.pending.popleft()
        self.last_sent = tuple(controls)

    def seen(self, frame: int, last_input) -> None:
        if last_input is None:
            return
        while self.pending and frame - self.pending[0][0] > 40:
            self.pending.popleft()
        for k, (s, c, before) in enumerate(self.pending):
            if frame > s and same_controls(last_input, c) and not same_controls(last_input, before):
                self.samples.append(frame - s)
                for _ in range(k + 1):
                    self.pending.popleft()
                break

    @property
    def delay(self) -> int | None:
        if len(self.samples) < 4:
            return None
        s = sorted(self.samples)
        return int(s[len(s) // 2])


@dataclass
class _Car:
    """What the corrector remembers about one car between packets."""
    obs: R.RSObserver = field(default_factory=R.RSObserver)
    assumed: set = field(default_factory=set)       # facts still taken from a packet, not physics
    events: set = field(default_factory=set)        # physics events since the last packet
    votes: dict = field(default_factory=dict)       # packet field -> {value: packets} while what it rests on is assumed
    history: deque = field(default_factory=lambda: deque(maxlen=90))   # (frame, CarIn)
    observed: dict = field(default_factory=dict)    # frame -> input the packet showed for that frame
    last_input: tuple = (0.0,) * 8
    synced: bool = False
    ticks_since_cold: int = 0
    drift: float = 0.0
    snaps: int = 0
    probed_frame: int = -1
    consistent: bool = False
    last_d: tuple = (0.0, 0.0, 0.0, 0.0)


# Hidden facts the packet can report wrong. Each is either known from physics (the tracker saw the event that set it:
# a landing, a jump from the ground, a flip, a double jump, a clear speed) or still an assumption taken from a packet.
AIR_FACTS = {"has_jumped", "has_double_jumped", "has_dodged", "jump_clock", "dj_clock", "dodge_clock"}
ALL_FACTS = AIR_FACTS | {"supersonic", "boost_clock", "handbrake"}
# which facts each packet field rests on
PROVENANCE = {"air_state": {"jump_clock", "dj_clock", "dodge_clock"}, "has_jumped": {"has_jumped"},
              "has_double_jumped": {"has_double_jumped"}, "has_dodged": {"has_dodged"},
              "dodge_timeout": {"has_jumped", "has_double_jumped", "has_dodged", "jump_clock"},
              "dodge_elapsed": {"has_dodged", "dodge_clock"}, "supersonic": {"supersonic"}, "on_ground": set()}


class Corrector:
    """catSim input correction for one bot (or a hivemind: pass several indices).

        fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0)
        state = fix.correct(packet)        # each packet, before the policy runs
        controls = policy(state)
        fix.sent(controls)                 # after, with what you return
    """

    def __init__(self, index: int | list = 0, data_dir: str | None = None, training_action_delay: int = 0,
                 input_delay: int | None = None, opponent_model=None, tolerances: Tolerances | None = None,
                 field_info=None, map: str = "Stadium_P", log_flag_errors: bool = False, trust_packet_flags: bool = False,
                 rlbot_air_order: str = "auto", last_input_is_applied: bool = True, pad_timer: str = "auto"):
        import catsim
        self.cs = catsim
        data = data_dir or os.environ.get("CATSIM_DATA") or "catsim_data"
        catsim.init(data, silent=True)
        self.data_dir = str(catsim._data_dir)
        self.lib = catsim._lib()
        self.own = [index] if isinstance(index, int) else list(index)
        self.training_action_delay = int(training_action_delay)
        self.fixed_delay = input_delay
        self.opponents = opponent_model or HoldLastInput()
        self.tol = tolerances or Tolerances()
        self.field_info = field_info
        self.map = map
        self.log_flag_errors = log_flag_errors
        self.flag_log: list = []
        self.trust_packet_flags = trust_packet_flags
        self.rlbot_air_order = rlbot_air_order          # "auto", "jump_first" or "ground_first"
        self.order_votes = {"jump_first": 0, "ground_first": 0}
        self.last_input_is_applied = last_input_is_applied
        # a taken pad's timer: "left" (seconds until it returns: what real RLBot v5 games show), "elapsed" (seconds
        # since it was taken: what RLBot's schema describes) or "auto" (learned from the first timer that moves)
        self.pad_timer = pad_timer
        self.pad_mode = None if pad_timer == "auto" else pad_timer
        self.prev_pads = None
        self.meter = LagMeter()
        self.stats: dict[str, FieldStats] = {}
        self.na = None
        self.layout = None
        self.frame = -1
        self.cars: list[_Car] = []
        self.sent_by_frame: dict[int, tuple] = {}    # packet frame the controls were chosen on -> controls (own[0])
        self.sent_extra: dict[tuple, tuple] = {}      # (car index, apply tick) -> controls, for hivemind / tests
        self.last_frame: Frame | None = None
        self.pad_map = None
        self.cold_starts = 0
        self.presets: list[str] = []
        self.predicted_arena = None
        self._prev = None
        self._prev_obs = []
        self._rows = []

    # ------------------------------------------------------------------ inputs

    @property
    def input_delay(self) -> int:
        """D: controls chosen on packet frame S apply from frame S + D. Measured unless you fixed it."""
        if self.fixed_delay is not None:
            return int(self.fixed_delay)
        d = self.meter.delay
        return 1 if d is None else d

    @property
    def horizon(self) -> int:
        """How far ahead `correct` predicts: to the frame before your next action applies, less the delay your
        policy already learned in training. Never negative."""
        return max(0, self.input_delay - 1 - self.training_action_delay)

    def sent(self, controls, frame: int | None = None) -> None:
        """Record the controls you returned for the packet of `frame` (default: the last packet)."""
        f = self.frame if frame is None else int(frame)
        c = game_controls(controls)
        self.sent_by_frame[f] = c
        self.meter.sent(f, c)
        for k in [k for k in self.sent_by_frame if k < f - 240]:
            del self.sent_by_frame[k]

    def record_sent(self, controls, tick: int, index: int | None = None) -> None:
        """Known controls for car `index` (default yours) applied from game tick `tick` on. For hiveminds, lockstep
        setups and tests."""
        i = self.own[0] if index is None else int(index)
        self.sent_extra[(i, int(tick))] = game_controls(controls)
        old = [k for k in self.sent_extra if k[1] < tick - 240]
        for k in old:
            del self.sent_extra[k]

    def own_controls(self, i: int, tick: int):
        """The controls car i (one of yours) has on game tick `tick`, from what you sent: None if unknown."""
        best, bt = None, -10 ** 9
        for (j, t), c in self.sent_extra.items():
            if j == i and bt < t <= tick:
                best, bt = c, t
        if i == self.own[0]:
            d = self.input_delay
            for s, c in self.sent_by_frame.items():
                t = s + d
                if bt < t <= tick:
                    best, bt = c, t
        return best

    def observed_input(self, i: int):
        return self.cars[i].last_input if i < len(self.cars) else (0.0,) * 8

    def _input(self, i: int, tick: int, fr: Frame | None):
        """The controls car i had on `tick` while catching up to packet `fr`."""
        c = self.cars[i]
        if fr is not None and tick == fr.frame and self.last_input_is_applied and fr.cars[i].last_input is not None:
            return fr.cars[i].last_input
        if i in self.own:
            o = self.own_controls(i, tick)
            if o is not None:
                return o
        if tick in c.observed:
            return c.observed[tick]
        return self.opponents.controls(self, i, tick) if i not in self.own else c.last_input

    # ------------------------------------------------------------------ the arena

    def _preset(self, car) -> str:
        """The body whose hitbox (size and offset) is nearest the packet's. Real RLBot v5 packets carry the game's own
        hitbox, equal to catSim's preset size; a size 2.5 uu smaller on each axis (RocketSim's numbers) is matched too."""
        best, name = 1e9, "Octane"
        off_in = car.hitbox_offset if car.hitbox_offset is not None else (0.0, 0.0, 0.0)
        for p in STANDARD_PRESETS:
            size, off = self.lib.preset_hitbox(p)
            d_off = sum(abs(off[k] - off_in[k]) for k in range(3))
            for under in (0.0, 2.5):
                d = sum(abs(size[k] - under - car.hitbox[k]) for k in range(3)) + d_off
                if d < best:
                    best, name = d, p
        return name

    def _build(self, fr: Frame) -> None:
        self.presets = [self._preset(c) for c in fr.cars]
        cars = [(p, int(c.team)) for p, c in zip(self.presets, fr.cars)]
        self.na = self.lib.NativeArena(cars, self.data_dir, False, self.map)
        self.layout = tuple(cars)
        self.cars = [_Car() for _ in fr.cars]
        pads = self.lib.pad_locations(self.map)
        locs = fr.extra.get("pad_locations")
        if locs:
            self.pad_map = [min(range(len(pads)), key=lambda k: (pads[k][0][0] - x) ** 2 + (pads[k][0][1] - y) ** 2) for x, y in locs]
        else:
            self.pad_map = list(range(len(pads)))
        self.pad_big = [b for _, b in pads]

    def _step(self, na, rows, cars) -> None:
        na.step([list(r) for r in rows], 1)
        for i, c in enumerate(cars):
            h = na.car_hidden(i)
            c.obs.tick(h)
            c.events |= c.obs.events

    # ------------------------------------------------------------------ per packet

    def update(self, packet) -> Corrected:
        """Take a packet; return the corrected state at the packet's own frame (no look-ahead)."""
        fr = as_frame(packet, self.field_info)
        for i in self.own:
            if i < len(fr.cars):
                self.meter.seen(fr.frame, fr.cars[i].last_input)
        if fr.phase in ("inactive", "replay", "paused", "ended", "goal"):
            self.last_frame = fr
            if fr.phase != "paused":
                self.frame = -1
                return None                       # nothing to correct between a goal and the next kickoff
            return self._read(self.na, self.cars, fr.frame) if self.na is not None else None
        layout = tuple((self._preset(c), int(c.team)) for c in fr.cars)
        k = fr.frame - self.frame
        cold = self.na is None or layout != self.layout or self.frame < 0 or k < 0 or k > 60
        if cold:
            if self.na is None or layout != self.layout:
                self._build(fr)
            self._remember(fr)
            self._cold_all(fr)
        elif k == 0:
            self._remember(fr)
        else:
            self._remember(fr)
            if self.last_frame is not None and self.last_frame.phase == "countdown" and fr.phase != "countdown":
                self._release()
            self._prev = self.na.copy()
            self._prev_obs = [c.obs.copy() for c in self.cars]
            self._rows = []
            for t in range(self.frame + 1, fr.frame + 1):
                rows = [self._input(i, t, fr) for i in range(len(self.cars))]
                self._rows.append(rows)
                self._step(self.na, rows, self.cars)
            self.frame = fr.frame
            for c in self.cars:
                c.ticks_since_cold += k
            self._resync(fr)
        self._probe(fr)
        self.last_frame = fr
        return self._read(self.na, self.cars, self.frame)

    def seed_exact(self, packet, native_arena, observers=None) -> None:
        """Start from a state you know exactly instead of a packet: a catSim native arena (`Arena._native_arena()`)
        holding this match at the packet's frame, e.g. one you ran in lockstep from a kickoff. Its cars must be in the
        packet's order. `observers`: RocketSim bookkeeping per car if you have it (default: fresh, right for cars on
        the ground)."""
        fr = as_frame(packet, self.field_info)
        self._build(fr)
        self.na = native_arena.copy()
        self.frame = fr.frame
        self._remember(fr)
        for i, c in enumerate(self.cars):
            c.obs = observers[i].copy() if observers else R.RSObserver()
            c.assumed, c.synced, c.consistent, c.probed_frame = set(), True, True, fr.frame
        self.last_frame = fr

    def predict(self, ticks: int | None = None) -> Corrected:
        """The state `ticks` ticks after the last packet (default: `horizon`), stepped with your sent controls and the
        opponent estimate."""
        n = self.horizon if ticks is None else int(ticks)
        if self.na is None or (self.last_frame is not None and self.last_frame.phase in ("inactive", "replay", "ended", "goal")):
            return None
        if n <= 0:
            self.predicted_arena = self.na
            return self._read(self.na, self.cars, self.frame)
        na = self.na.copy()
        cars = [_Car(obs=c.obs.copy(), assumed=set(c.assumed), synced=c.synced, consistent=c.consistent,
                     ticks_since_cold=c.ticks_since_cold, drift=c.drift) for c in self.cars]
        known = [i in self.own or any(j == i for j, _ in self.sent_extra) for i in range(len(cars))]
        for t in range(self.frame + 1, self.frame + n + 1):
            rows = []
            for i in range(len(cars)):
                o = self.own_controls(i, t) if known[i] else None
                rows.append(o if o is not None else (self.opponents.controls(self, i, t) if not known[i] else self.cars[i].last_input))
            self._step(na, rows, cars)
        self.predicted_arena = na            # the predicted catSim arena, for your own rollouts from it
        out = self._read(na, cars, self.frame + n)
        out.horizon = n
        for i, c in enumerate(out.cars):
            c.confidence["inputs_known"] = known[i]
        return out

    def correct(self, packet, ticks: int | None = None) -> Corrected:
        """`update` then `predict`: the state at the moment your next action takes effect, as your policy was trained to
        see it."""
        self.update(packet)
        return self.predict(ticks)

    # ------------------------------------------------------------------ remembering packets

    def _remember(self, fr: Frame) -> None:
        for i, (c, p) in enumerate(zip(self.cars, fr.cars)):
            c.history.append((fr.frame, p))
            if p.last_input is not None:
                c.observed[fr.frame] = p.last_input
                c.last_input = p.last_input
                for k in [k for k in c.observed if k < fr.frame - 120]:
                    del c.observed[k]

    def _run(self, i: int, test) -> int:
        """How many of car i's latest packets in a row pass `test` (counted in frames)."""
        h = self.cars[i].history
        if not h or not test(h[-1][1]):
            return 0
        first = h[-1][0]
        for f, p in reversed(h):
            if not test(p):
                break
            first = f
        return h[-1][0] - first + 1

    # ------------------------------------------------------------------ cold starts

    def _cold_all(self, fr: Frame) -> None:
        self.cold_starts += 1
        self.frame = fr.frame
        rh = list(self.na.round_hidden())
        self.na.set_round_hidden(int(fr.frame), 1, 0.0, None, True, fr.phase == "countdown", 300.0, list(rh[7]))
        for i in range(len(fr.cars)):
            self._cold_car(fr, i)
        self._set_ball(fr)
        self._set_pads(fr, force=True)

    def _release(self) -> None:
        """The kickoff countdown ended: every car takes input again and the ball is no longer held. (Real games: the
        first packet after the countdown already shows the cars driving, so this comes before its tick is stepped.)"""
        for i in range(len(self.cars)):
            if self.na.car_body(i) is not None:
                self.na.set_driving(i, True)
        rh = list(self.na.round_hidden())
        cc = None if rh[3] is None else (int(rh[3][0]), float(rh[3][1]), float(rh[3][2]))
        self.na.set_round_hidden(int(rh[0]), int(rh[1]), float(rh[2]), cc, bool(rh[4]), False, float(rh[6]), list(rh[7]))

    def _set_body(self, i: int, p) -> None:
        """Snap car i's body to the packet (the game's own state set: position, velocity, spin, the integer rotation),
        keeping every hidden field, the exact boost amount included."""
        pitch, yaw, roll = p.rot
        h = self.na.car_hidden(i)
        if h is None:                                      # demolished in the arena: give it a body back first
            self.na.set_car_body(i, [p.pos[0] / 50, p.pos[1] / 50, p.pos[2] / 50, 1, 0, 0, 0, 1, 0, 0, 0, 1,
                                     0, 0, 0, 0, 0, 0, 0.0, 0.0])
        self.na.set_car(i, [*p.pos, *p.vel, *p.ang_vel, yaw, pitch, roll, p.boost])
        if h is not None:
            self.na.set_car_hidden(i, list(h))

    def _cold_car(self, fr: Frame, i: int) -> None:
        """Start car i again from its packet: body from the packet, hidden state from the packet's flags where physics
        can't check them yet, everything else inferred from the packet history and geometry."""
        p = fr.cars[i]
        c = self.cars[i]
        c.ticks_since_cold, c.synced, c.consistent, c.drift = 0, False, False, 0.0
        c.assumed = set(ALL_FACTS)
        c.votes = {}
        if p.demolished:
            self.na.set_car_demolished(i, float(max(p.demo_left, DT)))
            c.obs = R.RSObserver()
            return
        self._set_body(i, p)
        h = list(self.na.car_hidden(i))
        self._hidden_from_packet(fr, i, h, full=True)
        self.na.set_car_hidden(i, h)
        if p.boost is not None:
            self._set_boost_exact(i, p.boost)
        c.obs = self._observer_from_packet(fr, i, h)
        c.probed_frame = -1
        self.na.set_driving(i, fr.phase != "countdown")

    def _set_boost_exact(self, i: int, boost: float) -> None:
        h = self.na.car_hidden(i)
        if h is not None and abs(h[R.H_BOOST_AMOUNT] * 100.0 - boost) > 1e-4:
            h = list(h)
            h[R.H_BOOST_AMOUNT] = max(0.0, min(1.0, boost / 100.0))
            self.na.set_car_hidden(i, h)

    def _air_state(self, p) -> int | None:
        if p.air_state is not None:
            return int(p.air_state)
        if p.on_ground is not None:
            return ON_GROUND if p.on_ground else IN_AIR
        return None

    def _hidden_from_packet(self, fr: Frame, i: int, h: list, full: bool) -> None:
        """Write what the packet says (and what its history implies) into hidden row h. Contacts are left for the
        probe; `full` also resets the clocks the packet can't speak to."""
        p = fr.cars[i]
        air = self._air_state(p)
        jumped = bool(p.has_jumped) if p.has_jumped is not None else False
        dj = bool(p.has_double_jumped) if p.has_double_jumped is not None else False
        dodged = bool(p.has_dodged) if p.has_dodged is not None else False
        li = p.last_input or self.cars[i].last_input
        h[R.H_DRIVING] = 0.0 if fr.phase == "countdown" else 1.0
        h[R.H_ON_GROUND] = 1.0 if air == ON_GROUND or (air is None and p.on_ground) else 0.0
        h[R.H_JUMPED] = 1.0 if (jumped or dj or dodged) else 0.0
        h[R.H_DOUBLE_JUMPED] = 1.0 if (dj or dodged) else 0.0
        h[R.H_SUPERSONIC] = 1.0 if p.supersonic else 0.0
        jumping = air == JUMPING
        if jumping:
            run = max(1, self._run(i, lambda q: q.air_state == JUMPING))
            h[R.H_JUMP_ON], h[R.H_JUMP_T] = 1.0, run * DT
            h[R.H_ON_GROUND] = 1.0 if run <= 6 else 0.0          # the probe settles it
        else:
            h[R.H_JUMP_ON] = 0.0
            if p.dodge_timeout is not None and p.dodge_timeout >= 0 and jumped:
                h[R.H_JUMP_T] = max(0.0, R.DOUBLEJUMP_MAX_DELAY - p.dodge_timeout)
            elif full:
                h[R.H_JUMP_T] = 2.0
        h[R.H_JUMP_RELEASED] = 1.0 if li[5] < 0.5 else 0.0      # let go now: let go during the jump too (a guess)
        if air == DOUBLE_JUMPING:
            h[R.H_DJ_ON], h[R.H_DJ_T] = 1.0, max(1, self._run(i, lambda q: q.air_state == DOUBLE_JUMPING)) * DT
        else:
            h[R.H_DJ_ON] = 0.0
            if full:
                h[R.H_DJ_T] = 2.0
        if air == DODGING or (dodged and p.dodge_elapsed is not None and 0 < p.dodge_elapsed < R.FLIP_TORQUE_TIME and air != ON_GROUND):
            h[R.H_DODGE_ON], h[R.H_DODGE_T] = 1.0, max(DT, float(p.dodge_elapsed or DT))
        else:
            h[R.H_DODGE_ON] = 0.0
            if full:
                h[R.H_DODGE_T] = 2.0
        if p.dodge_dir is not None and (p.dodge_dir[0] or p.dodge_dir[1]):
            t, d = self.na.dodge_from_input(i, float(p.dodge_dir[0]), float(p.dodge_dir[1]))
            h[R.H_DODGE_TORQUE:R.H_DODGE_TORQUE + 3], h[R.H_DODGE_DIR:R.H_DODGE_DIR + 3] = list(t), list(d)
        h[R.H_HELD:R.H_HELD + 8] = list(li)
        if full:
            boosting = li[6] > 0.5 and p.boost > 0
            run = self._run(i, lambda q: q.last_input is not None and (q.last_input[6] > 0.5) == boosting)
            h[R.H_BOOST_ON], h[R.H_BOOST_T] = (1.0 if boosting else 0.0), max(DT, run * DT)
            hb = li[7] > 0.5
            run = self._run(i, lambda q: q.last_input is not None and (q.last_input[7] > 0.5) == hb)
            h[R.H_HANDBRAKE] = min(1.0, run * DT * R.POWERSLIDE_RISE_RATE) if hb else max(0.0, 1.0 - run * DT * R.POWERSLIDE_FALL_RATE)
            h[R.H_AF_ON] = 0.0
            h[R.H_SS_TIME] = 0.0

    def _observer_from_packet(self, fr: Frame, i: int, h: list) -> R.RSObserver:
        p = fr.cars[i]
        o = R.RSObserver()
        air = self._air_state(p)
        o.is_jumping = h[R.H_JUMP_ON] > 0.5
        o.has_jumped = bool(p.has_jumped) or o.is_jumping
        o.has_double_jumped = bool(p.has_double_jumped)
        o.has_flipped = bool(p.has_dodged)
        o.flip_time = float(p.dodge_elapsed or 0.0) if o.has_flipped else 0.0
        o.is_flipping = o.has_flipped and o.flip_time < R.FLIP_TORQUE_TIME
        o.prev_dodge, o.prev_dj = h[R.H_DODGE_ON] > 0.5, h[R.H_DJ_ON] > 0.5
        if p.dodge_dir is not None:
            o.flip_rel_torque = (-float(p.dodge_dir[1]), float(p.dodge_dir[0]), 0.0)
        on = air == ON_GROUND
        if not on:
            o.air_time = max(1, self._run(i, lambda q: self._air_state(q) != ON_GROUND)) * DT
        if o.is_jumping:
            o.jump_time = h[R.H_JUMP_T]
        elif o.has_jumped and not on:
            since = h[R.H_JUMP_T] if (p.dodge_timeout is not None and p.dodge_timeout >= 0) else o.air_time
            o.air_time_since_jump = since
            o.jump_time = since + 0.1
        o.supersonic_time = self._run(i, lambda q: q.supersonic) * DT if p.supersonic else 0.0
        return o

    # ------------------------------------------------------------------ resync

    def _rlbot_view(self, h, obs: R.RSObserver) -> dict:
        """What an RLBot v5 packet should show for this car, from physics."""
        if h is None:
            return {"demolished": True}
        on, jumping = h[R.H_ON_GROUND] > 0.5, h[R.H_JUMP_ON] > 0.5
        dj, dodge = h[R.H_DJ_ON] > 0.5, h[R.H_DODGE_ON] > 0.5
        order = self.rlbot_air_order
        if order == "auto":
            v = self.order_votes
            order = "ground_first" if v["ground_first"] > v["jump_first"] else "jump_first"
        if order == "jump_first":
            air = JUMPING if jumping else DOUBLE_JUMPING if dj else DODGING if dodge else ON_GROUND if on else IN_AIR
        else:
            air = ON_GROUND if on else JUMPING if jumping else DOUBLE_JUMPING if dj else DODGING if dodge else IN_AIR
        # (real games) RLBot's dodge_timeout is the game's own jump clock: time since the jump ended, counted on the
        # ground too, and -1 while the jump is still on
        can = (obs.has_jumped and not (obs.has_double_jumped or obs.has_flipped) and not on and not jumping
               and h[R.H_JUMP_T] < R.DOUBLEJUMP_MAX_DELAY)                    # (and -1 once the window is over)
        return {"demolished": False, "air_state": air, "has_jumped": obs.has_jumped,
                "has_double_jumped": obs.has_double_jumped, "has_dodged": obs.has_flipped,
                "dodge_timeout": max(0.0, R.DOUBLEJUMP_MAX_DELAY - h[R.H_JUMP_T]) if can else -1.0,
                "dodge_elapsed": obs.flip_time if obs.has_flipped else 0.0, "supersonic": h[R.H_SUPERSONIC] > 0.5}


    def _stat(self, name: str) -> FieldStats:
        s = self.stats.get(name)
        if s is None:
            s = self.stats[name] = FieldStats()
        return s

    def _resync(self, fr: Frame) -> None:
        tol = self.tol
        for i, p in enumerate(fr.cars):
            c = self.cars[i]
            body = self.na.car_body(i)
            if p.demolished or body is None:
                if p.demolished != (body is None):
                    self._cold_car(fr, i)
                continue
            row = self.na.car_row(i)
            hrow = self.na.hidden_row(i)
            pos, vel, ang = row[2:5], row[5:8], row[8:11]
            d_pos = max(abs(pos[k] - p.pos[k]) for k in range(3))
            if d_pos > tol.teleport:
                self._cold_car(fr, i)
                continue
            d_vel = max(abs(vel[k] - p.vel[k]) for k in range(3))
            d_ang = max(abs(ang[k] - p.ang_vel[k]) for k in range(3))
            d_rot = max(_rot_diff(hrow[24 + k], p.rot[k] * ROT) for k in range(3))
            synced = d_pos <= tol.snap_pos and d_vel <= tol.snap_vel and d_ang <= tol.snap_ang_vel and d_rot <= tol.snap_rot
            consistent = d_pos <= tol.trust_pos and d_vel <= tol.trust_vel and d_ang <= tol.trust_ang_vel and d_rot <= tol.trust_rot
            c.drift = d_pos
            c.last_d = (d_pos, d_vel, d_ang, d_rot)
            if not synced:
                c.snaps += 1
                self._set_body(i, p)
            if not consistent:
                c.probed_frame = -1
            h = list(self.na.car_hidden(i))
            if abs(h[R.H_BOOST_AMOUNT] * 100.0 - p.boost) > tol.boost + 1e-3:
                h[R.H_BOOST_AMOUNT] = max(0.0, min(1.0, p.boost / 100.0))
                self.na.set_car_hidden(i, h)
            c.synced, c.consistent = synced, consistent
            self._check_flags(fr, i, consistent, row)

    def _learn(self, c: _Car, h, row) -> None:
        """Facts physics has now established for this car (they no longer rest on a packet)."""
        ev, c.events = c.events, set()
        if "landed" in ev or "jump" in ev or (h[R.H_ON_GROUND] > 0.5 and h[R.H_JUMP_ON] < 0.5 and not c.obs.has_jumped
                                              and not c.obs.has_flipped and not c.obs.has_double_jumped):
            c.assumed -= AIR_FACTS
        if "flip" in ev:
            c.assumed -= {"has_dodged", "dodge_clock", "has_double_jumped"}
        if "double_jump" in ev:
            c.assumed -= {"has_double_jumped", "dj_clock", "has_dodged"}
        speed = math.sqrt(sum(x * x for x in row[5:8]))
        if speed < R.SUPERSONIC_START_SPEED - 100.0 or speed >= R.SUPERSONIC_START_SPEED:
            c.assumed.discard("supersonic")
        if c.ticks_since_cold * DT > 0.6:
            c.assumed -= {"boost_clock", "handbrake"}
        for k in [k for k in c.votes if not (PROVENANCE[k] & c.assumed)]:
            del c.votes[k]

    def _check_flags(self, fr: Frame, i: int, consistent: bool, row) -> None:
        """Compare what the packet reports with what physics says it should report.
          - physics knows the fact and the body is in step: physics wins, the packet is counted wrong;
          - physics knows it but the body went off: replay the last tick with the packet's flags (`_arbitrate`);
          - the fact is still an assumption: every packet since votes, and the packet's value is taken once it leads.
        The ground itself is never taken from a packet: it is the wheels' raycasts."""
        p = fr.cars[i]
        c = self.cars[i]
        h = self.na.car_hidden(i)
        if h is None:
            return
        self._learn(c, h, row)
        if p.air_state is not None and h[R.H_ON_GROUND] > 0.5 and h[R.H_JUMP_ON] > 0.5 and consistent:
            if p.air_state == JUMPING:                    # learn how this RLBot reports a jump still on its wheels
                self.order_votes["jump_first"] += 1
            elif p.air_state == ON_GROUND:
                self.order_votes["ground_first"] += 1
        want = self._rlbot_view(h, c.obs)
        # (real games) RLBot's is_supersonic is the speed test alone (>= 2200 uu/s), not the game's flag with its grace
        want["supersonic"] = math.sqrt(sum(x * x for x in row[5:8])) >= R.SUPERSONIC_START_SPEED
        got = {"air_state": p.air_state, "has_jumped": p.has_jumped, "has_double_jumped": p.has_double_jumped,
               "has_dodged": p.has_dodged, "dodge_timeout": p.dodge_timeout, "dodge_elapsed": p.dodge_elapsed,
               "supersonic": p.supersonic}
        if p.air_state is None and p.on_ground is not None:       # RLBot v4: a separate ground flag
            got["on_ground"] = p.on_ground
            want["on_ground"] = h[R.H_ON_GROUND] > 0.5
        adopt, contested = {}, {}
        for k, g in got.items():
            if g is None or k not in want:
                continue
            w = want[k]
            if k in ("dodge_timeout", "dodge_elapsed"):
                agree = abs(float(g) - float(w)) <= self.tol.timer or (float(g) < 0 and float(w) < 0)
            else:
                agree = (g == w)
            s = self._stat(k)
            s.compared += 1
            if agree:
                s.agreed += 1
                continue
            if not consistent and not (PROVENANCE[k] & c.assumed) and not self.trust_packet_flags and k != "on_ground":
                contested[k] = (g, w)
                continue
            known = consistent and not (PROVENANCE[k] & c.assumed) and not self.trust_packet_flags
            if known or (k == "on_ground" and not self.trust_packet_flags):
                s.packet_wrong += 1                       # (the ground is physics, the wheels' raycasts: never adopted)
                if self.log_flag_errors:
                    self.flag_log.append({"frame": fr.frame, "car": i, "field": k, "packet": g, "physics": w,
                                          "decision": "packet wrong"})
                continue
            # an assumption: every packet since it began votes (timers by the event frame they imply, so a stuck
            # timer is outvoted by the ones that kept counting); the packet's value is taken once it leads the vote
            votes = c.votes.setdefault(k, {})
            kg, kw = self._vote_key(k, g, fr.frame), self._vote_key(k, w, fr.frame)
            lead = _count(votes, kg) > _count(votes, kw)
            if lead or self.trust_packet_flags or (not consistent and not votes):
                s.adopted += 1
                adopt[k] = g
            if self.log_flag_errors:
                why = "adopted" if k in adopt else "held"
                self.flag_log.append({"frame": fr.frame, "car": i, "field": k, "packet": g, "physics": w, "decision": why,
                                      "reason": "body off" if not consistent else "assumption"})
        for k, g in got.items():                      # count this packet's votes after deciding (it can't outvote alone)
            if g is not None and k in want and k in PROVENANCE and (PROVENANCE[k] & c.assumed):
                _vote(c.votes.setdefault(k, {}), self._vote_key(k, g, fr.frame))
        if contested:
            if self._arbitrate(fr, i, {k: g for k, (g, w) in contested.items()}):
                for k, (g, w) in contested.items():
                    self.stats[k].adopted += 1
                    adopt[k] = g
                    if self.log_flag_errors:
                        self.flag_log.append({"frame": fr.frame, "car": i, "field": k, "packet": g, "physics": w,
                                              "decision": "adopted", "reason": "packet explains the body better"})
            else:
                for k, (g, w) in contested.items():
                    self.stats[k].packet_wrong += 1
                    if self.log_flag_errors:
                        self.flag_log.append({"frame": fr.frame, "car": i, "field": k, "packet": g, "physics": w,
                                              "decision": "packet wrong", "reason": "physics explains the body better"})
        if adopt:
            self._adopt(fr, i, adopt)

    def _fit(self, na, i: int, p) -> float:
        """How far car i in arena na is from packet car p, in units of the trust tolerances (1 = at the edge)."""
        tol = self.tol
        body = na.car_body(i)
        if body is None:
            return 1e9
        row, hr = na.car_row(i), na.hidden_row(i)
        return max(max(abs(row[2 + k] - p.pos[k]) for k in range(3)) / tol.trust_pos,
                   max(abs(row[5 + k] - p.vel[k]) for k in range(3)) / tol.trust_vel,
                   max(abs(row[8 + k] - p.ang_vel[k]) for k in range(3)) / tol.trust_ang_vel,
                   max(_rot_diff(hr[24 + k], p.rot[k] * ROT) for k in range(3)) / tol.trust_rot)

    def _arbitrate(self, fr: Frame, i: int, contested: dict) -> bool:
        """The body was off and the packet's flags disagree with physics: replay the catch-up from the previous state
        once with the packet's flags. True when they explain the packet better (the body, or the air state at equal fit)."""
        if self._prev is None or not self._rows:
            return True
        p = fr.cars[i]
        alt = self._prev.copy()
        if alt.car_hidden(i) is None:
            return True
        obs = [o.copy() for o in self._prev_obs]
        cars = [_Car(obs=o) for o in obs]
        self._adopt_into(fr, i, contested, cars[i].obs, alt)
        for rows in self._rows:
            self._step(alt, rows, cars)
        # the tracked car as it was before this packet's snap: the catch-up again with physics' own flags
        mine = self._prev.copy()
        mcars = [_Car(obs=o.copy()) for o in self._prev_obs]
        for rows in self._rows:
            self._step(mine, rows, mcars)
        fa, fb = self._fit(mine, i, p), self._fit(alt, i, p)
        air = p.air_state
        ha, hb = mine.car_hidden(i), alt.car_hidden(i)
        aa = self._rlbot_view(ha, mcars[i].obs).get("air_state") if ha is not None else None
        ab = self._rlbot_view(hb, cars[i].obs).get("air_state") if hb is not None else None
        if fb < 0.5 * fa:
            return True
        return air is not None and ab == air and aa != air and fb <= fa * 1.05

    @staticmethod
    def _vote_key(k: str, v, frame: int):
        """What a packet value says happened: for the dodge clocks, the frame the flip started or the jump ended."""
        if k == "dodge_elapsed":
            return ("flip at", int(round(frame - float(v) * 120.0))) if float(v) > 0 else "none"
        if k == "dodge_timeout":
            return ("jump ended at", int(round(frame - (R.DOUBLEJUMP_MAX_DELAY - float(v)) * 120.0))) if float(v) >= 0 else "none"
        return v

    def _adopt(self, fr: Frame, i: int, adopt: dict) -> None:
        """Take these packet values into car i's hidden state; what they rest on becomes an assumption again."""
        c = self.cars[i]
        for k in adopt:
            c.assumed |= PROVENANCE[k]
        self._adopt_into(fr, i, adopt, c.obs, self.na)
        if "air_state" in adopt:
            c.probed_frame = -1                           # the wheels decide the ground, never the enum

    def _adopt_into(self, fr: Frame, i: int, adopt: dict, o: R.RSObserver, na) -> None:
        p = fr.cars[i]
        h = list(na.car_hidden(i))
        for k, g in adopt.items():
            if k == "has_jumped":
                o.has_jumped = bool(g)
            elif k == "has_double_jumped":
                o.has_double_jumped = bool(g)
            elif k == "has_dodged":
                o.has_flipped = bool(g)
                if g and p.dodge_elapsed is not None:
                    o.flip_time = float(p.dodge_elapsed)
                o.is_flipping = o.has_flipped and o.flip_time < R.FLIP_TORQUE_TIME
                if g and p.dodge_dir is not None:
                    o.flip_rel_torque = (-float(p.dodge_dir[1]), float(p.dodge_dir[0]), 0.0)
            elif k == "dodge_elapsed":
                if o.has_flipped:
                    o.flip_time = float(g)
                    o.is_flipping = o.flip_time < R.FLIP_TORQUE_TIME
                    if h[R.H_DODGE_ON] > 0.5:
                        h[R.H_DODGE_T] = max(DT, float(g))
            elif k == "dodge_timeout":
                if g >= 0:
                    o.air_time_since_jump = max(0.0, R.DOUBLEJUMP_MAX_DELAY - float(g))
                    if h[R.H_JUMP_ON] < 0.5:
                        h[R.H_JUMP_T] = o.air_time_since_jump
                elif o.has_jumped:
                    o.air_time_since_jump = max(o.air_time_since_jump, R.DOUBLEJUMP_MAX_DELAY)
            elif k == "air_state":
                jumping, dj, dodge = g == JUMPING, g == DOUBLE_JUMPING, g == DODGING
                if jumping and h[R.H_JUMP_ON] < 0.5:
                    h[R.H_JUMP_T] = max(1, self._run(i, lambda q: q.air_state == JUMPING)) * DT
                h[R.H_JUMP_ON] = 1.0 if jumping else 0.0
                h[R.H_DJ_ON] = 1.0 if dj else 0.0
                if dodge and h[R.H_DODGE_ON] < 0.5:
                    h[R.H_DODGE_T] = max(DT, float(p.dodge_elapsed or DT))
                h[R.H_DODGE_ON] = 1.0 if dodge else 0.0
                o.is_jumping = jumping
                if jumping:
                    o.has_jumped = True
            elif k == "supersonic":
                h[R.H_SUPERSONIC] = 1.0 if g else 0.0
        h[R.H_JUMPED] = 1.0 if (o.has_jumped or o.has_double_jumped or o.has_flipped) else 0.0
        h[R.H_DOUBLE_JUMPED] = 1.0 if (o.has_double_jumped or o.has_flipped) else 0.0
        na.set_car_hidden(i, h)

    def _set_ball(self, fr: Frame) -> None:
        b = fr.ball
        if b is not None:
            self.na.set_ball([*b.pos, *b.vel, *b.ang_vel], False)

    def _set_pads(self, fr: Frame, force: bool) -> None:
        if not fr.pads:
            return
        if self.pad_mode is None and self.prev_pads is not None and len(self.prev_pads) == len(fr.pads):
            for (a0, t0), (a1, t1) in zip(self.prev_pads, fr.pads):
                if not a0 and not a1 and t0 > 0 and t1 > 0 and t1 != t0:
                    self.pad_mode = "left" if t1 < t0 else "elapsed"
                    break
        self.prev_pads = list(fr.pads)
        mode = self.pad_mode or "left"
        for k, (active, timer) in enumerate(fr.pads):
            j = self.pad_map[k] if k < len(self.pad_map) else k
            ln = 10.0 if self.pad_big[j] else 4.0
            elapsed = float(timer) if mode == "elapsed" else max(0.0, ln - float(timer))
            taken, length, el = self.na.pad(j)
            if force or taken == active or (taken and abs(el - elapsed) > 0.05):
                self.na.set_pad(j, not active, ln, elapsed if not active else 0.0)

    def _probe(self, fr: Frame) -> None:
        """After a cold start or a snap: one tick on a copy finds each wheel's contact at the packet's pose."""
        need = [i for i, c in enumerate(self.cars) if c.probed_frame < 0 and self.na.car_body(i) is not None]
        if self.cars and self.frame >= 0:
            b = self.na.ball_body()
            if b is not None and fr.ball is not None:
                bp = self.na.arena_row()
                if max(abs(bp[k] - fr.ball.pos[k]) for k in range(3)) > self.tol.snap_pos or \
                        max(abs(bp[3 + k] - fr.ball.vel[k]) for k in range(3)) > self.tol.snap_vel:
                    self._set_ball(fr)
            self._set_pads(fr, force=False)
        if not need:
            return
        probe = self.na.copy()
        probe.step([list(self.cars[i].last_input) for i in range(len(self.cars))], 1)
        for i in need:
            ph = probe.car_hidden(i)
            if ph is None:
                continue
            h = list(self.na.car_hidden(i))
            h[R.H_WHEEL:R.H_WHEEL + 4] = ph[R.H_WHEEL:R.H_WHEEL + 4]
            h[R.H_WHEEL_N:R.H_WHEEL_N + 12] = ph[R.H_WHEEL_N:R.H_WHEEL_N + 12]
            h[R.H_WORLD:R.H_WORLD + 4] = ph[R.H_WORLD:R.H_WORLD + 4]
            h[R.H_WHEEL_G:R.H_WHEEL_G + 4] = ph[R.H_WHEEL_G:R.H_WHEEL_G + 4]
            h[R.H_ON_GROUND] = 1.0 if sum(1 for w in ph[R.H_WHEEL:R.H_WHEEL + 4] if w > 0.5) >= 3 else 0.0
            self.na.set_car_hidden(i, h)
            self.cars[i].probed_frame = self.frame
            if h[R.H_ON_GROUND] > 0.5 and h[R.H_JUMP_ON] < 0.5:
                self.cars[i].obs.air_time = 0.0

    # ------------------------------------------------------------------ reading out

    def _read(self, na, cars, frame: int) -> Corrected:
        out = Corrected(frame=frame, horizon=0, input_delay=self.input_delay, cars=[], ball=None, pads=[])
        pf = na.physics_frame
        for i, c in enumerate(cars):
            cs = read_car(na, i, c.obs, pf)
            cs.confidence.update(synced=c.synced, consistent=c.consistent, ticks_since_cold=c.ticks_since_cold, drift=c.drift,
                                 assumed=sorted(c.assumed), preset=self.presets[i] if i < len(self.presets) else None)
            out.cars.append(cs)
        r = na.arena_row()
        bb = na.ball_body()
        if bb is not None:
            rot = (tuple(bb[3:12:3]), tuple(bb[4:12:3]), tuple(bb[5:12:3]))
            out.ball = BallState(pos=tuple(r[0:3]), vel=tuple(r[3:6]), angVel=tuple(r[6:9]), rotMat=rot)
        n = len(self.pad_big) if self.na is not None else 0
        for j in range(n):
            taken, length, el = na.pad(j)
            out.pads.append(PadState(isActive=not taken, cooldown=max(0.0, length - el) if taken else 0.0,
                                     timer=el if taken else 0.0, isBig=self.pad_big[j]))
        return out

    def stats_report(self) -> str:
        lines = [f"{'field':<18} {'compared':>9} {'agreed':>8} {'packet wrong':>13} {'adopted':>8} {'error rate':>11}"]
        for k, s in sorted(self.stats.items()):
            lines.append(f"{k:<18} {s.compared:>9} {s.agreed:>8} {s.packet_wrong:>13} {s.adopted:>8} {s.error_rate:>10.2%}")
        return "\n".join(lines)


def _same(a, b) -> bool:
    if isinstance(a, tuple) and isinstance(b, tuple):
        return a[0] == b[0] and abs(a[1] - b[1]) <= 2           # event frames within two ticks are the same event
    return a == b


def _count(votes: dict, key) -> int:
    return sum(n for k, n in votes.items() if _same(k, key))


def _vote(votes: dict, key) -> None:
    for k in votes:
        if _same(k, key):
            votes[k] += 1
            return
    votes[key] = 1


def _row(c) -> tuple:
    if isinstance(c, (list, tuple)):
        return tuple(float(x) for x in c)
    return (float(c.throttle), float(c.steer), float(c.pitch), float(c.yaw), float(c.roll),
            1.0 if c.jump else 0.0, 1.0 if c.boost else 0.0, 1.0 if c.handbrake else 0.0)


def _rot_diff(a: float, b: float) -> float:
    d = (a - b) % 65536.0
    return min(d, 65536.0 - d)
