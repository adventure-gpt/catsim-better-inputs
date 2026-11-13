/* catSim input correction for RLBot bots: the C interface.
 *
 * An in-process library: your bot calls it inside its own per-packet function, on the packet it was just given,
 * before its policy runs. No separate process, no IPC.
 *
 *   CscCorrector* fix = csc_new("catsim_data", my_index, 0, 0, err, sizeof err);   // 0 action delay, measure D
 *   per packet:  fill a CscFrame from the packet; csc_correct(fix, &frame, &out);    // out.cars[i]: RocketSim names
 *                controls = policy(out); csc_sent(fix, &controls, frame.frame);
 *
 * MIT licence, copyright adventure-gpt. Built on the public catSim engine (github.com/adventure-gpt/catsim-public).
 */
#ifndef CATSIM_CORRECT_H
#define CATSIM_CORRECT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CSC_MAX_CARS 8
#define CSC_NUM_PADS 34
#define CSC_UNKNOWN (-1)

/* facts that can still rest on a packet instead of physics (CscCarState.assumedFacts) */
#define CSC_FACT_HAS_JUMPED 1u
#define CSC_FACT_HAS_DOUBLE_JUMPED 2u
#define CSC_FACT_HAS_DODGED 4u
#define CSC_FACT_JUMP_CLOCK 8u
#define CSC_FACT_DOUBLE_JUMP_CLOCK 16u
#define CSC_FACT_DODGE_CLOCK 32u
#define CSC_FACT_SUPERSONIC 64u
#define CSC_FACT_BOOST_CLOCK 128u
#define CSC_FACT_HANDBRAKE 256u

typedef struct CscVec { float x, y, z; } CscVec;
typedef struct CscRotMat { CscVec forward, right, up; } CscRotMat;
typedef struct CscControls { float throttle, steer, pitch, yaw, roll; bool jump, boost, handbrake; } CscControls;

/* One car as the packet reports it. Tri-state int8 flags: -1 the packet format doesn't have it, 0 false, 1 true. */
typedef struct CscCarIn {
    int32_t team;
    CscVec pos, vel, ang_vel;
    float pitch, yaw, roll;          /* radians, as RLBot reports the rotation */
    float boost;                     /* 0..100 */
    CscVec hitbox;                   /* length, width, height (uu) */
    CscVec hitbox_offset;
    bool demolished;
    float demo_left;
    bool supersonic;
    int8_t has_jumped, has_double_jumped, has_dodged;
    int8_t on_ground;                /* RLBot v4's has_wheel_contact; -1 for v5 */
    int32_t air_state;               /* RLBot v5 AirState 0..4; -1 for v4 */
    bool has_dodge_clocks;           /* v5: dodge_timeout, dodge_elapsed, dodge_dir are filled */
    float dodge_timeout, dodge_elapsed;
    float dodge_dir_x, dodge_dir_y;  /* forward, right */
    bool has_last_input;
    CscControls last_input;          /* the input the packet says was applied on its frame */
} CscCarIn;

typedef struct CscFrame {
    int32_t frame;                   /* RLBot's frame_num */
    int32_t match_phase;             /* RLBot v5 MatchPhase: 1 countdown, 2 kickoff, 3 active, 4 goal, 5 replay, 6 paused */
    int32_t num_cars;
    CscCarIn cars[CSC_MAX_CARS];
    bool has_ball;
    CscVec ball_pos, ball_vel, ball_ang_vel;
    int32_t num_pads;                /* 0: no pads in this packet */
    bool pad_active[CSC_NUM_PADS];
    float pad_timer[CSC_NUM_PADS];   /* RLBot's timer as it comes, 0 when active. Real RLBot v5 games count it down to the
                                        pad's return; its schema says up from the pickup. The direction is learned from the
                                        packets (option "pad_timer": 0 auto, 1 seconds left, 2 seconds since taken). */
} CscFrame;

typedef struct CscWorldContact { bool hasContact; CscVec contactNormal; } CscWorldContact;
typedef struct CscCarContact { uint32_t otherCarID; float cooldownTimer; } CscCarContact;
typedef struct CscBallHitInfo {
    bool isValid;
    CscVec relativePosOnBall, ballPos, extraHitVel;   /* not in catSim's public state: zero */
    uint64_t tickCountWhenHit, tickCountWhenExtraImpulseApplied;
} CscBallHitInfo;

/* RocketSim's CarState, field for field (names, meanings, order), then the extras. */
typedef struct CscCarState {
    CscVec pos;
    CscRotMat rotMat;
    CscVec vel, angVel;
    uint64_t tickCountSinceUpdate;
    bool isOnGround;
    bool wheelsWithContact[4];       /* RocketSim order: front left, front right, back left, back right */
    bool hasJumped, hasDoubleJumped, hasFlipped;
    CscVec flipRelTorque;
    float jumpTime, flipTime;
    bool isFlipping, isJumping;
    float airTime, airTimeSinceJump;
    float boost, timeSinceBoosted;
    bool isBoosting;
    float boostingTime;
    bool isSupersonic;
    float supersonicTime;
    float handbrakeVal;
    bool isAutoFlipping;
    float autoFlipTimer, autoFlipTorqueScale;
    CscWorldContact worldContact;
    CscCarContact carContact;
    bool isDemoed;
    float demoRespawnTimer;
    CscBallHitInfo ballHitInfo;
    CscControls lastControls;
    /* ---- extras */
    int32_t index, team;
    float timeSpentBoosting;         /* older RocketSim versions' name for boostingTime */
    float supersonicGrace;           /* seconds below supersonic speed still counted supersonic (the game's) */
    bool jumpHoldBroken;             /* jump let go during this jump (RocketSim v3's jump_hold_broken) */
    float boostLockLeft;             /* seconds the minimum boost time still holds boost on */
    float pitchLockLeft;             /* seconds the flip's pitch lock still holds */
    bool canFlip, hasFlipReset;      /* RocketSim HasFlipOrJump(), HasFlipReset() */
    float dodgeTimeout;              /* RLBot's meaning: seconds left to dodge or double jump, -1 none */
    CscVec wheelNormals[4];
    int32_t wheelGround[4];          /* 0 arena, 1 car, 2 ball */
    float wheelSuspensionLength[4];  /* uu, as catSim's raycast vehicle holds it */
    float wheelSuspensionRest[4];
    float wheelSuspensionTravel[4];
    float wheelRadius[4];
    int32_t otherCarIndex;           /* packet index of the car last bumped while its cooldown runs, -1 none */
    /* ---- confidence */
    bool synced;                     /* the body matched the packet to its rounding: exact tracking */
    bool consistent;                 /* moved as physics says: flags from physics are trusted over the packet's */
    int64_t ticksSinceColdStart;
    float drift;                     /* uu off the packet at the last resync */
    uint32_t assumedFacts;           /* CSC_FACT_* bits still taken from a packet */
    bool inputsKnown;                /* controls over the predicted ticks were known (yours), not estimated */
} CscCarState;

typedef struct CscBallState { CscVec pos; CscRotMat rotMat; CscVec vel, angVel; } CscBallState;
typedef struct CscPadState { bool isActive; float cooldown, timer; bool isBig; } CscPadState;

typedef struct CscCorrected {
    int32_t frame;                   /* the physics frame these states are for */
    int32_t horizon;                 /* ticks predicted past the packet */
    int32_t input_delay;             /* D in use */
    int32_t num_cars;
    CscCarState cars[CSC_MAX_CARS];
    bool has_ball;
    CscBallState ball;
    int32_t num_pads;
    CscPadState pads[CSC_NUM_PADS];
} CscCorrected;

typedef struct CscFieldStats { uint64_t compared, agreed, packet_wrong, adopted; } CscFieldStats;
typedef struct CscFlagEvent {
    int32_t frame, car;
    const char* field;
    double packet, physics;
    int32_t decision;                /* 0 packet wrong (physics kept), 1 held, 2 adopted (packet taken) */
} CscFlagEvent;

typedef struct CscCorrector CscCorrector;
typedef void (*CscOpponentFn)(void* user, int32_t car, int32_t tick, CscControls* controls);

const char* csc_version(void);
/* input_delay: the real delay D (ticks from a packet to the frame your answer first applies; 1 = none), or 0 to
 * measure it. training_action_delay: the action delay your policy trained with. NULL on failure (message in err). */
CscCorrector* csc_new(const char* data_dir, int32_t own_index, int32_t training_action_delay, int32_t input_delay,
                      char* err, size_t err_len);
void csc_free(CscCorrector* c);
const char* csc_last_error(const CscCorrector* c);

int32_t csc_update(CscCorrector* c, const CscFrame* frame, CscCorrected* out_or_null); /* the packet's own frame */
int32_t csc_predict(CscCorrector* c, int32_t ticks, CscCorrected* out);                /* ticks = -1: the horizon */
int32_t csc_correct(CscCorrector* c, const CscFrame* frame, CscCorrected* out);        /* update + predict(horizon) */
/* These return 0 with a state, 1 when there is nothing to correct (no packet yet, or between a goal and the next
   kickoff: out->num_cars is then 0), and -1 on an error (csc_last_error). */
void csc_sent(CscCorrector* c, const CscControls* controls, int32_t frame);            /* frame = -1: the last packet */
void csc_record_sent(CscCorrector* c, const CscControls* controls, int32_t tick, int32_t index);

int32_t csc_input_delay(const CscCorrector* c);
int32_t csc_horizon(const CscCorrector* c);
int32_t csc_set_option(CscCorrector* c, const char* name, double value);
void csc_set_pad_locations(CscCorrector* c, const float* xy, int32_t count);
void csc_set_opponent_model(CscCorrector* c, CscOpponentFn f, void* user);
int32_t csc_field_stats(const CscCorrector* c, const char* field, CscFieldStats* out);
int32_t csc_flag_log(const CscCorrector* c, int32_t start, CscFlagEvent* out, int32_t max);

#ifdef __cplusplus
}
#endif
#endif
