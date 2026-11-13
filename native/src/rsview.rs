//! RocketSim's CarState bookkeeping on top of catSim's game components (the Python reference: rsview.py).
//! RocketSim's constants and update rules are from its Car.cpp / RLConst.h (MIT).

pub const DT: f64 = 1.0 / 120.0;

/// One tick added to a timer the way RocketSim and the game keep it: in f32 (six ticks of 1/120 then reach 0.05,
/// which decides when a jump's `hasJumped` resets; in f64 they fall just short of it).
pub fn tick_add(x: f64) -> f64 {
    ((x as f32) + (DT as f32)) as f64
}
pub const JUMP_MIN_TIME: f64 = 0.025;
pub const JUMP_RESET_TIME_PAD: f64 = 1.0 / 40.0;
pub const DOUBLEJUMP_MAX_DELAY: f64 = 1.25;
pub const FLIP_TORQUE_TIME: f64 = 0.65;
pub const FLIP_PITCHLOCK_EXTRA_TIME: f64 = 0.3;
pub const BOOST_MIN_TIME: f64 = 0.1;
pub const BUMP_COOLDOWN_TIME: f64 = 0.25;
pub const SUPERSONIC_START_SPEED: f64 = 2200.0;
pub const POWERSLIDE_RISE_RATE: f64 = 5.0;
pub const POWERSLIDE_FALL_RATE: f64 = 2.0;

// catSim CarHidden row (CarHidden::to_row order)
pub const H_DRIVING: usize = 0;
pub const H_ON_GROUND: usize = 1;
pub const H_JUMPED: usize = 2;
pub const H_DOUBLE_JUMPED: usize = 3;
pub const H_SUPERSONIC: usize = 4;
pub const H_SS_TIME: usize = 5;
pub const H_JUMP_ON: usize = 6;
pub const H_JUMP_T: usize = 7;
pub const H_JUMP_RELEASED: usize = 8;
pub const H_DJ_ON: usize = 9;
pub const H_DJ_T: usize = 10;
pub const H_DODGE_ON: usize = 11;
pub const H_DODGE_T: usize = 12;
pub const H_DODGE_TORQUE: usize = 13;
pub const H_DODGE_DIR: usize = 16;
pub const H_AF_ON: usize = 19;
pub const H_AF_T: usize = 20;
pub const H_AF_RIGHT: usize = 21;
pub const H_AF_DUR: usize = 22;
pub const H_BOOST_ON: usize = 23;
pub const H_BOOST_T: usize = 24;
pub const H_BOOST_AMOUNT: usize = 25;
pub const H_HANDBRAKE: usize = 28;
pub const H_HELD: usize = 29;
pub const H_WHEEL: usize = 37;
pub const H_WHEEL_N: usize = 41;
pub const H_WORLD: usize = 53;
pub const H_WORLD_N: usize = 54;
pub const H_BUMP_VICTIM: usize = 57;
pub const H_BUMP_T: usize = 58;
pub const H_IMPACT_F: usize = 59;
pub const H_TOUCH_F: usize = 60;
pub const H_WHEEL_G: usize = 61;
/// RocketSim wheel k (FL, FR, BL, BR) is catSim wheel WHEEL_RS[k].
pub const WHEEL_RS: [usize; 4] = [1, 0, 3, 2];

pub const EV_LANDED: u8 = 1;
pub const EV_JUMP: u8 = 2;
pub const EV_DOUBLE_JUMP: u8 = 4;
pub const EV_FLIP: u8 = 8;

#[derive(Clone, Debug, Default)]
pub struct Observer {
    pub is_jumping: bool,
    pub has_jumped: bool,
    pub jump_time: f64,
    pub has_double_jumped: bool,
    pub has_flipped: bool,
    pub is_flipping: bool,
    pub flip_time: f64,
    pub flip_rel_torque: [f64; 3],
    pub air_time: f64,
    pub air_time_since_jump: f64,
    pub supersonic_time: f64,
    pub prev_dodge: bool,
    pub prev_dj: bool,
    pub events: u8,
}

impl Observer {
    pub fn tick(&mut self, h: Option<&[f64]>) {
        let mut ev = 0u8;
        let Some(h) = h else {
            self.events = 0;
            return;
        };
        let on = h[H_ON_GROUND] > 0.5;
        let jumping = h[H_JUMP_ON] > 0.5;
        let dodge = h[H_DODGE_ON] > 0.5;
        let dj = h[H_DJ_ON] > 0.5;
        if on {
            self.is_flipping = false;
        } else if self.is_flipping {
            self.is_flipping = self.has_flipped && self.flip_time < FLIP_TORQUE_TIME;
        }
        if on && !self.is_jumping && !(self.has_jumped && self.jump_time < JUMP_MIN_TIME + JUMP_RESET_TIME_PAD) {
            if self.has_jumped {
                ev |= EV_LANDED;
            }
            self.has_jumped = false;
            self.jump_time = 0.0;
        }
        if jumping && !self.is_jumping {
            self.jump_time = 0.0;
            ev |= EV_JUMP;
        }
        self.is_jumping = jumping;
        if jumping {
            self.has_jumped = true;
        }
        if jumping || self.has_jumped {
            self.jump_time = tick_add(self.jump_time);
        }
        if on {
            if self.has_flipped || self.has_double_jumped || self.air_time > 0.0 {
                ev |= EV_LANDED;
            }
            self.has_double_jumped = false;
            self.has_flipped = false;
            self.air_time = 0.0;
            self.air_time_since_jump = 0.0;
            self.flip_time = 0.0;
        } else {
            self.air_time = tick_add(self.air_time);
            self.air_time_since_jump = if self.has_jumped && !self.is_jumping { tick_add(self.air_time_since_jump) } else { 0.0 };
            if dodge && (!self.prev_dodge || h[H_DODGE_T] <= DT * 1.5) {
                self.flip_time = 0.0;
                self.has_flipped = true;
                self.is_flipping = true;
                let d0 = h[H_DODGE_DIR];
                let d1 = h[H_DODGE_DIR + 1];
                self.flip_rel_torque = [-d1, d0, 0.0];
                ev |= EV_FLIP;
            } else if dj && (!self.prev_dj || h[H_DJ_T] <= DT * 1.5) {
                self.has_double_jumped = true;
                ev |= EV_DOUBLE_JUMP;
            }
        }
        if self.is_flipping || self.has_flipped {
            self.flip_time = tick_add(self.flip_time);
        }
        self.supersonic_time = if h[H_SUPERSONIC] > 0.5 { tick_add(self.supersonic_time) } else { 0.0 };
        self.prev_dodge = dodge;
        self.prev_dj = dj;
        self.events = ev;
    }
}
