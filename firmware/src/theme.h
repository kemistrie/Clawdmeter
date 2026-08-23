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
// family (Fable terra-cotta, Opus plum, Sonnet green, Haiku slate), shaded by
// generation inside a family so Opus 5 and Opus 4.7 stay apart side by side.
// Fable carries the brand orange: it's the top model, so the bar's loudest
// color goes to the most expensive consumer.
#define THEME_M_FABLE     0xd97757
#define THEME_M_MYTHOS    0xc06744
#define THEME_M_OPUS_5    0xa8577e
#define THEME_M_OPUS_48   0x934c6e
#define THEME_M_OPUS_47   0x7e415e
#define THEME_M_OPUS_46   0x69364e
#define THEME_M_OPUS_OLD  0x552c3f
#define THEME_M_SONNET_5  0x788c5d
#define THEME_M_SONNET_46 0x5f7349
#define THEME_M_SONNET_OLD 0x4c5c3b
#define THEME_M_HAIKU     0x6a8caf
// The two "we can't name this" swatches. Both are near-neutral on purpose:
// the unattributed slice can easily be most of the bar, so the named model
// segments have to win the eye by *saturation* rather than by area. "Other"
// stays dark because it competes with model colors for meaning; "Elsewhere"
// is light because it reads as unfilled paper rather than as a participant.
#define THEME_M_OTHER     0x6b6960
// Not a model: the share of the window Claude Code can't account for (desktop
// app, claude.ai, the same plan on another machine). Soft wheat — yellow is
// the one hue no model family occupies (Fable orange, Opus plum, Sonnet green,
// Haiku slate). Kept light and low-chroma: darker golds turn mustard, and
// saturated yellow would read as a warning state.
#define THEME_M_ELSEWHERE 0xc9a45c
