"""Offline test bed: a catSim 'truth' match, RLBot v5 packets made from it, and a bot whose controls land late.

No game is launched. The truth is a catSim arena; packets are real `rlbot.flat.GamePacket` objects built from it the
way real RLBot v5 games report a car (air state with jumping over ground, positions rounded to 0.01, velocities to
0.001, angular velocities to 0.00001, the integer rotation, boost as a whole number, the hitbox as the preset's own
size, the input applied on the frame, quantized to one byte as the game applies it). Flag errors can be injected.
"""
from __future__ import annotations

import math
import random

import catsim
from rlbot import flat

from catsim_better_inputs import game_controls
from catsim_better_inputs import rsview as R
from catsim_better_inputs.state import read_car

DT = 1.0 / 120.0
PRESETS = ("Octane", "MuscleCar", "Darkcar", "LongCar", "Hybrid", "Merc")


class Truth:
    """A catSim match that plays itself: every car chases the ball with jumps, double jumps, flips, boost and powerslides."""

    def __init__(self, n_per_team: int, seed: int = 1, presets=None, warmup: int = 240):
        L = catsim._lib()
        self.L = L
        self.rng = random.Random(seed)
        n = 2 * n_per_team
        self.presets = presets or [PRESETS[(seed + k) % len(PRESETS)] for k in range(n)]
        self.teams = [k % 2 for k in range(n)]
        self.na = L.NativeArena([(p, t) for p, t in zip(self.presets, self.teams)], str(catsim._data_dir), False, "Stadium_P")
        self.obs = [R.RSObserver() for _ in range(n)]
        self.n = n
        self.seed = seed
        self.frame = self.na.physics_frame
        self.held = [(0.0,) * 8 for _ in range(n)]
        for _ in range(warmup):
            self.step([self.script(i) for i in range(n)])

    def copy(self) -> "Truth":
        t = Truth.__new__(Truth)
        t.__dict__.update(self.__dict__)
        t.na = self.na.copy()
        t.obs = [o.copy() for o in self.obs]
        t.held = list(self.held)
        t.rng = random.Random()
        t.rng.setstate(self.rng.getstate())
        return t

    def step(self, rows) -> None:
        rows = [game_controls(r) for r in rows]             # the game applies every input quantized to one byte
        self.na.step([list(r) for r in rows], 1)
        self.frame = self.na.physics_frame
        for i, o in enumerate(self.obs):
            o.tick(self.na.car_hidden(i))
        self.held = [tuple(r) for r in rows]
        if self.na.arena_row()[0] != self.na.arena_row()[0]:          # a goal took the ball: put it back in play
            self.na.set_ball([self.rng.uniform(-2000, 2000), self.rng.uniform(-2500, 2500), 400.0,
                              self.rng.uniform(-800, 800), self.rng.uniform(-800, 800), 300.0, 0, 0, 0], False)

    def script(self, i: int) -> tuple:
        """A busy, deterministic driver for car i at the current frame."""
        row = self.na.car_row(i)
        if self.na.car_hidden(i) is None:
            return (0.0,) * 8
        t = self.frame + 53 * i + 17 * self.seed
        ball = self.na.arena_row()[0:3]
        pos, fwd = row[2:5], row[11:14]
        dx, dy = ball[0] - pos[0], ball[1] - pos[1]
        ang = math.atan2(dy, dx) - math.atan2(fwd[1], fwd[0])
        ang = (ang + math.pi) % (2 * math.pi) - math.pi
        steer = max(-1.0, min(1.0, 2.5 * ang))
        ph = t % 260
        cyc = (t // 260) % 4
        jump = ph < 9 or (cyc != 3 and 22 <= ph < 24)
        pitch = yaw = roll = 0.0
        if 22 <= ph < 24 and cyc == 1:
            pitch = -1.0                                    # front flip
        elif 22 <= ph < 24 and cyc == 2:
            yaw = 1.0 if i % 2 else -1.0                    # side flip
        elif 30 <= ph < 90 and cyc == 3:
            pitch, roll = 0.6, 0.3 * math.sin(t / 7.0)      # air roll / aerial-ish
        handbrake = 140 <= ph < 165
        boost = abs(ang) < 0.35 and (ph % 70) < 45
        throttle = 1.0 if ph % 200 > 8 else -0.5
        return (throttle, steer, pitch, yaw, roll, 1.0 if jump else 0.0, 1.0 if boost else 0.0, 1.0 if handbrake else 0.0)

    def rs(self, i: int):
        return read_car(self.na, i, self.obs[i], self.na.physics_frame)

    def hold(self, on: bool) -> None:
        """A kickoff countdown, the game's way: no car takes input and the ball is held (on), or the release (off)."""
        for i in range(self.n):
            if self.na.car_hidden(i) is not None:
                self.na.set_driving(i, not on)
        rh = list(self.na.round_hidden())
        cc = None if rh[3] is None else (int(rh[3][0]), float(rh[3][1]), float(rh[3][2]))
        self.na.set_round_hidden(int(rh[0]), int(rh[1]), float(rh[2]), cc, bool(rh[4]), bool(on), float(rh[6]), list(rh[7]))


def rlbot_air_state(h, order: str = "jump_first") -> int:
    on, jumping, dj, dodge = h[R.H_ON_GROUND] > 0.5, h[R.H_JUMP_ON] > 0.5, h[R.H_DJ_ON] > 0.5, h[R.H_DODGE_ON] > 0.5
    if order == "jump_first":
        return 1 if jumping else 2 if dj else 3 if dodge else 0 if on else 4
    return 0 if on else 1 if jumping else 2 if dj else 3 if dodge else 4


class Faults:
    """Flag errors to inject into packets. Each kind starts on a car with probability `rate` per packet and then lasts a
    random run of packets, the way a dropped or stuck flag would.

      wrong_ground      the air state says OnGround while in the air, or InAir while on the ground   (1-6 packets)
      drop_flipped      has_dodged false after a flip                                                 (5-60)
      drop_jumped       has_jumped false after a jump                                                 (5-60)
      stale_timers      dodge_timeout and dodge_elapsed stuck at an old value                         (5-30)
      wrong_supersonic  is_supersonic flipped                                                         (1-10)
    """
    RUNS = {"wrong_ground": (1, 6), "drop_flipped": (5, 60), "drop_jumped": (5, 60), "stale_timers": (5, 30),
            "wrong_supersonic": (1, 10)}

    def __init__(self, seed: int = 7, rate: float = 0.0, kinds=None):
        self.rng = random.Random(seed)
        self.kinds = list(kinds or self.RUNS)
        self.rate = rate
        self.left: dict = {}              # (car, kind) -> packets left
        self.frozen: dict = {}            # car -> stuck timer values
        self.injected: list = []          # (frame, car, field, truth value, packet value)

    def active(self, car: int, kind: str) -> bool:
        k = (car, kind)
        if self.left.get(k, 0) > 0:
            self.left[k] -= 1
            return True
        if self.rate > 0 and self.rng.random() < self.rate:
            lo, hi = self.RUNS[kind]
            self.left[k] = self.rng.randint(lo, hi) - 1
            return True
        return False


def make_packet(truth: Truth, faults: Faults | None = None, order: str = "jump_first", round_bodies: bool = True,
                phase=flat.MatchPhase.Active):
    """An RLBot v5 GamePacket for the truth's current frame."""
    na = truth.na
    q = (lambda x: round(x, 2)) if round_bodies else (lambda x: x)        # positions: 0.01 uu
    qv = (lambda x: round(x, 3)) if round_bodies else (lambda x: x)       # velocities: 0.001 uu/s (real packets)
    qa = (lambda x: round(x, 5)) if round_bodies else (lambda x: x)       # angular velocities: 0.00001 rad/s
    players = []
    for i in range(truth.n):
        row = na.car_row(i)
        h = na.car_hidden(i)
        size, off = truth.L.preset_hitbox(truth.presets[i])
        hb = flat.BoxShape(size[0], size[1], size[2])          # real RLBot v5 packets: the preset size itself
        if h is None:
            players.append(flat.PlayerInfo(physics=flat.Physics(location=flat.Vector3(*row[2:5])), hitbox=hb,
                                           hitbox_offset=flat.Vector3(*off), demolished_timeout=float(row[28]),
                                           dodge_timeout=-1.0, team=truth.teams[i], air_state=flat.AirState(4), is_bot=True))
            continue
        hr = na.hidden_row(i)
        o = truth.obs[i]
        rot = flat.Rotator(hr[24] * math.pi / 32768.0, hr[25] * math.pi / 32768.0, hr[26] * math.pi / 32768.0)
        air = rlbot_air_state(h, order)
        # the game's jump clock (real games): time since the jump ended, counted on the ground too; -1 while jumping
        can = (o.has_jumped and not (o.has_double_jumped or o.has_flipped) and not (h[R.H_ON_GROUND] > 0.5)
               and not (h[R.H_JUMP_ON] > 0.5) and h[R.H_JUMP_T] < 1.25)
        f = dict(air_state=air, has_jumped=o.has_jumped, has_double_jumped=o.has_double_jumped, has_dodged=o.has_flipped,
                 dodge_timeout=max(0.0, 1.25 - h[R.H_JUMP_T]) if can else -1.0,
                 dodge_elapsed=o.flip_time if o.has_flipped else 0.0,
                 is_supersonic=math.sqrt(sum(x * x for x in row[5:8])) >= R.SUPERSONIC_START_SPEED)   # RLBot's: speed only
        if faults is not None:
            true = dict(f)
            if "wrong_ground" in faults.kinds and faults.active(i, "wrong_ground") and air in (0, 4):
                f["air_state"] = 4 if air == 0 else 0
            if "drop_flipped" in faults.kinds and faults.active(i, "drop_flipped") and f["has_dodged"]:
                f["has_dodged"] = False
            if "drop_jumped" in faults.kinds and faults.active(i, "drop_jumped") and f["has_jumped"]:
                f["has_jumped"] = False
            if "stale_timers" in faults.kinds and faults.active(i, "stale_timers"):
                f["dodge_timeout"], f["dodge_elapsed"] = faults.frozen.setdefault(i, (f["dodge_timeout"], f["dodge_elapsed"]))
            else:
                faults.frozen.pop(i, None)
            if "wrong_supersonic" in faults.kinds and faults.active(i, "wrong_supersonic"):
                f["is_supersonic"] = not f["is_supersonic"]
            for k in f:
                if k in ("dodge_timeout", "dodge_elapsed"):
                    if abs(f[k] - true[k]) > 2.5 * DT:
                        faults.injected.append((truth.frame, i, k, true[k], f[k]))
                elif f[k] != true[k]:
                    faults.injected.append((truth.frame, i, k, true[k], f[k]))
        li = h[R.H_HELD:R.H_HELD + 8]
        dd = h[R.H_DODGE_DIR], h[R.H_DODGE_DIR + 1]
        players.append(flat.PlayerInfo(
            physics=flat.Physics(location=flat.Vector3(*(q(x) for x in row[2:5])), rotation=rot,
                                 velocity=flat.Vector3(*(qv(x) for x in row[5:8])), angular_velocity=flat.Vector3(*(qa(x) for x in row[8:11]))),
            hitbox=hb, hitbox_offset=flat.Vector3(*off), air_state=flat.AirState(f["air_state"]),
            dodge_timeout=f["dodge_timeout"], demolished_timeout=-1.0, is_supersonic=f["is_supersonic"], is_bot=True,
            team=truth.teams[i], boost=float(int(h[R.H_BOOST_AMOUNT] * 100.0)), player_id=i,
            last_input=flat.ControllerState(li[0], li[1], li[2], li[3], li[4], li[5] > 0.5, li[6] > 0.5, li[7] > 0.5),
            has_jumped=f["has_jumped"], has_double_jumped=f["has_double_jumped"], has_dodged=f["has_dodged"],
            dodge_elapsed=f["dodge_elapsed"], dodge_dir=flat.Vector2(dd[0], dd[1])))
    a = na.arena_row()
    balls = [flat.BallInfo(physics=flat.Physics(location=flat.Vector3(*(q(x) for x in a[0:3])),
                                                velocity=flat.Vector3(*(qv(x) for x in a[3:6])),
                                                angular_velocity=flat.Vector3(*(qa(x) for x in a[6:9]))),
                                   shape=flat.SphereShape(91.25))]
    pads = []
    for j in range(34):
        taken, length, el = na.pad(j)
        pads.append(flat.BoostPadState(not taken, el if taken else 0.0))
    mi = flat.MatchInfo(seconds_elapsed=truth.frame * DT, game_time_remaining=300.0, match_phase=phase,
                        world_gravity_z=-650.0, game_speed=1.0, frame_num=truth.frame)
    # through bytes and back, as a packet arrives from RLBot's socket (every float becomes the f32 the wire holds)
    return flat.GamePacket.unpack(flat.GamePacket(players=players, boost_pads=pads, balls=balls, match_info=mi).pack())


class DelayedBot:
    """The bot's own car: controls chosen on packet frame S land on frame S + D."""

    def __init__(self, delay: int):
        self.delay = delay
        self.queue: dict = {}
        self.current = (0.0,) * 8

    def send(self, frame: int, controls) -> None:
        self.queue[frame + self.delay] = tuple(controls)

    def applied(self, tick: int) -> tuple:
        if tick in self.queue:
            self.current = self.queue.pop(tick)
        return self.current
