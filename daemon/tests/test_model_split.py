#!/usr/bin/env python3
"""Tests for the per-model split of the 5h window.

The API has no per-model rate limit, so the split is reconstructed from
Claude Code's transcripts. These cover the three things that actually bite:
the duplicate-record shape Claude Code writes, the window boundary, and the
percentages adding up to exactly 100.

Run: python -m pytest daemon/tests/test_model_split.py -x -q
"""
import json
import os
import time

from daemon.model_split import (
    classify,
    collect_weights,
    cost_weight,
    model_split,
    to_slices,
)

RESET = 1_800_000_000.0          # arbitrary "5h window ends here"
WINDOW_START = RESET - 5 * 3600


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + ".000Z"


def write_transcript(config_dir, name, records):
    proj = config_dir / "projects" / "-Users-someone-repo"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    # Every fixture file must look freshly written or the mtime pre-filter
    # (which skips files older than the window) throws it away.
    os.utime(path, (RESET, RESET))
    return path


def turn(msg_id, model, epoch, output_tokens=1000, **usage):
    return {
        "type": "assistant",
        "timestamp": iso(epoch),
        "requestId": "req_" + msg_id,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {"input_tokens": 0, "output_tokens": output_tokens, **usage},
        },
    }


# --- classify ---------------------------------------------------------------

def test_classify_known_models():
    assert classify("claude-opus-5")[0] == "o5"
    assert classify("claude-fable-5")[0] == "f5"
    assert classify("claude-opus-4-7")[0] == "o47"
    assert classify("claude-sonnet-5")[0] == "s5"
    assert classify("claude-haiku-4-5")[0] == "h45"


def test_classify_strips_variant_and_date_suffixes():
    # `[1m]` is the long-context variant; `-20251001` a dated snapshot. Both
    # bill against the same quota as the base model.
    assert classify("claude-opus-5[1m]")[0] == "o5"
    assert classify("claude-haiku-4-5-20251001")[0] == "h45"


def test_classify_unknown_model_prices_at_opus_tier():
    # A model released after this code was written must not be under-counted.
    code, pin, pout = classify("claude-something-9")
    assert code == "?"
    assert (pin, pout) == classify("claude-opus-5")[1:]


def test_fable_outweighs_opus_per_token():
    usage = {"output_tokens": 1000}
    _, fin, fout = classify("claude-fable-5")
    _, oin, oout = classify("claude-opus-5")
    assert cost_weight(usage, fin, fout) == 2 * cost_weight(usage, oin, oout)


def test_cache_reads_do_not_count_toward_the_quota():
    # They bill at 0.1x, but the unified quota appears to ignore them — see
    # CACHE_READ_MULTIPLIER. Pricing them in made long agent turns look far
    # more expensive than the window actually treated them.
    assert cost_weight({"cache_read_input_tokens": 10_000_000}, 5.0, 25.0) == 0.0


def test_cache_writes_still_count_above_fresh_input():
    fresh = cost_weight({"input_tokens": 1_000_000}, 5.0, 25.0)
    written = cost_weight({"cache_creation_input_tokens": 1_000_000}, 5.0, 25.0)
    assert written == 1.25 * fresh


# --- collect_weights --------------------------------------------------------

def test_dedups_repeated_records_for_one_message(tmp_path):
    # Claude Code appends one record per content block, each carrying the same
    # aggregate usage. Counting them all would triple this turn.
    t = turn("msg_a", "claude-opus-5", RESET - 60)
    write_transcript(tmp_path, "s1", [t, t, t])
    weights = collect_weights(tmp_path, WINDOW_START, RESET)
    assert weights == {"o5": cost_weight(t["message"]["usage"], 5.0, 25.0)}


def test_ignores_turns_outside_the_window(tmp_path):
    write_transcript(tmp_path, "s1", [
        turn("msg_old", "claude-opus-5", WINDOW_START - 60),
        turn("msg_in", "claude-sonnet-5", RESET - 60),
        turn("msg_future", "claude-fable-5", RESET + 60),
    ])
    assert set(collect_weights(tmp_path, WINDOW_START, RESET)) == {"s5"}


def test_skips_files_last_written_before_the_window(tmp_path):
    path = write_transcript(tmp_path, "stale",
                            [turn("msg_a", "claude-opus-5", RESET - 60)])
    os.utime(path, (WINDOW_START - 1, WINDOW_START - 1))
    assert collect_weights(tmp_path, WINDOW_START, RESET) == {}


def test_sums_across_sessions_and_models(tmp_path):
    write_transcript(tmp_path, "s1", [turn("m1", "claude-opus-5", RESET - 60)])
    write_transcript(tmp_path, "s2", [turn("m2", "claude-opus-5", RESET - 50),
                                      turn("m3", "claude-haiku-4-5", RESET - 40)])
    weights = collect_weights(tmp_path, WINDOW_START, RESET)
    assert set(weights) == {"o5", "h45"}
    assert weights["o5"] == 2 * cost_weight({"output_tokens": 1000}, 5.0, 25.0)


def test_survives_malformed_lines(tmp_path):
    proj = tmp_path / "projects" / "-repo"
    proj.mkdir(parents=True)
    path = proj / "s1.jsonl"
    path.write_text(
        '{"type": "assistant"  <-- truncated\n'
        '{"type": "assistant", "timestamp": "not-a-date", "message": {"id": "x"}}\n'
        '{"type": "assistant", "message": "a string, not an object"}\n'
        + json.dumps(turn("m1", "claude-opus-5", RESET - 60)) + "\n"
    )
    os.utime(path, (RESET, RESET))
    assert set(collect_weights(tmp_path, WINDOW_START, RESET)) == {"o5"}


def test_missing_projects_dir_is_not_an_error(tmp_path):
    assert collect_weights(tmp_path, WINDOW_START, RESET) == {}


# --- to_slices --------------------------------------------------------------

def test_slices_sum_to_exactly_100():
    # Three equal shares round to 33 each; the remainder lands on the largest.
    slices = to_slices({"o5": 1.0, "s5": 1.0, "h45": 1.0})
    assert sum(pct for _, pct in slices) == 100
    assert slices[0][1] == 34


def test_slices_are_sorted_heaviest_first():
    slices = to_slices({"h45": 1.0, "o5": 8.0, "s5": 4.0})
    assert [c for c, _ in slices] == ["o5", "s5", "h45"]


def test_overflow_models_fold_into_one_other_slice():
    weights = {"o5": 10.0, "f5": 8.0, "s5": 6.0, "h45": 4.0, "o47": 2.0}
    slices = to_slices(weights, max_slices=4)
    assert len(slices) == 4
    assert slices[-1][0] == "?"          # h45 + o47 merged, not dropped
    assert sum(pct for _, pct in slices) == 100


def test_invisible_slices_are_dropped():
    # 0.05% would render as a sub-pixel segment — not worth a wire slot.
    slices = to_slices({"o5": 2000.0, "h45": 1.0})
    assert slices == [["o5", 100]]


def test_no_usage_yields_no_slices():
    assert to_slices({}) == []
    assert to_slices({"o5": 0.0}) == []


# --- model_split ------------------------------------------------------------

def test_model_split_end_to_end(tmp_path):
    # Equal output tokens, but Fable costs 2x Opus per token → 67/33.
    write_transcript(tmp_path, "s1", [
        turn("m1", "claude-fable-5", RESET - 120),
        turn("m2", "claude-opus-5", RESET - 60),
    ])
    assert model_split(tmp_path, RESET) == [["f5", 67], ["o5", 33]]


def test_model_split_without_reset_returns_nothing(tmp_path):
    # No 5h reset header (e.g. an Enterprise account) → no window to scope to.
    write_transcript(tmp_path, "s1", [turn("m1", "claude-opus-5", RESET - 60)])
    assert model_split(tmp_path, 0) == []


# --- the unattributed slice -------------------------------------------------

def test_unattributed_slice_is_proportional_to_the_gap():
    # 20$ of Claude Code accounted for 40 of the window's 60 points, so the
    # other 20 points are worth another 10$ at the same rate -> a third.
    from daemon.model_split import add_unattributed
    folded = add_unattributed({"o5": 20.0}, utilization_pct=60.0, hidden_pct=20.0)
    assert to_slices(folded) == [["o5", 67], ["~", 33]]


def test_unattributed_is_never_folded_into_the_other_slice():
    # "some other model" and "some other app" are different answers — the
    # overflow fold must not merge them.
    from daemon.model_split import UNATTRIBUTED
    weights = {"o5": 10.0, "f5": 8.0, "s5": 6.0, "h45": 4.0, UNATTRIBUTED: 1.0}
    slices = to_slices(weights, max_slices=4)
    assert len(slices) == 4
    assert slices[-1][0] == UNATTRIBUTED          # kept its own reserved slot
    assert slices[-2][0] == "?"                   # s5 + h45 merged instead
    assert sum(pct for _, pct in slices) == 100


def test_no_gap_means_no_extra_slice():
    from daemon.model_split import add_unattributed
    assert add_unattributed({"o5": 20.0}, 40.0, 0.0) == {"o5": 20.0}


def test_gap_without_local_work_is_ignored():
    # Nothing local to scale against — the caller has no split to send anyway.
    from daemon.model_split import add_unattributed
    assert add_unattributed({}, 40.0, 40.0) == {}
