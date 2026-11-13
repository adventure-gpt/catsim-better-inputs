"""Make catSim's game data at setup time, before any match, so a bot never has to make it at start-up.

    python -m catsim_better_inputs.setup --keys keys.txt [--out catsim_data] [--game "C:/path/to/rocketleague"]

It checks the folder first (`python -m catsim.dump --check`): if it already holds catSim's checked data, nothing else
happens. Otherwise catSim's dumper makes it from this PC's own Rocket League files (found from Steam or Epic, or
--game), printing its progress, then the check runs again and its result is printed. It only reads the game's files
and never starts the game. Exit code 0: the folder is ready; 1: it isn't (the reason is printed).
Needs the dumper with its fast AES: pip install "catsim-rl[dump]".
"""
from __future__ import annotations

import argparse
import os
import sys

from .data import DUMP_TIMEOUT, ensure_data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m catsim_better_inputs.setup",
                                 description="Make catSim's game data (catsim_data/) from this PC's Rocket League files.")
    ap.add_argument("--keys", help="the key file for the game's packages (default: $CATSIM_RL_KEYS); see catSim's README")
    ap.add_argument("--out", default="catsim_data", help="the data folder to check or make (default: ./catsim_data)")
    ap.add_argument("--game", help="the Rocket League folder, if it isn't found from Steam or Epic")
    ap.add_argument("--timeout", type=float, default=DUMP_TIMEOUT, help="give up after this many seconds (default %(default)s)")
    a = ap.parse_args(argv)
    if a.keys and not os.path.isfile(a.keys):
        print(f"error: the key file {os.path.abspath(a.keys)} doesn't exist", file=sys.stderr, flush=True)
        return 1
    out = os.path.abspath(a.out)
    try:
        ensure_data(out, keys=a.keys, game=a.game, timeout=a.timeout, echo=True)
    except KeyboardInterrupt:
        print("stopped: the data folder isn't ready", file=sys.stderr, flush=True)
        return 130
    except Exception as e:
        print(f"error: {e}", file=sys.stderr, flush=True)
        print(f"the data folder {out} isn't ready", file=sys.stderr, flush=True)
        return 1
    print(f"ready: {out} holds catSim's game data. Keep it in your bot's folder (data_dir=\"catsim_data\") "
          f"or point data_dir at it.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
