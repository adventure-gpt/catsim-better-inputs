// catSim input correction for RLBot bots: header-only C++ wrapper over catsim_correct.h (C++17; the RocketSim helpers
// use C++20 `requires` when available).
//
//   #include "catsim_correct.hpp"
//   csc::Corrector fix("catsim_data", index, /*training_action_delay=*/0);
//   // in your per-packet function, before the policy:
//   const CscCorrected& s = fix.correct(csc::frame_from_rlbot_v5(packet));   // rlbot::flat::GamePacketT
//   RocketSim::CarState me;  csc::to_rocketsim(s.cars[index], me);         // your obs builder reads it unchanged
//   ... controls = policy(...);
//   fix.sent(controls);
//
// MIT licence, copyright adventure-gpt.
#pragma once

#include "catsim_correct.h"

#include <cmath>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <string>
#include <utility>

namespace csc {

inline CscControls controls(float throttle, float steer, float pitch, float yaw, float roll, bool jump, bool boost, bool handbrake) {
    CscControls c;
    c.throttle = throttle; c.steer = steer; c.pitch = pitch; c.yaw = yaw; c.roll = roll;
    c.jump = jump; c.boost = boost; c.handbrake = handbrake;
    return c;
}

class Corrector {
public:
    Corrector(const std::string& data_dir, int own_index, int training_action_delay = 0, int input_delay = 0) {
        char err[512] = {0};
        p_ = csc_new(data_dir.c_str(), own_index, training_action_delay, input_delay, err, sizeof err);
        if (!p_) throw std::runtime_error(std::string("catsim_correct: ") + err);
    }
    ~Corrector() { csc_free(p_); }
    Corrector(const Corrector&) = delete;
    Corrector& operator=(const Corrector&) = delete;
    Corrector(Corrector&& o) noexcept : p_(std::exchange(o.p_, nullptr)), out_(o.out_), opp_(std::move(o.opp_)) {}

    // update + predict(horizon): the state at the moment your next action takes effect. num_cars == 0: nothing to
    // correct (no packet yet, or between a goal and the next kickoff)
    const CscCorrected& correct(const CscFrame& f) { return got(csc_correct(p_, &f, &out_)); }
    // the packet's own frame, no look-ahead
    const CscCorrected& update(const CscFrame& f) { return got(csc_update(p_, &f, &out_)); }
    // `ticks` past the last packet (-1: the horizon)
    const CscCorrected& predict(int ticks = -1) { return got(csc_predict(p_, ticks, &out_)); }
    // after your policy: what you returned for this packet
    void sent(const CscControls& c, int frame = -1) { csc_sent(p_, &c, frame); }
    // known controls for any car from `tick` on (hiveminds, lockstep, tests)
    void record_sent(const CscControls& c, int tick, int index) { csc_record_sent(p_, &c, tick, index); }

    int input_delay() const { return csc_input_delay(p_); }
    int horizon() const { return csc_horizon(p_); }
    void set_option(const char* name, double value) {
        if (csc_set_option(p_, name, value) != 0) throw std::invalid_argument(std::string("catsim_correct: no option ") + name);
    }
    void set_pad_locations(const float* xy, int count) { csc_set_pad_locations(p_, xy, count); }
    CscFieldStats stats(const char* field) const { CscFieldStats s{}; csc_field_stats(p_, field, &s); return s; }
    int flag_log(int start, CscFlagEvent* out, int max) const { return csc_flag_log(p_, start, out, max); }

    // your own opponent estimate: f(car, tick, controls&) may change the held input
    void set_opponent_model(std::function<void(int, int, CscControls&)> f) {
        opp_ = std::move(f);
        csc_set_opponent_model(p_, opp_ ? &Corrector::trampoline : nullptr, this);
    }

    CscCorrector* raw() { return p_; }

private:
    static void trampoline(void* user, int32_t car, int32_t tick, CscControls* c) { static_cast<Corrector*>(user)->opp_(car, tick, *c); }
    void check(int r) {
        if (r < 0) throw std::runtime_error(std::string("catsim_correct: ") + csc_last_error(p_));
    }
    const CscCorrected& got(int r) {
        check(r);
        if (r == 1) out_.num_cars = 0;
        return out_;
    }
    CscCorrector* p_ = nullptr;
    CscCorrected out_{};
    std::function<void(int, int, CscControls&)> opp_;
};

// ---------------------------------------------------------------- RLBot v5 (cpp-interface, flatbuffers object API)

// Fill a CscFrame from an rlbot::flat::GamePacketT. A template so this header compiles without RLBot's headers.
template <class GamePacketT>
CscFrame frame_from_rlbot_v5(const GamePacketT& p) {
    CscFrame f;
    std::memset(&f, 0, sizeof f);
    f.frame = p.match_info ? static_cast<int32_t>(p.match_info->frame_num) : 0;
    f.match_phase = p.match_info ? static_cast<int32_t>(p.match_info->match_phase) : 3;
    f.num_cars = static_cast<int32_t>(p.players.size() < CSC_MAX_CARS ? p.players.size() : CSC_MAX_CARS);
    for (int i = 0; i < f.num_cars; ++i) {
        const auto& pl = *p.players[i];
        CscCarIn& c = f.cars[i];
        const auto& ph = *pl.physics;
        c.team = static_cast<int32_t>(pl.team);
        c.pos = {ph.location().x(), ph.location().y(), ph.location().z()};
        c.vel = {ph.velocity().x(), ph.velocity().y(), ph.velocity().z()};
        c.ang_vel = {ph.angular_velocity().x(), ph.angular_velocity().y(), ph.angular_velocity().z()};
        c.pitch = ph.rotation().pitch(); c.yaw = ph.rotation().yaw(); c.roll = ph.rotation().roll();
        c.boost = pl.boost;
        if (pl.hitbox) c.hitbox = {pl.hitbox->length, pl.hitbox->width, pl.hitbox->height};
        if (pl.hitbox_offset) c.hitbox_offset = {pl.hitbox_offset->x(), pl.hitbox_offset->y(), pl.hitbox_offset->z()};
        c.demolished = pl.demolished_timeout >= 0.0f;
        c.demo_left = pl.demolished_timeout > 0.0f ? pl.demolished_timeout : 0.0f;
        c.supersonic = pl.is_supersonic;
        c.has_jumped = pl.has_jumped; c.has_double_jumped = pl.has_double_jumped; c.has_dodged = pl.has_dodged;
        c.on_ground = CSC_UNKNOWN;
        c.air_state = static_cast<int32_t>(pl.air_state);
        c.has_dodge_clocks = true;
        c.dodge_timeout = pl.dodge_timeout; c.dodge_elapsed = pl.dodge_elapsed;
        if (pl.dodge_dir) { c.dodge_dir_x = pl.dodge_dir->x(); c.dodge_dir_y = pl.dodge_dir->y(); }
        if (pl.last_input) {
            const auto& li = *pl.last_input;
            c.has_last_input = true;
            c.last_input = controls(li.throttle(), li.steer(), li.pitch(), li.yaw(), li.roll(), li.jump(), li.boost(), li.handbrake());
        }
    }
    if (!p.balls.empty() && p.balls[0] && p.balls[0]->physics) {
        const auto& b = *p.balls[0]->physics;
        f.has_ball = true;
        f.ball_pos = {b.location().x(), b.location().y(), b.location().z()};
        f.ball_vel = {b.velocity().x(), b.velocity().y(), b.velocity().z()};
        f.ball_ang_vel = {b.angular_velocity().x(), b.angular_velocity().y(), b.angular_velocity().z()};
    }
    f.num_pads = static_cast<int32_t>(p.boost_pads.size() < CSC_NUM_PADS ? p.boost_pads.size() : CSC_NUM_PADS);
    for (int k = 0; k < f.num_pads; ++k) {
        f.pad_active[k] = p.boost_pads[k].is_active();
        f.pad_timer[k] = p.boost_pads[k].timer();
    }
    return f;
}

// The same from a `const rlbot::flat::GamePacket*`: the flatbuffers table the RLBot v5 C++ interface (RLBotCPP)
// hands your bot's update(). Equal, field for field, to converting packet->UnPack() with the function above.
template <class GamePacket>
CscFrame frame_from_rlbot_v5(const GamePacket* p) {
    CscFrame f;
    std::memset(&f, 0, sizeof f);
    if (!p) return f;
    const auto* mi = p->match_info();
    f.frame = mi ? static_cast<int32_t>(mi->frame_num()) : 0;
    f.match_phase = mi ? static_cast<int32_t>(mi->match_phase()) : 3;
    const auto* players = p->players();
    const int n = players ? static_cast<int>(players->size()) : 0;
    f.num_cars = n < CSC_MAX_CARS ? n : CSC_MAX_CARS;
    for (int i = 0; i < f.num_cars; ++i) {
        const auto* pl = players->Get(i);
        CscCarIn& c = f.cars[i];
        c.team = static_cast<int32_t>(pl->team());
        if (const auto* ph = pl->physics()) {
            c.pos = {ph->location().x(), ph->location().y(), ph->location().z()};
            c.vel = {ph->velocity().x(), ph->velocity().y(), ph->velocity().z()};
            c.ang_vel = {ph->angular_velocity().x(), ph->angular_velocity().y(), ph->angular_velocity().z()};
            c.pitch = ph->rotation().pitch(); c.yaw = ph->rotation().yaw(); c.roll = ph->rotation().roll();
        }
        c.boost = pl->boost();
        if (const auto* hb = pl->hitbox()) c.hitbox = {hb->length(), hb->width(), hb->height()};
        if (const auto* ho = pl->hitbox_offset()) c.hitbox_offset = {ho->x(), ho->y(), ho->z()};
        c.demolished = pl->demolished_timeout() >= 0.0f;
        c.demo_left = pl->demolished_timeout() > 0.0f ? pl->demolished_timeout() : 0.0f;
        c.supersonic = pl->is_supersonic();
        c.has_jumped = pl->has_jumped(); c.has_double_jumped = pl->has_double_jumped(); c.has_dodged = pl->has_dodged();
        c.on_ground = CSC_UNKNOWN;
        c.air_state = static_cast<int32_t>(pl->air_state());
        c.has_dodge_clocks = true;
        c.dodge_timeout = pl->dodge_timeout(); c.dodge_elapsed = pl->dodge_elapsed();
        if (const auto* dd = pl->dodge_dir()) { c.dodge_dir_x = dd->x(); c.dodge_dir_y = dd->y(); }
        if (const auto* li = pl->last_input()) {
            c.has_last_input = true;
            c.last_input = controls(li->throttle(), li->steer(), li->pitch(), li->yaw(), li->roll(), li->jump(), li->boost(), li->handbrake());
        }
    }
    const auto* balls = p->balls();
    if (balls && balls->size() > 0 && balls->Get(0)->physics()) {
        const auto* b = balls->Get(0)->physics();
        f.has_ball = true;
        f.ball_pos = {b->location().x(), b->location().y(), b->location().z()};
        f.ball_vel = {b->velocity().x(), b->velocity().y(), b->velocity().z()};
        f.ball_ang_vel = {b->angular_velocity().x(), b->angular_velocity().y(), b->angular_velocity().z()};
    }
    const auto* pads = p->boost_pads();
    const int np = pads ? static_cast<int>(pads->size()) : 0;
    f.num_pads = np < CSC_NUM_PADS ? np : CSC_NUM_PADS;
    for (int k = 0; k < f.num_pads; ++k) {
        f.pad_active[k] = pads->Get(k)->is_active();
        f.pad_timer[k] = pads->Get(k)->timer();
    }
    return f;
}

// Your field info's pad locations (RLBot's pad order can differ from catSim's): call once, before the first packet.
//   csc::set_pad_locations(fix, field_info);   // const rlbot::flat::FieldInfo* (RLBotCPP) or FieldInfoT
template <class FieldInfo>
void set_pad_locations(Corrector& fix, const FieldInfo* fi) {
    if (!fi || !fi->boost_pads()) return;
    float xy[2 * CSC_NUM_PADS];
    int n = 0;
    for (unsigned k = 0; k < fi->boost_pads()->size() && n < CSC_NUM_PADS; ++k, ++n) {
        const auto* loc = fi->boost_pads()->Get(k)->location();
        xy[2 * n] = loc ? loc->x() : 0.0f;
        xy[2 * n + 1] = loc ? loc->y() : 0.0f;
    }
    fix.set_pad_locations(xy, n);
}
template <class FieldInfoT>
void set_pad_locations(Corrector& fix, const FieldInfoT& fi) {
    float xy[2 * CSC_NUM_PADS];
    int n = 0;
    for (const auto& b : fi.boost_pads) {
        if (n >= CSC_NUM_PADS) break;
        xy[2 * n] = b->location ? b->location->x() : 0.0f;
        xy[2 * n + 1] = b->location ? b->location->y() : 0.0f;
        ++n;
    }
    fix.set_pad_locations(xy, n);
}

// ---------------------------------------------------------------- RocketSim / GigaLearn

// Copy a corrected car into RocketSim's CarState (any version: fields a version lacks are skipped). A template so
// this header doesn't need RocketSim's; instantiate it with RocketSim::CarState.
template <class V> inline void put_vec(V& o, const CscVec& v) { o.x = v.x; o.y = v.y; o.z = v.z; }

template <class RSCarState>
void to_rocketsim(const CscCarState& s, RSCarState& o) {
    put_vec(o.pos, s.pos);
    put_vec(o.rotMat.forward, s.rotMat.forward);
    put_vec(o.rotMat.right, s.rotMat.right);
    put_vec(o.rotMat.up, s.rotMat.up);
    put_vec(o.vel, s.vel);
    put_vec(o.angVel, s.angVel);
    o.isOnGround = s.isOnGround;
    for (int k = 0; k < 4; ++k) o.wheelsWithContact[k] = s.wheelsWithContact[k];
    o.hasJumped = s.hasJumped;
    o.hasDoubleJumped = s.hasDoubleJumped;
    o.hasFlipped = s.hasFlipped;
    put_vec(o.flipRelTorque, s.flipRelTorque);
    o.jumpTime = s.jumpTime;
    o.flipTime = s.flipTime;
    o.isFlipping = s.isFlipping;
    o.isJumping = s.isJumping;
    o.airTime = s.airTime;
    o.airTimeSinceJump = s.airTimeSinceJump;
    o.boost = s.boost;
    o.isSupersonic = s.isSupersonic;
    o.supersonicTime = s.supersonicTime;
    o.handbrakeVal = s.handbrakeVal;
    o.isAutoFlipping = s.isAutoFlipping;
    o.autoFlipTimer = s.autoFlipTimer;
    o.autoFlipTorqueScale = s.autoFlipTorqueScale;
    o.worldContact.hasContact = s.worldContact.hasContact;
    put_vec(o.worldContact.contactNormal, s.worldContact.contactNormal);
    o.carContact.otherCarID = s.carContact.otherCarID;
    o.carContact.cooldownTimer = s.carContact.cooldownTimer;
    o.isDemoed = s.isDemoed;
    o.demoRespawnTimer = s.demoRespawnTimer;
    o.ballHitInfo.isValid = s.ballHitInfo.isValid;
    o.ballHitInfo.tickCountWhenHit = s.ballHitInfo.tickCountWhenHit;
    o.ballHitInfo.tickCountWhenExtraImpulseApplied = s.ballHitInfo.tickCountWhenExtraImpulseApplied;
    o.lastControls.throttle = s.lastControls.throttle;
    o.lastControls.steer = s.lastControls.steer;
    o.lastControls.pitch = s.lastControls.pitch;
    o.lastControls.yaw = s.lastControls.yaw;
    o.lastControls.roll = s.lastControls.roll;
    o.lastControls.jump = s.lastControls.jump;
    o.lastControls.boost = s.lastControls.boost;
    o.lastControls.handbrake = s.lastControls.handbrake;
#if __cplusplus >= 202002L || (defined(_MSVC_LANG) && _MSVC_LANG >= 202002L)
    if constexpr (requires { o.timeSinceBoosted; }) o.timeSinceBoosted = s.timeSinceBoosted;    // RocketSim 2.1+
    if constexpr (requires { o.isBoosting; }) o.isBoosting = s.isBoosting;
    if constexpr (requires { o.boostingTime; }) o.boostingTime = s.boostingTime;
    if constexpr (requires { o.timeSpentBoosting; }) o.timeSpentBoosting = s.timeSpentBoosting;  // older RocketSim
    if constexpr (requires { o.tickCountSinceUpdate; }) o.tickCountSinceUpdate = s.tickCountSinceUpdate;
#else
    o.timeSinceBoosted = s.timeSinceBoosted;
    o.isBoosting = s.isBoosting;
    o.boostingTime = s.boostingTime;
#endif
}

template <class RSBallState>
void to_rocketsim(const CscBallState& b, RSBallState& o) {
    put_vec(o.pos, b.pos);
    put_vec(o.rotMat.forward, b.rotMat.forward);
    put_vec(o.rotMat.right, b.rotMat.right);
    put_vec(o.rotMat.up, b.rotMat.up);
    put_vec(o.vel, b.vel);
    put_vec(o.angVel, b.angVel);
}

}  // namespace csc
