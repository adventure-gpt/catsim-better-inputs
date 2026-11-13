"""RLBot v4 (rlbot 1.x, BaseAgent). v4 packets have a separate has_wheel_contact flag but no air state, dodge clocks
or other cars' last inputs, so opponents are estimated from their motion only (see the README's limits).
"""
from rlbot.agents.base_agent import BaseAgent, SimpleControllerState

from catsim_better_inputs import Corrector


class MyV4Bot(BaseAgent):
    def initialize_agent(self):
        self.fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0)

    def get_output(self, packet) -> SimpleControllerState:
        state = self.fix.correct(packet)               # reads the v4 GameTickPacket
        c = SimpleControllerState()
        if state is None:
            return c
        me = state.cars[self.index]
        c.throttle = 1.0
        c.jump = me.isOnGround and state.ball.pos[2] > 300
        self.fix.sent(c)                               # SimpleControllerState has the same field names
        return c
