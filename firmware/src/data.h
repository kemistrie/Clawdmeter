#pragma once
#include <Arduino.h>

// The Anthropic API bills every model against one unified quota — there is no
// per-model rate-limit header — so the daemon reconstructs the split from
// Claude Code's local transcripts and sends it as "ms". Absent (model_count
// == 0) on older daemons and whenever the user hasn't opted in; the usage
// screen then draws the plain single-color bar it always did.
#define MODEL_SLICES_MAX 4

struct ModelSlice {
    char    code[6];  // wire code: "o5", "f5", "o47", "h45", "?" (unrecognized)
    uint8_t pct;      // share of the window's consumption; slices sum to 100
};

struct UsageData {
    float session_pct;       // utilization 0-100 (5h window Pro/Max; spending % Enterprise)
    int session_reset_mins;  // minutes until reset
    float weekly_pct;        // 7-day utilization (Pro/Max only; 0 for Enterprise)
    int weekly_reset_mins;   // minutes until weekly reset (Pro/Max only)
    char status[16];         // "allowed", "limited", etc.
    bool chime;              // play the session-reset chime; false unless daemon opts in
    bool enterprise;         // true = Enterprise spending-limit account
    int time_pct;            // 0-100: fraction of billing period elapsed (Enterprise)
    int period_days;         // total billing period length in days (Enterprise)
    char reset_date[12];     // formatted reset date e.g. "Jul 1" (Enterprise)
    long clock_epoch;        // local wall-clock epoch (s) from daemon; 0 = not provided
    int  clock_fmt;          // 12 or 24 (hour format from daemon); defaults to 24
    ModelSlice models[MODEL_SLICES_MAX];  // which models ate the 5h window
    uint8_t model_count;     // 0 = no split available; draw the plain bar
    bool ok;                 // data parse succeeded
    bool valid;              // false until first successful parse
};
