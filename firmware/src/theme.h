#pragma once
#include <lvgl.h>

// Design tokens — single source of truth for UI colors. Anthropic-inspired
// dark palette, AMOLED-friendly (true black bg).
#define THEME_BG       lv_color_hex(0x000000)   // screen background
#define THEME_PANEL    lv_color_hex(0x1f1f1e)   // card/zone fill
#define THEME_TEXT     lv_color_hex(0xfaf9f5)   // primary text
#define THEME_DIM      lv_color_hex(0xb0aea5)   // secondary text
#define THEME_ACCENT   lv_color_hex(0xd97757)   // brand terra-cotta
#define THEME_GREEN    lv_color_hex(0x788c5d)
#define THEME_AMBER    lv_color_hex(0xd97757)
#define THEME_RED      lv_color_hex(0xc0392b)
#define THEME_BAR_BG   lv_color_hex(0x2a2a28)   // unfilled bar track

// Per-model segment colors for the "Current" bar breakdown. One hue per model
// family (Opus terra-cotta, Sonnet green, Fable plum, Haiku slate), shaded by
// generation inside a family so Opus 5 and Opus 4.7 stay apart side by side.
#define THEME_M_FABLE     0xa8577e
#define THEME_M_MYTHOS    0x8e5aa0
#define THEME_M_OPUS_5    0xd97757
#define THEME_M_OPUS_48   0xc06744
#define THEME_M_OPUS_47   0xa85a38
#define THEME_M_OPUS_46   0x8c4a2e
#define THEME_M_OPUS_OLD  0x74402a
#define THEME_M_SONNET_5  0x788c5d
#define THEME_M_SONNET_46 0x5f7349
#define THEME_M_SONNET_OLD 0x4c5c3b
#define THEME_M_HAIKU     0x6a8caf
#define THEME_M_OTHER     0xb0aea5
// Not a model: the share of the window Claude Code can't account for (desktop
// app, claude.ai, the same plan on another machine). Deliberately the most
// muted swatch — it names an absence of information, not a participant.
#define THEME_M_ELSEWHERE 0x8a8880
