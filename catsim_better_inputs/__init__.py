"""catSim input correction for RLBot bots: an in-process library your bot calls inside its own get_output, on the
packet it was just given, before its policy runs. Not an RLBot script, no separate process.

    from catsim_better_inputs import Corrector
    fix = Corrector(index=self.index, data_dir="catsim_data", training_action_delay=0)
    state = fix.correct(packet)          # lag-corrected, flags repaired, RocketSim CarState field names
    controls = my_policy(state)
    fix.sent(controls)

README.md has the details.
"""
from .core import Corrector, FieldStats, HoldLastInput, LagMeter, Neutral, Tolerances, game_controls, same_controls
from .packets import BallIn, CarIn, Frame, as_frame, from_rlbot_v4, from_rlbot_v5
from .state import BallHitInfo, BallState, CarContact, CarState, Corrected, PadState, WorldContact

__version__ = "0.1.2"
__all__ = ["Corrector", "Tolerances", "HoldLastInput", "Neutral", "LagMeter", "FieldStats", "same_controls", "game_controls",
           "Frame", "CarIn", "BallIn", "as_frame", "from_rlbot_v5", "from_rlbot_v4",
           "CarState", "BallState", "PadState", "Corrected", "WorldContact", "CarContact", "BallHitInfo", "__version__"]
