#!/usr/bin/env python3
"""Tests for the unattributed-usage tracker.

The tracker's whole job is to notice window that Claude Code did not cause.
The tests that matter are the ones about *which way it errs*: it must never
invent usage that isn't there, and it must survive a restart with its learned
rate intact.

Run: python -m pytest daemon/tests/test_unattributed.py -x -q
"""
import json

from daemon.unattributed import MIN_HIDDEN_PCT, UnattributedTracker

WINDOW = 1_800_000_000.0     # the 5h reset epoch identifying one window


def tracker(tmp_path):
    t = UnattributedTracker(tmp_path / "state.json")
    # Bind the raw two-arg calls below to one window so each test reads as
    # "these samples came from the same 5h stretch".
    return lambda u, l, window=WINDOW: t.update(window, u, l)


def teach(t):
    """Give the tracker a clean pair far enough apart to fix k at 0.5 %/$."""
    t(4.0, 8.0)
    t(10.0, 20.0)


def test_no_estimate_yet_reports_nothing(tmp_path):
    # A single sample is not a pair; nothing can be learned, nothing claimed.
    t = tracker(tmp_path)
    assert t(2.0, 4.0) == 0.0


def test_claude_code_only_leaves_no_remainder(tmp_path):
    # Utilization tracks cost exactly: k is learned and explains all of it.
    t = tracker(tmp_path)
    for cost in (2.0, 8.0, 20.0, 40.0, 80.0):
        assert t(cost * 0.5, cost) == 0.0


def test_usage_from_elsewhere_shows_up(tmp_path):
    t = tracker(tmp_path)
    teach(t)
    # Local cost unchanged but the window jumped 25 points — that's the
    # desktop app, and all 25 points of it are unattributed.
    assert abs(t(35.0, 20.0) - 25.0) < 0.5


def test_mixed_interval_still_separates_the_two(tmp_path):
    t = tracker(tmp_path)
    teach(t)
    hidden = t(40.0, 40.0)                    # 40$ explains 20 points; 20 are not
    assert abs(hidden - 20.0) < 0.5


def test_pairs_ignore_history_before_the_first_sample(tmp_path):
    # The window opened long before the daemon started watching: utilization
    # is already at 40 with only 4$ of local cost on record. A cumulative
    # ratio would read 10 %/$ and explain away everything that follows; paired
    # differences see the true 0.5 and surface the 36-point head start.
    t = tracker(tmp_path)
    t(40.0, 4.0)
    t(45.0, 14.0)                             # pair gives 5/10 = 0.5
    assert abs(t(45.0, 14.0) - (45.0 - 0.5 * 14.0)) < 0.5


def test_never_reports_more_than_the_window(tmp_path):
    t = tracker(tmp_path)
    teach(t)
    assert t(30.0, 0.0) <= 30.0


def test_a_sliver_is_not_reported(tmp_path):
    # Estimation noise must not put a permanent segment on the bar.
    t = tracker(tmp_path)
    teach(t)
    assert t(10.0 + MIN_HIDDEN_PCT / 2, 20.0) == 0.0


def test_samples_too_close_together_are_not_paired(tmp_path):
    # Utilization moves in whole percent; a 1-point step would put a 100%
    # quantization error straight into k.
    t = tracker(tmp_path)
    t(10.0, 20.0)
    t(11.0, 20.1)                             # 1 point apart -> no pair, no k
    assert t(11.0, 20.1) == 0.0


def test_k_is_the_lowest_ratio_seen(tmp_path):
    # A polluted pair must not win over a clean one, whichever came first.
    t = tracker(tmp_path)
    t(0.0, 0.0)
    t(30.0, 20.0)                             # polluted pair: 1.5 %/$
    t(40.0, 40.0)                             # cleaner pair: 0.5 %/$
    assert abs(t(65.0, 40.0) - (65.0 - 0.5 * 40.0)) < 1.0


def test_new_window_drops_stale_samples(tmp_path):
    # Utilization restarts at zero, so cross-window pairs would be negative
    # nonsense. k itself is a platform property and must survive.
    t = tracker(tmp_path)
    teach(t)
    later = t(35.0, 20.0, WINDOW + 18000)     # next window, same local cost
    assert abs(later - 25.0) < 0.5            # k carried over, still detects


def test_estimate_survives_a_restart(tmp_path):
    # Without persistence every daemon restart would go blind for an hour.
    teach(tracker(tmp_path))
    revived = tracker(tmp_path)
    assert abs(revived(35.0, 20.0) - 25.0) < 0.5


def test_corrupt_state_is_relearned_not_fatal(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    t = tracker(tmp_path)
    teach(t)
    assert abs(t(35.0, 20.0) - 25.0) < 0.5


def test_unwritable_state_dir_does_not_raise(tmp_path):
    bad = UnattributedTracker(tmp_path / "nope" / "\0bad" / "state.json")
    assert bad.update(WINDOW, 10.0, 20.0) == 0.0


def test_state_file_holds_the_learned_rate(tmp_path):
    t = tracker(tmp_path)
    teach(t)
    saved = json.loads((tmp_path / "state.json").read_text())
    assert 0.4 < saved["k"] < 0.6


def test_leak_lets_an_underestimate_heal(tmp_path):
    # A k pushed too low would otherwise report phantom usage forever.
    t = tracker(tmp_path)
    t(0.0, 0.0)
    t(5.0, 50.0)                              # k = 0.1, far too low
    for _ in range(2000):                     # ~33h of polls at 60s
        t(0.0, 0.0)
    assert t(30.0, 60.0) == 0.0               # k has healed past 0.5


def test_running_totals_give_an_estimate_before_any_pair_exists(tmp_path):
    # Pairs need the window to move three points, which can take half an hour.
    # The running-total ratio is coarser but available at once — without it
    # the bar would show nothing at all for the first stretch of a window.
    t = tracker(tmp_path)
    assert t(60.0, 20.0) == 0.0          # first sample: k := 3.0, explains it all
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["k"] is not None
    # Same local cost, window jumped 30 points — caught without ever pairing.
    assert abs(t(90.0, 20.0) - 30.0) < 1.0
