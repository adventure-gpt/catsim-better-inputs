"""A minimal RLBot v5 bot (rlbot 2.x) with catSim input correction.

The corrector is a library inside your bot: it runs in get_output, on the packet RLBot just handed you, before your
policy. The policy then reads the corrected state instead of the raw packet.
"""
from rlbot import flat
from rlbot.managers import Bot

from catsim_better_inputs import Corrector


class MyBot(Bot):
    def initialize(self):
        # training_action_delay: the action delay your policy was trained with (0 if controls applied at once).
        # The corrector measures RLBot's real delay itself and predicts the difference.
        self.fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0, field_info=self.field_info)
        # (no catsim_data on this PC? make it at setup: python -m catsim_better_inputs.setup --keys keys.txt. Or add
        #  dump_if_missing=True, keys="keys.txt": the bot then makes it in the background and plays on plain inputs
        #  until it's ready - self.fix.mode says which)

    def get_output(self, packet: flat.GamePacket) -> flat.ControllerState:
        # Call this on EVERY packet, even if your policy acts only every few ticks: the tracker steps from packet to
        # packet with the inputs each one shows.
        # BEFORE: state = packet                       (lagging, with RLBot's flags as they come)
        state = self.fix.correct(packet)               # AFTER: the state your next action will meet, flags repaired
        if state is None or state.ball is None:        # (between a goal and the kickoff there is nothing to correct;
            return flat.ControllerState()              #  at the goal's own tick the ball is already gone in catSim)

        me = state.cars[self.index]                    # RocketSim CarState names: me.isOnGround, me.wheelsWithContact,
        ball = state.ball                              # me.isJumping, me.jumpTime, me.hasFlipped, me.flipTime, ...
        controls = self.policy(me, ball, state)        # <- your policy reads the corrected state, not the packet

        self.fix.sent(controls)                        # tell it what you sent (for the lag meter and your own car)
        return controls

    def retire(self):
        # how often each RLBot flag disagreed with physics in this match (and was replaced)
        print(self.fix.stats_report(), flush=True)

    def policy(self, me, ball, state) -> flat.ControllerState:
        # a stand-in: drive at the ball, jump when it is close and above us
        import math
        fwd = me.rotMat[0]
        ang = math.atan2(ball.pos[1] - me.pos[1], ball.pos[0] - me.pos[0]) - math.atan2(fwd[1], fwd[0])
        ang = (ang + math.pi) % (2 * math.pi) - math.pi
        close = math.dist(me.pos, ball.pos) < 400 and ball.pos[2] > 200
        return flat.ControllerState(throttle=1.0, steer=max(-1.0, min(1.0, 3 * ang)), boost=abs(ang) < 0.3,
                                    jump=close and me.isOnGround and not me.isJumping)


if __name__ == "__main__":
    MyBot("example/catsim_better_inputs").run()
