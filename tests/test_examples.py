"""The RLBot v5 examples' get_output, run on fake packets (no game, no RLBot server: the bot is never connected)."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "examples"))

pytestmark = pytest.mark.skipif(not os.environ.get("CATSIM_DATA"), reason="set CATSIM_DATA to catSim's game data folder")


def drive(bot_cls, frames=200):
    import catsim
    catsim.init(silent=True)
    from harness import DelayedBot, Truth, make_packet
    from rlbot import flat
    os.environ.setdefault("RLBOT_AGENT_ID", "test/agent")
    bot = bot_cls()
    bot.index, bot.team = 0, 0
    bot.field_info = flat.FieldInfo()
    import catsim_better_inputs.core as core
    orig = core.Corrector.__init__

    def init(self, *a, **kw):                     # the examples say data_dir="catsim_data"; use the test's folder
        kw["data_dir"] = os.environ["CATSIM_DATA"]
        orig(self, *a, **kw)
    core.Corrector.__init__ = init
    try:
        bot.initialize()
    finally:
        core.Corrector.__init__ = orig
    truth = Truth(1, seed=4)
    lag = DelayedBot(2)
    out = None
    for _ in range(frames):
        rows = [lag.applied(truth.frame + 1)] + [truth.script(1)]
        truth.step(rows)
        out = bot.get_output(make_packet(truth))
        lag.send(truth.frame, (out.throttle, out.steer, out.pitch, out.yaw, out.roll, float(out.jump), float(out.boost),
                               float(out.handbrake)))
    return bot, out


def test_example_bot_v5():
    from example_bot_v5 import MyBot
    bot, out = drive(MyBot)
    assert out.throttle == 1.0
    assert bot.fix.input_delay == 2            # measured from the packets' last_input


def test_example_rlgym_v2():
    pytest.importorskip("rlgym.rocket_league.obs_builders")
    from example_rlgym_v2 import RLGymBot
    bot, out = drive(RLGymBot, 60)
    assert out.throttle == 1.0


def test_rlbot_v4_packet_reader():
    """A GameTickPacket-shaped object (RLBot v4's field names) through the corrector."""
    from types import SimpleNamespace as N

    import catsim
    catsim.init(silent=True)
    from harness import Truth

    from catsim_better_inputs import Corrector, from_rlbot_v4
    truth = Truth(1, seed=6)
    fix = Corrector(index=0, input_delay=1)
    v = lambda a: N(x=a[0], y=a[1], z=a[2])
    for _ in range(30):
        truth.step([truth.script(0), truth.script(1)])
        cars = []
        for i in range(2):
            r, hr, h = truth.na.car_row(i), truth.na.hidden_row(i), truth.na.car_hidden(i)
            import math
            rot = N(pitch=hr[24] * math.pi / 32768, yaw=hr[25] * math.pi / 32768, roll=hr[26] * math.pi / 32768)
            cars.append(N(physics=N(location=v(r[2:5]), rotation=rot, velocity=v(r[5:8]), angular_velocity=v(r[8:11])),
                          is_demolished=False, has_wheel_contact=h[1] > 0.5, is_super_sonic=h[4] > 0.5, is_bot=True,
                          jumped=truth.obs[i].has_jumped, double_jumped=truth.obs[i].has_double_jumped, team=i,
                          boost=int(h[25] * 100), hitbox=N(length=118.0, width=84.2, height=36.16), hitbox_offset=v((13.88, 0, 20.75))))
        a = truth.na.arena_row()
        pkt = N(num_cars=2, game_cars=cars, game_ball=N(physics=N(location=v(a[0:3]), velocity=v(a[3:6]), angular_velocity=v(a[6:9]))),
                num_boost=34, game_boosts=[N(is_active=True, timer=0.0) for _ in range(34)],
                game_info=N(frame_num=truth.frame, is_match_ended=False, is_kickoff_pause=False, is_round_active=True,
                            seconds_elapsed=truth.frame / 120, game_speed=1.0, world_gravity_z=-650.0))
        fr = from_rlbot_v4(pkt)
        assert fr.cars[0].air_state is None and fr.cars[0].on_ground is not None
        st = fix.update(pkt)
    assert st.cars[0].isOnGround == truth.rs(0).isOnGround
