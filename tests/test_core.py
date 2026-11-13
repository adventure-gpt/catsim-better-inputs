"""pytest: the guarantees. Needs CATSIM_DATA (a folder made by catSim's dumper) and the rlbot package.

    CATSIM_DATA=path/to/catsim_data python -m pytest tests -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(not os.environ.get("CATSIM_DATA"), reason="set CATSIM_DATA to catSim's game data folder")


def test_exact_when_nothing_is_hidden():
    from run_all import exactness
    for n in (1, 3):
        r = exactness(n, 3, (2, 4, 8), 40)
        assert all(v["exact"] == v["of"] for v in r.values()), r


def test_jump_on_the_ground_keeps_both_bits():
    from run_all import jump_on_ground
    rows = [r for r in jump_on_ground() if r["variant"] == "tracking"]
    overwritten = [r for r in rows if r["truth_on_ground"] and r["truth_jumping"]]
    assert overwritten, "the scripted jump should start with the wheels down"
    for r in overwritten:
        assert not r["raw_says_on_ground"]                # the packet's one enum says Jumping ...
        assert r["corrected_on_ground"] and r["corrected_jumping"]   # ... the corrected state has both
    for r in rows:
        assert r["corrected_on_ground"] == r["truth_on_ground"] and r["corrected_jumping"] == r["truth_jumping"]


def test_lag_meter_finds_the_delay():
    from run_all import lag_meter
    assert lag_meter((1, 3, 5), frames=300) == {1: 1, 3: 3, 5: 5}


def test_wrong_flags_never_reach_the_output_once_settled():
    from run_all import faults_run
    r = faults_run(1, 900, 61, 0.02)["per_field"]
    for k, v in r.items():
        if k.endswith("(after 2 s)"):
            assert v["false_alarms"] == 0, (k, v)
            assert v["kept_out_rate"] == 1.0, (k, v)


def test_cars_drive_again_after_the_kickoff_countdown():
    """Real games: the cars rest through the countdown and drive from the first packet after it. The tracker starts
    cars with driving off during a countdown, so it must turn it on again at the release, or it never stays in step."""
    from rlbot import flat

    from catsim_better_inputs import Corrector
    from harness import Truth, make_packet
    fix = Corrector(index=0, input_delay=1)                  # (first: it starts catSim on CATSIM_DATA)
    truth = Truth(1, seed=4)
    truth.hold(True)
    for _ in range(90):
        truth.step([truth.script(i) for i in range(truth.n)])
        fix.update(make_packet(truth, phase=flat.MatchPhase.Countdown))
    truth.hold(False)
    moving = 0
    for k in range(240):
        truth.step([truth.script(i) for i in range(truth.n)])
        fix.update(make_packet(truth, phase=flat.MatchPhase.Kickoff if k < 60 else flat.MatchPhase.Active))
        # in step: position and velocity within the packet's rounding (the rotation readout is not the point here)
        moving += all(c.last_d[0] <= fix.tol.snap_pos and c.last_d[1] <= fix.tol.snap_vel for c in fix.cars)
    assert moving >= 228, moving                            # (without the release: none after the first tick)
