"""Plain mode and the background dump (Corrector(dump_if_missing=True)), and the setup command.

The dumper is never run for real here except once, pointed at a Rocket League folder that doesn't exist (it fails at
once, before reading anything). Everywhere else `subprocess.Popen` is replaced by a fake dumper that the test finishes
when it wants, and catSim's data check by a switch the test flips. Tests that build packets need CATSIM_DATA (the
harness's truth match runs on catSim); the others don't. Nothing is ever written next to the real data folder.

    CATSIM_DATA=path/to/catsim_data python -m pytest tests/test_plain_mode.py -q
"""
import dataclasses
import math
import os
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import catsim_better_inputs.core as core  # noqa: E402
import catsim_better_inputs.data as data  # noqa: E402
from catsim_better_inputs import Corrected, CarState, Corrector, DumpJob  # noqa: E402

needs_data = pytest.mark.skipif(not os.environ.get("CATSIM_DATA"), reason="set CATSIM_DATA to catSim's game data folder")


class FakeDump:
    """Stands in for subprocess.Popen. A dumper (`catsim.dump --out`) 'runs' until the test calls finish(rc); a
    check (`catsim.dump --check`) ends at once, passing when the test's `data_ok` says so."""
    made: list = []                               # the dumper processes started
    checks: list = []                             # the checks started
    data = {"data_ok": False}

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = list(cmd), kw
        self.pid = os.getpid()                    # a live process, as a running dumper would be
        self.returncode = None
        self.stdout = None
        if "--check" in self.cmd:
            FakeDump.checks.append(self)
            ok = FakeDump.data["data_ok"]
            self._write("data: " + ("the checked data" if ok else "NOT the checked data") + "\n")
            self.returncode = 0 if ok else 1
        else:
            FakeDump.made.append(self)

    def _write(self, text):
        out = self.kw.get("stdout")
        if text and hasattr(out, "write"):
            out.write(text)
            out.flush()

    def finish(self, rc=0, output=""):
        if self.returncode is not None:
            return
        self._write(output)
        if rc == 0:                               # a dumper that worked has made its folder
            os.makedirs(self.cmd[self.cmd.index("--out") + 1], exist_ok=True)
        self.returncode = rc

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.finish(-9)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Fake dumper processes, a switchable data check, and the dump's lock and log files kept in tmp_path."""
    FakeDump.made, FakeDump.checks = [], []
    monkeypatch.setattr(subprocess, "Popen", FakeDump)
    state = FakeDump.data = {"data_ok": False}

    def check(d, echo=False):
        return state["data_ok"], "checked (test)"
    monkeypatch.setattr(data, "check_data", check)
    monkeypatch.setattr(core, "check_data", check)

    class Job(DumpJob):                          # never write next to a real data folder
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.lock_path = str(tmp_path / "test.dump.lock")
            self.log_path = str(tmp_path / "test.dump.log")
    monkeypatch.setattr(core, "DumpJob", Job)
    yield state


def _names(x):
    """Every field name, nested through dataclasses and lists (the shape a caller's code reads)."""
    if dataclasses.is_dataclass(x):
        return {f.name: _names(getattr(x, f.name)) for f in dataclasses.fields(x)}
    if isinstance(x, list):
        return [_names(v) for v in x]
    return None


# ---------------------------------------------------------------- no game data needed

def test_missing_data_raises_and_says_what_to_do(tmp_path):
    with pytest.raises(FileNotFoundError) as e:
        Corrector(index=0, data_dir=str(tmp_path / "catsim_data"))
    msg = str(e.value)
    assert "python -m catsim_better_inputs.setup --keys" in msg
    assert str(tmp_path / "catsim_data") in msg
    assert "dump_if_missing=True" in msg


def test_background_dump_is_a_subprocess_and_never_blocks(fake, tmp_path):
    keys = tmp_path / "keys.txt"
    keys.write_text("0" * 64 + "\n")
    lines = []
    threads = threading.active_count()
    t0 = time.monotonic()
    fix = Corrector(index=0, data_dir=str(tmp_path / "catsim_data"), dump_if_missing=True, keys=str(keys),
                    log=lines.append)
    assert time.monotonic() - t0 < 2.0                    # the dump is still 'running': construction didn't wait
    assert threading.active_count() == threads            # a separate process, no thread in the bot's
    assert fix.mode == "dumping" and fix.dump_error is None
    assert len(FakeDump.made) == 1
    p = FakeDump.made[0]
    assert p.cmd[:3] == [sys.executable, "-m", "catsim.dump"] or p.cmd[3:6] == [sys.executable, "-m", "catsim.dump"]
    assert p.cmd[p.cmd.index("--out") + 1] == str(tmp_path / "catsim_data")
    assert p.cmd[p.cmd.index("--keys") + 1] == str(keys)
    assert hasattr(p.kw["stdout"], "write")                # into the log file, not a pipe nobody reads
    if os.name == "nt":
        assert p.kw["creationflags"] & subprocess.BELOW_NORMAL_PRIORITY_CLASS
        assert p.kw["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert os.path.exists(fix.dump.lock_path)              # one dump per folder at a time
    assert len(lines) == 1 and "background" in lines[0] and "plain RLBot inputs" in lines[0]
    fix.sent((1.0, 0, 0, 0, 0, 0, 0, 0))                  # accepted while there is nothing to track
    p.finish(0)
    assert fix.dump.wait(5)
    assert fix.dump.state == "failed"                      # the (fake) check still says no data
    assert not os.path.exists(fix.dump.lock_path)


def test_a_second_dump_into_the_same_folder_waits_for_the_first(fake, tmp_path):
    d = str(tmp_path / "catsim_data")
    a = core.DumpJob(d).start()
    assert len(FakeDump.made) == 1
    b = core.DumpJob(d).start()
    assert not b.wait(1.5)                                # still waiting for a's dump
    assert len(FakeDump.made) == 1 and b.state == "running" and b.waited_for_other
    fake["data_ok"] = True
    FakeDump.made[0].finish(0)
    assert a.wait(5) and b.wait(5)
    assert a.state == "done" and b.state == "done" and len(FakeDump.made) == 1


def test_real_dumper_failure_leaves_plain_mode(tmp_path):
    """The real dumper, as a real subprocess, pointed at a game folder that doesn't exist: it fails at once."""
    lines = []
    d = tmp_path / "catsim_data"
    fix = Corrector(index=0, data_dir=str(d), dump_if_missing=True, game=str(tmp_path / "no_game_here"),
                    log=lines.append)
    assert fix.mode == "dumping"
    assert fix.dump.wait(120)
    assert fix.mode == "plain"
    assert "no_game_here" in fix.dump_error and "exit code" in fix.dump_error
    assert any("failed" in ln and "plain RLBot inputs" in ln for ln in lines), lines
    assert os.path.isfile(fix.dump.log_path) and not os.path.exists(fix.dump.lock_path)


def test_setup_command(tmp_path, capsys):
    from catsim_better_inputs import setup
    assert setup.main(["--out", str(tmp_path / "catsim_data"), "--keys", str(tmp_path / "missing_keys.txt")]) == 1
    assert "doesn't exist" in capsys.readouterr().err


@needs_data
def test_setup_command_on_good_data_only_checks(capsys, monkeypatch):
    from catsim_better_inputs import setup

    def no_dump(*a, **kw):
        raise AssertionError("good data must not be dumped again")
    monkeypatch.setattr(data.DumpJob, "start", no_dump)
    assert setup.main(["--out", os.environ["CATSIM_DATA"]]) == 0      # the real --check, read-only
    out = capsys.readouterr().out
    assert "data:" in out and "ready:" in out


@needs_data
def test_real_background_check_of_good_data_starts_no_dump(tmp_path, monkeypatch):
    """The background check as a real subprocess (read-only) on good data: the job ends "done" without a dump."""
    def no_dump(self):
        raise AssertionError("good data must not be dumped again")
    monkeypatch.setattr(DumpJob, "_start_dump", no_dump)
    job = DumpJob(os.environ["CATSIM_DATA"])
    job.lock_path, job.log_path = str(tmp_path / "test.dump.lock"), str(tmp_path / "test.dump.log")
    job.start()
    assert job.state == "running"                         # the check runs in its own process; start() didn't wait
    assert job.wait(60) and job.state == "done", job.error
    assert not os.path.exists(job.lock_path)


# ---------------------------------------------------------------- with packets (catSim's truth match)

def _truth(seed=4, warmup=240):
    import catsim
    catsim.init(silent=True)
    from harness import Truth
    return Truth(1, seed=seed, warmup=warmup)


@needs_data
def test_soccar_pad_table_is_catsims_order():
    import catsim
    catsim.init(silent=True)
    pads = catsim._lib().pad_locations("Stadium_P")
    assert [(round(p[0][0]), round(p[0][1]), int(b)) for p, b in zip(pads, [b for _, b in pads])] == \
        [tuple(x) for x in core.SOCCAR_PADS]


@needs_data
def test_plain_state_from_an_rlbot_v5_packet(fake, tmp_path):
    from harness import DelayedBot, make_packet
    from rlbot import flat
    truth = _truth()
    fix = Corrector(index=0, data_dir=str(tmp_path / "catsim_data"), dump_if_missing=True, log=lambda m: None)
    ref = Corrector(index=0, input_delay=2)                    # a normal corrector on the same packets
    assert fix.mode == "dumping"
    lag = DelayedBot(2)
    for k in range(300):
        truth.step([lag.applied(truth.frame + 1), truth.script(1)])
        pkt = make_packet(truth)
        st = fix.correct(pkt)
        good = ref.update(pkt)                                # (the packet's own frame, as plain mode gives)
        c = truth.script(0)
        fix.sent(c)
        ref.sent(c)
        lag.send(truth.frame, c)
        assert isinstance(st, Corrected) and st.plain and not good.plain
        assert st.frame == truth.frame and st.horizon == 0 and len(st.cars) == truth.n
        assert _names(st) == _names(good)                     # the same types and field names as corrected mode
        for i, car in enumerate(st.cars):
            assert isinstance(car, CarState) and car.index == i and car.team == truth.teams[i]
            p = pkt.players[i]
            assert car.pos == pytest.approx((p.physics.location.x, p.physics.location.y, p.physics.location.z))
            assert car.vel == pytest.approx((p.physics.velocity.x, p.physics.velocity.y, p.physics.velocity.z))
            assert car.boost == p.boost and car.isSupersonic == p.is_supersonic
            assert car.isOnGround == (int(p.air_state) == 0) and car.wheelsWithContact == (car.isOnGround,) * 4
            assert car.isJumping == (int(p.air_state) == 1)
            assert (car.hasJumped, car.hasDoubleJumped, car.hasFlipped) == (p.has_jumped, p.has_double_jumped, p.has_dodged)
            assert car.dodgeTimeout == pytest.approx(p.dodge_timeout)
            truth_rot = truth.rs(i).rotMat
            assert max(abs(a - b) for u, v in zip(car.rotMat, truth_rot) for a, b in zip(u, v)) < 1e-3
            assert car.confidence["plain"] and not car.confidence["synced"]
            assert set(car.confidence) >= set(good.cars[i].confidence)
            assert all(math.isfinite(x) for x in (car.jumpTime, car.flipTime, car.airTime, car.airTimeSinceJump))
        b = pkt.balls[0].physics.location
        assert st.ball.pos == pytest.approx((b.x, b.y, b.z))
        assert len(st.pads) == 34 and [q.isBig for q in st.pads] == [q.isBig for q in good.pads]
        assert [q.isActive for q in st.pads] == [q.isActive for q in good.pads]
    assert fix.input_delay == 2                                 # the lag meter ran in plain mode too
    assert fix.mode == "dumping"
    assert fix.update(make_packet(truth, phase=flat.MatchPhase.GoalScored)) is None   # as in corrected mode


@needs_data
def test_correction_switches_on_only_between_plays(fake):
    from harness import make_packet
    from rlbot import flat
    truth = _truth()
    lines = []
    fix = Corrector(index=0, data_dir=os.environ["CATSIM_DATA"], dump_if_missing=True, input_delay=1,
                    log=lines.append)                       # (the fake check says the data is missing)
    assert fix.mode == "dumping"
    drive = lambda phase=flat.MatchPhase.Active: (truth.step([truth.script(i) for i in range(truth.n)]),
                                                  fix.correct(make_packet(truth, phase=phase)))[1]
    for _ in range(30):
        assert drive().plain
    assert len(FakeDump.made) == 1                          # the (fake) dumper is running
    fake["data_ok"] = True
    FakeDump.made[0].finish(0)                              # the dump finishes in the middle of play
    assert fix.dump.wait(5) and fix.dump.state == "done"
    for _ in range(120):                                    # still in play: never switched mid-play
        st = drive(flat.MatchPhase.Kickoff if _ % 2 else flat.MatchPhase.Active)
        assert st.plain and fix.mode == "dumping"
    assert not any("correction is on" in ln for ln in lines)
    truth.hold(True)                                        # a goal later: the next kickoff countdown
    truth.step([truth.script(i) for i in range(truth.n)])
    pkt = make_packet(truth, phase=flat.MatchPhase.Countdown)
    st = fix.correct(pkt)
    assert fix.mode == "corrected" and not st.plain
    assert sum("correction is on" in ln for ln in lines) == 1
    fresh = Corrector(index=0, input_delay=1)               # a corrector started on that same packet
    same = lambda a, b: a.cars == b.cars and a.ball == b.ball
    assert same(st, fresh.correct(pkt))
    for k in range(300):                                    # the countdown, its release, the kickoff, play
        if k == 60:
            truth.hold(False)
        truth.step([truth.script(i) for i in range(truth.n)])
        pkt = make_packet(truth, phase=flat.MatchPhase.Countdown if k < 60 else
                          flat.MatchPhase.Kickoff if k < 120 else flat.MatchPhase.Active)
        st = fix.correct(pkt)
        assert not st.plain and same(st, fresh.correct(pkt)), k   # exactly as if it had started there


@needs_data
def test_a_new_match_is_a_boundary_too(fake):
    from harness import make_packet
    truth = _truth(seed=5, warmup=400)
    fix = Corrector(index=0, data_dir=os.environ["CATSIM_DATA"], dump_if_missing=True, log=lambda m: None)
    for _ in range(10):
        truth.step([truth.script(i) for i in range(truth.n)])
        assert fix.correct(make_packet(truth)).plain
    assert len(FakeDump.made) == 1
    fake["data_ok"] = True
    FakeDump.made[0].finish(0)
    assert fix.dump.wait(5)
    truth.step([truth.script(i) for i in range(truth.n)])
    assert fix.correct(make_packet(truth)).plain             # same match, in play
    new = _truth(seed=6, warmup=30)                          # a new match: the frame count starts again
    new.step([new.script(i) for i in range(new.n)])
    st = fix.correct(make_packet(new))
    assert fix.mode == "corrected" and not st.plain


@needs_data
def test_a_failed_dump_stays_plain_and_never_raises(fake, tmp_path):
    from harness import make_packet
    from rlbot import flat
    truth = _truth()
    lines = []
    fix = Corrector(index=0, data_dir=str(tmp_path / "catsim_data"), dump_if_missing=True, log=lines.append)
    for _ in range(20):
        truth.step([truth.script(i) for i in range(truth.n)])
        fix.sent(truth.script(0))
        assert fix.correct(make_packet(truth)).plain
    assert len(FakeDump.made) == 1
    FakeDump.made[0].finish(2, "reading the game's packages\nerror: no Rocket League install found\n")
    assert fix.dump.wait(5)
    assert fix.mode == "plain" and "no Rocket League install found" in fix.dump_error
    assert any("making catSim's data failed" in ln and "no Rocket League install found" in ln for ln in lines)
    truth.hold(True)
    for phase in (flat.MatchPhase.Countdown, flat.MatchPhase.Kickoff, flat.MatchPhase.Active):
        for _ in range(20):
            truth.step([truth.script(i) for i in range(truth.n)])
            st = fix.correct(make_packet(truth, phase=phase))
            fix.sent(truth.script(0))
            assert st.plain and fix.mode == "plain"
    with pytest.raises(RuntimeError):
        fix.seed_exact(make_packet(truth), truth.na)
    assert len(FakeDump.made) == 1                           # one attempt per session
