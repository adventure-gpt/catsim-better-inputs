"""Packet readers: turn an RLBot packet into one plain, framework-free `Frame`.

Everything else in this package reads only `Frame`, so supporting another packet format means writing one reader.

    from_rlbot_v5(packet, field_info=None)   RLBot v5 (`rlbot` 2.x, rlbot.flat.GamePacket)
    from_rlbot_v4(packet)                    RLBot v4 (`rlbot` 1.x, GameTickPacket) - fewer fields, see the README
    as_frame(x)                              any of the above, or a Frame already

Units are the game's: uu, uu/s, rad/s, radians, boost 0..100, seconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# RLBot v5 AirState values
ON_GROUND, JUMPING, DOUBLE_JUMPING, DODGING, IN_AIR = 0, 1, 2, 3, 4

# RLBot v5 MatchPhase values -> the names used here
_PHASES = {0: "inactive", 1: "countdown", 2: "kickoff", 3: "active", 4: "goal", 5: "replay", 6: "paused", 7: "ended"}


@dataclass
class CarIn:
    """One car as a packet reports it. `None` means the packet format doesn't have that field."""
    team: int
    pos: tuple
    vel: tuple
    ang_vel: tuple
    rot: tuple                       # (pitch, yaw, roll), radians
    boost: float                     # 0..100
    hitbox: tuple = (118.0, 84.2, 36.2)
    hitbox_offset: tuple = (13.88, 0.0, 20.75)
    demolished: bool = False
    demo_left: float = 0.0
    supersonic: bool = False
    has_jumped: bool | None = None
    has_double_jumped: bool | None = None
    has_dodged: bool | None = None
    air_state: int | None = None     # RLBot v5 AirState (one enum: it can't say "jumping" and "on the ground" at once)
    on_ground: bool | None = None    # RLBot v4 has_wheel_contact
    dodge_timeout: float | None = None
    dodge_elapsed: float | None = None
    dodge_dir: tuple | None = None   # (forward, right) unit direction of the latest dodge
    last_input: tuple | None = None  # (throttle, steer, pitch, yaw, roll, jump, boost, handbrake) applied on this frame
    player_id: int = 0
    is_bot: bool = True


@dataclass
class BallIn:
    pos: tuple
    vel: tuple
    ang_vel: tuple


@dataclass
class Frame:
    frame: int                       # the physics frame this state is after (RLBot's frame_num)
    cars: list
    ball: BallIn | None
    pads: list | None = None         # per pad (is_active, seconds since it was taken; 0 when active), packet order
    phase: str = "active"
    seconds: float = 0.0
    game_speed: float = 1.0
    gravity_z: float = -650.0
    source: str = "frame"
    extra: dict = field(default_factory=dict)


def _v(v) -> tuple:
    return (float(v.x), float(v.y), float(v.z))


def _ctl(c) -> tuple:
    return (float(c.throttle), float(c.steer), float(c.pitch), float(c.yaw), float(c.roll),
            1.0 if c.jump else 0.0, 1.0 if c.boost else 0.0, 1.0 if c.handbrake else 0.0)


def from_rlbot_v5(packet, field_info=None) -> Frame:
    """An RLBot v5 GamePacket (field names from rlbot_flatbuffers' public schema)."""
    cars = []
    for p in packet.players:
        ph = p.physics
        r = ph.rotation
        hb, ho = p.hitbox, p.hitbox_offset
        dd = p.dodge_dir
        cars.append(CarIn(
            team=int(p.team), pos=_v(ph.location), vel=_v(ph.velocity), ang_vel=_v(ph.angular_velocity),
            rot=(float(r.pitch), float(r.yaw), float(r.roll)), boost=float(p.boost),
            hitbox=(float(hb.length), float(hb.width), float(hb.height)), hitbox_offset=_v(ho),
            demolished=float(p.demolished_timeout) >= 0.0, demo_left=max(0.0, float(p.demolished_timeout)),
            supersonic=bool(p.is_supersonic), has_jumped=bool(p.has_jumped), has_double_jumped=bool(p.has_double_jumped),
            has_dodged=bool(p.has_dodged), air_state=int(p.air_state), dodge_timeout=float(p.dodge_timeout),
            dodge_elapsed=float(p.dodge_elapsed), dodge_dir=(float(dd.x), float(dd.y)), last_input=_ctl(p.last_input),
            player_id=int(p.player_id), is_bot=bool(p.is_bot)))
    ball = None
    if len(packet.balls) > 0:
        b = packet.balls[0].physics
        ball = BallIn(_v(b.location), _v(b.velocity), _v(b.angular_velocity))
    pads = [(bool(s.is_active), float(s.timer)) for s in packet.boost_pads]
    mi = packet.match_info
    return Frame(frame=int(mi.frame_num), cars=cars, ball=ball, pads=pads or None,
                 phase=_PHASES.get(int(mi.match_phase), "active"), seconds=float(mi.seconds_elapsed),
                 game_speed=float(mi.game_speed) or 1.0, gravity_z=float(mi.world_gravity_z) or -650.0, source="rlbot5",
                 extra={"pad_locations": None if field_info is None else
                        [(float(b.location.x), float(b.location.y)) for b in field_info.boost_pads]})


def from_rlbot_v4(packet) -> Frame:
    """An RLBot v4 GameTickPacket (field names from the RLBot v4 wiki's 'Input and output data'). v4 has a separate
    has_wheel_contact flag but no air state, dodge clocks or other cars' inputs."""
    cars = []
    for i in range(int(packet.num_cars)):
        c = packet.game_cars[i]
        ph = c.physics
        hb, ho = c.hitbox, c.hitbox_offset
        cars.append(CarIn(
            team=int(c.team), pos=_v(ph.location), vel=_v(ph.velocity), ang_vel=_v(ph.angular_velocity),
            rot=(float(ph.rotation.pitch), float(ph.rotation.yaw), float(ph.rotation.roll)), boost=float(c.boost),
            hitbox=(float(hb.length), float(hb.width), float(hb.height)), hitbox_offset=_v(ho),
            demolished=bool(c.is_demolished), supersonic=bool(c.is_super_sonic), has_jumped=bool(c.jumped),
            has_double_jumped=bool(c.double_jumped), on_ground=bool(c.has_wheel_contact), is_bot=bool(c.is_bot)))
    b = packet.game_ball.physics
    ball = BallIn(_v(b.location), _v(b.velocity), _v(b.angular_velocity))
    pads = [(bool(packet.game_boosts[i].is_active), float(packet.game_boosts[i].timer)) for i in range(int(packet.num_boost))]
    gi = packet.game_info
    phase = "ended" if gi.is_match_ended else "kickoff" if gi.is_kickoff_pause else "active" if gi.is_round_active else "countdown"
    return Frame(frame=int(getattr(gi, "frame_num", 0)), cars=cars, ball=ball, pads=pads or None, phase=phase,
                 seconds=float(gi.seconds_elapsed), game_speed=float(gi.game_speed) or 1.0,
                 gravity_z=float(gi.world_gravity_z) or -650.0, source="rlbot4")


def as_frame(x, field_info=None) -> Frame:
    if isinstance(x, Frame):
        return x
    if hasattr(x, "players") and hasattr(x, "match_info"):
        return from_rlbot_v5(x, field_info)
    if hasattr(x, "game_cars") and hasattr(x, "game_info"):
        return from_rlbot_v4(x)
    raise TypeError(f"not an RLBot v4 or v5 packet: {type(x).__name__}")
