"""
Phase 3, piece 1: SUSTAINED_DRIFT.

The second operator. TRACE_CAUSE answered "given the symptom, what's the cause?"
but it cheated — it was handed a span labelled FAULT_INJECTED. SUSTAINED_DRIFT
removes the cheat: it FINDS the symptom by detecting a sustained upward trend in a
metric (heap_mb) across the time-ordered stream, with no labels.

Why windowed, not global: ordered by time the metric ramps during the leak then
drops back to baseline. A single regression over everything is meaningless (the
drop cancels the ramp). Drift is LOCAL — so we slide a window and ask, at each
position, "is the metric trending up *here*?" The leak region answers yes; the
flat region answers no. The first window that fires is drift onset; its span is
the symptom handed to TRACE_CAUSE.

"Sustained" = the least-squares slope over W consecutive points exceeds theta.
A lone spike doesn't move a 7-point slope; a real climb does. That's what
separates signal from the heavy noise (SIGMA ~2x the per-step SLOPE).
"""

from typing import Optional


def _slope(ys: list[float]) -> float:
    """
    Least-squares slope of ys against x = 0,1,2,...,n-1.

    slope = Σ(x-x̄)(y-ȳ) / Σ(x-x̄)²

    x is just the index, so x̄ and Σ(x-x̄)² are constants for a given window
    length — but we compute them plainly here for clarity, not speed.
    Returns 0.0 for a degenerate (length<2) window.
    """
    n = len(ys)
    if n < 2:
        return 0.0
    xbar = (n - 1) / 2.0                 # mean of 0..n-1
    ybar = sum(ys) / n
    num = sum((i - xbar) * (y - ybar) for i, y in enumerate(ys))
    den = sum((i - xbar) ** 2 for i in range(n))
    return num / den if den else 0.0


def sustained_drift(
    series: list[tuple[float, float, str]],   # (ts, metric_value, span_id), time-ordered
    window: int = 12,
    threshold: float = 3.0,
    persist: int = 3,                          # how many consecutive windows must drift
) -> Optional[str]:
    """
    Scan a time-ordered metric series for the onset of SUSTAINED upward drift.

    series: list of (ts, value, span_id), already sorted by ts. One entry per
            trace (the compute span's heap_mb and its span_id).
    window: how many consecutive points each slope is fit over.
    threshold: MB-per-trace slope above which a single window counts as drifting.
    persist: how many CONSECUTIVE drifting windows are required before we believe
             it. This is what makes drift "sustained": noise can fake one window
             by luck, but faking `persist` in a row is exponentially unlikely.
             A clean stream gets the occasional lucky window and is correctly
             rejected because the run never reaches `persist`.

    Returns the span_id at drift ONSET — the first point of the first window in
    the first qualifying run — the symptom handed to TRACE_CAUSE. Returns None if
    no run of `persist` consecutive drifting windows exists (correct on clean).
    """
    values = [v for (_ts, v, _sid) in series]
    n_windows = len(values) - window + 1
    if n_windows <= 0:
        return None

    run_start = None          # index where the current drifting run began
    run_len = 0               # how many consecutive drifting windows so far

    for start in range(n_windows):
        drifting = _slope(values[start:start + window]) > threshold

        if drifting:
            if run_len == 0:
                run_start = start         # remember where this run began
            run_len += 1
            if run_len >= persist:
                # onset = first span of the window that STARTED the run
                return series[run_start][2]
        else:
            run_len = 0                    # break in drift resets the run
            run_start = None

    return None   # no run long enough -> no sustained drift -> no symptom


# Helper: turn raw log records into the (ts, heap_mb, span_id) series the
# detector consumes. Only spans that carry heap_mb participate (the compute
# spans); everything else is irrelevant to the metric trend.
def build_metric_series(records: dict[str, dict], metric: str = "heap_mb"):
    series = [
        (r["ts"], r[metric], r["span_id"])
        for r in records.values()
        if metric in r
    ]
    series.sort(key=lambda t: t[0])     # time order is what makes drift detectable
    return series
