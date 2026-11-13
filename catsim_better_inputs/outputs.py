"""Hand the corrected state to the observation code you already have.

    to_dict(state)                 plain dict (JSON-able), RocketSim's field names
    to_numpy(state)                {"cars": float32 [n, len(CAR_COLUMNS)], "ball": [12], "pads": [34, 3], ...}
    to_rocketsim_v3(car)           one car as RocketSim v3 / RocketGPU-suite field names (snake_case, jump_ticks, ...)
    to_rlgym_v2(state, ...)        rlgym.rocket_league.api.GameState (RLGym 2.x, rlgym-ppo); rlgym_compat's or a
                                   look-alike when rlgym isn't installed
    to_rlgym_v1(state, ...)        RLGym 1.x / rlgym-compat v1 / rlgym-sim objects: GameState with players[PlayerData]

Nothing here imports numpy, rlgym or rlgym_compat until you call the function that needs it.
"""
from __future__ import annotations

import dataclasses
import math

from .rsview import DOUBLEJUMP_MAX_DELAY

# RLGym's hitbox_type numbers (rlgym/rocket_league/common_values.py)
HITBOX_TYPE = {"Octane": 0, "MuscleCar": 1, "Darkcar": 2, "LongCar": 3, "Hybrid": 4, "Merc": 5}

CAR_COLUMNS = (
    "pos_x", "pos_y", "pos_z", "fwd_x", "fwd_y", "fwd_z", "right_x", "right_y", "right_z", "up_x", "up_y", "up_z",
    "vel_x", "vel_y", "vel_z", "angvel_x", "angvel_y", "angvel_z", "isOnGround", "wheel_fl", "wheel_fr", "wheel_bl", "wheel_br",
    "hasJumped", "hasDoubleJumped", "hasFlipped", "flipRelTorque_x", "flipRelTorque_y", "flipRelTorque_z", "jumpTime",
    "flipTime", "isFlipping", "isJumping", "airTime", "airTimeSinceJump", "boost", "timeSinceBoosted", "isBoosting",
    "boostingTime", "isSupersonic", "supersonicTime", "handbrakeVal", "isAutoFlipping", "autoFlipTimer",
    "autoFlipTorqueScale", "worldContact", "otherCarID", "bumpCooldown", "isDemoed", "demoRespawnTimer", "team",
    "supersonicGrace", "jumpHoldBroken", "boostLockLeft", "pitchLockLeft", "canFlip", "hasFlipReset", "dodgeTimeout",
    "synced", "consistent", "inputsKnown")


def to_dict(state) -> dict:
    return dataclasses.asdict(state)


def _car_row(c) -> list:
    f = lambda b: 1.0 if b else 0.0
    conf = c.confidence
    return [*c.pos, *c.rotMat[0], *c.rotMat[1], *c.rotMat[2], *c.vel, *c.angVel, f(c.isOnGround), *map(f, c.wheelsWithContact),
            f(c.hasJumped), f(c.hasDoubleJumped), f(c.hasFlipped), *c.flipRelTorque, c.jumpTime, c.flipTime, f(c.isFlipping),
            f(c.isJumping), c.airTime, c.airTimeSinceJump, c.boost, c.timeSinceBoosted, f(c.isBoosting), c.boostingTime,
            f(c.isSupersonic), c.supersonicTime, c.handbrakeVal, f(c.isAutoFlipping), c.autoFlipTimer, c.autoFlipTorqueScale,
            f(c.worldContact.hasContact), float(c.carContact.otherCarID), c.carContact.cooldownTimer, f(c.isDemoed),
            c.demoRespawnTimer, float(c.team), c.supersonicGrace, f(c.jumpHoldBroken), c.boostLockLeft, c.pitchLockLeft,
            f(c.canFlip), f(c.hasFlipReset), c.dodgeTimeout, f(conf.get("synced")), f(conf.get("consistent")),
            f(conf.get("inputs_known", True))]


def to_numpy(state) -> dict:
    import numpy as np
    cars = np.asarray([_car_row(c) for c in state.cars], dtype=np.float32).reshape(len(state.cars), len(CAR_COLUMNS))
    b = state.ball
    ball = np.zeros(12, np.float32) if b is None else np.asarray([*b.pos, *b.vel, *b.angVel, *b.rotMat[0]], np.float32)
    pads = np.asarray([[1.0 if p.isActive else 0.0, p.cooldown, p.timer] for p in state.pads], np.float32).reshape(-1, 3)
    return {"cars": cars, "car_columns": CAR_COLUMNS, "ball": ball, "ball_columns": ("pos", "vel", "angVel", "forward"),
            "pads": pads, "pad_columns": ("isActive", "cooldown", "timer"), "frame": state.frame, "horizon": state.horizon}


def to_rocketsim_v3(c) -> dict:
    """One car in RocketSim v3's CarState field names (as catSim's public bench documents them, the RocketGPU
    full-state suite's 'public' rows). Note the differences: jump_ticks counts ticks, not seconds; supersonic_grace_timer
    is the game's grace, not RocketSim 2's supersonicTime; bump_victim is a packet index."""
    return {
        "pos": c.pos, "fwd": c.rotMat[0], "right": c.rotMat[1], "up": c.rotMat[2], "vel": c.vel, "ang_vel": c.angVel,
        "boost": c.boost, "on_ground": c.isOnGround, "has_jumped": c.hasJumped, "has_double_jumped": c.hasDoubleJumped,
        "has_flipped": c.hasFlipped, "is_supersonic": c.isSupersonic, "is_demoed": c.isDemoed,
        "demo_respawn_timer": c.demoRespawnTimer, "flip_rel_torque": c.flipRelTorque,
        "jump_ticks": int(round(c.jumpTime * 120.0)) if c.isJumping else 0, "flip_time": c.flipTime, "is_flipping": c.isFlipping,
        "is_jumping": c.isJumping, "jump_hold_broken": c.jumpHoldBroken, "air_time": c.airTime,
        "air_time_since_jump": c.airTimeSinceJump, "handbrake_val": c.handbrakeVal, "supersonic_grace_timer": c.supersonicGrace,
        "time_since_boosted": c.timeSinceBoosted, "boosting_time": c.boostingTime, "is_boosting": c.isBoosting,
        "is_auto_flipping": c.isAutoFlipping, "auto_flip_timer": c.autoFlipTimer, "auto_flip_torque_scale": c.autoFlipTorqueScale,
        "bump_cooldown_timer": c.carContact.cooldownTimer, "bump_victim": c.carContact.otherCarIndex,
        "last_extra_hit_tick": c.ballHitInfo.tickCountWhenExtraImpulseApplied, "wheels_contact": c.wheelsWithContact,
        "prev_controls": c.lastControls,
    }


# ---------------------------------------------------------------- RLGym 2.x

class _Obj:
    """A stand-in with the same attribute names when the real package isn't installed."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):
        return f"{type(self).__name__}({', '.join(f'{k}=...' for k in self.__dict__)})"


def _v2_classes():
    try:
        from rlgym.rocket_league.api import Car, GameConfig, GameState, PhysicsObject
        return Car, GameState, PhysicsObject, GameConfig
    except ImportError:
        pass
    try:
        from rlgym_compat import Car, GameConfig, GameState, PhysicsObject   # rlgym-compat 2.x (RLBot v5)
        return Car, GameState, PhysicsObject, GameConfig
    except ImportError:
        return None


def _physics(cls, pos, rot, vel, ang):
    import numpy as np
    m = np.asarray(rot, dtype=np.float32).T.copy()          # columns: forward, right, up
    if cls is None:
        return _Obj(position=np.asarray(pos, np.float32), linear_velocity=np.asarray(vel, np.float32),
                    angular_velocity=np.asarray(ang, np.float32), rotation_mtx=m, forward=m[:, 0], right=m[:, 1], up=m[:, 2])
    p = cls()
    p.position = np.asarray(pos, np.float32)
    p.linear_velocity = np.asarray(vel, np.float32)
    p.angular_velocity = np.asarray(ang, np.float32)
    p._quaternion = None
    p._rotation_mtx = m
    p._euler_angles = None
    return p


def to_rlgym_v2(state, agent_ids=None, presets=None):
    """An RLGym 2.x GameState (what rlgym-ppo observation builders take). `agent_ids`: one id per packet index
    (default: the index). Ball touches are not known here (0)."""
    import numpy as np
    classes = _v2_classes()
    Car, GameState, PhysicsObject, GameConfig = classes if classes else (None, None, None, None)
    ids = list(agent_ids) if agent_ids is not None else list(range(len(state.cars)))
    cars = {}
    for i, c in enumerate(state.cars):
        car = Car() if Car else _Obj()
        car.team_num = c.team
        preset = presets[i] if presets else c.confidence.get("preset")
        car.hitbox_type = HITBOX_TYPE.get(preset, 0)
        car.ball_touches = 0
        car.bump_victim_id = ids[c.carContact.otherCarIndex] if c.carContact.otherCarIndex >= 0 else None
        car.demo_respawn_timer = c.demoRespawnTimer if c.isDemoed else 0.0
        car.wheels_with_contact = tuple(bool(w) for w in c.wheelsWithContact)
        car.supersonic_time = c.supersonicTime
        car.boost_amount = c.boost
        car.boost_active_time = c.boostingTime
        car.handbrake = c.handbrakeVal
        car.is_jumping = c.isJumping
        car.has_jumped = c.hasJumped
        car.is_holding_jump = c.lastControls[5] > 0.5
        car.jump_time = c.jumpTime
        car.has_flipped = c.hasFlipped
        car.has_double_jumped = c.hasDoubleJumped
        car.air_time_since_jump = c.airTimeSinceJump
        car.flip_time = c.flipTime
        car.flip_torque = np.asarray(c.flipRelTorque, np.float32)
        car.is_autoflipping = c.isAutoFlipping
        car.autoflip_timer = c.autoFlipTimer
        car.autoflip_direction = c.autoFlipTorqueScale
        car.physics = _physics(PhysicsObject, c.pos, c.rotMat, c.vel, c.angVel)
        car._inverted_physics = None
        if Car is None:
            car.on_ground = sum(car.wheels_with_contact) >= 3
            car.is_demoed = c.isDemoed
            car.has_flip = not c.hasDoubleJumped and not c.hasFlipped and c.airTimeSinceJump < DOUBLEJUMP_MAX_DELAY
        cars[ids[i]] = car
    gs = GameState() if GameState else _Obj()
    gs.tick_count = state.frame
    gs.goal_scored = False
    if GameConfig:
        cfg = GameConfig()
        cfg.gravity, cfg.boost_consumption, cfg.dodge_deadzone = 1.0, 1.0, 0.5
        gs.config = cfg
    else:
        gs.config = _Obj(gravity=1.0, boost_consumption=1.0, dodge_deadzone=0.5)
    gs.cars = cars
    b = state.ball
    gs.ball = _physics(PhysicsObject, b.pos if b else (0, 0, 93.15), b.rotMat if b else ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                       b.vel if b else (0, 0, 0), b.angVel if b else (0, 0, 0))
    gs._inverted_ball = None
    gs.boost_pad_timers = np.asarray([p.cooldown for p in state.pads], np.float32)
    gs._inverted_boost_pad_timers = None
    return gs


# ---------------------------------------------------------------- RLGym 1.x

def to_rlgym_v1(state, presets=None, blue_score: int = 0, orange_score: int = 0, last_touch: int = -1):
    """An RLGym 1.x-style GameState: players (PlayerData with car_data / inverted_car_data), ball, boost_pads. Uses
    rlgym-compat's v1 classes when installed, a look-alike with the same names otherwise."""
    import numpy as np
    v2 = to_rlgym_v2(state, presets=presets)
    try:
        from rlgym_compat.v1 import PhysicsObject as P1
        from rlgym_compat.v1 import PlayerData
    except ImportError:
        P1 = PlayerData = None
    players = []
    for i, (aid, car) in enumerate(v2.cars.items()):
        c = state.cars[i]
        if PlayerData is not None and hasattr(PlayerData, "update_from_v2") and not isinstance(car, _Obj):
            p = PlayerData()
            p.update_from_v2(car, i, 0, False)
        else:
            inv = _invert(c)
            p = _Obj(car_id=i, team_num=c.team, is_demoed=c.isDemoed, on_ground=c.isOnGround, ball_touched=False,
                     has_jump=not c.hasJumped,
                     has_flip=not c.hasFlipped and not c.hasDoubleJumped and c.airTimeSinceJump < DOUBLEJUMP_MAX_DELAY,
                     boost_amount=c.boost / 100.0, car_data=_physics(None, c.pos, c.rotMat, c.vel, c.angVel),
                     inverted_car_data=_physics(None, *inv), match_goals=0, match_saves=0, match_shots=0,
                     match_demolishes=0, boost_pickups=0)
        players.append(p)
    pads = np.asarray([1.0 if pd.isActive else 0.0 for pd in state.pads], np.float32)
    b = state.ball
    ball = _physics(None, b.pos, b.rotMat, b.vel, b.angVel) if b else None
    inv_ball = _physics(None, *_invert(b)) if b else None
    return _Obj(game_type=0, blue_score=blue_score, orange_score=orange_score, last_touch=last_touch, players=players,
                ball=ball, inverted_ball=inv_ball, boost_pads=pads, inverted_boost_pads=pads[::-1].copy())


def _invert(o):
    """The same object seen from the orange side (x and y negated), as RLGym's inverted_* fields."""
    n = lambda v: (-v[0], -v[1], v[2])
    rot = tuple(n(r) for r in o.rotMat)
    return n(o.pos), rot, n(o.vel), n(o.angVel)


def angle_of(rot) -> tuple:
    """(pitch, yaw, roll) from (forward, right, up), RLBot's convention."""
    f, r, u = rot
    return (math.atan2(f[2], math.hypot(f[0], f[1])), math.atan2(f[1], f[0]), math.atan2(-r[2], u[2]))
