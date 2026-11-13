//! One catSim arena, driven through the public `catsim` crate the same way catSim's own Python binding drives it
//! (catsim-public, engine/python/src/lib.rs), so the C library and the Python package give the same numbers.

use catsim::adapter::{self, ArenaSlot};
use catsim::game::arena::{Arena, Phase, RoundConfig};
use catsim::game::car::VehicleInputs;
use catsim::game::config::{body_spawn, preset, Preset};
use catsim::game::hidden::{BodyState, CarHidden, RoundHidden};
use catsim::game::pads::PadLayout;
use catsim::math::v3;
use std::path::{Path, PathBuf};

fn soccar_dir(dir: &Path) -> PathBuf {
    if dir.join("soccar").is_dir() { dir.join("soccar") } else { dir.to_path_buf() }
}

pub fn find_preset(name: &str) -> Option<&'static Preset> {
    if let Some(b) = body_spawn(name) {
        return preset(b.preset);
    }
    let mut c = name.chars();
    let cap = c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default();
    preset(name).or_else(|| preset(&cap))
}

/// Load the dumper's game data (every arena needs it).
pub fn init(data_dir: &Path) -> Result<PathBuf, String> {
    let dir = soccar_dir(data_dir);
    catsim::data::load(&dir).map_err(|e| e.to_string())?;
    catsim::bt::bvh::soccar_meshes(&dir).map_err(|e| e.to_string())?;
    Ok(data_dir.to_path_buf())
}

/// A preset's hitbox: full size and offset, uu.
pub fn preset_hitbox(name: &str) -> Option<([f32; 3], [f32; 3])> {
    let p = find_preset(name)?;
    let f = f32::from_bits;
    Some(([f(p.half[0]) * 100.0, f(p.half[1]) * 100.0, f(p.half[2]) * 100.0], [f(p.offset_xz[0]) * 50.0, 0.0, f(p.offset_xz[1]) * 50.0]))
}

pub fn pad_locations(map: &str) -> Option<Vec<([f32; 3], bool)>> {
    let l = PadLayout::from_map(map)?;
    Some((0..catsim::game::pads::NUM_PADS).map(|i| l.definition(i)).map(|p| ([p.loc.x, p.loc.y, p.loc.z], p.big)).collect())
}

#[derive(Clone)]
pub struct Native {
    pub slot: ArenaSlot,
}

fn input(r: &[f32; 8]) -> VehicleInputs {
    adapter::input_from_row(r)
}

impl Native {
    pub fn new(cars: &[(&str, u8)], data_dir: &Path, map: &str) -> Result<Native, String> {
        let layout = PadLayout::from_map(map).ok_or_else(|| format!("unknown map {map}"))?;
        let dir = soccar_dir(data_dir);
        catsim::data::load(&dir).map_err(|e| e.to_string())?;
        let mut list = Vec::new();
        let mut bodies = Vec::new();
        for (name, team) in cars {
            let p = find_preset(name).ok_or_else(|| format!("no car preset {name}"))?;
            list.push((p, *team));
            bodies.push(body_spawn(name));
        }
        let cfg = RoundConfig { countdown_ticks: 0, post_goal_time: 3.0, match_seconds: None };
        let mut arena = Arena::new_with_layout(&list, &dir, cfg, layout).map_err(|e| e.to_string())?;
        for (i, b) in bodies.iter().enumerate() {
            if let Some(b) = b {
                arena.players[i].body = b;
            }
        }
        let n = list.len();
        let slot = ArenaSlot {
            arena,
            num_cars: n,
            time_since_touch: 0.0,
            episode_time: 0.0,
            applied: vec![VehicleInputs::default(); n],
            delay: vec![0; n],
            queue: vec![Vec::new(); n],
            events: vec![[0.0; adapter::E_DIM]; n],
            goal: -1.0,
        };
        let mut me = Native { slot };
        me.kickoff();
        Ok(me)
    }

    /// As the Python binding's kickoff (round flow off).
    fn kickoff(&mut self) {
        let a = &mut self.slot.arena;
        a.start_at_drop(None);
        let n = self.slot.num_cars;
        for _ in 0..2 {
            a.frame(&vec![VehicleInputs::default(); n], 1);
        }
        a.phase = Phase::Active;
        a.ball_held_2d = false;
        a.scripted = true;
        a.rule_respawns = true;
        for p in &mut a.players {
            if let Some(c) = p.car.as_mut() {
                c.driving = true;
                c.dodge.c.time = 0.0;
            }
        }
        self.slot.time_since_touch = 0.0;
        self.slot.episode_time = 0.0;
        self.slot.goal = -1.0;
    }

    pub fn num_cars(&self) -> usize {
        self.slot.num_cars
    }

    pub fn step(&mut self, rows: &[[f32; 8]]) {
        let inp: Vec<VehicleInputs> = rows.iter().map(input).collect();
        self.slot.step(&inp);
    }

    pub fn physics_frame(&self) -> i32 {
        self.slot.arena.physics_frame
    }

    pub fn car_row(&self, i: usize) -> Vec<f32> {
        let mut r = vec![0.0; adapter::C_DIM];
        self.slot.write_car(i, &mut r);
        r
    }

    pub fn hidden_row(&self, i: usize) -> Vec<f32> {
        let mut r = vec![0.0; adapter::H_DIM];
        self.slot.write_hidden(i, &mut r);
        r
    }

    pub fn arena_row(&self) -> Vec<f32> {
        let mut r = vec![0.0; adapter::A_DIM];
        self.slot.write_arena(&mut r);
        r
    }

    pub fn car_hidden(&self, i: usize) -> Option<Vec<f64>> {
        self.slot.arena.car_hidden(i).map(|h| h.to_row())
    }

    pub fn set_car_hidden(&mut self, i: usize, row: &[f64]) -> bool {
        match CarHidden::from_row(row) {
            Some(h) => self.slot.arena.set_car_hidden(i, &h),
            None => false,
        }
    }

    pub fn car_body(&self, i: usize) -> Option<Vec<f64>> {
        self.slot.arena.car_body(i).map(|b| b.to_row())
    }

    pub fn set_car_body(&mut self, i: usize, row: &[f64]) {
        let Some(b) = BodyState::from_row(row) else { return };
        let a = &mut self.slot.arena;
        if a.players[i].car.is_none() {
            let s = catsim::game::rb_state::RbState { location: v3(b.origin.x * 50.0, b.origin.y * 50.0, b.origin.z * 50.0), ..Default::default() };
            a.set_car_fresh(i, &s, 0.0);
        }
        a.set_car_body(i, &b);
    }

    pub fn ball_body(&self) -> Option<Vec<f64>> {
        self.slot.arena.ball_body().map(|b| b.to_row())
    }

    /// [x, y, z, vx, vy, vz, wx, wy, wz, yaw, pitch, roll (radians), boost 0..100]: the game's own state set.
    pub fn set_car(&mut self, i: usize, row: &[f32; 13]) -> bool {
        if self.slot.arena.players[i].car.is_none() {
            return false;
        }
        self.slot.set_car_row(i, row);
        true
    }

    pub fn set_ball(&mut self, row: &[f32; 9], sleep_at_rest: bool) {
        self.slot.set_ball_row(row, sleep_at_rest);
    }

    pub fn pad(&self, j: usize) -> (bool, f32, f32) {
        let p = &self.slot.arena.pads.pads[j];
        (p.picked_up, p.timer.rate, p.timer.elapsed)
    }

    pub fn set_pad(&mut self, j: usize, taken: bool, rate: f32, elapsed: f32) {
        let p = &mut self.slot.arena.pads.pads[j];
        *p = catsim::game::pads::Pad::cooldown(if taken { rate } else { 0.0 });
        if taken {
            p.timer.elapsed = elapsed;
            p.respawn_left = (rate - elapsed).max(0.0);
        }
    }

    pub fn set_car_demolished(&mut self, i: usize, left: f32) {
        self.slot.arena.set_car_demolished(i, left);
    }

    pub fn set_driving(&mut self, i: usize, on: bool) {
        if let Some(c) = self.slot.arena.players[i].car.as_mut() {
            c.set_driving(on);
        }
    }

    /// The kickoff countdown ended: every car drives, the ball is no longer held; the rest of the round state is kept.
    pub fn release_countdown(&mut self) {
        for p in self.slot.arena.players.iter_mut() {
            if let Some(c) = p.car.as_mut() {
                c.set_driving(true);
            }
        }
        let mut r = self.slot.arena.round_hidden();
        r.ball_held_2d = false;
        self.slot.arena.set_round_hidden(&r);
    }

    pub fn round_score(&self) -> [i32; 2] {
        self.slot.arena.round_hidden().score
    }

    pub fn set_round_hidden(&mut self, physics_frame: i32, phase: i32, phase_left: f32, ball_has_been_hit: bool, ball_held: bool,
                            game_time: f32, score: [i32; 2]) {
        let r = RoundHidden { physics_frame, phase, phase_left, countdown_clock: None, ball_has_been_hit, ball_held_2d: ball_held, game_time, score };
        self.slot.arena.set_round_hidden(&r);
    }

    pub fn dodge_from_input(&self, i: usize, forward: f32, right: f32) -> Option<([f32; 3], [f32; 3])> {
        self.slot.arena.dodge_from_input(i, forward, right).map(|(t, d)| ([t.x, t.y, t.z], [d.x, d.y, d.z]))
    }

    /// Per wheel (catSim's wheel order): suspension length, rest length, travel, radius, uu.
    pub fn wheel_suspension(&self, i: usize) -> Option<[[f32; 4]; 4]> {
        let c = self.slot.arena.players[i].car.as_ref()?;
        let mut out = [[0.0f32; 4]; 4];
        for (w, o) in out.iter_mut().enumerate() {
            let x = &c.vehicle.wheels[w];
            *o = [x.suspension_length * 50.0, x.rest * 50.0, x.travel * 50.0, x.radius * 50.0];
        }
        Some(out)
    }
}
