//! A Rust bot uses the crate directly (no C layer): `cargo run --release --example rust_bot -- <catsim_data> <trace.txt>`.
//! Same packet trace and checks as examples/correct_trace.cpp.
use catsim_correct::core::{CarIn, Corrector, Frame, Phase};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut fix = Corrector::new(&PathBuf::from(&args[1]), vec![0], 0, None).expect("catSim data");
    let file = BufReader::new(std::fs::File::open(&args[2]).expect("trace"));
    let (mut frame, mut packets, mut checked, mut right) = (None::<Frame>, 0, 0, [0usize; 4]);
    let mut predicted: std::collections::BTreeMap<i32, catsim_correct::core::CarOut> = Default::default();
    for line in file.lines().skip(1) {
        let line = line.unwrap();
        let mut w = line.split_whitespace();
        let tag = w.next().unwrap_or("");
        let v: Vec<f64> = w.map(|x| x.parse().unwrap()).collect();
        let f32s = |a: &[f64]| [a[0] as f32, a[1] as f32, a[2] as f32];
        match tag {
            "F" => frame = Some(Frame { frame: v[0] as i32, cars: vec![], ball: None, pads: None, phase: Phase::from_rlbot(v[1] as i32), pad_locations: None }),
            "C" => {
                let t = |x: f64| if x < 0.0 { None } else { Some(x != 0.0) };
                let mut li = [0f64; 8];
                for k in 0..8 { li[k] = v[31 + k] as f32 as f64; }
                frame.as_mut().unwrap().cars.push(CarIn {
                    team: v[0] as u8, pos: f32s(&v[1..]), vel: f32s(&v[4..]), ang_vel: f32s(&v[7..]), rot: f32s(&v[10..]), boost: v[13] as f32,
                    hitbox: f32s(&v[14..]), hitbox_offset: f32s(&v[17..]), demolished: v[20] != 0.0, demo_left: v[21] as f32, supersonic: v[22] != 0.0,
                    has_jumped: t(v[23]), has_double_jumped: t(v[24]), has_dodged: t(v[25]), air_state: Some(v[26] as i32), on_ground: None,
                    dodge_timeout: Some(v[27] as f32), dodge_elapsed: Some(v[28] as f32), dodge_dir: Some([v[29] as f32, v[30] as f32]),
                    last_input: Some(li),
                });
            }
            "B" if v[0] != 0.0 => frame.as_mut().unwrap().ball = Some((f32s(&v[1..]), f32s(&v[4..]), f32s(&v[7..]))),
            "P" => {
                let f = frame.as_mut().unwrap();
                f.pads = Some((0..v[0] as usize).map(|k| (v[1 + 2 * k] != 0.0, v[2 + 2 * k] as f32)).collect());
                fix.update(f).unwrap();                       // the bot's per-packet call, before its policy
                let s = fix.predict(None).unwrap();
                predicted.insert(s.frame, s.cars[0].clone());
                packets += 1;
            }
            "T" if v[0] == 0.0 && packets >= 120 => {
                let f = frame.as_ref().unwrap().frame;
                if let Some(e) = predicted.remove(&f) {
                    checked += 1;
                    right[0] += (e.is_on_ground == (v[1] != 0.0)) as usize;
                    right[1] += (e.has_flipped == (v[5] != 0.0)) as usize;
                    right[2] += ((e.jump_time - v[11]).abs() <= 1e-3) as usize;
                    right[3] += ((e.air_time_since_jump - v[14]).abs() <= 1e-3) as usize;
                }
            }
            "S" => {
                let mut c = [0f64; 8];
                for k in 0..8 { c[k] = v[k] as f32 as f64; }
                let f = frame.as_ref().unwrap().frame;
                fix.sent(c, Some(f));
            }
            _ => {}
        }
    }
    let pct = |n: usize| 100.0 * n as f64 / checked.max(1) as f64;
    println!("rust: {packets} packets, measured delay {}, horizon {}; own car on {checked} frames: isOnGround {:.2}%  hasFlipped {:.2}%  jumpTime {:.2}%  airTimeSinceJump {:.2}%",
             fix.input_delay(), fix.horizon(), pct(right[0]), pct(right[1]), pct(right[2]), pct(right[3]));
}
