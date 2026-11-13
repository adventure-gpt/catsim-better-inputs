"""RocketSim's CarState meanings, computed from catSim's game components.

catSim holds a car the way the game does: components (jump, double jump, dodge, boost, auto flip) that are on or off,
each with the seconds since it last changed. RocketSim (and RocketGPU, which follows it) keeps its own bookkeeping
instead: hasJumped, jumpTime, flipTime, airTimeSinceJump and so on. `RSObserver` runs RocketSim's update rules
(Car.cpp: _UpdateJump, _UpdateAirTorque, _UpdateDoubleJumpOrFlip, _PostTickUpdate) once per tick on top of catSim's
components, so a policy trained on RocketSim or RocketGPU reads the numbers it was trained on.

Hidden-row indices are catSim's public `CarHidden.to_row()` order (catsim/__init__.py).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

DT = 1.0 / 120.0
# RocketSim's RLConst (MIT)
JUMP_MIN_TIME = 0.025
JUMP_MAX_TIME = 0.2
JUMP_RESET_TIME_PAD = 1.0 / 40.0
DOUBLEJUMP_MAX_DELAY = 1.25
FLIP_TORQUE_TIME = 0.65
FLIP_PITCHLOCK_EXTRA_TIME = 0.3
BOOST_MIN_TIME = 0.1
BUMP_COOLDOWN_TIME = 0.25
SUPERSONIC_START_SPEED = 2200.0
POWERSLIDE_RISE_RATE = 5.0
POWERSLIDE_FALL_RATE = 2.0

# catSim CarHidden row
H_DRIVING, H_ON_GROUND, H_JUMPED, H_DOUBLE_JUMPED, H_SUPERSONIC, H_SS_TIME = 0, 1, 2, 3, 4, 5
H_JUMP_ON, H_JUMP_T, H_JUMP_RELEASED, H_DJ_ON, H_DJ_T, H_DODGE_ON, H_DODGE_T = 6, 7, 8, 9, 10, 11, 12
H_DODGE_TORQUE, H_DODGE_DIR = 13, 16
H_AF_ON, H_AF_T, H_AF_RIGHT, H_AF_DUR = 19, 20, 21, 22
H_BOOST_ON, H_BOOST_T, H_BOOST_AMOUNT, H_AC_ON, H_AC_T, H_HANDBRAKE, H_HELD = 23, 24, 25, 26, 27, 28, 29
H_WHEEL, H_WHEEL_N, H_WORLD, H_WORLD_N, H_BUMP_VICTIM, H_BUMP_T, H_IMPACT_F, H_TOUCH_F, H_WHEEL_G = 37, 41, 53, 54, 57, 58, 59, 60, 61
WHEEL_RS = (1, 0, 3, 2)          # RocketSim wheel k (FL, FR, BL, BR) is catSim wheel WHEEL_RS[k] (left and right swapped)


_DT32 = struct.unpack("f", struct.pack("f", DT))[0]


def tick_add(x: float) -> float:
    """One tick added to a timer the way RocketSim and the game keep it: in f32 (six ticks of 1/120 then reach 0.05,
    which decides when a jump's `hasJumped` resets; in f64 they fall just short of it)."""
    return struct.unpack("f", struct.pack("f", struct.unpack("f", struct.pack("f", x))[0] + _DT32))[0]


@dataclass
class RSObserver:
    """RocketSim's per-car bookkeeping. One per car; `tick(h)` after every physics tick with the car's hidden row."""
    is_jumping: bool = False
    has_jumped: bool = False
    jump_time: float = 0.0
    has_double_jumped: bool = False
    has_flipped: bool = False
    is_flipping: bool = False
    flip_time: float = 0.0
    flip_rel_torque: tuple = (0.0, 0.0, 0.0)
    air_time: float = 0.0
    air_time_since_jump: float = 0.0
    supersonic_time: float = 0.0
    prev_dodge: bool = False
    prev_dj: bool = False
    events: set = field(default_factory=set)      # what happened on the last tick: landed, jump, double_jump, flip

    def copy(self) -> "RSObserver":
        o = RSObserver(**{k: v for k, v in self.__dict__.items() if k != "events"})
        o.events = set()
        return o

    def tick(self, h) -> None:
        ev = set()
        if h is None:                                 # demolished: RocketSim leaves the flags alone
            self.events = ev
            return
        on = h[H_ON_GROUND] > 0.5
        jumping = h[H_JUMP_ON] > 0.5
        dodge, dj = h[H_DODGE_ON] > 0.5, h[H_DJ_ON] > 0.5
        # _PreTickUpdate / _UpdateAirTorque: isFlipping ends on the ground or after the torque time
        if on:
            self.is_flipping = False
        elif self.is_flipping:
            self.is_flipping = self.has_flipped and self.flip_time < FLIP_TORQUE_TIME
        # _UpdateJump
        if on and not self.is_jumping:
            if not (self.has_jumped and self.jump_time < JUMP_MIN_TIME + JUMP_RESET_TIME_PAD):
                if self.has_jumped:
                    ev.add("landed")
                self.has_jumped = False
                self.jump_time = 0.0
        if jumping and not self.is_jumping:
            self.jump_time = 0.0
            ev.add("jump")
        self.is_jumping = jumping
        if jumping:
            self.has_jumped = True
        if jumping or self.has_jumped:
            self.jump_time = tick_add(self.jump_time)
        # _UpdateDoubleJumpOrFlip
        if on:
            if self.has_flipped or self.has_double_jumped or self.air_time > 0:
                ev.add("landed")
            self.has_double_jumped = self.has_flipped = False
            self.air_time = self.air_time_since_jump = self.flip_time = 0.0
        else:
            self.air_time = tick_add(self.air_time)
            self.air_time_since_jump = tick_add(self.air_time_since_jump) if (self.has_jumped and not self.is_jumping) else 0.0
            if dodge and (not self.prev_dodge or h[H_DODGE_T] <= DT * 1.5):
                self.flip_time = 0.0
                self.has_flipped = self.is_flipping = True
                d = h[H_DODGE_DIR], h[H_DODGE_DIR + 1]
                self.flip_rel_torque = (-float(d[1]), float(d[0]), 0.0)
                ev.add("flip")
            elif dj and (not self.prev_dj or h[H_DJ_T] <= DT * 1.5):
                self.has_double_jumped = True
                ev.add("double_jump")
        if self.is_flipping or self.has_flipped:
            self.flip_time = tick_add(self.flip_time)
        # _PostTickUpdate
        self.supersonic_time = tick_add(self.supersonic_time) if h[H_SUPERSONIC] > 0.5 else 0.0
        self.prev_dodge, self.prev_dj = dodge, dj
        self.events = ev
