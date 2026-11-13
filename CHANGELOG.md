# Changelog

## 0.1.4

- **`dump_if_missing=True` never blocks the bot any more.** In 0.1.3 the dump ran inside the bot's start-up, so a bot
  on a fresh PC froze for minutes and could miss RLBot's launch timeout. Now the dump starts in the background (a
  separate, low-priority process, output in `catsim_data.dump.log`) and the corrector runs in **plain mode** until it
  is done: `correct(packet)` returns the packet's own state in the same types and field names (`state.plain` is
  `True`; the README lists what each field holds), and `sent()` works as usual. When catSim's `--check` passes,
  correction switches itself on at the next kickoff countdown, goal or new match, never mid-play. If the dump fails,
  the bot stays on plain inputs for the session and says why; nothing raises. `fix.mode` is `"corrected"`,
  `"dumping"` or `"plain"`, and `fix.dump_error` holds the reason. There are no threads: the dump is looked at on
  each packet. Several bots on one PC share one dump (a lock file next to the folder).
- **Setup command:** `python -m catsim_better_inputs.setup --keys keys.txt [--out catsim_data] [--game DIR]` makes the
  data before any match: it checks the folder, dumps only if needed with the dumper's progress shown, and prints the
  check of the result (`catsim-better-inputs-setup` does the same when the package is pip-installed). The README now
  recommends this first.
- **Clearer error without data:** without `dump_if_missing`, a missing or broken data folder still raises at
  construction, now a `FileNotFoundError` that gives the exact setup command for that folder.
- New in the API: `Corrector(..., game=, log=)`, `Corrector.mode`, `Corrector.dump_error`, `Corrected.plain`,
  `check_data()`, `DumpJob`; `ensure_data()` takes `echo=`.
- README: make the data at setup time, then ship your dump or ship the key file with `dump_if_missing` as a safety
  net. C, C++, C#, Java and Rust bots (the native library, which has no plain mode) should make the data at setup.
- Tests: `tests/test_plain_mode.py` (plain state from RLBot v5 packets, the switch only between plays and identical to
  a fresh start, a failed dump staying plain, the dump as a subprocess, one dump per folder, the setup command).

## 0.1.3

- **Dump on first run:** `ensure_data(data_dir, keys=...)` and `Corrector(..., dump_if_missing=True, keys=...)` make `catsim_data/` from the machine's own Rocket League install when it is missing, so a bot can ship only the key file. README: the two ways to run a bot on another machine.

## 0.1.2

First release checked in real Rocket League matches (RLBot v5, 120 fps): every packet recorded, the packet of the
predicted frame taken as the truth. With the fixes below, the real 1v1 changed like this from 0.1.1:
- Your car's predicted position 1 tick ahead: mean error from 0.13 uu to 0.0017 uu.
- RLBot flags counted wrong: from 2,960 to 115. Most of the old ones were the corrector's own model, not RLBot.

The fixes:

- **Kickoff countdown.** A start during the countdown turned every car's input off, and nothing turned it on again at
  the release, so after each kickoff the tracker was snapped to the packets on most ticks instead of following them.
  The corrector now releases the cars and the ball when the countdown ends.
- **Car bodies.** Real packets carry the preset's own hitbox size. The matcher expected it 2.5 uu smaller and took a
  Dominus for a Breakout's body. It now matches size and offset in either convention.
- **Inputs as the game applies them.** The game stores each analog input in one byte: k/127 at or above zero, k/128
  below. Your sent controls are quantized the same way (`game_controls`) before they step your car.
- **Timers in f32**, as RocketSim and the game keep them. In f64, six ticks fall just short of 0.05 s, which kept
  `hasJumped` one tick too long after a short jump tap.
- **RLBot's flags as RLBot really computes them.** `dodge_timeout` is the game's jump clock: it counts from the jump's
  end on the ground too, and is -1 while the jump is on. `is_supersonic` is the speed test alone (2200 uu/s), not the
  game's flag with its grace band. The corrected `isSupersonic` keeps the game's meaning; only the comparison changed, so
  these are no longer counted as wrong packets. `CarState.dodgeTimeout` uses the game's clock.
- **C++: RLBotCPP packets.** `csc::frame_from_rlbot_v5` also takes the `const rlbot::flat::GamePacket*` that the RLBot v5
  C++ interface hands a bot. Before, it only compiled with the object API (`GamePacketT`). New
  `csc::set_pad_locations(fix, field_info)` (RLBot's pad order isn't catSim's).
- **No state between a goal and the next kickoff.** `correct` and `predict` return `None` there. The C functions return
  1 with `num_cars = 0`, where 0.1.1 returned -1 and the C++, C# and Java wrappers threw. catSim removes the ball on
  the goal's own tick, so `state.ball` can be `None` on that tick, and the example checks for it. In 0.1.1 the 3v3
  example raised on every packet of a goal (361 packets).
- **Docs.** RLBot v5's Python package installs with `pip install --pre rlbot`, since plain `pip install rlbot` gets v4's
  1.x. There is now `examples/bot.toml` for running the example. The real-game results are in the README. Call
  `correct` on every packet, even if your policy acts only every few ticks.
- Tests: the offline packets follow the real conventions found: preset hitbox, velocities to 0.001, angular
  velocities to 0.00001, quantized inputs, the game's dodge clock and the speed-only supersonic flag. There is a new
  kickoff-countdown test and a countdown case in the C/Python parity test.

## 0.1.1

- Python reference, C library (Rust over public catSim 0.1.3), C header, header-only C++ wrapper, Rust / C# / Java
  examples; offline tests against catSim truth.
