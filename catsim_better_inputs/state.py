"""The corrected state. Field names and meanings follow RocketSim's C++ CarState / BallState (MIT),
so observation code written for RocketSim, RocketGPU or GigaLearn reads it unchanged. Vectors are (x, y, z) tuples;
`rotMat` is (forward, right, up), RocketSim's three columns.

Extras that RocketSim doesn't have are marked "extra" below; `confidence` says how far each car's numbers can be
trusted (README, "Which fields are exact").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import rsview as R

GROUNDS = ("arena", "car", "ball")


@dataclass
class WorldContact:
    hasContact: bool = False
    contactNormal: tuple = (0.0, 0.0, 0.0)


@dataclass
class CarContact:
    otherCarID: int = 0              # RocketSim car id (index + 1); 0 = none
    cooldownTimer: float = 0.0       # the bump cooldown left, s
    otherCarIndex: int = -1          # extra: packet index of that car


@dataclass
class BallHitInfo:
    isValid: bool = False
    relativePosOnBall: tuple = (0.0, 0.0, 0.0)      # not known from catSim's public state: zero
    ballPos: tuple = (0.0, 0.0, 0.0)                # not known from catSim's public state: zero
    extraHitVel: tuple = (0.0, 0.0, 0.0)            # not known from catSim's public state: zero
    tickCountWhenHit: int = -1                      # physics frame of the last touch
    tickCountWhenExtraImpulseApplied: int = -1      # physics frame of the last hit with the extra impulse


@dataclass
class CarState:
    # motion
    pos: tuple = (0.0, 0.0, 17.0)
    rotMat: tuple = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    vel: tuple = (0.0, 0.0, 0.0)
    angVel: tuple = (0.0, 0.0, 0.0)
    # contact
    isOnGround: bool = True
    wheelsWithContact: tuple = (False, False, False, False)     # RocketSim order: FL, FR, BL, BR
    # jumps and flips
    hasJumped: bool = False
    hasDoubleJumped: bool = False
    hasFlipped: bool = False
    flipRelTorque: tuple = (0.0, 0.0, 0.0)
    jumpTime: float = 0.0
    flipTime: float = 0.0
    isFlipping: bool = False
    isJumping: bool = False
    airTime: float = 0.0
    airTimeSinceJump: float = 0.0
    # boost and speed
    boost: float = 33.3
    timeSinceBoosted: float = 0.0
    isBoosting: bool = False
    boostingTime: float = 0.0
    isSupersonic: bool = False
    supersonicTime: float = 0.0
    # handbrake and auto flip
    handbrakeVal: float = 0.0
    isAutoFlipping: bool = False
    autoFlipTimer: float = 0.0
    autoFlipTorqueScale: float = 0.0
    # contacts
    worldContact: WorldContact = field(default_factory=WorldContact)
    carContact: CarContact = field(default_factory=CarContact)
    # demo
    isDemoed: bool = False
    demoRespawnTimer: float = 0.0
    # other
    ballHitInfo: BallHitInfo = field(default_factory=BallHitInfo)
    lastControls: tuple = (0.0,) * 8                             # throttle, steer, pitch, yaw, roll, jump, boost, handbrake
    # extras
    index: int = 0
    team: int = 0
    timeSpentBoosting: float = 0.0          # older RocketSim versions' name for boostingTime
    supersonicGrace: float = 0.0            # the game's (and RocketSim v3's) seconds below supersonic speed still counted
    jumpHoldBroken: bool = False            # the jump button was let go during this jump (RocketSim v3 jump_hold_broken)
    boostLockLeft: float = 0.0              # seconds the minimum boost time still holds the boost on
    pitchLockLeft: float = 0.0              # seconds the flip's pitch lock still holds (torque time + extra lock)
    canFlip: bool = True                    # RocketSim HasFlipOrJump()
    hasFlipReset: bool = False              # RocketSim HasFlipReset()
    dodgeTimeout: float = -1.0              # RLBot's meaning (the game's jump clock): seconds left to dodge or double
                                            # jump, counted from the jump's end on the ground too; -1 none or jumping
    wheelNormals: tuple = ((0.0, 0.0, 1.0),) * 4
    wheelGround: tuple = ("arena",) * 4      # what each wheel touches: arena, car or ball
    wheelSuspension: tuple | None = None     # per wheel suspension length (C/C++ library only; None in Python)
    catsimHidden: tuple | None = None        # catSim's own hidden row (CarHidden.to_row order), for exact work
    confidence: dict = field(default_factory=dict)

    def HasFlipOrJump(self) -> bool:
        return self.canFlip

    def HasFlipReset(self) -> bool:
        return self.hasFlipReset


@dataclass
class BallState:
    pos: tuple = (0.0, 0.0, 93.15)
    rotMat: tuple = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    vel: tuple = (0.0, 0.0, 0.0)
    angVel: tuple = (0.0, 0.0, 0.0)


@dataclass
class PadState:
    isActive: bool = True
    cooldown: float = 0.0          # seconds until it comes back (RocketSim BoostPadState.cooldown)
    timer: float = 0.0             # seconds since it was taken (RLBot's meaning)
    isBig: bool = False


@dataclass
class Corrected:
    frame: int                     # the physics frame these states are for
    horizon: int                   # ticks predicted past the last packet
    input_delay: int               # D in use
    cars: list
    ball: BallState | None
    pads: list
    plain: bool = False            # True: built straight from the packet, no catSim (Corrector.mode isn't "corrected")

    def car(self, i: int) -> CarState:
        return self.cars[i]


def read_car(na, i: int, obs: R.RSObserver, physics_frame: int) -> CarState:
    """Car i of a catSim native arena, in RocketSim's CarState meanings."""
    row = na.car_row(i)
    h = na.car_hidden(i)
    s = CarState(index=i, team=int(row[1]))
    if h is None:
        s.isDemoed, s.demoRespawnTimer = True, float(row[28])
        s.pos = tuple(row[2:5])
        s.isOnGround = False
        return s
    hr = na.hidden_row(i)
    s.pos, s.vel, s.angVel = tuple(row[2:5]), tuple(row[5:8]), tuple(row[8:11])
    s.rotMat = (tuple(hr[0:3]), tuple(hr[3:6]), tuple(hr[6:9]))
    on = h[R.H_ON_GROUND] > 0.5
    s.isOnGround = on
    s.wheelsWithContact = tuple(h[R.H_WHEEL + R.WHEEL_RS[k]] > 0.5 for k in range(4))
    s.wheelNormals = tuple(tuple(h[R.H_WHEEL_N + 3 * R.WHEEL_RS[k]:R.H_WHEEL_N + 3 * R.WHEEL_RS[k] + 3]) for k in range(4))
    s.wheelGround = tuple(GROUNDS[int(h[R.H_WHEEL_G + R.WHEEL_RS[k]])] for k in range(4))
    s.hasJumped, s.hasDoubleJumped, s.hasFlipped = obs.has_jumped, obs.has_double_jumped, obs.has_flipped
    s.flipRelTorque = obs.flip_rel_torque if obs.has_flipped else (0.0, 0.0, 0.0)
    s.jumpTime, s.flipTime = obs.jump_time, obs.flip_time
    s.isFlipping, s.isJumping = obs.is_flipping, h[R.H_JUMP_ON] > 0.5
    s.airTime, s.airTimeSinceJump = obs.air_time, obs.air_time_since_jump
    s.boost = h[R.H_BOOST_AMOUNT] * 100.0
    boosting = h[R.H_BOOST_ON] > 0.5
    s.isBoosting = boosting
    s.boostingTime = h[R.H_BOOST_T] if boosting else 0.0
    s.timeSinceBoosted = 0.0 if boosting else h[R.H_BOOST_T]
    s.timeSpentBoosting = s.boostingTime
    s.isSupersonic, s.supersonicTime, s.supersonicGrace = h[R.H_SUPERSONIC] > 0.5, obs.supersonic_time, h[R.H_SS_TIME]
    s.handbrakeVal = h[R.H_HANDBRAKE]
    af = h[R.H_AF_ON] > 0.5
    s.isAutoFlipping = af
    s.autoFlipTimer = max(0.0, h[R.H_AF_DUR] - h[R.H_AF_T]) if af else 0.0
    s.autoFlipTorqueScale = (1.0 if h[R.H_AF_RIGHT] > 0.5 else -1.0) if af else 0.0
    s.worldContact = WorldContact(h[R.H_WORLD] > 0.5, tuple(h[R.H_WORLD_N:R.H_WORLD_N + 3]))
    v = h[R.H_BUMP_VICTIM]
    if v >= 0:
        left = max(0.0, R.BUMP_COOLDOWN_TIME - (physics_frame * R.DT - h[R.H_BUMP_T]))
        s.carContact = CarContact(int(v) + 1 if left > 0 else 0, left, int(v) if left > 0 else -1)
    s.ballHitInfo = BallHitInfo(isValid=h[R.H_TOUCH_F] > 0, tickCountWhenHit=int(h[R.H_TOUCH_F]),
                                tickCountWhenExtraImpulseApplied=int(h[R.H_IMPACT_F]))
    s.lastControls = tuple(h[R.H_HELD:R.H_HELD + 8])
    s.jumpHoldBroken = h[R.H_JUMP_RELEASED] > 0.5
    s.boostLockLeft = max(0.0, R.BOOST_MIN_TIME - s.boostingTime) if boosting else 0.0
    s.pitchLockLeft = max(0.0, R.FLIP_TORQUE_TIME + R.FLIP_PITCHLOCK_EXTRA_TIME - s.flipTime) if s.hasFlipped else 0.0
    s.canFlip = on or (not s.hasFlipped and not s.hasDoubleJumped and s.airTimeSinceJump < R.DOUBLEJUMP_MAX_DELAY)
    s.hasFlipReset = (not on) and s.canFlip and not s.hasJumped
    can = (s.hasJumped and not (s.hasDoubleJumped or s.hasFlipped) and not on and not s.isJumping
           and h[R.H_JUMP_T] < R.DOUBLEJUMP_MAX_DELAY)
    s.dodgeTimeout = max(0.0, R.DOUBLEJUMP_MAX_DELAY - h[R.H_JUMP_T]) if can else -1.0   # the game's jump clock
    s.catsimHidden = tuple(h)
    return s


def rot_mat(pitch: float, yaw: float, roll: float) -> tuple:
    """(forward, right, up) from the game's rotator in radians: RocketSim's rotMat columns."""
    cp, sp, cy, sy, cr, sr = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw), math.cos(roll), math.sin(roll)
    return ((cp * cy, cp * sy, sp),
            (cy * sp * sr - cr * sy, sy * sp * sr + cr * cy, -cp * sr),
            (-cr * cy * sp - sr * sy, -cr * sy * sp + sr * cy, cp * cr))


# what plain mode marks in every car's confidence: nothing is checked or tracked
PLAIN_CONFIDENCE = {"plain": True, "synced": False, "consistent": False, "ticks_since_cold": 0, "drift": 0.0,
                    "preset": None, "inputs_known": False}


def plain_car(p, i: int) -> CarState:
    """Car i straight from its packet (`packets.CarIn`), with no catSim: plain mode (README, "Plain inputs").
    What the packet carries is copied as RLBot reports it (flags unchecked). What it can't say gets a stand-in:
    wheels all touching when the air state says on the ground, the jump/flip clocks from RLBot's dodge clocks where
    it has them, boosting and the handbrake from the buttons held, the boost, air and supersonic timers 0, the contacts
    empty."""
    s = CarState(index=i, team=int(p.team))
    s.pos, s.vel, s.angVel = tuple(p.pos), tuple(p.vel), tuple(p.ang_vel)
    s.rotMat = rot_mat(*p.rot)
    li = tuple(float(x) for x in p.last_input) if p.last_input is not None else (0.0,) * 8
    s.lastControls = li
    s.boost = float(p.boost)
    s.confidence = dict(PLAIN_CONFIDENCE)
    if p.demolished:
        s.isDemoed, s.demoRespawnTimer = True, float(p.demo_left)
        s.isOnGround, s.wheelsWithContact = False, (False,) * 4
        return s
    air = p.air_state
    if air is not None:
        on = int(air) == 0                                   # RLBot v5 AirState.OnGround
    else:
        on = bool(p.on_ground)                               # RLBot v4 has_wheel_contact (False if unknown)
    s.isOnGround = on
    s.wheelsWithContact = (on,) * 4
    if on:
        s.wheelNormals = (s.rotMat[2],) * 4                   # the surface under the car: its own up axis
    jumped, dj, flipped = bool(p.has_jumped), bool(p.has_double_jumped), bool(p.has_dodged)
    s.hasJumped, s.hasDoubleJumped, s.hasFlipped = jumped, dj, flipped
    s.isJumping = air is not None and int(air) == 1          # AirState.Jumping
    if flipped:
        s.flipTime = float(p.dodge_elapsed or 0.0)
        s.isFlipping = s.flipTime < R.FLIP_TORQUE_TIME
        s.pitchLockLeft = max(0.0, R.FLIP_TORQUE_TIME + R.FLIP_PITCHLOCK_EXTRA_TIME - s.flipTime)
        if p.dodge_dir is not None:
            s.flipRelTorque = (-float(p.dodge_dir[1]), float(p.dodge_dir[0]), 0.0)
    dt = p.dodge_timeout                                     # RLBot's: the game's jump clock, -1 none / jumping
    s.dodgeTimeout = float(dt) if dt is not None else -1.0
    if jumped and not s.isJumping and not on and dt is not None:   # (RocketSim zeroes both on the ground)
        s.airTimeSinceJump = max(0.0, R.DOUBLEJUMP_MAX_DELAY - float(dt)) if dt >= 0 else R.DOUBLEJUMP_MAX_DELAY
        s.jumpTime = s.airTimeSinceJump + 0.1                    # (as the corrector's own cold start guesses it)
    s.isBoosting = li[6] > 0.5 and s.boost > 0.0
    s.isSupersonic = bool(p.supersonic)
    s.handbrakeVal = 1.0 if li[7] > 0.5 else 0.0
    s.jumpHoldBroken = li[5] < 0.5
    s.canFlip = on or (not flipped and not dj and s.airTimeSinceJump < R.DOUBLEJUMP_MAX_DELAY)
    s.hasFlipReset = (not on) and s.canFlip and not jumped
    return s
