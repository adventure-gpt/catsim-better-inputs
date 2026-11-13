// Time the C library on a packet trace (tests/make_trace.py): update per packet, then predict 2, 4 and 8 ticks ahead.
//   g++ -std=c++20 -O2 -static -I../include bench.cpp path/to/catsim_correct.dll -o bench && bench <catsim_data> <trace>
#include "catsim_correct.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

static void report(const char* name, std::vector<double>& v) {
    std::sort(v.begin(), v.end());
    if (v.empty()) return;
    std::printf("  %-10s median %.3f ms   p99 %.3f ms   (%zu calls)\n", name, v[v.size() / 2], v[(size_t)(0.99 * (v.size() - 1))], v.size());
}

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    std::ifstream in(argv[2]);
    std::string line, tag;
    std::getline(in, line);
    csc::Corrector fix(argv[1], 0, 0);
    std::vector<double> up, p2, p4, p8;
    CscFrame f{};
    auto ms = [](auto a, auto b) { return std::chrono::duration<double, std::milli>(b - a).count(); };
    while (in >> tag) {
        if (tag == "F") { f = CscFrame{}; in >> f.frame >> f.match_phase; }
        else if (tag == "C") {
            CscCarIn& c = f.cars[f.num_cars++];
            float v[39];
            for (float& x : v) in >> x;
            int k = 0;
            c.team = (int)v[k++];
            c.pos = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.vel = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.ang_vel = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.pitch = v[k++]; c.yaw = v[k++]; c.roll = v[k++]; c.boost = v[k++];
            c.hitbox = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.hitbox_offset = {v[k], v[k + 1], v[k + 2]}; k += 3;
            c.demolished = v[k++] != 0; c.demo_left = v[k++]; c.supersonic = v[k++] != 0;
            c.has_jumped = (int8_t)v[k++]; c.has_double_jumped = (int8_t)v[k++]; c.has_dodged = (int8_t)v[k++];
            c.on_ground = CSC_UNKNOWN; c.air_state = (int32_t)v[k++]; c.has_dodge_clocks = true;
            c.dodge_timeout = v[k++]; c.dodge_elapsed = v[k++]; c.dodge_dir_x = v[k++]; c.dodge_dir_y = v[k++];
            c.has_last_input = true;
            c.last_input = csc::controls(v[k], v[k + 1], v[k + 2], v[k + 3], v[k + 4], v[k + 5] > 0.5f, v[k + 6] > 0.5f, v[k + 7] > 0.5f);
        } else if (tag == "B") {
            int has; in >> has; f.has_ball = has != 0;
            if (has) { float* b[9] = {&f.ball_pos.x, &f.ball_pos.y, &f.ball_pos.z, &f.ball_vel.x, &f.ball_vel.y, &f.ball_vel.z,
                                      &f.ball_ang_vel.x, &f.ball_ang_vel.y, &f.ball_ang_vel.z};
                       for (float* x : b) in >> *x; }
        } else if (tag == "P") {
            in >> f.num_pads;
            for (int k = 0; k < f.num_pads; ++k) { int a; in >> a >> f.pad_timer[k]; f.pad_active[k] = a != 0; }
            auto t0 = std::chrono::steady_clock::now();
            fix.update(f);
            auto t1 = std::chrono::steady_clock::now();
            fix.predict(2);
            auto t2 = std::chrono::steady_clock::now();
            fix.predict(4);
            auto t3 = std::chrono::steady_clock::now();
            fix.predict(8);
            auto t4 = std::chrono::steady_clock::now();
            up.push_back(ms(t0, t1)); p2.push_back(ms(t1, t2)); p4.push_back(ms(t2, t3)); p8.push_back(ms(t3, t4));
        } else if (tag == "S") {
            float c[8]; for (float& x : c) in >> x;
            fix.sent(csc::controls(c[0], c[1], c[2], c[3], c[4], c[5] > 0.5f, c[6] > 0.5f, c[7] > 0.5f), f.frame);
        } else std::getline(in, line);
    }
    std::printf("%s, %d cars\n", argv[2], f.num_cars);
    report("update", up); report("predict 2", p2); report("predict 4", p4); report("predict 8", p8);
    return 0;
}
