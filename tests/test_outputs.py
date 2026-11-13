"""The output adapters: RLGym 2.x (with RLGym's own DefaultObs), RLGym 1.x, numpy, dict, RocketSim v3 names.

    CATSIM_DATA=... python -m pytest tests/test_outputs.py -q      (rlgym / rlgym-compat tests skip if not installed)
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(not os.environ.get("CATSIM_DATA"), reason="set CATSIM_DATA to catSim's game data folder")


def corrected_and_truth(n_per_team=2, frames=300, exact_seed=False):
    import catsim
    catsim.init(silent=True)
    from harness import DelayedBot, Truth, make_packet

    from catsim_better_inputs import Corrected, Corrector
    truth = Truth(n_per_team, seed=12)
    bot = DelayedBot(1)
    fix = Corrector(index=0, input_delay=1)
    if exact_seed:
        fix.seed_exact(make_packet(truth), truth.na, truth.obs)
    for _ in range(frames):
        rows = [bot.applied(truth.frame + 1)] + [truth.script(i) for i in range(1, truth.n)]
        truth.step(rows)
        st = fix.correct(make_packet(truth))
        c = truth.script(0)
        fix.sent(c, truth.frame)
        bot.send(truth.frame, c)
    t = [truth.rs(i) for i in range(truth.n)]
    for k, c in enumerate(t):
        c.confidence = dict(st.cars[k].confidence)
    ts = Corrected(frame=st.frame, horizon=0, input_delay=1, cars=t, ball=st.ball, pads=st.pads)
    return st, ts


def test_numpy_and_dict():
    from catsim_better_inputs.outputs import CAR_COLUMNS, to_dict, to_numpy, to_rocketsim_v3
    st, _ = corrected_and_truth(1, 120)
    a = to_numpy(st)
    assert a["cars"].shape == (2, len(CAR_COLUMNS)) and a["pads"].shape == (34, 3)
    d = to_dict(st)
    assert d["cars"][0]["isOnGround"] in (True, False)
    v3 = to_rocketsim_v3(st.cars[0])
    assert set(("jump_ticks", "supersonic_grace_timer", "wheels_contact", "prev_controls")) <= set(v3)


def test_rlgym_v2_default_obs_reads_it_unchanged():
    pytest.importorskip("rlgym.rocket_league.api")
    import numpy as np
    from rlgym.rocket_league.api import GameState
    from rlgym.rocket_league.obs_builders import DefaultObs

    from catsim_better_inputs.outputs import to_rlgym_v2
    for exact in (True, False):
        st, ts = corrected_and_truth(2, 300, exact_seed=exact)
        gs, gt = to_rlgym_v2(st), to_rlgym_v2(ts)
        assert isinstance(gs, GameState)
        obs = DefaultObs(zero_padding=None)
        agents = list(gs.cars)
        obs.reset(agents, gs, {})
        a = obs.build_obs(agents, gs, {})
        b = obs.build_obs(agents, gt, {})
        for k in agents:
            if exact:      # tracked from an exact start: RLGym's own obs builder gives the truth's obs, bit for bit
                assert np.array_equal(a[k], b[k]), k
            else:          # from packets alone: the bodies carry the packet's rounding
                assert np.allclose(a[k], b[k], atol=1e-3), (k, float(np.abs(a[k] - b[k]).max()))
        car = gs.cars[0]
        assert car.on_ground == st.cars[0].isOnGround and car.is_flipping == st.cars[0].isFlipping


def test_rlgym_v1_players():
    from catsim_better_inputs.outputs import to_rlgym_v1
    st, _ = corrected_and_truth(1, 120)
    g = to_rlgym_v1(st)
    assert len(g.players) == 2 and g.boost_pads.shape == (34,)
    p = g.players[0]
    assert p.on_ground == st.cars[0].isOnGround and abs(p.boost_amount - st.cars[0].boost / 100) < 1e-6
    assert p.car_data.position.shape == (3,)
