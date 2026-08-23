"""How much of the 5h window did something other than Claude Code use up.

The per-model split can only see Claude Code's transcripts. The Claude desktop
app, claude.ai in a browser, and the same subscription on a second machine all
draw on the *same* unified quota and leave nothing behind locally — so a split
built from transcripts alone silently reports "Opus 5 100%" while Fable in the
desktop app is eating the window next to it.

That gap is measurable even though its contents aren't. Two numbers arrive
every poll: `u`, the window's utilization in percent (from the API header),
and `l`, the list-price cost of the Claude Code turns inside the same window
(from the transcripts). If Claude Code were the only consumer these would be
proportional, `u = k * l`, for some constant `k` — percent of window per
dollar — that we don't know but can learn.

Anything else consuming the window only ever pushes `u` up, never down, so
every observed `u / l` is an *over*estimate of `k`:

    u / l  =  k + hidden / l  >=  k

The smallest ratio seen is therefore the best available estimate of `k`, and
it is exact for any moment when nothing else was running. Whatever `k * l`
fails to explain is the unattributed remainder.

Two kinds of ratio feed the estimate, and the smallest of all of them wins:

* the **running totals**, `u / l`. Available from the first poll, but a
  window's totals carry whatever happened before the daemon started watching
  it, so this only creeps toward the true rate.
* the difference between a **pair of samples**, which cancels that history
  out and is the sharper estimate — but needs the window to move a few
  percent before it says anything.

Totals alone converge too slowly to catch a gap inside one window; pairs alone
say nothing for the first half hour. Both are valid upper bounds on `k`, so
taking the minimum over both is strictly better than either.

Failure direction matters here and it is the safe one: if something else was
running during *every* pair, `k` stays too high and the remainder is
under-reported. The tracker never invents usage that isn't there.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Floors for the running-total ratio: the utilization header carries two
# decimals, so below a few percent a single rounding step swamps the reading.
MIN_UTIL_FOR_K = 5.0     # percent
MIN_COST_FOR_K = 0.50    # dollars of Claude Code work

# A pair of samples has to be far enough apart to say anything, for the same
# reason — utilization only ever moves in whole percent, so three points keeps
# the quantization error under a third.
MIN_DELTA_UTIL = 3.0     # percentage points between the two samples
MIN_DELTA_COST = 0.20    # dollars of Claude Code work between them

# How many samples to hold for pairing. At a 60s poll this reaches back two
# hours, comfortably inside one 5h window.
MAX_SAMPLES = 120

# k is a property of the platform's token weighting, not of one window, so it
# carries across windows and restarts. A slow upward leak lets an estimate
# that got pushed too low (a quantization artifact, a transcript the daemon
# couldn't read) heal on its own instead of skewing every later reading; a
# genuinely lower observation still snaps it straight back down.
K_LEAK_PER_POLL = 1.002  # ~+12%/h at a 60s poll

# A remainder smaller than this is indistinguishable from estimation error,
# and rendering it would put a permanent sliver on the bar.
MIN_HIDDEN_PCT = 2.0


class UnattributedTracker:
    """Learns `k` from sample pairs and reports the unexplained share.

    State is one small JSON file so the estimate survives daemon restarts —
    without it every restart would start blind and under-report for an hour.
    """

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self._k: float | None = None
        self._reset: float = 0.0
        self._samples: list[tuple[float, float]] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.state_path.read_text())
            k = raw.get("k")
            if isinstance(k, (int, float)) and k > 0:
                self._k = float(k)
            self._reset = float(raw.get("reset") or 0.0)
            self._samples = [(float(u), float(l))
                             for u, l in raw.get("samples") or []]
        except (OSError, ValueError, TypeError, AttributeError):
            # No state yet, or it's corrupt — relearn from scratch.
            self._k, self._reset, self._samples = None, 0.0, []

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "k": self._k,
                "reset": self._reset,
                "samples": [[u, l] for u, l in self._samples],
                "updated": int(time.time()),
            }))
            tmp.replace(self.state_path)   # atomic: a torn file would be relearned
        except (OSError, ValueError):
            # An unwritable or malformed state path costs accuracy after a
            # restart, never correctness — and must never take the daemon down.
            pass

    def update(self, reset_epoch: float, utilization_pct: float,
               local_cost: float) -> float:
        """Fold in this poll and return the unattributed percentage points.

        `reset_epoch` identifies the window — samples from a previous one can't
        be paired against this one. `utilization_pct` is the window's 0-100
        utilization, `local_cost` the Claude Code cost inside the same window.
        Returns 0.0 while `k` is still unknown, and never more than the
        utilization itself.
        """
        self._load()

        if reset_epoch != self._reset:
            # New window: utilization restarts from zero, so old samples would
            # pair into nonsense. `k` is a property of the platform, not of the
            # window, so that survives.
            self._reset = reset_epoch
            self._samples = []

        if self._k is not None:
            self._k *= K_LEAK_PER_POLL

        # Running totals: coarse, but available immediately.
        if utilization_pct >= MIN_UTIL_FOR_K and local_cost >= MIN_COST_FOR_K:
            ratio = utilization_pct / local_cost
            self._k = ratio if self._k is None else min(self._k, ratio)

        # Sample pairs: slower to arrive, immune to the window's pre-history.
        for prev_u, prev_l in self._samples:
            if (utilization_pct - prev_u) < MIN_DELTA_UTIL:
                continue
            delta_cost = local_cost - prev_l
            if delta_cost < MIN_DELTA_COST:
                continue
            ratio = (utilization_pct - prev_u) / delta_cost
            self._k = ratio if self._k is None else min(self._k, ratio)

        self._samples.append((utilization_pct, local_cost))
        if len(self._samples) > MAX_SAMPLES:
            del self._samples[:-MAX_SAMPLES]
        self._save()

        if self._k is None:
            return 0.0
        hidden = utilization_pct - self._k * local_cost
        if hidden < MIN_HIDDEN_PCT:
            return 0.0
        return min(hidden, utilization_pct)
