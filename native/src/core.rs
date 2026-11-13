//! The corrector (the Python reference is catsim_better_inputs/core.py; this follows it step for step, in the same
//! f64 arithmetic, so both give the same numbers on the same packets).

use crate::arena::{self, Native};
use crate::rsview::*;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::path::{Path, PathBuf};

pub const ON_GROUND: i32 = 0;
pub const JUMPING: i32 = 1;
pub const DOUBLE_JUMPING: i32 = 2;
pub const DODGING: i32 = 3;
pub const IN_AIR: i32 = 4;
const ROT: f64 = 32768.0 / std::f64::consts::PI;
const STANDARD_PRESETS: [&str; 6] = ["Octane", "MuscleCar", "Darkcar", "LongCar", "Hybrid", "Merc"];

// facts (bits)
pub const F_HAS_JUMPED: u32 = 1;
pub const F_HAS_DOUBLE_JUMPED: u32 = 2;
pub const F_HAS_DODGED: u32 = 4;
pub const F_JUMP_CLOCK: u32 = 8;
pub const F_DJ_CLOCK: u32 = 16;
pub const F_DODGE_CLOCK: u32 = 32;
pub const F_SUPERSONIC: u32 = 64;
pub const F_BOOST_CLOCK: u32 = 128;
pub const F_HANDBRAKE: u32 = 256;
pub const AIR_FACTS: u32 = 63;
pub const ALL_FACTS: u32 = 511;

/// The packet fields compared with physics, in the order they are checked.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Field {
    AirState = 0,
    HasJumped = 1,
    HasDoubleJumped = 2,
    HasDodged = 3,
    DodgeTimeout = 4,
    DodgeElapsed = 5,
    Supersonic = 6,
    OnGround = 7,
}
pub const FIELD_NAMES: [&str; 8] = ["air_state", "has_jumped", "has_double_jumped", "has_dodged", "dodge_timeout", "dodge_elapsed",
                                    "supersonic", "on_ground"];

fn provenance(k: Field) -> u32 {
    match k {
        Field::AirState => F_JUMP_CLOCK | F_DJ_CLOCK | F_DODGE_CLOCK,
        Field::HasJumped => F_HAS_JUMPED,
        Field::HasDoubleJumped => F_HAS_DOUBLE_JUMPED,
        Field::HasDodged => F_HAS_DODGED,
        Field::DodgeTimeout => F_HAS_JUMPED | F_HAS_DOUBLE_JUMPED | F_HAS_DODGED | F_JUMP_CLOCK,
        Field::DodgeElapsed => F_HAS_DODGED | F_DODGE_CLOCK,
        Field::Supersonic => F_SUPERSONIC,
        Field::OnGround => 0,
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CarIn {
    pub team: u8,
    pub pos: [f32; 3],
    pub vel: [f32; 3],
    pub ang_vel: [f32; 3],
    pub rot: [f32; 3],
    pub boost: f32,
    pub hitbox: [f32; 3],
    pub hitbox_offset: [f32; 3],
    pub demolished: bool,
    pub demo_left: f32,
    pub supersonic: bool,
    pub has_jumped: Option<bool>,
    pub has_double_jumped: Option<bool>,
    pub has_dodged: Option<bool>,
    pub air_state: Option<i32>,
    pub on_ground: Option<bool>,
    pub dodge_timeout: Option<f32>,
    pub dodge_elapsed: Option<f32>,
    pub dodge_dir: Option<[f32; 2]>,
    pub last_input: Option<[f64; 8]>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Inactive,
    Countdown,
    Kickoff,
    Active,
    Goal,
    Replay,
    Paused,
    Ended,
}

impl Phase {
    pub fn from_rlbot(v: i32) -> Phase {
        match v {
            0 => Phase::Inactive,
            1 => Phase::Countdown,
            2 => Phase::Kickoff,
            4 => Phase::Goal,
            5 => Phase::Replay,
            6 => Phase::Paused,
            7 => Phase::Ended,
            _ => Phase::Active,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Frame {
    pub frame: i32,
    pub cars: Vec<CarIn>,
    pub ball: Option<([f32; 3], [f32; 3], [f32; 3])>,
    pub pads: Option<Vec<(bool, f32)>>,
    pub phase: Phase,
    pub pad_locations: Option<Vec<(f32, f32)>>,
}

#[derive(Clone, Copy, Debug)]
pub struct Tolerances {
    pub snap_pos: f64,
    pub snap_vel: f64,
    pub snap_ang_vel: f64,
    pub snap_rot: f64,
    pub trust_pos: f64,
    pub trust_vel: f64,
    pub trust_ang_vel: f64,
    pub trust_rot: f64,
    pub boost: f64,
    pub teleport: f64,
    pub timer: f64,
}

impl Default for Tolerances {
    fn default() -> Self {
        Tolerances { snap_pos: 0.02, snap_vel: 0.05, snap_ang_vel: 0.003, snap_rot: 1.5, trust_pos: 5.0, trust_vel: 40.0,
                     trust_ang_vel: 1.0, trust_rot: 1200.0, boost: 1.0, teleport: 400.0, timer: 2.5 * DT }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct FieldStats {
    pub compared: u64,
    pub agreed: u64,
    pub packet_wrong: u64,
    pub adopted: u64,
}

#[derive(Clone, Debug)]
pub struct FlagEvent {
    pub frame: i32,
    pub car: i32,
    pub field: Field,
    pub packet: f64,
    pub physics: f64,
    pub decision: u8, // 0 packet wrong, 1 held, 2 adopted
}

fn py_round(x: f64) -> f64 {
    x.round_ties_even()
}

/// An analog input as the game applies it: one byte, k/127 at or above zero and k/128 below, k rounded to nearest
/// (real RLBot v5 games: every applied `last_input` axis is one of these). Rounded to f32 first, as the C API's floats.
pub fn game_axis(x: f64) -> f64 {
    let x = ((x as f32) as f64).clamp(-1.0, 1.0);
    if x >= 0.0 { (x * 127.0 + 0.5).floor() / 127.0 } else { -(-x * 128.0 + 0.5).floor() / 128.0 }
}

/// Controls as the game applies them (axes quantized, buttons 0 / 1).
pub fn game_controls(c: [f64; 8]) -> [f64; 8] {
    let b = |x: f64| if x > 0.5 { 1.0 } else { 0.0 };
    [game_axis(c[0]), game_axis(c[1]), game_axis(c[2]), game_axis(c[3]), game_axis(c[4]), b(c[5]), b(c[6]), b(c[7])]
}

pub fn same_controls(a: &[f64; 8], b: &[f64; 8]) -> bool {
    (0..5).all(|k| game_axis(a[k]) == game_axis(b[k])) && (5..8).all(|k| (a[k] > 0.5) == (b[k] > 0.5))
}

#[derive(Clone, Debug, Default)]
pub struct LagMeter {
    pending: VecDeque<(i32, [f64; 8], [f64; 8])>,
    samples: VecDeque<i32>,
    last_sent: Option<[f64; 8]>,
}

impl LagMeter {
    pub fn sent(&mut self, frame: i32, c: [f64; 8]) {
        if let Some(l) = self.last_sent {
            if !same_controls(&c, &l) {
                self.pending.push_back((frame, c, l));
                while self.pending.len() > 16 {
                    self.pending.pop_front();
                }
            }
        }
        self.last_sent = Some(c);
    }

    pub fn seen(&mut self, frame: i32, last_input: Option<[f64; 8]>) {
        let Some(li) = last_input else { return };
        while let Some(f) = self.pending.front() {
            if frame - f.0 > 40 { self.pending.pop_front(); } else { break; }
        }
        let mut hit = None;
        for (k, (s, c, before)) in self.pending.iter().enumerate() {
            if frame > *s && same_controls(&li, c) && !same_controls(&li, before) {
                hit = Some((k, frame - *s));
                break;
            }
        }
        if let Some((k, d)) = hit {
            self.samples.push_back(d);
            while self.samples.len() > 64 {
                self.samples.pop_front();
            }
            for _ in 0..=k {
                self.pending.pop_front();
            }
        }
    }

    pub fn delay(&self) -> Option<i32> {
        if self.samples.len() < 4 {
            return None;
        }
        let mut s: Vec<i32> = self.samples.iter().copied().collect();
        s.sort();
        Some(s[s.len() / 2])
    }
}

#[derive(Clone, Debug, PartialEq)]
enum VoteKey {
    Val(i64),
    Event(u8, i64),
    None,
}

fn same_key(a: &VoteKey, b: &VoteKey) -> bool {
    match (a, b) {
        (VoteKey::Event(ka, fa), VoteKey::Event(kb, fb)) => ka == kb && (fa - fb).abs() <= 2,
        _ => a == b,
    }
}

fn count(votes: &[(VoteKey, i64)], key: &VoteKey) -> i64 {
    votes.iter().filter(|(k, _)| same_key(k, key)).map(|(_, n)| *n).sum()
}

fn vote(votes: &mut Vec<(VoteKey, i64)>, key: VoteKey) {
    for (k, n) in votes.iter_mut() {
        if same_key(k, &key) {
            *n += 1;
            return;
        }
    }
    votes.push((key, 1));
}

/// A compared value: what the packet says or what physics says it should say.
#[derive(Clone, Copy, Debug)]
enum Val {
    I(i64),
    B(bool),
    F(f64),
}

impl Val {
    fn as_f64(self) -> f64 {
        match self {
            Val::I(v) => v as f64,
            Val::B(b) => if b { 1.0 } else { 0.0 },
            Val::F(f) => f,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct CarTrack {
    pub obs: Observer,
    pub assumed: u32,
    pub events: u8,
    votes: HashMap<Field, Vec<(VoteKey, i64)>>,
    history: VecDeque<(i32, CarIn)>,
    observed: BTreeMap<i32, [f64; 8]>,
    pub last_input: [f64; 8],
    pub synced: bool,
    pub consistent: bool,
    pub ticks_since_cold: i64,
    pub drift: f64,
    pub snaps: u64,
    pub probed_frame: i32,
}

impl CarTrack {
    fn fresh() -> CarTrack {
        CarTrack { probed_frame: -1, ..Default::default() }
    }
}

struct View {
    air: i32,
    has_jumped: bool,
    has_dj: bool,
    has_dodged: bool,
    dodge_timeout: f64,
    dodge_elapsed: f64,
    supersonic: bool,
}

/// One car read out in RocketSim's CarState meanings (and the extras).
#[derive(Clone, Debug, Default)]
pub struct CarOut {
    pub index: i32,
    pub team: i32,
    pub pos: [f64; 3],
    pub rot: [[f64; 3]; 3], // forward, right, up
    pub vel: [f64; 3],
    pub ang_vel: [f64; 3],
    pub is_on_ground: bool,
    pub wheels: [bool; 4],
    pub has_jumped: bool,
    pub has_double_jumped: bool,
    pub has_flipped: bool,
    pub flip_rel_torque: [f64; 3],
    pub jump_time: f64,
    pub flip_time: f64,
    pub is_flipping: bool,
    pub is_jumping: bool,
    pub air_time: f64,
    pub air_time_since_jump: f64,
    pub boost: f64,
    pub time_since_boosted: f64,
    pub is_boosting: bool,
    pub boosting_time: f64,
    pub is_supersonic: bool,
    pub supersonic_time: f64,
    pub handbrake_val: f64,
    pub is_auto_flipping: bool,
    pub auto_flip_timer: f64,
    pub auto_flip_torque_scale: f64,
    pub world_contact: bool,
    pub world_contact_normal: [f64; 3],
    pub other_car_id: u32,
    pub other_car_index: i32,
    pub bump_cooldown: f64,
    pub is_demoed: bool,
    pub demo_respawn_timer: f64,
    pub ball_hit_valid: bool,
    pub tick_when_hit: i64,
    pub tick_when_extra_impulse: i64,
    pub last_controls: [f64; 8],
    pub supersonic_grace: f64,
    pub jump_hold_broken: bool,
    pub boost_lock_left: f64,
    pub pitch_lock_left: f64,
    pub can_flip: bool,
    pub has_flip_reset: bool,
    pub dodge_timeout: f64,
    pub wheel_normals: [[f64; 3]; 4],
    pub wheel_ground: [i32; 4],
    pub suspension: [[f32; 4]; 4], // per RocketSim wheel: length, rest, travel, radius (uu)
    pub synced: bool,
    pub consistent: bool,
    pub ticks_since_cold: i64,
    pub drift: f64,
    pub assumed: u32,
    pub inputs_known: bool,
}

#[derive(Clone, Debug, Default)]
pub struct Corrected {
    pub frame: i32,
    pub horizon: i32,
    pub input_delay: i32,
    pub cars: Vec<CarOut>,
    pub ball: Option<([f64; 3], [[f64; 3]; 3], [f64; 3], [f64; 3])>,
    pub pads: Vec<(bool, f64, f64, bool)>,
}

pub type OpponentFn = Box<dyn FnMut(usize, i32, [f64; 8]) -> [f64; 8] + Send>;

pub struct Corrector {
    pub data_dir: PathBuf,
    pub own: Vec<usize>,
    pub training_action_delay: i32,
    pub fixed_delay: Option<i32>,
    pub tol: Tolerances,
    pub map: String,
    pub log_flag_errors: bool,
    pub flag_log: Vec<FlagEvent>,
    pub trust_packet_flags: bool,
    pub air_order: i32, // 0 auto, 1 jump first, 2 ground first
    pub order_votes: [i64; 2],
    pub last_input_is_applied: bool,
    pub meter: LagMeter,
    pub stats: [FieldStats; 8],
    pub na: Option<Native>,
    layout: Vec<(&'static str, u8)>,
    pub frame: i32,
    pub cars: Vec<CarTrack>,
    sent_by_frame: BTreeMap<i32, [f64; 8]>,
    sent_extra: BTreeMap<(usize, i32), [f64; 8]>,
    pad_map: Vec<usize>,
    pad_big: Vec<bool>,
    pub cold_starts: u64,
    pub presets: Vec<&'static str>,
    pub predicted: Option<Native>,
    prev: Option<Native>,
    prev_obs: Vec<Observer>,
    rows: Vec<Vec<[f64; 8]>>,
    pub opponents: Option<OpponentFn>,
    pub pad_locations: Option<Vec<(f32, f32)>>,
    /// a taken pad's timer: 0 auto (learned from the first timer that moves), 1 seconds left (what real RLBot v5 games
    /// show), 2 seconds since taken (what RLBot's schema describes)
    pub pad_timer: i32,
    pad_mode: i32,
    prev_pads: Option<Vec<(bool, f32)>>,
    last_phase: Option<Phase>,
}

fn rot_diff(a: f64, b: f64) -> f64 {
    let d = (a - b).rem_euclid(65536.0);
    d.min(65536.0 - d)
}

fn f32row(r: &[f64; 8]) -> [f32; 8] {
    let mut o = [0f32; 8];
    for k in 0..8 {
        o[k] = r[k] as f32;
    }
    o
}

impl Corrector {
    pub fn new(data_dir: &Path, own: Vec<usize>, training_action_delay: i32, input_delay: Option<i32>) -> Result<Corrector, String> {
        arena::init(data_dir)?;
        Ok(Corrector {
            data_dir: data_dir.to_path_buf(), own, training_action_delay, fixed_delay: input_delay, tol: Tolerances::default(),
            map: "Stadium_P".into(), log_flag_errors: false, flag_log: Vec::new(), trust_packet_flags: false, air_order: 0,
            order_votes: [0, 0], last_input_is_applied: true, meter: LagMeter::default(), stats: [FieldStats::default(); 8], na: None,
            layout: Vec::new(), frame: -1, cars: Vec::new(), sent_by_frame: BTreeMap::new(), sent_extra: BTreeMap::new(),
            pad_map: Vec::new(), pad_big: Vec::new(), cold_starts: 0, presets: Vec::new(), predicted: None, prev: None,
            prev_obs: Vec::new(), rows: Vec::new(), opponents: None, pad_locations: None, pad_timer: 0, pad_mode: 0,
            prev_pads: None, last_phase: None,
        })
    }

    // ------------------------------------------------------------------ inputs

    pub fn input_delay(&self) -> i32 {
        if let Some(d) = self.fixed_delay {
            return d;
        }
        self.meter.delay().unwrap_or(1)
    }

    pub fn horizon(&self) -> i32 {
        (self.input_delay() - 1 - self.training_action_delay).max(0)
    }

    pub fn sent(&mut self, c: [f64; 8], frame: Option<i32>) {
        let c = game_controls(c);
        let f = frame.unwrap_or(self.frame);
        self.sent_by_frame.insert(f, c);
        self.meter.sent(f, c);
        self.sent_by_frame.retain(|k, _| *k >= f - 240);
    }

    pub fn record_sent(&mut self, c: [f64; 8], tick: i32, index: Option<usize>) {
        let c = game_controls(c);
        let i = index.unwrap_or(self.own[0]);
        self.sent_extra.insert((i, tick), c);
        self.sent_extra.retain(|k, _| k.1 >= tick - 240);
    }

    pub fn own_controls(&self, i: usize, tick: i32) -> Option<[f64; 8]> {
        let mut best = None;
        let mut bt = i32::MIN;
        for (&(j, t), c) in &self.sent_extra {
            if j == i && bt < t && t <= tick {
                best = Some(*c);
                bt = t;
            }
        }
        if i == self.own[0] {
            let d = self.input_delay();
            for (&s, c) in &self.sent_by_frame {
                let t = s + d;
                if bt < t && t <= tick {
                    best = Some(*c);
                    bt = t;
                }
            }
        }
        best
    }

    fn opponent(&mut self, i: usize, tick: i32) -> [f64; 8] {
        let held = self.cars[i].last_input;
        match self.opponents.as_mut() {
            Some(f) => f(i, tick, held),
            None => held,
        }
    }

    fn input(&mut self, i: usize, tick: i32, fr: &Frame) -> [f64; 8] {
        if tick == fr.frame && self.last_input_is_applied {
            if let Some(li) = fr.cars[i].last_input {
                return li;
            }
        }
        if self.own.contains(&i) {
            if let Some(o) = self.own_controls(i, tick) {
                return o;
            }
        }
        if let Some(o) = self.cars[i].observed.get(&tick) {
            return *o;
        }
        if !self.own.contains(&i) { self.opponent(i, tick) } else { self.cars[i].last_input }
    }

    // ------------------------------------------------------------------ the arena

    /// The body whose hitbox (size and offset) is nearest the packet's. Real RLBot v5 packets carry the game's own
    /// hitbox, equal to catSim's preset size; a size 2.5 uu smaller on each axis (RocketSim's numbers) is matched too.
    fn preset_of(c: &CarIn) -> &'static str {
        let mut best = 1e9f64;
        let mut name = "Octane";
        for p in STANDARD_PRESETS {
            let (size, off) = arena::preset_hitbox(p).unwrap();
            let d_off = (off[0] as f64 - c.hitbox_offset[0] as f64).abs() + (off[1] as f64 - c.hitbox_offset[1] as f64).abs()
                + (off[2] as f64 - c.hitbox_offset[2] as f64).abs();
            for under in [0.0f64, 2.5] {
                let d = (size[0] as f64 - under - c.hitbox[0] as f64).abs() + (size[1] as f64 - under - c.hitbox[1] as f64).abs()
                    + (size[2] as f64 - under - c.hitbox[2] as f64).abs() + d_off;
                if d < best {
                    best = d;
                    name = p;
                }
            }
        }
        name
    }

    fn build(&mut self, fr: &Frame) -> Result<(), String> {
        self.presets = fr.cars.iter().map(Self::preset_of).collect();
        let cars: Vec<(&str, u8)> = self.presets.iter().zip(&fr.cars).map(|(p, c)| (*p, c.team)).collect();
        self.na = Some(Native::new(&cars, &self.data_dir, &self.map)?);
        self.layout = self.presets.iter().zip(&fr.cars).map(|(p, c)| (*p, c.team)).collect();
        self.cars = fr.cars.iter().map(|_| CarTrack::fresh()).collect();
        let pads = arena::pad_locations(&self.map).ok_or("unknown map")?;
        let locs = fr.pad_locations.clone().or_else(|| self.pad_locations.clone());
        self.pad_map = match locs {
            Some(locs) if !locs.is_empty() => locs.iter().map(|(x, y)| {
                let mut best = 0;
                let mut bd = f64::INFINITY;
                for (k, (p, _)) in pads.iter().enumerate() {
                    let d = (p[0] as f64 - *x as f64).powi(2) + (p[1] as f64 - *y as f64).powi(2);
                    if d < bd {
                        bd = d;
                        best = k;
                    }
                }
                best
            }).collect(),
            _ => (0..pads.len()).collect(),
        };
        self.pad_big = pads.iter().map(|(_, b)| *b).collect();
        Ok(())
    }

    fn step_arena(na: &mut Native, rows: &[[f64; 8]], cars: &mut [CarTrack]) {
        let r: Vec<[f32; 8]> = rows.iter().map(f32row).collect();
        na.step(&r);
        for (i, c) in cars.iter_mut().enumerate() {
            let h = na.car_hidden(i);
            c.obs.tick(h.as_deref());
            c.events |= c.obs.events;
        }
    }

    // ------------------------------------------------------------------ per packet

    pub fn update(&mut self, fr: &Frame) -> Result<Option<Corrected>, String> {
        for idx in 0..self.own.len() {
            let i = self.own[idx];
            if i < fr.cars.len() {
                self.meter.seen(fr.frame, fr.cars[i].last_input);
            }
        }
        if matches!(fr.phase, Phase::Inactive | Phase::Replay | Phase::Paused | Phase::Ended | Phase::Goal) {
            self.last_phase = Some(fr.phase);
            if fr.phase != Phase::Paused {
                self.frame = -1;
                return Ok(None); // nothing to correct between a goal and the next kickoff
            }
            return Ok(self.na.as_ref().map(|na| self.read(na, &self.cars, fr.frame)));
        }
        let layout: Vec<(&'static str, u8)> = fr.cars.iter().map(|c| (Self::preset_of(c), c.team)).collect();
        let k = fr.frame - self.frame;
        let cold = self.na.is_none() || layout != self.layout || self.frame < 0 || k < 0 || k > 60;
        if cold {
            if self.na.is_none() || layout != self.layout {
                self.build(fr)?;
            }
            self.remember(fr);
            self.cold_all(fr);
        } else if k == 0 {
            self.remember(fr);
        } else {
            self.remember(fr);
            if self.last_phase == Some(Phase::Countdown) && fr.phase != Phase::Countdown {
                // the kickoff countdown ended: every car takes input again and the ball is no longer held (real games:
                // the first packet after the countdown already shows the cars driving, so before its tick is stepped)
                self.na().release_countdown();
            }
            self.prev = self.na.clone();
            self.prev_obs = self.cars.iter().map(|c| c.obs.clone()).collect();
            self.rows.clear();
            for t in self.frame + 1..=fr.frame {
                let rows: Vec<[f64; 8]> = (0..self.cars.len()).map(|i| self.input(i, t, fr)).collect();
                let mut na = self.na.take().unwrap();
                Self::step_arena(&mut na, &rows, &mut self.cars);
                self.na = Some(na);
                self.rows.push(rows);
            }
            self.frame = fr.frame;
            for c in self.cars.iter_mut() {
                c.ticks_since_cold += k as i64;
            }
            self.resync(fr);
        }
        self.probe(fr);
        self.last_phase = Some(fr.phase);
        Ok(Some(self.read(self.na.as_ref().unwrap(), &self.cars, self.frame)))
    }

    pub fn predict(&mut self, ticks: Option<i32>) -> Option<Corrected> {
        let n = ticks.unwrap_or_else(|| self.horizon());
        self.na.as_ref()?;
        if matches!(self.last_phase, Some(Phase::Inactive | Phase::Replay | Phase::Ended | Phase::Goal)) {
            return None;
        }
        if n <= 0 {
            self.predicted = None;
            let mut out = self.read(self.na.as_ref().unwrap(), &self.cars, self.frame);
            for (i, c) in out.cars.iter_mut().enumerate() {
                c.inputs_known = self.known(i);
            }
            return Some(out);
        }
        let mut na = self.na.clone().unwrap();
        let mut cars: Vec<CarTrack> = self.cars.iter().map(|c| CarTrack {
            obs: c.obs.clone(), assumed: c.assumed, synced: c.synced, consistent: c.consistent, ticks_since_cold: c.ticks_since_cold,
            drift: c.drift, ..CarTrack::fresh()
        }).collect();
        let known: Vec<bool> = (0..cars.len()).map(|i| self.known(i)).collect();
        for t in self.frame + 1..=self.frame + n {
            let mut rows = Vec::with_capacity(cars.len());
            for i in 0..cars.len() {
                let o = if known[i] { self.own_controls(i, t) } else { None };
                rows.push(match o {
                    Some(o) => o,
                    None => if !known[i] { self.opponent(i, t) } else { self.cars[i].last_input },
                });
            }
            Self::step_arena(&mut na, &rows, &mut cars);
        }
        let mut out = self.read(&na, &cars, self.frame + n);
        out.horizon = n;
        for (i, c) in out.cars.iter_mut().enumerate() {
            c.inputs_known = known[i];
        }
        self.predicted = Some(na);
        Some(out)
    }

    fn known(&self, i: usize) -> bool {
        self.own.contains(&i) || self.sent_extra.keys().any(|(j, _)| *j == i)
    }

    pub fn seed_exact(&mut self, fr: &Frame, arena: &Native, observers: Option<&[Observer]>) -> Result<(), String> {
        self.build(fr)?;
        self.na = Some(arena.clone());
        self.frame = fr.frame;
        self.remember(fr);
        for (i, c) in self.cars.iter_mut().enumerate() {
            c.obs = observers.map(|o| o[i].clone()).unwrap_or_default();
            c.assumed = 0;
            c.synced = true;
            c.consistent = true;
            c.probed_frame = fr.frame;
        }
        Ok(())
    }

    // ------------------------------------------------------------------ remembering packets

    fn remember(&mut self, fr: &Frame) {
        for (c, p) in self.cars.iter_mut().zip(&fr.cars) {
            c.history.push_back((fr.frame, *p));
            while c.history.len() > 90 {
                c.history.pop_front();
            }
            if let Some(li) = p.last_input {
                c.observed.insert(fr.frame, li);
                c.last_input = li;
                c.observed.retain(|k, _| *k >= fr.frame - 120);
            }
        }
    }

    fn run(&self, i: usize, test: impl Fn(&CarIn) -> bool) -> i32 {
        let h = &self.cars[i].history;
        let Some(last) = h.back() else { return 0 };
        if !test(&last.1) {
            return 0;
        }
        let mut first = last.0;
        for (f, p) in h.iter().rev() {
            if !test(p) {
                break;
            }
            first = *f;
        }
        last.0 - first + 1
    }

    // ------------------------------------------------------------------ cold starts

    fn na(&mut self) -> &mut Native {
        self.na.as_mut().unwrap()
    }

    fn cold_all(&mut self, fr: &Frame) {
        self.cold_starts += 1;
        self.frame = fr.frame;
        let score = self.na.as_ref().unwrap().round_score();
        self.na().set_round_hidden(fr.frame, 1, 0.0, true, fr.phase == Phase::Countdown, 300.0, score);
        for i in 0..fr.cars.len() {
            self.cold_car(fr, i);
        }
        self.set_ball(fr);
        self.set_pads(fr, true);
    }

    fn set_body(&mut self, i: usize, p: &CarIn) {
        let [pitch, yaw, roll] = p.rot;
        let na = self.na();
        let h = na.car_hidden(i);
        if h.is_none() {
            na.set_car_body(i, &[p.pos[0] as f64 / 50.0, p.pos[1] as f64 / 50.0, p.pos[2] as f64 / 50.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
                                 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]);
        }
        na.set_car(i, &[p.pos[0], p.pos[1], p.pos[2], p.vel[0], p.vel[1], p.vel[2], p.ang_vel[0], p.ang_vel[1], p.ang_vel[2], yaw, pitch,
                        roll, p.boost]);
        if let Some(h) = h {
            na.set_car_hidden(i, &h);
        }
    }

    fn cold_car(&mut self, fr: &Frame, i: usize) {
        let p = fr.cars[i];
        {
            let c = &mut self.cars[i];
            c.ticks_since_cold = 0;
            c.synced = false;
            c.consistent = false;
            c.drift = 0.0;
            c.assumed = ALL_FACTS;
            c.votes.clear();
        }
        if p.demolished {
            self.na().set_car_demolished(i, (p.demo_left as f64).max(DT) as f32);
            self.cars[i].obs = Observer::default();
            return;
        }
        self.set_body(i, &p);
        let mut h = self.na.as_ref().unwrap().car_hidden(i).unwrap();
        self.hidden_from_packet(fr, i, &mut h, true);
        self.na().set_car_hidden(i, &h);
        self.set_boost_exact(i, p.boost as f64);
        self.cars[i].obs = self.observer_from_packet(fr, i, &h);
        self.cars[i].probed_frame = -1;
        let drive = fr.phase != Phase::Countdown;
        self.na().set_driving(i, drive);
    }

    fn set_boost_exact(&mut self, i: usize, boost: f64) {
        if let Some(mut h) = self.na.as_ref().unwrap().car_hidden(i) {
            if (h[H_BOOST_AMOUNT] * 100.0 - boost).abs() > 1e-4 {
                h[H_BOOST_AMOUNT] = (boost / 100.0).clamp(0.0, 1.0);
                self.na().set_car_hidden(i, &h);
            }
        }
    }

    fn air_state(p: &CarIn) -> Option<i32> {
        if let Some(a) = p.air_state {
            return Some(a);
        }
        p.on_ground.map(|g| if g { ON_GROUND } else { IN_AIR })
    }

    fn hidden_from_packet(&self, fr: &Frame, i: usize, h: &mut [f64], full: bool) {
        let p = &fr.cars[i];
        let air = Self::air_state(p);
        let jumped = p.has_jumped.unwrap_or(false);
        let dj = p.has_double_jumped.unwrap_or(false);
        let dodged = p.has_dodged.unwrap_or(false);
        let li = p.last_input.unwrap_or(self.cars[i].last_input);
        let b = |x: bool| if x { 1.0 } else { 0.0 };
        h[H_DRIVING] = b(fr.phase != Phase::Countdown);
        h[H_ON_GROUND] = b(air == Some(ON_GROUND) || (air.is_none() && p.on_ground == Some(true)));
        h[H_JUMPED] = b(jumped || dj || dodged);
        h[H_DOUBLE_JUMPED] = b(dj || dodged);
        h[H_SUPERSONIC] = b(p.supersonic);
        let jumping = air == Some(JUMPING);
        if jumping {
            let run = self.run(i, |q| q.air_state == Some(JUMPING)).max(1);
            h[H_JUMP_ON] = 1.0;
            h[H_JUMP_T] = run as f64 * DT;
            h[H_ON_GROUND] = b(run <= 6);
        } else {
            h[H_JUMP_ON] = 0.0;
            match p.dodge_timeout {
                Some(t) if t >= 0.0 && jumped => h[H_JUMP_T] = (DOUBLEJUMP_MAX_DELAY - t as f64).max(0.0),
                _ => if full { h[H_JUMP_T] = 2.0 },
            }
        }
        h[H_JUMP_RELEASED] = b(li[5] < 0.5);
        if air == Some(DOUBLE_JUMPING) {
            h[H_DJ_ON] = 1.0;
            h[H_DJ_T] = self.run(i, |q| q.air_state == Some(DOUBLE_JUMPING)).max(1) as f64 * DT;
        } else {
            h[H_DJ_ON] = 0.0;
            if full {
                h[H_DJ_T] = 2.0;
            }
        }
        let de = p.dodge_elapsed;
        let mid_dodge = dodged && matches!(de, Some(d) if 0.0 < d as f64 && (d as f64) < FLIP_TORQUE_TIME) && air != Some(ON_GROUND);
        if air == Some(DODGING) || mid_dodge {
            h[H_DODGE_ON] = 1.0;
            let d = de.map(|d| d as f64).unwrap_or(0.0);
            h[H_DODGE_T] = DT.max(if d == 0.0 { DT } else { d });
        } else {
            h[H_DODGE_ON] = 0.0;
            if full {
                h[H_DODGE_T] = 2.0;
            }
        }
        if let Some(dd) = p.dodge_dir {
            if dd[0] != 0.0 || dd[1] != 0.0 {
                if let Some((t, d)) = self.na.as_ref().unwrap().dodge_from_input(i, dd[0], dd[1]) {
                    for k in 0..3 {
                        h[H_DODGE_TORQUE + k] = t[k] as f64;
                        h[H_DODGE_DIR + k] = d[k] as f64;
                    }
                }
            }
        }
        h[H_HELD..H_HELD + 8].copy_from_slice(&li);
        if full {
            let boosting = li[6] > 0.5 && p.boost > 0.0;
            let run = self.run(i, |q| q.last_input.map(|l| (l[6] > 0.5) == boosting).unwrap_or(false));
            h[H_BOOST_ON] = b(boosting);
            h[H_BOOST_T] = DT.max(run as f64 * DT);
            let hb = li[7] > 0.5;
            let run = self.run(i, |q| q.last_input.map(|l| (l[7] > 0.5) == hb).unwrap_or(false));
            h[H_HANDBRAKE] = if hb { (run as f64 * DT * POWERSLIDE_RISE_RATE).min(1.0) } else { (1.0 - run as f64 * DT * POWERSLIDE_FALL_RATE).max(0.0) };
            h[H_AF_ON] = 0.0;
            h[H_SS_TIME] = 0.0;
        }
    }

    fn observer_from_packet(&self, fr: &Frame, i: usize, h: &[f64]) -> Observer {
        let p = &fr.cars[i];
        let mut o = Observer::default();
        let air = Self::air_state(p);
        o.is_jumping = h[H_JUMP_ON] > 0.5;
        o.has_jumped = p.has_jumped.unwrap_or(false) || o.is_jumping;
        o.has_double_jumped = p.has_double_jumped.unwrap_or(false);
        o.has_flipped = p.has_dodged.unwrap_or(false);
        o.flip_time = if o.has_flipped { p.dodge_elapsed.map(|d| d as f64).unwrap_or(0.0) } else { 0.0 };
        o.is_flipping = o.has_flipped && o.flip_time < FLIP_TORQUE_TIME;
        o.prev_dodge = h[H_DODGE_ON] > 0.5;
        o.prev_dj = h[H_DJ_ON] > 0.5;
        if let Some(dd) = p.dodge_dir {
            o.flip_rel_torque = [-(dd[1] as f64), dd[0] as f64, 0.0];
        }
        let on = air == Some(ON_GROUND);
        if !on {
            o.air_time = self.run(i, |q| Self::air_state(q) != Some(ON_GROUND)).max(1) as f64 * DT;
        }
        if o.is_jumping {
            o.jump_time = h[H_JUMP_T];
        } else if o.has_jumped && !on {
            let since = if matches!(p.dodge_timeout, Some(t) if t >= 0.0) { h[H_JUMP_T] } else { o.air_time };
            o.air_time_since_jump = since;
            o.jump_time = since + 0.1;
        }
        o.supersonic_time = if p.supersonic { self.run(i, |q| q.supersonic) as f64 * DT } else { 0.0 };
        o
    }

    // ------------------------------------------------------------------ resync

    fn order(&self) -> i32 {
        match self.air_order {
            0 => if self.order_votes[1] > self.order_votes[0] { 2 } else { 1 },
            o => o,
        }
    }

    fn rlbot_view(&self, h: &[f64], obs: &Observer) -> View {
        let on = h[H_ON_GROUND] > 0.5;
        let jumping = h[H_JUMP_ON] > 0.5;
        let dj = h[H_DJ_ON] > 0.5;
        let dodge = h[H_DODGE_ON] > 0.5;
        let air = if self.order() == 1 {
            if jumping { JUMPING } else if dj { DOUBLE_JUMPING } else if dodge { DODGING } else if on { ON_GROUND } else { IN_AIR }
        } else if on { ON_GROUND } else if jumping { JUMPING } else if dj { DOUBLE_JUMPING } else if dodge { DODGING } else { IN_AIR };
        // (real games) RLBot's dodge_timeout is the game's own jump clock: time since the jump ended, counted on the
        // ground too, and -1 while the jump is still on
        let can = obs.has_jumped && !(obs.has_double_jumped || obs.has_flipped) && !on && !jumping && h[H_JUMP_T] < DOUBLEJUMP_MAX_DELAY;
        View {
            air, has_jumped: obs.has_jumped, has_dj: obs.has_double_jumped, has_dodged: obs.has_flipped,
            dodge_timeout: if can { (DOUBLEJUMP_MAX_DELAY - h[H_JUMP_T]).max(0.0) } else { -1.0 },
            dodge_elapsed: if obs.has_flipped { obs.flip_time } else { 0.0 }, supersonic: h[H_SUPERSONIC] > 0.5,
        }
    }

    fn resync(&mut self, fr: &Frame) {
        let tol = self.tol;
        for i in 0..fr.cars.len() {
            let p = fr.cars[i];
            let body = self.na.as_ref().unwrap().car_body(i);
            if p.demolished || body.is_none() {
                if p.demolished != body.is_none() {
                    self.cold_car(fr, i);
                }
                continue;
            }
            let row = self.na.as_ref().unwrap().car_row(i);
            let hrow = self.na.as_ref().unwrap().hidden_row(i);
            let m3 = |a: &[f32], b: &[f32; 3]| (0..3).map(|k| (a[k] as f64 - b[k] as f64).abs()).fold(f64::NEG_INFINITY, f64::max);
            let d_pos = m3(&row[2..5], &p.pos);
            if d_pos > tol.teleport {
                self.cold_car(fr, i);
                continue;
            }
            let d_vel = m3(&row[5..8], &p.vel);
            let d_ang = m3(&row[8..11], &p.ang_vel);
            let d_rot = (0..3).map(|k| rot_diff(hrow[24 + k] as f64, p.rot[k] as f64 * ROT)).fold(f64::NEG_INFINITY, f64::max);
            let synced = d_pos <= tol.snap_pos && d_vel <= tol.snap_vel && d_ang <= tol.snap_ang_vel && d_rot <= tol.snap_rot;
            let consistent = d_pos <= tol.trust_pos && d_vel <= tol.trust_vel && d_ang <= tol.trust_ang_vel && d_rot <= tol.trust_rot;
            self.cars[i].drift = d_pos;
            if !synced {
                self.cars[i].snaps += 1;
                self.set_body(i, &p);
            }
            if !consistent {
                self.cars[i].probed_frame = -1;
            }
            let mut h = self.na.as_ref().unwrap().car_hidden(i).unwrap();
            if (h[H_BOOST_AMOUNT] * 100.0 - p.boost as f64).abs() > tol.boost + 1e-3 {
                h[H_BOOST_AMOUNT] = (p.boost as f64 / 100.0).clamp(0.0, 1.0);
                self.na().set_car_hidden(i, &h);
            }
            self.cars[i].synced = synced;
            self.cars[i].consistent = consistent;
            self.check_flags(fr, i, consistent, &row);
        }
    }

    fn learn(&mut self, i: usize, h: &[f64], row: &[f32]) {
        let c = &mut self.cars[i];
        let ev = c.events;
        c.events = 0;
        if ev & (EV_LANDED | EV_JUMP) != 0
            || (h[H_ON_GROUND] > 0.5 && h[H_JUMP_ON] < 0.5 && !c.obs.has_jumped && !c.obs.has_flipped && !c.obs.has_double_jumped) {
            c.assumed &= !AIR_FACTS;
        }
        if ev & EV_FLIP != 0 {
            c.assumed &= !(F_HAS_DODGED | F_DODGE_CLOCK | F_HAS_DOUBLE_JUMPED);
        }
        if ev & EV_DOUBLE_JUMP != 0 {
            c.assumed &= !(F_HAS_DOUBLE_JUMPED | F_DJ_CLOCK | F_HAS_DODGED);
        }
        let speed = (0.0 + (row[5] as f64) * (row[5] as f64) + (row[6] as f64) * (row[6] as f64) + (row[7] as f64) * (row[7] as f64)).sqrt();
        if speed < SUPERSONIC_START_SPEED - 100.0 || speed >= SUPERSONIC_START_SPEED {
            c.assumed &= !F_SUPERSONIC;
        }
        if c.ticks_since_cold as f64 * DT > 0.6 {
            c.assumed &= !(F_BOOST_CLOCK | F_HANDBRAKE);
        }
        let assumed = c.assumed;
        c.votes.retain(|k, _| provenance(*k) & assumed != 0);
    }

    fn vote_key(k: Field, v: Val, frame: i32) -> VoteKey {
        match k {
            Field::DodgeElapsed => {
                let x = v.as_f64();
                if x > 0.0 { VoteKey::Event(0, py_round(frame as f64 - x * 120.0) as i64) } else { VoteKey::None }
            }
            Field::DodgeTimeout => {
                let x = v.as_f64();
                if x >= 0.0 { VoteKey::Event(1, py_round(frame as f64 - (DOUBLEJUMP_MAX_DELAY - x) * 120.0) as i64) } else { VoteKey::None }
            }
            _ => VoteKey::Val(v.as_f64() as i64),
        }
    }

    fn log(&mut self, frame: i32, car: usize, k: Field, g: Val, w: Val, decision: u8) {
        if self.log_flag_errors {
            self.flag_log.push(FlagEvent { frame, car: car as i32, field: k, packet: g.as_f64(), physics: w.as_f64(), decision });
        }
    }

    fn check_flags(&mut self, fr: &Frame, i: usize, consistent: bool, row: &[f32]) {
        let p = fr.cars[i];
        let Some(h) = self.na.as_ref().unwrap().car_hidden(i) else { return };
        self.learn(i, &h, row);
        if let Some(a) = p.air_state {
            if h[H_ON_GROUND] > 0.5 && h[H_JUMP_ON] > 0.5 && consistent {
                if a == JUMPING {
                    self.order_votes[0] += 1;
                } else if a == ON_GROUND {
                    self.order_votes[1] += 1;
                }
            }
        }
        let mut want = self.rlbot_view(&h, &self.cars[i].obs);
        // (real games) RLBot's is_supersonic is the speed test alone (>= 2200 uu/s), not the game's flag with its grace
        let speed = (0.0 + (row[5] as f64) * (row[5] as f64) + (row[6] as f64) * (row[6] as f64) + (row[7] as f64) * (row[7] as f64)).sqrt();
        want.supersonic = speed >= SUPERSONIC_START_SPEED;
        let mut got: Vec<(Field, Option<Val>, Val)> = vec![
            (Field::AirState, p.air_state.map(|a| Val::I(a as i64)), Val::I(want.air as i64)),
            (Field::HasJumped, p.has_jumped.map(Val::B), Val::B(want.has_jumped)),
            (Field::HasDoubleJumped, p.has_double_jumped.map(Val::B), Val::B(want.has_dj)),
            (Field::HasDodged, p.has_dodged.map(Val::B), Val::B(want.has_dodged)),
            (Field::DodgeTimeout, p.dodge_timeout.map(|x| Val::F(x as f64)), Val::F(want.dodge_timeout)),
            (Field::DodgeElapsed, p.dodge_elapsed.map(|x| Val::F(x as f64)), Val::F(want.dodge_elapsed)),
            (Field::Supersonic, Some(Val::B(p.supersonic)), Val::B(want.supersonic)),
        ];
        if p.air_state.is_none() {
            if let Some(g) = p.on_ground {
                got.push((Field::OnGround, Some(Val::B(g)), Val::B(h[H_ON_GROUND] > 0.5)));
            }
        }
        let mut adopt: Vec<(Field, Val)> = Vec::new();
        let mut contested: Vec<(Field, Val, Val)> = Vec::new();
        for &(k, g, w) in &got {
            let Some(g) = g else { continue };
            let agree = match k {
                Field::DodgeTimeout | Field::DodgeElapsed => {
                    let (gf, wf) = (g.as_f64(), w.as_f64());
                    (gf - wf).abs() <= self.tol.timer || (gf < 0.0 && wf < 0.0)
                }
                _ => g.as_f64() == w.as_f64(),
            };
            let s = &mut self.stats[k as usize];
            s.compared += 1;
            if agree {
                s.agreed += 1;
                continue;
            }
            let assumed = provenance(k) & self.cars[i].assumed != 0;
            if !consistent && !assumed && !self.trust_packet_flags && k != Field::OnGround {
                contested.push((k, g, w));
                continue;
            }
            let known = consistent && !assumed && !self.trust_packet_flags;
            if known || (k == Field::OnGround && !self.trust_packet_flags) {
                self.stats[k as usize].packet_wrong += 1;
                self.log(fr.frame, i, k, g, w, 0);
                continue;
            }
            let (kg, kw) = (Self::vote_key(k, g, fr.frame), Self::vote_key(k, w, fr.frame));
            let votes = self.cars[i].votes.entry(k).or_default();
            let empty = votes.is_empty();
            let lead = count(votes, &kg) > count(votes, &kw);
            let take = lead || self.trust_packet_flags || (!consistent && empty);
            if take {
                self.stats[k as usize].adopted += 1;
                adopt.push((k, g));
            }
            self.log(fr.frame, i, k, g, w, if take { 2 } else { 1 });
        }
        for &(k, g, _) in &got {
            let Some(g) = g else { continue };
            if provenance(k) & self.cars[i].assumed != 0 {
                let key = Self::vote_key(k, g, fr.frame);
                vote(self.cars[i].votes.entry(k).or_default(), key);
            }
        }
        if !contested.is_empty() {
            let c: Vec<(Field, Val)> = contested.iter().map(|&(k, g, _)| (k, g)).collect();
            let take = self.arbitrate(fr, i, &c);
            for &(k, g, w) in &contested {
                if take {
                    self.stats[k as usize].adopted += 1;
                    adopt.push((k, g));
                    self.log(fr.frame, i, k, g, w, 2);
                } else {
                    self.stats[k as usize].packet_wrong += 1;
                    self.log(fr.frame, i, k, g, w, 0);
                }
            }
        }
        if !adopt.is_empty() {
            for &(k, _) in &adopt {
                self.cars[i].assumed |= provenance(k);
            }
            let mut obs = self.cars[i].obs.clone();
            let mut na = self.na.take().unwrap();
            self.adopt_into(fr, i, &adopt, &mut obs, &mut na);
            self.na = Some(na);
            self.cars[i].obs = obs;
            if adopt.iter().any(|(k, _)| *k == Field::AirState) {
                self.cars[i].probed_frame = -1;
            }
        }
    }

    fn fit(&self, na: &Native, i: usize, p: &CarIn) -> f64 {
        let tol = &self.tol;
        if na.car_body(i).is_none() {
            return 1e9;
        }
        let row = na.car_row(i);
        let hr = na.hidden_row(i);
        let m = |o: usize, b: &[f32; 3]| (0..3).map(|k| (row[o + k] as f64 - b[k] as f64).abs()).fold(f64::NEG_INFINITY, f64::max);
        let r = (0..3).map(|k| rot_diff(hr[24 + k] as f64, p.rot[k] as f64 * ROT)).fold(f64::NEG_INFINITY, f64::max);
        (m(2, &p.pos) / tol.trust_pos).max(m(5, &p.vel) / tol.trust_vel).max(m(8, &p.ang_vel) / tol.trust_ang_vel).max(r / tol.trust_rot)
    }

    fn arbitrate(&mut self, fr: &Frame, i: usize, contested: &[(Field, Val)]) -> bool {
        let (Some(prev), false) = (self.prev.clone(), self.rows.is_empty()) else { return true };
        let p = fr.cars[i];
        let mut alt = prev.clone();
        if alt.car_hidden(i).is_none() {
            return true;
        }
        let mut cars: Vec<CarTrack> = self.prev_obs.iter().map(|o| CarTrack { obs: o.clone(), ..CarTrack::fresh() }).collect();
        let mut o = cars[i].obs.clone();
        self.adopt_into(fr, i, contested, &mut o, &mut alt);
        cars[i].obs = o;
        let rows = self.rows.clone();
        for r in &rows {
            Self::step_arena(&mut alt, r, &mut cars);
        }
        let mut mine = prev;
        let mut mcars: Vec<CarTrack> = self.prev_obs.iter().map(|o| CarTrack { obs: o.clone(), ..CarTrack::fresh() }).collect();
        for r in &rows {
            Self::step_arena(&mut mine, r, &mut mcars);
        }
        let fa = self.fit(&mine, i, &p);
        let fb = self.fit(&alt, i, &p);
        let aa = mine.car_hidden(i).map(|h| self.rlbot_view(&h, &mcars[i].obs).air);
        let ab = alt.car_hidden(i).map(|h| self.rlbot_view(&h, &cars[i].obs).air);
        if fb < 0.5 * fa {
            return true;
        }
        match p.air_state {
            Some(air) => ab == Some(air) && aa != Some(air) && fb <= fa * 1.05,
            None => false,
        }
    }

    fn adopt_into(&self, fr: &Frame, i: usize, adopt: &[(Field, Val)], o: &mut Observer, na: &mut Native) {
        let p = &fr.cars[i];
        let mut h = na.car_hidden(i).unwrap();
        for &(k, g) in adopt {
            match k {
                Field::HasJumped => o.has_jumped = g.as_f64() != 0.0,
                Field::HasDoubleJumped => o.has_double_jumped = g.as_f64() != 0.0,
                Field::HasDodged => {
                    let gb = g.as_f64() != 0.0;
                    o.has_flipped = gb;
                    if gb {
                        if let Some(d) = p.dodge_elapsed {
                            o.flip_time = d as f64;
                        }
                    }
                    o.is_flipping = o.has_flipped && o.flip_time < FLIP_TORQUE_TIME;
                    if gb {
                        if let Some(dd) = p.dodge_dir {
                            o.flip_rel_torque = [-(dd[1] as f64), dd[0] as f64, 0.0];
                        }
                    }
                }
                Field::DodgeElapsed => {
                    if o.has_flipped {
                        o.flip_time = g.as_f64();
                        o.is_flipping = o.flip_time < FLIP_TORQUE_TIME;
                        if h[H_DODGE_ON] > 0.5 {
                            h[H_DODGE_T] = DT.max(g.as_f64());
                        }
                    }
                }
                Field::DodgeTimeout => {
                    let gv = g.as_f64();
                    if gv >= 0.0 {
                        o.air_time_since_jump = (DOUBLEJUMP_MAX_DELAY - gv).max(0.0);
                        if h[H_JUMP_ON] < 0.5 {
                            h[H_JUMP_T] = o.air_time_since_jump;
                        }
                    } else if o.has_jumped {
                        o.air_time_since_jump = o.air_time_since_jump.max(DOUBLEJUMP_MAX_DELAY);
                    }
                }
                Field::AirState => {
                    let a = g.as_f64() as i32;
                    let (jumping, dj, dodge) = (a == JUMPING, a == DOUBLE_JUMPING, a == DODGING);
                    if jumping && h[H_JUMP_ON] < 0.5 {
                        h[H_JUMP_T] = self.run(i, |q| q.air_state == Some(JUMPING)).max(1) as f64 * DT;
                    }
                    h[H_JUMP_ON] = if jumping { 1.0 } else { 0.0 };
                    h[H_DJ_ON] = if dj { 1.0 } else { 0.0 };
                    if dodge && h[H_DODGE_ON] < 0.5 {
                        let d = p.dodge_elapsed.map(|d| d as f64).unwrap_or(0.0);
                        h[H_DODGE_T] = DT.max(if d == 0.0 { DT } else { d });
                    }
                    h[H_DODGE_ON] = if dodge { 1.0 } else { 0.0 };
                    o.is_jumping = jumping;
                    if jumping {
                        o.has_jumped = true;
                    }
                }
                Field::Supersonic => h[H_SUPERSONIC] = if g.as_f64() != 0.0 { 1.0 } else { 0.0 },
                Field::OnGround => {}
            }
        }
        h[H_JUMPED] = if o.has_jumped || o.has_double_jumped || o.has_flipped { 1.0 } else { 0.0 };
        h[H_DOUBLE_JUMPED] = if o.has_double_jumped || o.has_flipped { 1.0 } else { 0.0 };
        na.set_car_hidden(i, &h);
    }

    fn set_ball(&mut self, fr: &Frame) {
        if let Some((p, v, w)) = fr.ball {
            self.na().set_ball(&[p[0], p[1], p[2], v[0], v[1], v[2], w[0], w[1], w[2]], false);
        }
    }

    fn set_pads(&mut self, fr: &Frame, force: bool) {
        let Some(pads) = fr.pads.as_ref() else { return };
        if pads.is_empty() {
            return;
        }
        if self.pad_timer != 0 {
            self.pad_mode = self.pad_timer;
        }
        if self.pad_mode == 0 {
            if let Some(prev) = self.prev_pads.as_ref() {
                if prev.len() == pads.len() {
                    for (&(a0, t0), &(a1, t1)) in prev.iter().zip(pads.iter()) {
                        if !a0 && !a1 && t0 > 0.0 && t1 > 0.0 && t1 != t0 {
                            self.pad_mode = if t1 < t0 { 1 } else { 2 };
                            break;
                        }
                    }
                }
            }
        }
        self.prev_pads = Some(pads.clone());
        let mode = if self.pad_mode == 0 { 1 } else { self.pad_mode };
        for (k, &(active, timer)) in pads.iter().enumerate() {
            let j = if k < self.pad_map.len() { self.pad_map[k] } else { k };
            if j >= self.pad_big.len() {
                continue;
            }
            let ln = if self.pad_big[j] { 10.0f64 } else { 4.0 };
            let elapsed = if mode == 2 { timer as f64 } else { (ln - timer as f64).max(0.0) };
            let (taken, _len, el) = self.na.as_ref().unwrap().pad(j);
            if force || taken == active || (taken && (el as f64 - elapsed).abs() > 0.05) {
                self.na().set_pad(j, !active, ln as f32, if !active { elapsed as f32 } else { 0.0 });
            }
        }
    }

    fn probe(&mut self, fr: &Frame) {
        let need: Vec<usize> = (0..self.cars.len())
            .filter(|&i| self.cars[i].probed_frame < 0 && self.na.as_ref().unwrap().car_body(i).is_some()).collect();
        if !self.cars.is_empty() && self.frame >= 0 {
            let na = self.na.as_ref().unwrap();
            if na.ball_body().is_some() {
                if let Some((bp_, bv, _)) = fr.ball {
                    let bp = na.arena_row();
                    let dp = (0..3).map(|k| (bp[k] as f64 - bp_[k] as f64).abs()).fold(f64::NEG_INFINITY, f64::max);
                    let dv = (0..3).map(|k| (bp[3 + k] as f64 - bv[k] as f64).abs()).fold(f64::NEG_INFINITY, f64::max);
                    if dp > self.tol.snap_pos || dv > self.tol.snap_vel {
                        self.set_ball(fr);
                    }
                }
            }
            self.set_pads(fr, false);
        }
        if need.is_empty() {
            return;
        }
        let mut probe = self.na.clone().unwrap();
        let rows: Vec<[f32; 8]> = self.cars.iter().map(|c| f32row(&c.last_input)).collect();
        probe.step(&rows);
        for i in need {
            let Some(ph) = probe.car_hidden(i) else { continue };
            let mut h = self.na.as_ref().unwrap().car_hidden(i).unwrap();
            h[H_WHEEL..H_WHEEL + 4].copy_from_slice(&ph[H_WHEEL..H_WHEEL + 4]);
            h[H_WHEEL_N..H_WHEEL_N + 12].copy_from_slice(&ph[H_WHEEL_N..H_WHEEL_N + 12]);
            h[H_WORLD..H_WORLD + 4].copy_from_slice(&ph[H_WORLD..H_WORLD + 4]);
            h[H_WHEEL_G..H_WHEEL_G + 4].copy_from_slice(&ph[H_WHEEL_G..H_WHEEL_G + 4]);
            let n = ph[H_WHEEL..H_WHEEL + 4].iter().filter(|w| **w > 0.5).count();
            h[H_ON_GROUND] = if n >= 3 { 1.0 } else { 0.0 };
            self.na().set_car_hidden(i, &h);
            self.cars[i].probed_frame = self.frame;
            if h[H_ON_GROUND] > 0.5 && h[H_JUMP_ON] < 0.5 {
                self.cars[i].obs.air_time = 0.0;
            }
        }
    }

    // ------------------------------------------------------------------ reading out

    fn read(&self, na: &Native, cars: &[CarTrack], frame: i32) -> Corrected {
        let mut out = Corrected { frame, horizon: 0, input_delay: self.input_delay(), ..Default::default() };
        let pf = na.physics_frame();
        for (i, c) in cars.iter().enumerate() {
            let mut s = read_car(na, i, &c.obs, pf);
            s.synced = c.synced;
            s.consistent = c.consistent;
            s.ticks_since_cold = c.ticks_since_cold;
            s.drift = c.drift;
            s.assumed = c.assumed;
            out.cars.push(s);
        }
        let r = na.arena_row();
        if let Some(bb) = na.ball_body() {
            let col = |c: usize| [bb[3 + c], bb[6 + c], bb[9 + c]];
            out.ball = Some(([r[0] as f64, r[1] as f64, r[2] as f64], [col(0), col(1), col(2)], [r[3] as f64, r[4] as f64, r[5] as f64],
                             [r[6] as f64, r[7] as f64, r[8] as f64]));
        }
        for j in 0..self.pad_big.len() {
            let (taken, length, el) = na.pad(j);
            out.pads.push((!taken, if taken { (length as f64 - el as f64).max(0.0) } else { 0.0 }, if taken { el as f64 } else { 0.0 },
                           self.pad_big[j]));
        }
        out
    }
}

pub fn read_car(na: &Native, i: usize, obs: &Observer, physics_frame: i32) -> CarOut {
    let row = na.car_row(i);
    let mut s = CarOut { index: i as i32, team: row[1] as i32, is_on_ground: true, ..Default::default() };
    let Some(h) = na.car_hidden(i) else {
        s.is_demoed = true;
        s.demo_respawn_timer = row[28] as f64;
        s.pos = [row[2] as f64, row[3] as f64, row[4] as f64];
        s.is_on_ground = false;
        s.dodge_timeout = -1.0;
        return s;
    };
    let hr = na.hidden_row(i);
    let v3f = |o: usize| [row[o] as f64, row[o + 1] as f64, row[o + 2] as f64];
    s.pos = v3f(2);
    s.vel = v3f(5);
    s.ang_vel = v3f(8);
    for k in 0..3 {
        s.rot[k] = [hr[3 * k] as f64, hr[3 * k + 1] as f64, hr[3 * k + 2] as f64];
    }
    let on = h[H_ON_GROUND] > 0.5;
    s.is_on_ground = on;
    for k in 0..4 {
        let w = WHEEL_RS[k];
        s.wheels[k] = h[H_WHEEL + w] > 0.5;
        s.wheel_normals[k] = [h[H_WHEEL_N + 3 * w], h[H_WHEEL_N + 3 * w + 1], h[H_WHEEL_N + 3 * w + 2]];
        s.wheel_ground[k] = h[H_WHEEL_G + w] as i32;
    }
    if let Some(sus) = na.wheel_suspension(i) {
        for k in 0..4 {
            s.suspension[k] = sus[WHEEL_RS[k]];
        }
    }
    s.has_jumped = obs.has_jumped;
    s.has_double_jumped = obs.has_double_jumped;
    s.has_flipped = obs.has_flipped;
    s.flip_rel_torque = if obs.has_flipped { obs.flip_rel_torque } else { [0.0; 3] };
    s.jump_time = obs.jump_time;
    s.flip_time = obs.flip_time;
    s.is_flipping = obs.is_flipping;
    s.is_jumping = h[H_JUMP_ON] > 0.5;
    s.air_time = obs.air_time;
    s.air_time_since_jump = obs.air_time_since_jump;
    s.boost = h[H_BOOST_AMOUNT] * 100.0;
    let boosting = h[H_BOOST_ON] > 0.5;
    s.is_boosting = boosting;
    s.boosting_time = if boosting { h[H_BOOST_T] } else { 0.0 };
    s.time_since_boosted = if boosting { 0.0 } else { h[H_BOOST_T] };
    s.is_supersonic = h[H_SUPERSONIC] > 0.5;
    s.supersonic_time = obs.supersonic_time;
    s.supersonic_grace = h[H_SS_TIME];
    s.handbrake_val = h[H_HANDBRAKE];
    let af = h[H_AF_ON] > 0.5;
    s.is_auto_flipping = af;
    s.auto_flip_timer = if af { (h[H_AF_DUR] - h[H_AF_T]).max(0.0) } else { 0.0 };
    s.auto_flip_torque_scale = if af { if h[H_AF_RIGHT] > 0.5 { 1.0 } else { -1.0 } } else { 0.0 };
    s.world_contact = h[H_WORLD] > 0.5;
    s.world_contact_normal = [h[H_WORLD_N], h[H_WORLD_N + 1], h[H_WORLD_N + 2]];
    let v = h[H_BUMP_VICTIM];
    s.other_car_index = -1;
    if v >= 0.0 {
        let left = (BUMP_COOLDOWN_TIME - (physics_frame as f64 * DT - h[H_BUMP_T])).max(0.0);
        s.bump_cooldown = left;
        if left > 0.0 {
            s.other_car_id = v as u32 + 1;
            s.other_car_index = v as i32;
        }
    }
    s.ball_hit_valid = h[H_TOUCH_F] > 0.0;
    s.tick_when_hit = h[H_TOUCH_F] as i64;
    s.tick_when_extra_impulse = h[H_IMPACT_F] as i64;
    s.last_controls.copy_from_slice(&h[H_HELD..H_HELD + 8]);
    s.jump_hold_broken = h[H_JUMP_RELEASED] > 0.5;
    s.boost_lock_left = if boosting { (BOOST_MIN_TIME - s.boosting_time).max(0.0) } else { 0.0 };
    s.pitch_lock_left = if s.has_flipped { (FLIP_TORQUE_TIME + FLIP_PITCHLOCK_EXTRA_TIME - s.flip_time).max(0.0) } else { 0.0 };
    s.can_flip = on || (!s.has_flipped && !s.has_double_jumped && s.air_time_since_jump < DOUBLEJUMP_MAX_DELAY);
    s.has_flip_reset = !on && s.can_flip && !s.has_jumped;
    let can = s.has_jumped && !(s.has_double_jumped || s.has_flipped) && !on && h[H_JUMP_ON] < 0.5 && h[H_JUMP_T] < DOUBLEJUMP_MAX_DELAY;
    s.dodge_timeout = if can { (DOUBLEJUMP_MAX_DELAY - h[H_JUMP_T]).max(0.0) } else { -1.0 };
    s
}
