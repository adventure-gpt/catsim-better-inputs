# catSim input correction for RLBot

**An in-process library.** Your bot calls it inside its own per-packet function (`get_output` in Python, your tick
callback in C++), on the packet RLBot just handed you, before your policy runs. It is not an RLBot script: there is no
separate process and no IPC. Python, C, C++, Rust, C# and Java bots can all use it; they share one algorithm and
give identical results.

It gives your policy a better input than the raw packet:

- **Flags RLBot gets wrong are caught and replaced.** Every packet is checked against physics. catSim, a bit-exact
  Rocket League simulator, steps the last corrected state forward with the inputs that were really applied. When a
  reported flag or timer contradicts what physics says, the packet is counted wrong and the physics value is used.
  This works at 0 action delay too. In real matches (below, "Real games") the one systematic error was the single
  air-state enum at a jump's start, on 0.3-0.5% of car-frames. The other flags agreed with physics on all but a
  handful of frames.
- **The state is lag-corrected.** It is predicted forward to the moment your next action will actually take effect,
  less the action delay your policy was trained with. The delay is measured from the packets.
- **Every car comes as separate fields with RocketSim's names**, never RLBot's single air-state enum: `isOnGround`
  *and* `isJumping`, per-wheel contact, jump, flip and air timers, the boost lock, the pitch lock, the bump cooldown,
  per-wheel suspension (C library). GigaLearn, RocketSim, RocketGPU and RLGym observation code reads it unchanged.

```python
from catsim_better_inputs import Corrector

fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0)   # in initialize()
state = fix.correct(packet)      # in get_output: replaces the raw packet
controls = my_policy(state)      # state.cars[i].isOnGround, .wheelsWithContact, .isJumping, .jumpTime, ...
fix.sent(controls)
```

## What it fixes, and why

| Problem | What you see in RLBot | What the corrected state gives |
|---|---|---|
| A jump starts with the wheels still down | `air_state = Jumping`: the single enum overwrites `OnGround` | `isOnGround = true` **and** `isJumping = true`, `jumpTime` counting |
| A flag is sometimes simply wrong (ground, has jumped, has flipped, supersonic, stuck dodge clocks) | the bad value, with nothing saying so | the physics value, with the disagreement counted per field (and logged, if you ask) |
| Your action lands 1-3 ticks late | the state from before your input | the state at the tick your input applies (minus your training delay) |
| Timers the packet never carries | nothing | `airTime`, `airTimeSinceJump`, `flipTime`, `boostingTime`, `handbrakeVal`, `supersonicTime`, the boost lock, the pitch lock, the bump cooldown, wheel contacts |

## How it works

1. A tracking arena (catSim) follows the match. Each packet, it steps from the previous packet's frame to this one
   using the inputs that were really applied: the packet's `last_input` for every car on its own frame, and your
   own sent controls for any ticks in between.
2. Each body is compared with the packet. Within the packet's rounding (positions to 0.01 uu, the integer rotation)
   the tracked body is kept, since it is exact and the packet is not. Beyond that it is snapped to the packet (drift
   correction).
3. Each flag and timer the packet reports is compared with what physics says the packet *should* report:
   - The fact behind the flag was set by an event the tracker saw (a landing, a jump from the ground, a flip, a double
     jump, a clear speed) and the car moved as physics says: physics wins, and the packet is counted wrong.
   - The car moved unexpectedly (a bump, an input RLBot never showed): the last tick is replayed with the packet's
     flags, and they are taken only if they explain the motion better.
   - The fact is still an assumption (right after a start, before the car has landed): every packet since votes.
     Timers vote by the event frame they imply, so a stuck clock is outvoted by the ones that kept counting.
   - The ground is never taken from a packet. It comes from the wheels' raycasts.
4. After a start or a large snap, a one-tick probe on a copy finds each wheel's contact at the packet's pose.
5. `predict` copies the tracking arena and steps it ahead. Your car uses your buffered sent controls, which is exact.
   Every other car uses an estimate: by default it holds its last reported input, and you can plug in your own.
   Every car is then read out in RocketSim's meanings, through a per-tick observer that runs RocketSim's own
   bookkeeping rules on catSim's game components.

## Install

**1. catSim's game data** (once per PC, at setup time). catSim ships no game data. You make it from your own copy of
Rocket League with a key file (see catSim's README: https://github.com/adventure-gpt/catsim-public). One command:

```
pip install "catsim-better-inputs[dump]"                         # this package + catSim with its dumper
python -m catsim_better_inputs.setup --keys path/to/keys.txt     # writes ./catsim_data; a few minutes
```

Run it in your bot's folder, with the Python your bot uses. It checks the
folder first and stops there if the data is already good, so running it again is harmless. Otherwise it makes the
data, printing the dumper's progress, and ends with catSim's own check of the result (exit code 0 when the folder is
ready). `--out DIR` writes somewhere else; `--game DIR` points at Rocket League if it isn't found from Steam or Epic.
(`catsim-dump --out catsim_data --keys keys.txt` is the same dump without the checks around it.) The dumper only reads
files: it never starts the game and never touches a running one. Nothing in this package contains game data.

**Make the data before matches, not during them.** It takes a few minutes, which is far too long for a bot's start-up.

**Running your bot on another machine** (a tournament host, a friend's PC) - pick one:

- **Ship your dump** (simplest): put your `catsim_data/` inside your bot's folder, the same way RLBot bots already
  ship RocketSim's `collision_meshes/`. Nothing has to run on the host.
- **Ship the key file, with a safety net:** ask the host to run the setup command once. If they don't, the bot still
  starts at once:

  ```python
  fix = Corrector(index=self.index, data_dir="catsim_data", dump_if_missing=True, keys="keys.txt")
  ```

  If `catsim_data/` is missing, this starts the dump in the background (a separate, low-priority process; its output
  goes to `catsim_data.dump.log`) and the bot plays on **plain inputs** for the first few minutes (see "Plain inputs"
  below). Once the data is ready, correction switches itself on at the next kickoff. The bot's Python needs the
  dumper: `pip install "catsim-better-inputs[dump]"`.

Without the data and without `dump_if_missing`, creating the `Corrector` raises `FileNotFoundError`. Its message
gives the exact setup command for that folder.

**C, C++, C#, Java and Rust bots** (the native library): make the data at setup time, or ship your dump. The dumper
needs Python once on that PC, not at match time: `pip install "catsim-rl[dump]"`, then `catsim-dump --out catsim_data
--keys keys.txt`. The native library has no plain mode and doesn't run the dumper. Without the data, `csc_new` returns
NULL with the reason in `err` (the C++ wrapper throws it), so catch that and play on the raw packet.

**2a. Python:** it's on PyPI - `pip install catsim-better-inputs`, or put `catsim-better-inputs` in your bot's
requirements. If you'd rather vendor it, copying the `catsim_better_inputs/` folder into your bot works the same.
It depends only on `catsim` (installed with it) and, for reading RLBot packets, `rlbot`. numpy, rlgym and rlgym-compat are needed only by the adapters that use them.
RLBot v5's Python package is the 2.0 pre-release, so install it with `--pre`. A plain `pip install rlbot` gets v4's
1.x:

```
pip install --pre rlbot                                         # RLBot v5 (rlbot 2.x)
```

**2b. C / C++ / C# / Java:** build the library (Rust 1.95, `rustup` installs it from `rust-toolchain.toml`):

```
cd native
cargo build --release          # target/release/catsim_correct.dll (.so on Linux), also a static lib
```

It depends on the public catSim engine, the `catsim` crate at tag v0.1.3 (the same engine as the PyPI package).
Then include `include/catsim_correct.h` (C) or `include/catsim_correct.hpp` (header-only C++), and ship the DLL
next to your bot. **Rust:** depend on the crate (`catsim_correct = { path = "native" }`) and call
`catsim_correct::core::Corrector`; no C layer is involved.

## Use

Each adapter in about five lines. Every one first calls `correct` (or `csc_correct`), then runs your policy on the
result, then calls `sent` with what you return.

**RLBot v5, Python** (`examples/example_bot_v5.py`, with `examples/bot.toml`):
```python
def initialize(self):
    self.fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0, field_info=self.field_info)
def get_output(self, packet):
    state = self.fix.correct(packet)                 # replaces the raw packet
    controls = self.policy(state.cars[self.index], state.ball, state)
    self.fix.sent(controls); return controls
```
To run the example, put `example_bot_v5.py`, `bot.toml`, the `catsim_better_inputs/` folder and your `catsim_data/`
in one folder, set `run_command` in `bot.toml` to your Python, and add the folder in RLBot. RLBot starts the bot in
that folder, so `data_dir="catsim_data"` finds the data. At the end of a match the bot prints how often each RLBot
flag disagreed with physics.

**Call `correct` (or `update`) on every packet,** even if your policy decides only every few ticks. Between two packets
the tracker steps every car with the input that packet shows. If you feed it only every 8th packet, it has to guess the
other cars' inputs for the 7 ticks between. Predicting is the cheap part: `update` on every packet, `predict()` when
you decide. **Hiveminds:** `Corrector(index=[i, j, k])`. Give the first car's controls to `sent(controls, frame)`, which
also drives the lag meter, and the others' to `record_sent(controls, frame + fix.input_delay, index=j)`.

**RLBot v4, Python** (`examples/example_bot_v4.py`): the same calls with a `GameTickPacket`. v4 has no
`last_input`, so pass `input_delay=` yourself.

**RLGym 2.x / rlgym-ppo** (`examples/example_rlgym_v2.py`):
```python
from catsim_better_inputs.outputs import to_rlgym_v2
gs = to_rlgym_v2(self.fix.correct(packet))          # rlgym.rocket_league.api.GameState
obs = self.obs_builder.build_obs([self.index], gs, {})[self.index]
```
In the tests, RLGym's own `DefaultObs` gives the truth's observation bit for bit from the corrected state when
tracking started exact, and within 1e-3 from packets alone.

**RLGym 1.x / rlgym-compat v1 / rlgym-sim:** `to_rlgym_v1(state)` builds a `GameState` with `players`
(`PlayerData`: `on_ground`, `has_jump`, `has_flip`, `boost_amount`, `car_data`, `inverted_car_data`), `ball` and
`boost_pads`.

**Plain data:** `to_numpy(state)` gives float32 `[n_cars, 61]` (columns in `outputs.CAR_COLUMNS`), the ball and
the pads. `to_dict(state)` is JSON-able. `to_rocketsim_v3(car)` uses RocketSim v3's snake_case names (the field set
of the RocketGPU full-state suite).

**C++ (GigaLearn, RocketSim-based bots)**, in an RLBot v5 C++ bot (RLBotCPP, `rlbot::Bot`):
```cpp
#include "catsim_correct.hpp"
void MyBot::initialize(const rlbot::flat::ControllableTeamInfo* t, const rlbot::flat::FieldInfo* fi,
                       const rlbot::flat::MatchConfiguration* m) noexcept {
    rlbot::Bot::initialize(t, fi, m);
    fix = std::make_unique<csc::Corrector>("catsim_data", index, /*training_action_delay=*/0);
    csc::set_pad_locations(*fix, fi);                                      // RLBot's pad order isn't catSim's
}
void MyBot::update(const rlbot::flat::GamePacket* packet, const rlbot::flat::BallPrediction*) noexcept {
    const CscCorrected& s = fix->correct(csc::frame_from_rlbot_v5(packet));
    RocketSim::CarState me;  csc::to_rocketsim(s.cars[index], me);         // your obs builder, unchanged
    // ... your policy ...
    fix->sent(csc::controls(throttle, steer, pitch, yaw, roll, jump, boost, handbrake));
}
```
`frame_from_rlbot_v5` takes the `const rlbot::flat::GamePacket*` RLBotCPP passes to `update`, or a flatbuffers
object-API `rlbot::flat::GamePacketT`. Both give the same frame. `CscCarState` itself already has RocketSim's field
names (`s.cars[i].isOnGround`, `.wheelsWithContact[k]`, ...). `to_rocketsim` fills any RocketSim version's `CarState`
(checked against RocketSim's own header) and skips fields a version lacks (C++20). Link `catsim_correct.dll.lib`
(MSVC) or the DLL itself (MinGW: `g++ ... catsim_correct.dll`), and ship the DLL next to your bot.
`native/examples/correct_trace.cpp` runs the same calls over a packet trace.

**C:** `csc_new`, `csc_correct`, `csc_sent`, `csc_free` (see `catsim_correct.h`).
**Rust:** `native/examples/rust_bot.rs`. **C#:** `native/examples/csharp/` (P/Invoke).
**Java 22+:** `native/examples/java/CatsimCorrect.java` (the Foreign Function & Memory API; no JNI, no jars).
Each of these examples runs the same packet trace and prints the same checks.

### Bots trained with 0 action delay

Your policy learned to act on state *t* with its action applying on tick *t+1*. In RLBot the action computed on
the packet for frame *F* first applies on frame *F + D*, where *D* is the input delay (1 = no lag). So the
corrector predicts

    horizon = max(0, D - 1 - training_action_delay)

ticks past the packet: the situation your action will actually meet, as it would have in training. *D* is measured
from the packets: your sent controls change, and it counts how many frames until `last_input` shows the change. A
0-delay policy just passes `training_action_delay=0`. A policy trained with a 1-tick action delay passes 1, which
predicts one tick less. Measured error at the predicted frame, with D = 3 (tests below):

| | horizon | your car, position | opponents, position (mean / p99) |
|---|---|---|---|
| 1v1, trained with 0 delay | 2 ticks | 0.0010 uu | 0.056 / 2.4 uu |
| 1v1, trained with 1 delay | 1 tick | 0.0009 uu | 0.018 / 0.11 uu |
| 3v3, trained with 0 delay | 2 ticks | 0.0008 uu | 0.076 / 2.5 uu |
| 3v3, trained with 1 delay | 1 tick | 0.0007 uu | 0.026 / 0.24 uu |

Your own car is exact because its inputs are known. Opponents' error comes only from inputs they change inside the
horizon.

### Plain inputs (while catSim's data is being made)

With `dump_if_missing=True` and no data yet, the bot starts at once and `fix.mode` says where things stand:

| `fix.mode` | What `correct(packet)` returns |
|---|---|
| `"corrected"` | the corrected state (the normal case) |
| `"dumping"` | plain inputs: the data is being made, and correction will switch itself on |
| `"plain"` | plain inputs for the rest of this session: the dump failed, and `fix.dump_error` says why |

**Plain inputs** are the packet's own state in the same types and field names, so your policy code doesn't change.
Nothing is lag-corrected (`horizon` is 0) and no flag is checked. `state.plain` is `True`, and every car's
`confidence["plain"]` is `True` with `synced` and `consistent` `False`.

- **As RLBot reports them:** `pos`, `vel`, `angVel`, `rotMat` (from the rotator), `boost`, `isSupersonic`,
  `hasJumped`, `hasDoubleJumped`, `hasFlipped`, `dodgeTimeout`, `isDemoed`, `demoRespawnTimer`, `lastControls`, the
  ball's `pos`, `vel` and `angVel`, and the pads (in the same order as corrected).
- **From RLBot's single air-state enum:** `isOnGround` and `isJumping`, with all four `wheelsWithContact` equal to
  `isOnGround`. So on a jump's first ticks `isOnGround` is `False`: the very thing correction fixes.
- **From RLBot's dodge clocks** (RLBot v5): `flipTime`, `isFlipping`, `flipRelTorque`, `pitchLockLeft` and
  `airTimeSinceJump` (with `jumpTime` as `airTimeSinceJump + 0.1`), so `canFlip` and `hasFlipReset` work.
- **Stand-ins for what packets never carry:** `airTime`, `boostingTime`, `timeSinceBoosted`, `supersonicTime`,
  `boostLockLeft` and the auto-flip fields are 0. `isBoosting` and `handbrakeVal` follow the boost and handbrake
  buttons in `last_input`, and `jumpHoldBroken` is "jump isn't held". `worldContact`, `carContact` and `ballHitInfo`
  are empty, the ball's `rotMat` is the identity, and on the ground the wheel normals are the car's up axis.

`sent(controls)` works as usual. The lag meter keeps measuring, so the delay is already known when correction
starts. `predict()` returns the last plain state, and `seed_exact()` raises.

**When correction switches on.** When the dumper finishes, catSim's own `--check` looks at the folder. If the data
is good, correction starts on the next packet between plays: a kickoff countdown, a goal or replay, or a new match.
Never mid-play, so your bot never sees its inputs change under it in a challenge. From that packet on it tracks
exactly as a corrector created there would, except that it has already measured the delay. One line is printed
then. (Pass `log=` for your own logger, e.g. `log=self.logger.info`. The start of the dump and a failure print one
line each too.)

**If the dump fails** (no key file, Rocket League not found, a game version the dumper doesn't know), the bot keeps
playing on plain inputs for the rest of the session. One line says why and gives the setup command that fixes it.
Nothing raises.

**Under the hood.** The dump is a separate process at below-normal priority, so your bot's process isn't slowed and
no window pops up over the game. There is no thread in your bot: the corrector looks at the dump on each packet.
Several bots on one PC (a 2v2 of your bot, a mirror match) make the data only once: the first takes a lock
(`catsim_data.dump.lock` next to the folder), the others wait for its dump and switch on when it's done. If a bot
closes mid-dump, the next start picks up from there: it waits for a dumper that is still running, or starts again.

## The corrected state

Each car, under RocketSim's `CarState` names, in RocketSim's meanings (see the differences below). The measured
columns are from the offline tests: tracked from packets alone (cold start, D = 3, 2-tick prediction), against the
truth, counting a timer as right within 1 ms.

**Source:** *packet*: taken from the packet (and checked). *exact*: catSim physics, exact when the tracking is in
step (your car always; opponents up to their unknown future inputs). *after event*: exact once the car has landed or
been seen doing the thing; an estimate before that. *estimate*: not knowable from packets.

| Field | Source | Your car, 3v3 | Opponents, 3v3 | Set from each packet alone (no tracking) |
|---|---|---|---|---|
| `pos`, `vel`, `angVel`, `rotMat` | packet, kept exact while in step | 0.0008 uu, 0.006 uu/s | 0.076 uu | 0.025 uu |
| `isOnGround` | exact (wheel raycasts), never the enum | 100% | 100% | 100% |
| `wheelsWithContact[4]` | exact | 100% | 100% | 99.93% |
| `isJumping` | exact | 100% | 98.5% | 100% |
| `hasJumped`, `hasDoubleJumped`, `hasFlipped` | after event | 100% | 99.3-99.9% | 100% |
| `isFlipping`, `flipTime`, `flipRelTorque` | after event | 100% | 99.5% | 99.8-100% |
| `jumpTime` | after event | 100% | 97.9% | 34.9% |
| `airTime` | after event | 100% | 98.6% | 33.8% |
| `airTimeSinceJump` | after event | 100% | 97.9% | 38.7% |
| `boost` | exact (the packet rounds it) | 100% | 99.6% | 14.9% |
| `isBoosting`, `boostingTime`, `boostLockLeft` (min boost time) | exact | 100% | 99.3% | 87.3% |
| `timeSinceBoosted` | estimate until the next boost press | 70% | 45% | 10% |
| `isSupersonic`, `supersonicTime`, `supersonicGrace` | exact | 100% | 100% | 100% |
| `handbrakeVal` (the ramp) | exact | 100% | 98.5% | 100% |
| `isAutoFlipping`, `autoFlipTimer`, `autoFlipTorqueScale` | exact | 100% | 100% | 100% |
| `worldContact` | exact | 100% | 99.98% | 99.4% |
| `carContact` (`otherCarID`, `cooldownTimer`: the bump cooldown) | exact | 100% | 100% | 100% |
| `pitchLockLeft` (flip pitch lock) | after event | 100% | 99.5% | 100% |
| `jumpHoldBroken`, `canFlip`, `hasFlipReset`, `dodgeTimeout` | after event | 100% | 98.5-99.4% | 98.8-100% |
| `isDemoed`, `demoRespawnTimer` | packet | 100% | 100% | 100% |
| `lastControls` | packet / your sent controls | 100% | held input (62% at 2 ticks) | 99% |
| `ballHitInfo` | tick counts only: `relativePosOnBall`, `ballPos`, `extraHitVel` are zero (not in catSim's public state) | | | |
| `wheelSuspensionLength/Rest/Travel`, `wheelRadius` | exact (C library only) | | | |
| ball `pos`, `vel`, `angVel` | packet, kept exact while in step | 0.002 uu | | 0.01 uu |
| pads `isActive`, `cooldown`, `timer` | packet + physics | | | |

Every car also carries confidence: `synced` (in step to the packet's rounding), `consistent` (moved as physics says,
so its physics flags are trusted), ticks since its last start, drift, the facts still assumed, and whether its
inputs over the predicted ticks were known. The "set from each packet" column is the usual approach of loading every
packet into a simulator fresh. It loses every timer the packet doesn't carry.

### Differences from RocketSim and RocketGPU

- **Physics.** catSim follows the game bit for bit; RocketSim and RocketGPU approximate it. The corrected state is
  the game's truth, which is a little different from what an approximate simulator would have produced. That is the
  point: it's what your bot is really facing.
- **`hasJumped`.** RocketSim's meaning (jumped to get into the air; false after a flip reset). The game's own flag
  (catSim's `jumped`) is also set by dodges.
- **`hasDoubleJumped`.** RocketSim's meaning: flips do not count. The game's flag counts both.
- **`jumpTime`.** RocketSim's meaning: counts from the jump's start while `hasJumped`. RLGym 2's docs describe its
  `jump_time` as clamped to 0.2; the value is passed through unclamped.
- **`flipRelTorque`.** RocketSim's (-right, forward, 0) direction, not the game's whole-number dodge torque (that is in
  `catsimHidden`).
- **`supersonicTime`.** RocketSim's meaning: time since becoming supersonic. The game's grace timer (seconds below
  supersonic speed still counted, RocketSim v3's `supersonic_grace_timer`) is `supersonicGrace`.
- **`wheelsWithContact`.** RocketSim's order: FL, FR, BL, BR. catSim's internal order swaps left and right; it is
  converted for you.
- **`isOnGround`.** The game's flag, which is RocketSim's "3 or more wheels". It reflects the raycasts made at the
  start of the tick, the same convention as RocketSim's `CarState` after a step.
- **`tickCountSinceUpdate`.** Ticks since this car's last start in the tracker.
- **RocketSim v3 / RocketGPU names** (`to_rocketsim_v3`). `jump_ticks` counts ticks, not seconds;
  `supersonic_grace_timer` is the grace above; `bump_victim` is a packet index.
- **Older RocketSim** (`timeSpentBoosting`). Filled from `boostingTime`.

## Flag-error detection

Measured offline at **0 action delay** (D = 1, no look-ahead), with realistic wrong flags injected into RLBot v5
packets. Each kind starts on a car with 2% probability per packet and then lasts a run of packets:
- the air state reports OnGround while in the air, or InAir while on the ground (1-6 packets);
- `has_dodged` drops to false after a flip (5-60);
- `has_jumped` drops to false (5-60);
- `dodge_timeout` and `dodge_elapsed` stick at an old value (5-30);
- `is_supersonic` flips (1-10).

The jumping-overwrites-ground behaviour is always on, as RLBot does. 1500 packets per match.

After the first 2 s (once the tracker has seen the cars land):

| Packet field | 1v1 injected | detected | kept out of the output | 3v3 injected | detected | kept out | false alarms |
|---|---|---|---|---|---|---|---|
| `air_state` | 155 | 100% | 100% | 535 | 94.4% | 100% | 0 |
| `has_jumped` | 772 | 100% | 100% | 1633 | 92.7% | 100% | 0 |
| `has_dodged` | 308 | 100% | 100% | 916 | 100% | 100% | 0 |
| `dodge_timeout` | 108 | 100% | 100% | 297 | 100% | 100% | 0 |
| `dodge_elapsed` | 275 | 100% | 100% | 380 | 100% | 100% | 0 |
| `is_supersonic` | 290 | 100% | 100% | 686 | 100% | 100% | 0 |

*Detected* means the packet was flagged wrong because physics knew better. *Kept out* adds the cases still under
an assumption where the vote held the right value. No wrong flag reached the output, and no right flag was flagged.
Over the whole match, including the first 2 s, 94.8-100% are kept out (`has_jumped` 94.8% in 1v1 and 96.4% in 3v3;
every other field 99.8-100%). The rest are faults present in the very first packets, before anything could be known.

The output's accuracy under those faults, against trusting the packet's flags (all cars, no look-ahead):

| | `hasJumped` | `hasFlipped` | `jumpTime` | `airTimeSinceJump` | `isSupersonic` | `dodgeTimeout` |
|---|---|---|---|---|---|---|
| 1v1 with detection | 100% | 100% | 100% | 100% | 100% | 100% |
| 1v1 trusting the packet | 70.3% | 88.8% | 44.3% | 48.4% | 88.4% | 89.2% |
| 3v3 with detection | 98.5% | 100% | 94.5% | 95.0% | 100% | 100% |
| 3v3 trusting the packet | 78.7% | 88.4% | 53.9% | 53.9% | 90.6% | 87.3% |

**The jump-on-ground case** (`tests/run_all.py`, `jump_on_ground`): a jump pressed with all four wheels down. For its
first 6 ticks the packet's air state says Jumping, so the raw packet says "not on the ground". The corrected state
says `isOnGround = true`, all four `wheelsWithContact`, and `isJumping = true`, matching catSim's truth on every tick.
Which way your RLBot orders the enum (jumping first or ground first) is learned from the packets. Both were tested:
the order was learned and no flags were miscounted.

**Your own numbers.** `Corrector(..., log_flag_errors=True)` keeps every disagreement: frame, car, field, the packet's
value, the physics value, and the decision (*packet wrong*, *held*, *adopted*). `fix.stats_report()` (C:
`csc_field_stats`) prints how often each RLBot field agreed with physics in your matches.

### What can't be told apart, even in principle

- **Anything set before the first packet.** If tracking starts with a car already in the air, nothing says whether
  it jumped or rolled off a wall (a flip reset), when its jump ended, or how long it has been airborne. These
  facts are assumptions until the car lands or does something that settles them. A bot that connects before kickoff,
  as RLBot bots do, starts with every car on the ground and is not affected.
- **Flags that don't change motion until they are used.** `has_jumped` and `has_dodged` decide whether a dodge is
  still possible. While the car doesn't try one, no motion can refute a wrong value; only having seen the event
  can.
- **Inputs RLBot never showed.** For a packet RLBot didn't deliver, an opponent's input on that tick is unknown. If two
  explanations (a flag, or an input) fit the observed motion equally well, physics can't choose. The corrector then
  keeps the fact it saw established.
- **Clocks of components that haven't toggled since the start.** These are the "seconds since it last changed"
  timers, such as `timeSinceBoosted`. A packet never carries them, so they are estimates until the component next
  changes.
- **The very first packet.** With a single sample there is nothing to vote against. If it is wrong, it is believed
  until later packets outvote it.

## Exactness and accuracy (offline tests, no game launched)

The truth is a catSim match: scripted cars chase the ball with jumps, double jumps, flips, aerial rolls, boost and
powerslides. Packets are real `rlbot.flat.GamePacket` objects built from it the way real RLBot v5 games report a car
(see "Real games"):
- the air state with jumping over ground;
- positions rounded to 0.01, velocities to 0.001, angular velocities to 0.00001;
- the integer rotation, and boost as a whole number;
- the preset's own hitbox;
- the game's jump clock as `dodge_timeout`, and speed ≥ 2200 as `is_supersonic`;
- the input applied on the frame, quantized to one byte as the game applies it.

They are serialized to bytes and back, as on RLBot's socket. Your car's controls land D ticks late. A
kickoff-countdown test checks that the cars drive again at the release.

- **Exact when nothing is hidden.** The tracker was seeded with the exact state and given every car's true
  controls. Predictions 2, 4 and 8 ticks ahead equal the truth bit for bit: every body, every hidden row, every
  output field. 200/200 in 1v1 and 200/200 in 3v3 at each horizon.
- **From packets alone**: the table above. Your car's position is within 0.001 uu and every timer is right once it
  has landed.
- **Dropped packets** (2 of every 10 never delivered): your car's position is within 0.002 uu (1v1) and 0.0008 uu (3v3).
- **Lag meter**: injected delays of 1, 2, 3, 4 and 6 ticks were each measured exactly.
- **C library = Python reference**: on the same packets, every output field is equal (Python's values rounded to
  f32). That is 36,000 car, ball and pad states over 9 scenarios: delays 1-3, a training delay of 1, 1v1 and 3v3,
  injected faults, dropped packets, restarts and a kickoff countdown. The flag statistics and the measured delay are
  identical too. On the real matches' packets as well (see "Real games").
- **C++, Rust, C#, Java** examples on one packet trace (D = 3, measured; 2-tick prediction for a 0-delay policy):
  your car's `isOnGround`, `wheelsWithContact`, `isJumping`, `hasJumped`, `hasDoubleJumped`, `hasFlipped`,
  `isFlipping`, `jumpTime`, `flipTime`, `airTime`, `airTimeSinceJump` and `boost` were 100% right, with a mean
  position error of 0.0011 uu. On a 3v3 trace with injected faults, every timer and flag was 100% right after
  the car's first landing.

## Speed

Per call, on one core of an i7-11700K. A packet costs `update` plus one `predict`.

| | 1v1 update | 1v1 predict 2 / 4 / 8 ticks | 3v3 update | 3v3 predict 2 / 4 / 8 ticks |
|---|---|---|---|---|
| C library (median) | 0.021 ms | 0.018 / 0.025 / 0.042 ms | 0.040 ms | 0.034 / 0.049 / 0.081 ms |
| C library (p99) | 0.077 ms | 0.038 / 0.057 / 0.089 ms | 0.102 ms | 0.065 / 0.090 / 0.144 ms |
| Python (median) | 0.19 ms | 0.13 / 0.17 / 0.25 ms | 0.40 ms | 0.23 / 0.29 / 0.42 ms |
| Python (p99) | 0.45 ms | 0.30 / 0.34 / 0.50 ms | 0.62 ms | 0.45 / 0.50 / 0.67 ms |

A tick at 120 Hz is 8.3 ms, so either fits comfortably in realtime.

## Real games (RLBot v5, 120 fps)

Checked in real Rocket League matches on Windows: RLBot v5 with `rlbot` 2.0.0b56, Stadium_P, and the game locked at
120 fps.
- **1v1:** `examples/example_bot_v5.py` in a Dominus against a Psyonix All-Star.
- **3v3:** two example bots (Breakout, Merc) and a Psyonix teammate against three All-Stars.

Every packet was recorded: 38,000 frames in play per match, of which 99.7% arrived one frame apart. The bots' packets
and controls were then replayed through this version. The truth for a prediction made at frame F for h ticks ahead is
the real packet of frame F+h. *Raw* is the packet of frame F, held. Positions are in uu, velocities in uu/s, mean
over every car-frame.

| | h | your car, position raw / corrected | velocity raw / corrected | opponents, position raw / corrected | velocity raw / corrected |
|---|---|---|---|---|---|
| 1v1 | 1 | 11.7 / 0.0017 | 16.6 / 0.087 | 10.6 / 0.013 | 13.2 / 1.2 |
| 1v1 | 2 | 23.5 / 0.0030 | 32.8 / 0.15 | 21.1 / 0.034 | 26.2 / 2.6 |
| 1v1 | 4 | 46.9 / 0.0076 | 64.8 / 0.33 | 42.3 / 0.12 | 51.7 / 6.1 |
| 3v3 | 1 | 11.3 / 0.0020 | 15.7 / 0.054 | 9.5 / 0.0093 | 15.2 / 1.0 |
| 3v3 | 2 | 22.5 / 0.0028 | 30.9 / 0.12 | 19.1 / 0.028 | 30.1 / 2.3 |
| 3v3 | 4 | 45.0 / 0.0064 | 60.8 / 0.27 | 38.2 / 0.11 | 59.3 / 5.7 |

Your car's p99 position error stays within 0.03 uu up to 4 ticks. Its mean rotation error is 0.013-0.017° (raw: 0.7°
a tick).
Opponents' errors come from inputs they change inside the horizon. Teammates that are other bots land between the
two (3v3, 1 tick: 0.007 uu).

**How much look-ahead you get.** The lag meter measured **D = 1** for the example bot: controls chosen on frame F's
packet were applied on frame F+1, on 38,258 of 38,258 packets. A heavier bot (a 15M-weight network deciding every
8 ticks) measured D = 1 or 2. With D = 1, a policy trained with 0 action delay needs no look-ahead
(`horizon = D - 1 - 0 = 0`). It gets the packet's own frame, in step, with RLBot's flags repaired. The 1-4 tick rows
above are what a slower bot or a larger delay gets. In them your controls for the predicted ticks are known, as they
are under lag.

**RLBot's flags, against physics, on the packet's own frame** (all cars, 1v1 / 3v3):
- **The single air-state enum:** while a jump starts with the wheels down, RLBot reports Jumping and loses
  OnGround. This happened on 391 / 619 car-frames (0.5% / 0.3%). The corrected state keeps `isOnGround` and `isJumping`
  true together.
- **The other flags agree with physics** once RLBot's own definitions are modelled:
  - `air_state`: 2 of 82,907 car-frames / 2 of 252,720 counted wrong.
  - `has_jumped`: 39 / 1 counted wrong.
  - `has_double_jumped`: 65 / 1 counted wrong.
  - `dodge_timeout`: 8 / 17 counted wrong.
  - `supersonic`: 0 / 0 counted wrong.
- **Where the corrected flags and the packet still disagree** (0.12% of your car's frames in 1v1 for `has_jumped`,
  0.17% for `has_double_jumped`, almost none in 3v3), the later packets mostly keep the packet's value. These are
  events physics did not see, not RLBot errors.
- **What 0.1.1 counted as RLBot errors was mostly its own model.** RLBot's `is_supersonic` is speed ≥ 2200 uu/s. It
  matched that test on 32,334 of 32,334 car-frames, while the game's flag, with its grace band, differs on 1% of them.
  The corrected `isSupersonic` keeps the game's meaning. RLBot's `dodge_timeout` is the game's jump clock: it counts
  on the ground too, is -1 while the jump is on, and is -1 once the window is over. The changelog lists the rest.

**What real packets look like** (the offline packets now follow these):
- Positions are rounded to 0.01 uu, velocities to 0.001 uu/s and angular velocities to 0.00001 rad/s. The rotation
  is the integer rotator.
- The hitbox is the preset's own size (catSim's `preset_hitbox`).
- Every applied input axis is k/127 at or above zero and k/128 below.
- `last_input` is the input applied on the packet's own frame. Taken one frame earlier or later, the tracker lost
  step 3 to 8 times as often.
- A taken pad's `timer` counts down: the seconds left until the pad returns. RLBot's schema describes seconds since
  the pickup. The corrector learns the direction (`pad_timer="auto"`).
- The cars rest through the kickoff countdown and drive from the first packet after it.
- `synced` is false on more packets than offline. The packet's integer rotation differs from catSim's own readout by
  2-6 rotator units (0.01-0.03°) on some packets, which counts as out of step. Positions, velocities and the
  predictions above are not affected.

**C++ on the same real packets:** a C++ program read each recorded packet as RLBot's C++ interface (RLBotCPP) hands
it to a bot (`const rlbot::flat::GamePacket*`), converted it with `csc::frame_from_rlbot_v5`, and corrected it with
the bot's own controls. It matched the Python reference on every field, the flag statistics and the measured delay
included: 590,910 car states over the two matches, 0 different. Converting through the object API gives the same frames.

**Live with 0.1.2:** a fresh install from this package, following this README. `example_bot_v5.py` (Dominus) played a
5-minute 1v1 against an RLBotCPP bot built with `catsim_correct.hpp` (MSVC) and the DLL. Both corrected live.
- No errors through 3 goals and their kickoffs.
- Time per packet: Python 0.53 ms median (p99 2.2 ms), C++ 0.11 ms (p99 0.42 ms).
- Both measured D = 1.
- RLBot flags counted wrong: 22 of 87,529 car-frames (`air_state` 2, `has_jumped` 1, `dodge_timeout` 19).

**Time per packet in the matches** (Python, `update` + `predict`, i7-11700K with the game and a GPU training job
running): 1v1 median 0.54 ms (p99 2.4 ms), 3v3 median 0.87 ms (p99 3.0 ms). The C library took 0.06 ms (1v1) and
0.11 ms (3v3) on the same packets.

## Limits

- **Opponents' future inputs are unknown.** Over the predicted ticks they hold their last input by default. Plug in
  your own estimate (Python: `opponent_model=`, an object with `controls(corrector, car, tick)`; C:
  `csc_set_opponent_model`). On the packet's own frame their inputs are known (`last_input`), so the tracked state
  itself is exact for every car while packets arrive every tick.
- **`last_input` is taken as the input applied on the packet's frame**, as real RLBot v5 games showed. The lag meter
  and the `synced` rate show quickly if your RLBot behaves differently (`last_input_is_applied=False` turns it off).
- **Soccar only**, on Stadium_P (`map="UF_Night_P"` also works). These are the maps and modes catSim supports.
- **Checked in two real matches** (above): one machine, Windows, Psyonix opponents, simple bots. A bot that flips and
  aerials a lot exercises the flip clocks more than these did. Watch `fix.stats_report()` in your own matches.
- **The ball can be `None`** on the tick a goal is scored, because catSim has already removed it. Between a goal and
  the next kickoff `correct` returns `None` (C: `num_cars == 0`).
- **RLBot v4 packets** carry no `last_input` and no dodge clocks. Pass `input_delay=`, and expect opponents to be
  tracked from motion alone.
- **The per-wheel suspension** (`wheelSuspensionLength` and the rest) is in the C library only; catSim's Python
  package doesn't expose it.

## Approaches to lag

1. **Replay your own inputs from a buffer every packet (what this does).** Each packet resyncs to the real state,
   and only the last few ticks are predicted, with your exact inputs. Robust to everything: dropped packets, bumps,
   demolitions, state setting.
2. **Lockstep from kickoff.** Every input is known (yours from your buffer, everyone else's from `last_input` one
   frame late), so a catSim arena can in principle run the whole match from the kickoff without ever reading a
   packet's physics. This works at 100% game speed and is exact while nothing is missed. It still needs periodic
   resync to packets: a missed packet, a goal reset, a demolition. The tracker here is that idea with the resync built
   in. It keeps its own exact state as long as the packets agree with it. To start it from an exact state you
   already have, call `seed_exact()` (C: build from the first packet).
3. **Opponents.** Holding the last input is the honest default. Over 1-3 ticks it is right most of the time,
   since humans and bots rarely change inputs every tick. A better estimate is your own policy run on the
   opponent's view.

## Tests

```
pip install --pre rlbot "catsim-rl==0.1.3" numpy pytest   # rlgym==2.0.1 rlgym-rocket-league rlgym-compat for the adapter tests
CATSIM_DATA=path/to/catsim_data python -m pytest tests -q          # (test_plain_mode.py: plain mode and the dump)
CATSIM_DATA=path/to/catsim_data python tests/run_all.py        # every offline number in this README (about 30 s)
CATSIM_DATA=path/to/catsim_data python tests/parity.py --lib native/target/release/catsim_correct.dll
CATSIM_DATA=path/to/catsim_data python tests/make_trace.py trace.txt
g++ -std=c++20 -O2 -static -Inative/include native/examples/correct_trace.cpp native/target/release/catsim_correct.dll -o correct_trace
./correct_trace <catsim_data> trace.txt                        # C++; also rust_bot, csharp, java
```

## Files

```
catsim_better_inputs/    the Python library: core.py (the corrector), rsview.py (RocketSim bookkeeping),
                         packets.py (RLBot v5 / v4 readers), state.py (the output types), outputs.py (adapters),
                         data.py (checking and making catSim's data), setup.py (the setup command)
native/                  the C library (Rust crate over public catSim): src/, include/catsim_correct.h, .hpp
native/examples/         correct_trace.cpp, bench.cpp, rust_bot.rs, csharp/, java/
examples/                RLBot v5 (with bot.toml), RLGym v2 and RLBot v4 bots
tests/                   the offline tests and the offline numbers above
CHANGELOG.md             what changed in each version
```

## License

MIT, copyright adventure-gpt (see LICENSE). Built on catSim (MIT, adventure-gpt). The CarState field names and
bookkeeping rules follow RocketSim (MIT). RLBot's packet formats follow its public schema.
