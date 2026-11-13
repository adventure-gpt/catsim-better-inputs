"""An RLBot v5 bot whose policy was trained with RLGym 2.x / rlgym-ppo: the corrected state goes to the same
observation builder the policy trained with, as an RLGym GameState.
"""
import numpy as np
from rlbot import flat
from rlbot.managers import Bot
from rlgym.rocket_league.obs_builders import DefaultObs

from catsim_better_inputs import Corrector
from catsim_better_inputs.outputs import to_rlgym_v2


class RLGymBot(Bot):
    def initialize(self):
        self.fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0, field_info=self.field_info)
        self.obs = DefaultObs(zero_padding=None)      # the obs builder your policy trained with
        self.started = False

    def get_output(self, packet: flat.GamePacket) -> flat.ControllerState:
        state = self.fix.correct(packet)
        if state is None:
            return flat.ControllerState()
        gs = to_rlgym_v2(state)                        # rlgym.rocket_league.api.GameState, agent ids = packet indices
        agents = list(gs.cars)
        if not self.started:
            self.obs.reset(agents, gs, {})
            self.started = True
        obs = self.obs.build_obs([self.index], gs, {})[self.index]
        controls = self.act(obs)
        self.fix.sent(controls)
        return controls

    def act(self, obs: np.ndarray) -> flat.ControllerState:
        return flat.ControllerState(throttle=1.0)     # your policy(obs) here


if __name__ == "__main__":
    RLGymBot("example/catsim_better_inputs_rlgym").run()
