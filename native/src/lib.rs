#![allow(non_snake_case)]
//! catSim input correction for RLBot bots, as a C library (`catsim_correct.h`, `catsim_correct.hpp`).
//!
//! Built on the public `catsim` crate (github.com/adventure-gpt/catsim-public, MIT). The algorithm is the Python
//! reference's (`catsim_better_inputs`), step for step.

pub mod arena;
pub mod core;
pub mod rsview;

use crate::core::{CarIn, Corrected, Corrector, Field, Frame, Phase, FIELD_NAMES};
use std::ffi::{c_char, c_void, CStr};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;

pub const CSC_MAX_CARS: usize = 8;
pub const CSC_NUM_PADS: usize = 34;
pub const CSC_UNKNOWN: i8 = -1;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscVec {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscRotMat {
    pub forward: CscVec,
    pub right: CscVec,
    pub up: CscVec,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscControls {
    pub throttle: f32,
    pub steer: f32,
    pub pitch: f32,
    pub yaw: f32,
    pub roll: f32,
    pub jump: bool,
    pub boost: bool,
    pub handbrake: bool,
}

/// One car as the packet reports it. Tri-state flags: -1 the packet format doesn't have it, 0 false, 1 true.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscCarIn {
    pub team: i32,
    pub pos: CscVec,
    pub vel: CscVec,
    pub ang_vel: CscVec,
    pub pitch: f32,
    pub yaw: f32,
    pub roll: f32,
    pub boost: f32,
    pub hitbox: CscVec,
    pub hitbox_offset: CscVec,
    pub demolished: bool,
    pub demo_left: f32,
    pub supersonic: bool,
    pub has_jumped: i8,
    pub has_double_jumped: i8,
    pub has_dodged: i8,
    pub on_ground: i8,
    pub air_state: i32,       // RLBot v5 AirState, -1 unknown
    pub has_dodge_clocks: bool,
    pub dodge_timeout: f32,
    pub dodge_elapsed: f32,
    pub dodge_dir_x: f32,     // forward
    pub dodge_dir_y: f32,     // right
    pub has_last_input: bool,
    pub last_input: CscControls,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct CscFrame {
    pub frame: i32,
    pub match_phase: i32,     // RLBot v5 MatchPhase (3 = active)
    pub num_cars: i32,
    pub cars: [CscCarIn; CSC_MAX_CARS],
    pub has_ball: bool,
    pub ball_pos: CscVec,
    pub ball_vel: CscVec,
    pub ball_ang_vel: CscVec,
    pub num_pads: i32,        // 0: the packet has no pads
    pub pad_active: [bool; CSC_NUM_PADS],
    pub pad_timer: [f32; CSC_NUM_PADS], // RLBot's timer as it comes (its direction is learned: option "pad_timer")
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscWorldContact {
    pub hasContact: bool,
    pub contactNormal: CscVec,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscCarContact {
    pub otherCarID: u32,
    pub cooldownTimer: f32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscBallHitInfo {
    pub isValid: bool,
    pub relativePosOnBall: CscVec,
    pub ballPos: CscVec,
    pub extraHitVel: CscVec,
    pub tickCountWhenHit: u64,
    pub tickCountWhenExtraImpulseApplied: u64,
}

/// RocketSim's CarState, field for field (names and meanings), then the extras.
#[repr(C)]
#[derive(Clone, Copy, Default)]
#[allow(non_snake_case)]
pub struct CscCarState {
    pub pos: CscVec,
    pub rotMat: CscRotMat,
    pub vel: CscVec,
    pub angVel: CscVec,
    pub tickCountSinceUpdate: u64,
    pub isOnGround: bool,
    pub wheelsWithContact: [bool; 4],
    pub hasJumped: bool,
    pub hasDoubleJumped: bool,
    pub hasFlipped: bool,
    pub flipRelTorque: CscVec,
    pub jumpTime: f32,
    pub flipTime: f32,
    pub isFlipping: bool,
    pub isJumping: bool,
    pub airTime: f32,
    pub airTimeSinceJump: f32,
    pub boost: f32,
    pub timeSinceBoosted: f32,
    pub isBoosting: bool,
    pub boostingTime: f32,
    pub isSupersonic: bool,
    pub supersonicTime: f32,
    pub handbrakeVal: f32,
    pub isAutoFlipping: bool,
    pub autoFlipTimer: f32,
    pub autoFlipTorqueScale: f32,
    pub worldContact: CscWorldContact,
    pub carContact: CscCarContact,
    pub isDemoed: bool,
    pub demoRespawnTimer: f32,
    pub ballHitInfo: CscBallHitInfo,
    pub lastControls: CscControls,
    // ---- extras
    pub index: i32,
    pub team: i32,
    pub timeSpentBoosting: f32,
    pub supersonicGrace: f32,
    pub jumpHoldBroken: bool,
    pub boostLockLeft: f32,
    pub pitchLockLeft: f32,
    pub canFlip: bool,
    pub hasFlipReset: bool,
    pub dodgeTimeout: f32,
    pub wheelNormals: [CscVec; 4],
    pub wheelGround: [i32; 4],               // 0 arena, 1 car, 2 ball
    pub wheelSuspensionLength: [f32; 4],     // uu, RocketSim wheel order
    pub wheelSuspensionRest: [f32; 4],
    pub wheelSuspensionTravel: [f32; 4],
    pub wheelRadius: [f32; 4],
    pub otherCarIndex: i32,
    // ---- confidence
    pub synced: bool,         // the body matched the packet to its rounding: exact tracking
    pub consistent: bool,     // the body moved as physics says: flags from physics are trusted
    pub ticksSinceColdStart: i64,
    pub drift: f32,           // uu off the packet at the last resync
    pub assumedFacts: u32,    // bits of the facts still taken from a packet (CSC_FACT_*)
    pub inputsKnown: bool,    // this car's controls over the predicted ticks were known (yours), not estimated
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
#[allow(non_snake_case)]
pub struct CscBallState {
    pub pos: CscVec,
    pub rotMat: CscRotMat,
    pub vel: CscVec,
    pub angVel: CscVec,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
#[allow(non_snake_case)]
pub struct CscPadState {
    pub isActive: bool,
    pub cooldown: f32,
    pub timer: f32,
    pub isBig: bool,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct CscCorrected {
    pub frame: i32,
    pub horizon: i32,
    pub input_delay: i32,
    pub num_cars: i32,
    pub cars: [CscCarState; CSC_MAX_CARS],
    pub has_ball: bool,
    pub ball: CscBallState,
    pub num_pads: i32,
    pub pads: [CscPadState; CSC_NUM_PADS],
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CscFieldStats {
    pub compared: u64,
    pub agreed: u64,
    pub packet_wrong: u64,
    pub adopted: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct CscFlagEvent {
    pub frame: i32,
    pub car: i32,
    pub field: *const c_char,
    pub packet: f64,
    pub physics: f64,
    pub decision: i32, // 0 packet wrong (physics kept), 1 held (assumption kept), 2 adopted (packet taken)
}

pub type CscOpponentFn = Option<extern "C" fn(user: *mut c_void, car: i32, tick: i32, controls: *mut CscControls)>;

pub struct CscCorrector {
    inner: Corrector,
    err: String,
}

static FIELD_CSTR: [&CStr; 8] = [c"air_state", c"has_jumped", c"has_double_jumped", c"has_dodged", c"dodge_timeout", c"dodge_elapsed",
                                 c"supersonic", c"on_ground"];

fn v(a: [f64; 3]) -> CscVec {
    CscVec { x: a[0] as f32, y: a[1] as f32, z: a[2] as f32 }
}

fn ctl_in(c: &CscControls) -> [f64; 8] {
    let b = |x: bool| if x { 1.0 } else { 0.0 };
    [c.throttle as f64, c.steer as f64, c.pitch as f64, c.yaw as f64, c.roll as f64, b(c.jump), b(c.boost), b(c.handbrake)]
}

fn ctl_out(r: &[f64; 8]) -> CscControls {
    CscControls { throttle: r[0] as f32, steer: r[1] as f32, pitch: r[2] as f32, yaw: r[3] as f32, roll: r[4] as f32, jump: r[5] > 0.5,
                  boost: r[6] > 0.5, handbrake: r[7] > 0.5 }
}

fn tri(x: i8) -> Option<bool> {
    if x < 0 { None } else { Some(x != 0) }
}

fn frame_in(f: &CscFrame) -> Frame {
    let n = (f.num_cars.max(0) as usize).min(CSC_MAX_CARS);
    let cars = f.cars[..n].iter().map(|c| CarIn {
        team: c.team as u8, pos: [c.pos.x, c.pos.y, c.pos.z], vel: [c.vel.x, c.vel.y, c.vel.z],
        ang_vel: [c.ang_vel.x, c.ang_vel.y, c.ang_vel.z], rot: [c.pitch, c.yaw, c.roll], boost: c.boost,
        hitbox: [c.hitbox.x, c.hitbox.y, c.hitbox.z], hitbox_offset: [c.hitbox_offset.x, c.hitbox_offset.y, c.hitbox_offset.z],
        demolished: c.demolished, demo_left: c.demo_left, supersonic: c.supersonic, has_jumped: tri(c.has_jumped),
        has_double_jumped: tri(c.has_double_jumped), has_dodged: tri(c.has_dodged),
        air_state: if c.air_state < 0 { None } else { Some(c.air_state) }, on_ground: tri(c.on_ground),
        dodge_timeout: if c.has_dodge_clocks { Some(c.dodge_timeout) } else { None },
        dodge_elapsed: if c.has_dodge_clocks { Some(c.dodge_elapsed) } else { None },
        dodge_dir: if c.has_dodge_clocks { Some([c.dodge_dir_x, c.dodge_dir_y]) } else { None },
        last_input: if c.has_last_input { Some(ctl_in(&c.last_input)) } else { None },
    }).collect();
    let np = (f.num_pads.max(0) as usize).min(CSC_NUM_PADS);
    Frame {
        frame: f.frame,
        cars,
        ball: if f.has_ball { Some(([f.ball_pos.x, f.ball_pos.y, f.ball_pos.z], [f.ball_vel.x, f.ball_vel.y, f.ball_vel.z],
                                   [f.ball_ang_vel.x, f.ball_ang_vel.y, f.ball_ang_vel.z])) } else { None },
        pads: if np > 0 { Some((0..np).map(|k| (f.pad_active[k], f.pad_timer[k])).collect()) } else { None },
        phase: Phase::from_rlbot(f.match_phase),
        pad_locations: None,
    }
}

fn write_out(c: &Corrected, out: &mut CscCorrected) {
    out.frame = c.frame;
    out.horizon = c.horizon;
    out.input_delay = c.input_delay;
    out.num_cars = c.cars.len().min(CSC_MAX_CARS) as i32;
    for (k, s) in c.cars.iter().take(CSC_MAX_CARS).enumerate() {
        let o = &mut out.cars[k];
        *o = CscCarState::default();
        o.pos = v(s.pos);
        o.rotMat = CscRotMat { forward: v(s.rot[0]), right: v(s.rot[1]), up: v(s.rot[2]) };
        o.vel = v(s.vel);
        o.angVel = v(s.ang_vel);
        o.tickCountSinceUpdate = s.ticks_since_cold.max(0) as u64;
        o.isOnGround = s.is_on_ground;
        o.wheelsWithContact = s.wheels;
        o.hasJumped = s.has_jumped;
        o.hasDoubleJumped = s.has_double_jumped;
        o.hasFlipped = s.has_flipped;
        o.flipRelTorque = v(s.flip_rel_torque);
        o.jumpTime = s.jump_time as f32;
        o.flipTime = s.flip_time as f32;
        o.isFlipping = s.is_flipping;
        o.isJumping = s.is_jumping;
        o.airTime = s.air_time as f32;
        o.airTimeSinceJump = s.air_time_since_jump as f32;
        o.boost = s.boost as f32;
        o.timeSinceBoosted = s.time_since_boosted as f32;
        o.isBoosting = s.is_boosting;
        o.boostingTime = s.boosting_time as f32;
        o.isSupersonic = s.is_supersonic;
        o.supersonicTime = s.supersonic_time as f32;
        o.handbrakeVal = s.handbrake_val as f32;
        o.isAutoFlipping = s.is_auto_flipping;
        o.autoFlipTimer = s.auto_flip_timer as f32;
        o.autoFlipTorqueScale = s.auto_flip_torque_scale as f32;
        o.worldContact = CscWorldContact { hasContact: s.world_contact, contactNormal: v(s.world_contact_normal) };
        o.carContact = CscCarContact { otherCarID: s.other_car_id, cooldownTimer: s.bump_cooldown as f32 };
        o.isDemoed = s.is_demoed;
        o.demoRespawnTimer = s.demo_respawn_timer as f32;
        o.ballHitInfo = CscBallHitInfo {
            isValid: s.ball_hit_valid,
            tickCountWhenHit: if s.tick_when_hit >= 0 { s.tick_when_hit as u64 } else { u64::MAX },
            tickCountWhenExtraImpulseApplied: if s.tick_when_extra_impulse >= 0 { s.tick_when_extra_impulse as u64 } else { u64::MAX },
            ..Default::default()
        };
        o.lastControls = ctl_out(&s.last_controls);
        o.index = s.index;
        o.team = s.team;
        o.timeSpentBoosting = s.boosting_time as f32;
        o.supersonicGrace = s.supersonic_grace as f32;
        o.jumpHoldBroken = s.jump_hold_broken;
        o.boostLockLeft = s.boost_lock_left as f32;
        o.pitchLockLeft = s.pitch_lock_left as f32;
        o.canFlip = s.can_flip;
        o.hasFlipReset = s.has_flip_reset;
        o.dodgeTimeout = s.dodge_timeout as f32;
        for w in 0..4 {
            o.wheelNormals[w] = v(s.wheel_normals[w]);
            o.wheelGround[w] = s.wheel_ground[w];
            o.wheelSuspensionLength[w] = s.suspension[w][0];
            o.wheelSuspensionRest[w] = s.suspension[w][1];
            o.wheelSuspensionTravel[w] = s.suspension[w][2];
            o.wheelRadius[w] = s.suspension[w][3];
        }
        o.otherCarIndex = s.other_car_index;
        o.synced = s.synced;
        o.consistent = s.consistent;
        o.ticksSinceColdStart = s.ticks_since_cold;
        o.drift = s.drift as f32;
        o.assumedFacts = s.assumed;
        o.inputsKnown = s.inputs_known;
    }
    out.has_ball = c.ball.is_some();
    if let Some((p, r, vel, w)) = c.ball {
        out.ball = CscBallState { pos: v(p), rotMat: CscRotMat { forward: v(r[0]), right: v(r[1]), up: v(r[2]) }, vel: v(vel), angVel: v(w) };
    }
    out.num_pads = c.pads.len().min(CSC_NUM_PADS) as i32;
    for (k, &(a, cd, t, big)) in c.pads.iter().take(CSC_NUM_PADS).enumerate() {
        out.pads[k] = CscPadState { isActive: a, cooldown: cd as f32, timer: t as f32, isBig: big };
    }
}

fn guard<T>(f: impl FnOnce() -> T, fallback: T) -> T {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(fallback)
}

/// The library's version, e.g. "0.1.2 (catsim 0.1.3)".
#[unsafe(no_mangle)]
pub extern "C" fn csc_version() -> *const c_char {
    c"0.1.2 (catsim 0.1.3)".as_ptr()
}

/// A corrector for the car at `own_index`. `data_dir`: the folder catSim's dumper wrote. `training_action_delay`: the
/// action delay (ticks) your policy was trained with. `input_delay`: the real delay D if you know it, or 0 to measure it.
/// Returns NULL on failure (the message goes to `err`, if given).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_new(data_dir: *const c_char, own_index: i32, training_action_delay: i32, input_delay: i32,
                                 err: *mut c_char, err_len: usize) -> *mut CscCorrector {
    let r = guard(|| {
        if data_dir.is_null() {
            return Err("data_dir is NULL".to_string());
        }
        let d = unsafe { CStr::from_ptr(data_dir) }.to_string_lossy().into_owned();
        Corrector::new(&PathBuf::from(d), vec![own_index.max(0) as usize], training_action_delay.max(0),
                       if input_delay > 0 { Some(input_delay) } else { None })
    }, Err("panic while starting".to_string()));
    match r {
        Ok(c) => Box::into_raw(Box::new(CscCorrector { inner: c, err: String::new() })),
        Err(e) => {
            if !err.is_null() && err_len > 0 {
                let b = e.as_bytes();
                let n = b.len().min(err_len - 1);
                unsafe {
                    std::ptr::copy_nonoverlapping(b.as_ptr(), err as *mut u8, n);
                    *err.add(n) = 0;
                }
            }
            std::ptr::null_mut()
        }
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_free(c: *mut CscCorrector) {
    if !c.is_null() {
        drop(unsafe { Box::from_raw(c) });
    }
}

/// The last error message ("" if none).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_last_error(c: *const CscCorrector) -> *const c_char {
    static EMPTY: &CStr = c"";
    if c.is_null() {
        return EMPTY.as_ptr();
    }
    let c = unsafe { &*c };
    if c.err.is_empty() { EMPTY.as_ptr() } else { c.err.as_ptr() as *const c_char }
}

fn set_err(c: &mut CscCorrector, e: String) {
    c.err = e;
    c.err.push('\0');
}

/// Take a packet. `out` (may be NULL) gets the corrected state at the packet's own frame. Returns 0, or -1 on error.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_update(c: *mut CscCorrector, frame: *const CscFrame, out: *mut CscCorrected) -> i32 {
    if c.is_null() || frame.is_null() {
        return -1;
    }
    let c = unsafe { &mut *c };
    let fr = frame_in(unsafe { &*frame });
    let r = guard(|| c.inner.update(&fr), Err("panic in update".into()));
    match r {
        Ok(Some(s)) => {
            if !out.is_null() {
                write_out(&s, unsafe { &mut *out });
            }
            0
        }
        Ok(None) => {
            if !out.is_null() {
                unsafe { &mut *out }.num_cars = 0;
            }
            1
        }
        Err(e) => {
            set_err(c, e);
            -1
        }
    }
}

/// The state `ticks` ticks after the last packet (-1: the horizon, max(0, D - 1 - training delay)). Returns 0 or -1.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_predict(c: *mut CscCorrector, ticks: i32, out: *mut CscCorrected) -> i32 {
    if c.is_null() || out.is_null() {
        return -1;
    }
    let c = unsafe { &mut *c };
    let r = guard(|| c.inner.predict(if ticks < 0 { None } else { Some(ticks) }), None);
    match r {
        Some(s) => {
            write_out(&s, unsafe { &mut *out });
            0
        }
        None => {
            // nothing to correct (no packet yet, or between a goal and the next kickoff): not an error
            unsafe { &mut *out }.num_cars = 0;
            1
        }
    }
}

/// update + predict(horizon): the state at the moment your next action takes effect. Call it on every packet before
/// your policy runs.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_correct(c: *mut CscCorrector, frame: *const CscFrame, out: *mut CscCorrected) -> i32 {
    let r = unsafe { csc_update(c, frame, std::ptr::null_mut()) };
    if r < 0 {
        return r;
    }
    unsafe { csc_predict(c, -1, out) }
}

/// Record the controls you returned for the packet of `frame` (-1: the last packet). Call it after your policy.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_sent(c: *mut CscCorrector, controls: *const CscControls, frame: i32) {
    if c.is_null() || controls.is_null() {
        return;
    }
    let c = unsafe { &mut *c };
    let k = ctl_in(unsafe { &*controls });
    guard(|| c.inner.sent(k, if frame < 0 { None } else { Some(frame) }), ());
}

/// Known controls for car `index` applied from game tick `tick` on (hiveminds, lockstep, tests).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_record_sent(c: *mut CscCorrector, controls: *const CscControls, tick: i32, index: i32) {
    if c.is_null() || controls.is_null() {
        return;
    }
    let c = unsafe { &mut *c };
    let k = ctl_in(unsafe { &*controls });
    guard(|| c.inner.record_sent(k, tick, if index < 0 { None } else { Some(index as usize) }), ());
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_input_delay(c: *const CscCorrector) -> i32 {
    if c.is_null() { -1 } else { unsafe { &*c }.inner.input_delay() }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_horizon(c: *const CscCorrector) -> i32 {
    if c.is_null() { -1 } else { unsafe { &*c }.inner.horizon() }
}

/// Options by name: "trust_packet_flags" (0/1), "log_flag_errors" (0/1), "air_order" (0 auto, 1 jump first, 2 ground
/// first), "last_input_is_applied" (0/1), "training_action_delay", "input_delay" (0 = measure), and the tolerances
/// "snap_pos", "snap_vel", "snap_ang_vel", "snap_rot", "trust_pos", "trust_vel", "trust_ang_vel", "trust_rot",
/// "teleport", "timer", and "pad_timer" (0 auto, 1 seconds left, 2 seconds since taken). Returns 0, or -1 for an
/// unknown name.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_set_option(c: *mut CscCorrector, name: *const c_char, value: f64) -> i32 {
    if c.is_null() || name.is_null() {
        return -1;
    }
    let c = &mut unsafe { &mut *c }.inner;
    let n = unsafe { CStr::from_ptr(name) }.to_string_lossy();
    let t = &mut c.tol;
    match n.as_ref() {
        "trust_packet_flags" => c.trust_packet_flags = value != 0.0,
        "log_flag_errors" => c.log_flag_errors = value != 0.0,
        "air_order" => c.air_order = value as i32,
        "last_input_is_applied" => c.last_input_is_applied = value != 0.0,
        "training_action_delay" => c.training_action_delay = value as i32,
        "input_delay" => c.fixed_delay = if value > 0.0 { Some(value as i32) } else { None },
        "snap_pos" => t.snap_pos = value,
        "snap_vel" => t.snap_vel = value,
        "snap_ang_vel" => t.snap_ang_vel = value,
        "snap_rot" => t.snap_rot = value,
        "trust_pos" => t.trust_pos = value,
        "trust_vel" => t.trust_vel = value,
        "trust_ang_vel" => t.trust_ang_vel = value,
        "trust_rot" => t.trust_rot = value,
        "teleport" => t.teleport = value,
        "timer" => t.timer = value,
        "pad_timer" => c.pad_timer = value as i32,
        _ => return -1,
    }
    0
}

/// The boost pads' positions in your packet's order (x, y pairs), e.g. from RLBot's FieldInfo. Without it the packet's
/// order is taken to be catSim's (both sort by y, then x).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_set_pad_locations(c: *mut CscCorrector, xy: *const f32, count: i32) {
    if c.is_null() || xy.is_null() || count <= 0 {
        return;
    }
    let s = unsafe { std::slice::from_raw_parts(xy, 2 * count as usize) };
    unsafe { &mut *c }.inner.pad_locations = Some(s.chunks(2).map(|p| (p[0], p[1])).collect());
}

/// Replace the opponent estimate (default: each car holds its last reported input). The callback gets the held input
/// in `controls` and may change it.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_set_opponent_model(c: *mut CscCorrector, f: CscOpponentFn, user: *mut c_void) {
    if c.is_null() {
        return;
    }
    let c = unsafe { &mut *c };
    let user = user as usize;
    c.inner.opponents = f.map(|f| -> crate::core::OpponentFn {
        Box::new(move |i: usize, tick: i32, held: [f64; 8]| {
            let mut k = ctl_out(&held);
            f(user as *mut c_void, i as i32, tick, &mut k);
            ctl_in(&k)
        })
    });
}

/// How often the packet's `field` agreed with physics ("air_state", "has_jumped", "has_double_jumped", "has_dodged",
/// "dodge_timeout", "dodge_elapsed", "supersonic", "on_ground"). Returns 0, or -1 for an unknown field.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_field_stats(c: *const CscCorrector, field: *const c_char, out: *mut CscFieldStats) -> i32 {
    if c.is_null() || field.is_null() || out.is_null() {
        return -1;
    }
    let n = unsafe { CStr::from_ptr(field) }.to_string_lossy();
    let Some(k) = FIELD_NAMES.iter().position(|x| *x == n) else { return -1 };
    let s = unsafe { &*c }.inner.stats[k];
    unsafe { *out = CscFieldStats { compared: s.compared, agreed: s.agreed, packet_wrong: s.packet_wrong, adopted: s.adopted } };
    0
}

/// The flag log (with log_flag_errors on): copies up to `max` events from `start` into `out`; returns the total count.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn csc_flag_log(c: *const CscCorrector, start: i32, out: *mut CscFlagEvent, max: i32) -> i32 {
    if c.is_null() {
        return 0;
    }
    let log = &unsafe { &*c }.inner.flag_log;
    if !out.is_null() && max > 0 && start >= 0 {
        for (k, e) in log.iter().skip(start as usize).take(max as usize).enumerate() {
            let f: Field = e.field;
            unsafe {
                *out.add(k) = CscFlagEvent { frame: e.frame, car: e.car, field: FIELD_CSTR[f as usize].as_ptr(), packet: e.packet,
                                             physics: e.physics, decision: e.decision as i32 };
            }
        }
    }
    log.len() as i32
}
