"""Which model is eating the current 5-hour window.

The Anthropic API exposes one *unified* quota — `anthropic-ratelimit-unified-5h-*`
and its 7d/overage siblings apply to every model together. There is no
per-model rate-limit header (verified against a live Pro/Max account across
Haiku, Opus and Fable requests: identical header sets every time). So the
answer to "is Opus or Fable burning my session?" cannot come from the API.

It can come from Claude Code's own transcripts. Every assistant turn is
appended to `<config_dir>/projects/<slug>/<session>.jsonl` with the model id
and the full `usage` block. Summing a price weight over the turns inside the
current 5h window reconstructs the split the API won't tell us.

The result is an *approximation* of quota share: Anthropic doesn't publish the
weighting behind unified utilization, so we use list price as the proxy —
which is the right shape (an Opus token costs 5x a Haiku token) even if the
constant differs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

# Wire codes are what goes over BLE (the firmware maps them to labels and
# colors); the price pair is ($/MTok in, $/MTok out) used only as a ratio.
# Order matters — the first prefix that matches wins, so longer ids first.
_MODELS: list[tuple[str, str, float, float]] = [
    # id prefix            code    in     out
    ("claude-fable-5",     "f5",   10.0,  50.0),
    ("claude-mythos-5",    "m5",   10.0,  50.0),
    ("claude-mythos",      "m5",   10.0,  50.0),
    ("claude-opus-5",      "o5",    5.0,  25.0),
    ("claude-opus-4-8",    "o48",   5.0,  25.0),
    ("claude-opus-4-7",    "o47",   5.0,  25.0),
    ("claude-opus-4-6",    "o46",   5.0,  25.0),
    ("claude-opus-4-5",    "o45",   5.0,  25.0),
    ("claude-opus",        "op",    5.0,  25.0),
    ("claude-sonnet-5",    "s5",    3.0,  15.0),
    ("claude-sonnet-4-6",  "s46",   3.0,  15.0),
    ("claude-sonnet-4-5",  "s45",   3.0,  15.0),
    ("claude-sonnet",      "so",    3.0,  15.0),
    ("claude-haiku-4-5",   "h45",   1.0,   5.0),
    ("claude-haiku",       "ha",    1.0,   5.0),
]

# Unrecognized model — priced at the Opus tier so a brand-new top model isn't
# silently under-counted, and shown on the device as a neutral "Other" slice.
_UNKNOWN = ("?", 5.0, 25.0)

# Not a model: the share of the window that Claude Code's transcripts cannot
# account for — the desktop app, claude.ai, or the same plan on another
# machine. See daemon/unattributed.py for how it's measured.
UNATTRIBUTED = "~"

MAX_SLICES = 4          # what the firmware's UsageData has room for
_WINDOW_SECS = 5 * 3600  # the unified "5h" session window

# `claude-opus-5[1m]` (context variant) and `claude-haiku-4-5-20251001`
# (dated snapshot) are the same quota-wise as their base id.
_VARIANT_RE = re.compile(r"\[.*?\]|-\d{8}$")


def classify(model_id: str) -> tuple[str, float, float]:
    """Map a raw model id to its (wire code, input price, output price)."""
    base = _VARIANT_RE.sub("", (model_id or "").strip().lower())
    for prefix, code, pin, pout in _MODELS:
        if base.startswith(prefix):
            return code, pin, pout
    return _UNKNOWN


# Cache *writes* bill at 1.25x the input rate, matching list price. Cache
# *reads* are deliberately free here even though they bill at 0.1x: the
# unified quota appears not to count them at all. Measured on a live window —
# sweeping this weight from 0 to 0.10 made the observed "percent of window per
# dollar" drift by 1.38x to 1.63x across a stretch where cache-read intensity
# rose sharply, and only 0 kept it flat. Pricing them in made long cache-heavy
# agent turns look far more expensive than the quota treats them.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.0


def cost_weight(usage: dict, price_in: float, price_out: float) -> float:
    """Quota-weight of one turn, in list-price dollars.

    Not a billing figure — a proxy for how much of the window a turn consumed.
    See the multipliers above for where it deliberately parts from list price.
    """
    def n(key: str) -> float:
        v = usage.get(key)
        return float(v) if isinstance(v, (int, float)) else 0.0

    tokens_in = (
        n("input_tokens")
        + CACHE_WRITE_MULTIPLIER * n("cache_creation_input_tokens")
        + CACHE_READ_MULTIPLIER * n("cache_read_input_tokens")
    )
    return (tokens_in * price_in + n("output_tokens") * price_out) / 1_000_000.0


def _epoch(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def collect_weights(config_dir: Path, window_start: float,
                    window_end: float) -> dict[str, float]:
    """Sum per-model cost weight over the transcripts inside the window.

    Claude Code appends one record per *content block*, so a single assistant
    message shows up two or three times carrying the same aggregate `usage`.
    Dedup on `message.id` or the whole turn is counted several times over.
    """
    weights: dict[str, float] = {}
    seen: set[str] = set()
    projects = config_dir / "projects"
    if not projects.is_dir():
        return weights

    for path in sorted(projects.rglob("*.jsonl")):
        try:
            # A file last written before the window opened cannot hold a turn
            # inside it — skip without reading. Keeps the 60s poll cheap.
            if path.stat().st_mtime < window_start:
                continue
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if rec.get("type") != "assistant":
                    continue
                ts = _epoch(rec.get("timestamp", ""))
                if ts is None or not (window_start <= ts <= window_end):
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("id")
                if not isinstance(msg_id, str) or msg_id in seen:
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                seen.add(msg_id)
                code, pin, pout = classify(msg.get("model", ""))
                weights[code] = weights.get(code, 0.0) + cost_weight(usage, pin, pout)
    return weights


def to_slices(weights: dict[str, float],
              max_slices: int = MAX_SLICES) -> list[list]:
    """Turn raw weights into the wire form: [[code, pct], ...] summing to 100.

    Sorted heaviest-first and truncated to `max_slices`; anything past the cut
    is folded into a trailing "?" slice rather than dropped, so the bar on the
    device always spans the whole window. Slices that round to 0% are dropped
    (they'd be invisible anyway) and the rounding remainder goes to the
    largest slice, so the percentages always add up to exactly 100.
    """
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return []

    # The unattributed share is not a model and must never be folded into the
    # "?" overflow slice — "some other model" and "some other app" are
    # different answers. It gets its own reserved slot at the end.
    hidden = weights.get(UNATTRIBUTED, 0.0)
    budget = max(1, max_slices - 1) if hidden > 0 else max_slices

    ranked = sorted(((w, c) for c, w in weights.items()
                     if w > 0 and c != UNATTRIBUTED), reverse=True)
    if len(ranked) > budget:
        head, tail = ranked[:budget - 1], ranked[budget - 1:]
        ranked = head + [(sum(w for w, _ in tail), "?")]
    if hidden > 0:
        ranked.append((hidden, UNATTRIBUTED))

    slices = [[code, int(round(w / total * 100))] for w, code in ranked]
    slices = [s for s in slices if s[1] > 0]
    if not slices:
        return []
    slices[0][1] += 100 - sum(pct for _, pct in slices)
    return slices


def window_weights(config_dir: Path, reset_epoch: float) -> dict[str, float]:
    """Per-model cost weight inside the 5h window that ends at `reset_epoch`."""
    if not reset_epoch or reset_epoch <= 0:
        return {}
    return collect_weights(config_dir, reset_epoch - _WINDOW_SECS, reset_epoch)


def add_unattributed(weights: dict[str, float], utilization_pct: float,
                     hidden_pct: float) -> dict[str, float]:
    """Fold the share of the window Claude Code can't account for into
    `weights`, as the `"~"` ("Elsewhere") pseudo-model.

    Everything here is in cost-weight units so the caller can keep treating the
    result as one flat weight map. The remainder arrives in *percentage points
    of the window*, so it is converted at the rate the local turns imply:
    `local_total` dollars stand for `utilization - hidden` percent.
    """
    local_total = sum(w for w in weights.values() if w > 0)
    explained = utilization_pct - hidden_pct
    if hidden_pct <= 0 or local_total <= 0 or explained <= 0:
        return weights
    out = dict(weights)
    out[UNATTRIBUTED] = local_total * hidden_pct / explained
    return out


def model_split(config_dir: Path, reset_epoch: float,
                max_slices: int = MAX_SLICES) -> list[list]:
    """Per-model share of the 5h window that ends at `reset_epoch`.

    Returns [] when nothing usable was found — no transcripts, no turns in the
    window, an unreadable projects dir — and the caller then omits the field
    so the device falls back to its plain single-color bar.
    """
    return to_slices(window_weights(config_dir, reset_epoch), max_slices)
