"""ctypes view of the C library (native/include/catsim_correct.h), for the parity test and for Python users who want the
C library's speed or its per-wheel suspension."""
from __future__ import annotations

import ctypes as C
import os

MAX_CARS, NUM_PADS = 8, 34


class Vec(C.Structure):
    _fields_ = [("x", C.c_float), ("y", C.c_float), ("z", C.c_float)]


class RotMat(C.Structure):
    _fields_ = [("forward", Vec), ("right", Vec), ("up", Vec)]


class Controls(C.Structure):
    _fields_ = [("throttle", C.c_float), ("steer", C.c_float), ("pitch", C.c_float), ("yaw", C.c_float), ("roll", C.c_float),
                ("jump", C.c_bool), ("boost", C.c_bool), ("handbrake", C.c_bool)]


class CarIn(C.Structure):
    _fields_ = [("team", C.c_int32), ("pos", Vec), ("vel", Vec), ("ang_vel", Vec), ("pitch", C.c_float), ("yaw", C.c_float),
                ("roll", C.c_float), ("boost", C.c_float), ("hitbox", Vec), ("hitbox_offset", Vec), ("demolished", C.c_bool),
                ("demo_left", C.c_float), ("supersonic", C.c_bool), ("has_jumped", C.c_int8), ("has_double_jumped", C.c_int8),
                ("has_dodged", C.c_int8), ("on_ground", C.c_int8), ("air_state", C.c_int32), ("has_dodge_clocks", C.c_bool),
                ("dodge_timeout", C.c_float), ("dodge_elapsed", C.c_float), ("dodge_dir_x", C.c_float), ("dodge_dir_y", C.c_float),
                ("has_last_input", C.c_bool), ("last_input", Controls)]


class FrameC(C.Structure):
    _fields_ = [("frame", C.c_int32), ("match_phase", C.c_int32), ("num_cars", C.c_int32), ("cars", CarIn * MAX_CARS),
                ("has_ball", C.c_bool), ("ball_pos", Vec), ("ball_vel", Vec), ("ball_ang_vel", Vec), ("num_pads", C.c_int32),
                ("pad_active", C.c_bool * NUM_PADS), ("pad_timer", C.c_float * NUM_PADS)]


class WorldContact(C.Structure):
    _fields_ = [("hasContact", C.c_bool), ("contactNormal", Vec)]


class CarContact(C.Structure):
    _fields_ = [("otherCarID", C.c_uint32), ("cooldownTimer", C.c_float)]


class BallHitInfo(C.Structure):
    _fields_ = [("isValid", C.c_bool), ("relativePosOnBall", Vec), ("ballPos", Vec), ("extraHitVel", Vec),
                ("tickCountWhenHit", C.c_uint64), ("tickCountWhenExtraImpulseApplied", C.c_uint64)]


class CarState(C.Structure):
    _fields_ = [("pos", Vec), ("rotMat", RotMat), ("vel", Vec), ("angVel", Vec), ("tickCountSinceUpdate", C.c_uint64),
                ("isOnGround", C.c_bool), ("wheelsWithContact", C.c_bool * 4), ("hasJumped", C.c_bool),
                ("hasDoubleJumped", C.c_bool), ("hasFlipped", C.c_bool), ("flipRelTorque", Vec), ("jumpTime", C.c_float),
                ("flipTime", C.c_float), ("isFlipping", C.c_bool), ("isJumping", C.c_bool), ("airTime", C.c_float),
                ("airTimeSinceJump", C.c_float), ("boost", C.c_float), ("timeSinceBoosted", C.c_float), ("isBoosting", C.c_bool),
                ("boostingTime", C.c_float), ("isSupersonic", C.c_bool), ("supersonicTime", C.c_float), ("handbrakeVal", C.c_float),
                ("isAutoFlipping", C.c_bool), ("autoFlipTimer", C.c_float), ("autoFlipTorqueScale", C.c_float),
                ("worldContact", WorldContact), ("carContact", CarContact), ("isDemoed", C.c_bool), ("demoRespawnTimer", C.c_float),
                ("ballHitInfo", BallHitInfo), ("lastControls", Controls),
                ("index", C.c_int32), ("team", C.c_int32), ("timeSpentBoosting", C.c_float), ("supersonicGrace", C.c_float),
                ("jumpHoldBroken", C.c_bool), ("boostLockLeft", C.c_float), ("pitchLockLeft", C.c_float), ("canFlip", C.c_bool),
                ("hasFlipReset", C.c_bool), ("dodgeTimeout", C.c_float), ("wheelNormals", Vec * 4), ("wheelGround", C.c_int32 * 4),
                ("wheelSuspensionLength", C.c_float * 4), ("wheelSuspensionRest", C.c_float * 4),
                ("wheelSuspensionTravel", C.c_float * 4), ("wheelRadius", C.c_float * 4), ("otherCarIndex", C.c_int32),
                ("synced", C.c_bool), ("consistent", C.c_bool), ("ticksSinceColdStart", C.c_int64), ("drift", C.c_float),
                ("assumedFacts", C.c_uint32), ("inputsKnown", C.c_bool)]


class BallState(C.Structure):
    _fields_ = [("pos", Vec), ("rotMat", RotMat), ("vel", Vec), ("angVel", Vec)]


class PadState(C.Structure):
    _fields_ = [("isActive", C.c_bool), ("cooldown", C.c_float), ("timer", C.c_float), ("isBig", C.c_bool)]


class CorrectedC(C.Structure):
    _fields_ = [("frame", C.c_int32), ("horizon", C.c_int32), ("input_delay", C.c_int32), ("num_cars", C.c_int32),
                ("cars", CarState * MAX_CARS), ("has_ball", C.c_bool), ("ball", BallState), ("num_pads", C.c_int32),
                ("pads", PadState * NUM_PADS)]


class FieldStatsC(C.Structure):
    _fields_ = [("compared", C.c_uint64), ("agreed", C.c_uint64), ("packet_wrong", C.c_uint64), ("adopted", C.c_uint64)]


def load(path: str | None = None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = path or os.environ.get("CSC_LIB") or os.path.join(here, "native", "target", "release", "catsim_correct.dll")
    lib = C.CDLL(path)
    lib.csc_new.restype = C.c_void_p
    lib.csc_new.argtypes = [C.c_char_p, C.c_int32, C.c_int32, C.c_int32, C.c_char_p, C.c_size_t]
    lib.csc_free.argtypes = [C.c_void_p]
    for f in ("csc_update", "csc_correct"):
        getattr(lib, f).argtypes = [C.c_void_p, C.POINTER(FrameC), C.POINTER(CorrectedC)]
        getattr(lib, f).restype = C.c_int32
    lib.csc_predict.argtypes = [C.c_void_p, C.c_int32, C.POINTER(CorrectedC)]
    lib.csc_sent.argtypes = [C.c_void_p, C.POINTER(Controls), C.c_int32]
    lib.csc_record_sent.argtypes = [C.c_void_p, C.POINTER(Controls), C.c_int32, C.c_int32]
    lib.csc_set_option.argtypes = [C.c_void_p, C.c_char_p, C.c_double]
    lib.csc_field_stats.argtypes = [C.c_void_p, C.c_char_p, C.POINTER(FieldStatsC)]
    lib.csc_input_delay.argtypes = [C.c_void_p]
    lib.csc_horizon.argtypes = [C.c_void_p]
    lib.csc_last_error.argtypes = [C.c_void_p]
    lib.csc_last_error.restype = C.c_char_p
    lib.csc_version.restype = C.c_char_p
    return lib


def controls(c) -> Controls:
    c = [float(x) for x in c]
    return Controls(c[0], c[1], c[2], c[3], c[4], c[5] > 0.5, c[6] > 0.5, c[7] > 0.5)


def frame_to_c(fr, phase_code: int = 3) -> FrameC:
    """A catsim_better_inputs Frame as the C struct."""
    f = FrameC()
    f.frame = fr.frame
    f.match_phase = {"inactive": 0, "countdown": 1, "kickoff": 2, "active": 3, "goal": 4, "replay": 5, "paused": 6, "ended": 7}.get(fr.phase, phase_code)
    f.num_cars = len(fr.cars)
    tri = lambda x: -1 if x is None else (1 if x else 0)
    for i, p in enumerate(fr.cars):
        c = f.cars[i]
        c.team = p.team
        c.pos, c.vel, c.ang_vel = Vec(*p.pos), Vec(*p.vel), Vec(*p.ang_vel)
        c.pitch, c.yaw, c.roll = p.rot
        c.boost = p.boost
        c.hitbox, c.hitbox_offset = Vec(*p.hitbox), Vec(*p.hitbox_offset)
        c.demolished, c.demo_left, c.supersonic = p.demolished, p.demo_left, p.supersonic
        c.has_jumped, c.has_double_jumped, c.has_dodged, c.on_ground = tri(p.has_jumped), tri(p.has_double_jumped), tri(p.has_dodged), tri(p.on_ground)
        c.air_state = -1 if p.air_state is None else p.air_state
        c.has_dodge_clocks = p.dodge_timeout is not None
        if c.has_dodge_clocks:
            c.dodge_timeout, c.dodge_elapsed = p.dodge_timeout, p.dodge_elapsed
            c.dodge_dir_x, c.dodge_dir_y = p.dodge_dir
        c.has_last_input = p.last_input is not None
        if c.has_last_input:
            c.last_input = controls(p.last_input)
    if fr.ball is not None:
        f.has_ball = True
        f.ball_pos, f.ball_vel, f.ball_ang_vel = Vec(*fr.ball.pos), Vec(*fr.ball.vel), Vec(*fr.ball.ang_vel)
    if fr.pads:
        f.num_pads = len(fr.pads)
        for k, (a, t) in enumerate(fr.pads):
            f.pad_active[k], f.pad_timer[k] = a, t
    return f
