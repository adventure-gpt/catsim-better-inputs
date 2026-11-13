// A tiny C++ bot loop over a recorded packet trace (tests/make_trace.py): each packet goes through the corrector
// before the "policy", the corrected car is copied into a RocketSim CarState, and the result is checked against the
// truth the trace carries.
//
//   g++ -std=c++20 -O2 -static -I../include correct_trace.cpp path/to/catsim_correct.dll -o correct_trace
//   (-static: the C++ runtime inside the program, so another libstdc++ on PATH cannot clash with it)
//   correct_trace path/to/catsim_data trace.txt
//
// Build with -DCSC_WITH_ROCKETSIM -I<RocketSim>/src to fill RocketSim's own RocketSim::CarState instead of the
// look-alike below.
#include "catsim_correct.hpp"

#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#ifdef CSC_WITH_ROCKETSIM
#include "Sim/Car/Car.h"
#include "Sim/Ball/Ball.h"
using RSCarState = RocketSim::CarState;
#else
// The fields of RocketSim's CarState that this example reads, under RocketSim's names.
namespace demo {
struct Vec { float x = 0, y = 0, z = 0; };
struct RotMat { Vec forward, right, up; };
struct CarControls { float throttle, steer, pitch, yaw, roll; bool jump, boost, handbrake; };
struct BallHitInfo { bool isValid; Vec relativePosOnBall, ballPos, extraHitVel; uint64_t tickCountWhenHit, tickCountWhenExtraImpulseApplied; };
struct CarState {
    Vec pos; RotMat rotMat; Vec vel, angVel; uint64_t tickCountSinceUpdate = 0;
    bool isOnGround; bool wheelsWithContact[4]; bool hasJumped, hasDoubleJumped, hasFlipped; Vec flipRelTorque;
    float jumpTime, flipTime; bool isFlipping, isJumping; float airTime, airTimeSinceJump, boost, timeSinceBoosted;
    bool isBoosting; float boostingTime; bool isSupersonic; float supersonicTime, handbrakeVal; bool isAutoFlipping;
    float autoFlipTimer, autoFlipTorqueScale; struct { bool hasContact; Vec contactNormal; } worldContact;
    struct { uint32_t otherCarID; float cooldownTimer; } carContact; bool isDemoed; float demoRespawnTimer;
    BallHitInfo ballHitInfo; CarControls lastControls;
};
}  // namespace demo
using RSCarState = demo::CarState;
#endif

struct Truth { int onGround, jumping, hasJumped, hasDJ, hasFlipped, flipping, wheels[4]; float jumpTime, flipTime, airTime, atsj, boost, pos[3]; };

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc < 3) {
        std::printf("usage: correct_trace <catsim_data> <trace.txt>\n");
        return 2;
    }
    std::ifstream in(argv[2]);
    std::string line, tag;
    std::getline(in, line);
    int ncars = 0;
    {
        std::istringstream h(line);
        std::string w;
        while (h >> w) if (w == "cars") h >> ncars;
    }
    csc::Corrector fix(argv[1], /*own index*/ 0, /*training_action_delay*/ 0);   // input delay: measured
    std::printf("catsim_correct %s, %d cars\n", csc_version(), ncars);

    std::map<int, std::vector<CscCarState>> predicted;   // target frame -> corrected cars predicted for it
    CscFrame f{};
    int packets = 0, compared = 0, printed = 0;
    std::map<std::string, int> ok, ok_landed;
    double pos_err = 0;
    int landed = 0;
    bool seen_ground = false;   // a timer that began before the first packet can only be known after a landing
    while (in >> tag) {
        if (tag == "F") {
            f = CscFrame{};
            in >> f.frame >> f.match_phase;
            f.num_cars = 0;
        } else if (tag == "C") {
            CscCarIn& c = f.cars[f.num_cars++];
            float v[39];
            for (float& x : v) in >> x;
            int k = 0;
            c.team = (int)v[k++];
            c.pos = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.vel = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.ang_vel = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.pitch = v[k++]; c.yaw = v[k++]; c.roll = v[k++];
            c.boost = v[k++];
            c.hitbox = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.hitbox_offset = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.demolished = v[k++] != 0; c.demo_left = v[k++]; c.supersonic = v[k++] != 0;
            c.has_jumped = (int8_t)v[k++]; c.has_double_jumped = (int8_t)v[k++]; c.has_dodged = (int8_t)v[k++];
            c.on_ground = CSC_UNKNOWN;
            c.air_state = (int32_t)v[k++];
            c.has_dodge_clocks = true;
            c.dodge_timeout = v[k++]; c.dodge_elapsed = v[k++]; c.dodge_dir_x = v[k++]; c.dodge_dir_y = v[k++];
            c.has_last_input = true;
            c.last_input = csc::controls(v[k], v[k + 1], v[k + 2], v[k + 3], v[k + 4], v[k + 5] > 0.5f, v[k + 6] > 0.5f, v[k + 7] > 0.5f);
        } else if (tag == "B") {
            int has;
            in >> has;
            f.has_ball = has != 0;
            if (has) {
                float* b[9] = {&f.ball_pos.x, &f.ball_pos.y, &f.ball_pos.z, &f.ball_vel.x, &f.ball_vel.y, &f.ball_vel.z,
                               &f.ball_ang_vel.x, &f.ball_ang_vel.y, &f.ball_ang_vel.z};
                for (float* x : b) in >> *x;
            }
        } else if (tag == "P") {
            in >> f.num_pads;
            for (int k = 0; k < f.num_pads; ++k) {
                int a;
                in >> a >> f.pad_timer[k];
                f.pad_active[k] = a != 0;
            }
            // ---- the bot's per-packet function: correct first, then the policy reads the corrected state ----
            const CscCorrected& s = fix.correct(f);
            ++packets;
            predicted[s.frame] = std::vector<CscCarState>(s.cars, s.cars + s.num_cars);
            RSCarState me{};
            csc::to_rocketsim(s.cars[0], me);           // what a GigaLearn / RocketSim obs builder reads
            if (packets % 150 == 1 && printed < 5) {
                ++printed;
                std::printf("packet %d -> state for frame %d (horizon %d, delay %d): pos (%.2f %.2f %.2f) isOnGround %d "
                            "wheels %d%d%d%d isJumping %d hasJumped %d hasFlipped %d jumpTime %.4f flipTime %.4f "
                            "airTimeSinceJump %.4f boost %.2f synced %d\n",
                            f.frame, s.frame, s.horizon, s.input_delay, me.pos.x, me.pos.y, me.pos.z, me.isOnGround,
                            me.wheelsWithContact[0], me.wheelsWithContact[1], me.wheelsWithContact[2], me.wheelsWithContact[3],
                            me.isJumping, me.hasJumped, me.hasFlipped, me.jumpTime, me.flipTime, me.airTimeSinceJump, me.boost,
                            s.cars[0].synced);
            }
        } else if (tag == "T") {
            Truth t;
            int i;
            in >> i >> t.onGround >> t.jumping >> t.hasJumped >> t.hasDJ >> t.hasFlipped >> t.flipping >> t.wheels[0] >> t.wheels[1] >>
                t.wheels[2] >> t.wheels[3] >> t.jumpTime >> t.flipTime >> t.airTime >> t.atsj >> t.boost >> t.pos[0] >> t.pos[1] >> t.pos[2];
            auto it = predicted.find(f.frame);
            if (it == predicted.end() || i != 0 || packets < 120) continue;   // own car, after a 1 s warm-up
            const CscCarState& e = it->second[0];
            ++compared;
            ok["isOnGround"] += e.isOnGround == (t.onGround != 0);
            ok["isJumping"] += e.isJumping == (t.jumping != 0);
            ok["hasJumped"] += e.hasJumped == (t.hasJumped != 0);
            ok["hasDoubleJumped"] += e.hasDoubleJumped == (t.hasDJ != 0);
            ok["hasFlipped"] += e.hasFlipped == (t.hasFlipped != 0);
            ok["isFlipping"] += e.isFlipping == (t.flipping != 0);
            bool w = true;
            for (int k = 0; k < 4; ++k) w &= e.wheelsWithContact[k] == (t.wheels[k] != 0);
            ok["wheelsWithContact"] += w;
            ok["jumpTime"] += std::fabs(e.jumpTime - t.jumpTime) <= 1e-3f;
            ok["flipTime"] += std::fabs(e.flipTime - t.flipTime) <= 1e-3f;
            ok["airTime"] += std::fabs(e.airTime - t.airTime) <= 1e-3f;
            ok["airTimeSinceJump"] += std::fabs(e.airTimeSinceJump - t.atsj) <= 1e-3f;
            ok["boost"] += std::fabs(e.boost - t.boost) <= 1e-3f;
            seen_ground |= t.onGround != 0;
            if (seen_ground) {
                ++landed;
                ok_landed["hasJumped"] += e.hasJumped == (t.hasJumped != 0);
                ok_landed["hasFlipped"] += e.hasFlipped == (t.hasFlipped != 0);
                ok_landed["jumpTime"] += std::fabs(e.jumpTime - t.jumpTime) <= 1e-3f;
                ok_landed["airTime"] += std::fabs(e.airTime - t.airTime) <= 1e-3f;
                ok_landed["airTimeSinceJump"] += std::fabs(e.airTimeSinceJump - t.atsj) <= 1e-3f;
            }
            pos_err += std::sqrt(std::pow(e.pos.x - t.pos[0], 2) + std::pow(e.pos.y - t.pos[1], 2) + std::pow(e.pos.z - t.pos[2], 2));
            predicted.erase(predicted.begin(), it);
        } else if (tag == "S") {
            float c[8];
            for (float& x : c) in >> x;
            fix.sent(csc::controls(c[0], c[1], c[2], c[3], c[4], c[5] > 0.5f, c[6] > 0.5f, c[7] > 0.5f), f.frame);
        } else {
            std::getline(in, line);
        }
    }
    std::printf("\n%d packets; measured input delay %d, horizon %d; own car checked on %d frames:\n", packets, fix.input_delay(),
                fix.horizon(), compared);
    for (auto& [k, n] : ok) std::printf("  %-18s %6.2f%% right\n", k.c_str(), compared ? 100.0 * n / compared : 0.0);
    std::printf("  position error     %.4f uu mean\n", compared ? pos_err / compared : 0.0);
    std::printf("after the car's first landing (%d frames):\n", landed);
    for (auto& [k, n] : ok_landed) std::printf("  %-18s %6.2f%% right\n", k.c_str(), landed ? 100.0 * n / landed : 0.0);
    const char* fields[] = {"air_state", "has_jumped", "has_double_jumped", "has_dodged", "dodge_timeout", "dodge_elapsed", "supersonic"};
    std::printf("\nRLBot's flags against physics (every car):\n");
    for (const char* fl : fields) {
        CscFieldStats st = fix.stats(fl);
        std::printf("  %-18s compared %6llu  agreed %6llu  packet wrong %5llu  adopted %4llu\n", fl, (unsigned long long)st.compared,
                    (unsigned long long)st.agreed, (unsigned long long)st.packet_wrong, (unsigned long long)st.adopted);
    }
    return 0;
}
