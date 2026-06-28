"""
Phase 4, piece 2: DRILL_DOWN.

The operator that makes the evidence set both causally complete AND
context-window-sized. Phases 1-3 returned the causal SPINE (span_ids). DRILL_DOWN
decides how much CONTENT each part of the timeline contributes:

  - spans ON the causal path  -> RAW, full detail (the LLM needs to reason here)
  - everything else in window -> COARSE summary (one aggregate line, not N spans)

That asymmetry is the whole bounded-context claim: high resolution exactly where
the cause lives, a cheap summary everywhere else. An LLM sees the full picture
without drowning in raw spans it doesn't need.

It composes on top of the earlier operators:
    SUSTAINED_DRIFT -> symptom span        (Phase 3, where)
    TRACE_CAUSE     -> causal path spans   (Phase 1, why)
    DRILL_DOWN      -> path raw + rest coarse (Phase 4, how much detail)

The evidence it returns is the prompt. The token win shows up in evaluate.py:
DRILL_DOWN should beat even TRACE_CAUSE-as-spans on tokens once the coarse layer
replaces raw siblings/neighbors with a one-line summary — and beats the dumps by
a wide margin.
"""

import json
from typing import Optional
from store import AggregationStore


def drill_down(
    causal_path: list[str],          # span_ids on the path (from TRACE_CAUSE)
    window_trace_ids: list[str],     # traces forming the coarse-summary context
    store: AggregationStore,
) -> dict:
    """
    Assemble the bounded evidence set:

      raw      = full records for the causal-path spans (point lookup)
      summary  = coarse per-event_type aggregate over the window (GROUP BY)

    Returns {"raw": [...], "summary": [...]}. The caller serializes this into a
    prompt; the summary is a handful of lines no matter how many spans the window
    holds, while raw stays small because the path is short.
    """
    raw = store.fetch_raw(causal_path)
    summary = store.summarize(window_trace_ids)
    return {"raw": raw, "summary": summary}


def evidence_to_prompt(evidence: dict) -> str:
    """
    Render the DRILL_DOWN evidence into the text an LLM receives. The summary is
    a compact header (the coarse layer); the raw records follow (the path detail).
    This is DRILL_DOWN's own serializer because its evidence has structure the
    flat span-set serializer doesn't — but it must be counted by the SAME
    tokenizer as every other strategy for the comparison to stay fair.
    """
    lines = ["== SUMMARY (coarse, whole window) =="]
    for row in evidence["summary"]:
        avg = row.get("avg_heap")
        avg_s = f" avg_heap={avg:.1f}" if avg is not None else ""
        lines.append(f"{row['event_type']}: {row['n']} spans{avg_s}")

    lines.append("== CAUSAL PATH (raw, full detail) ==")
    raw_sorted = sorted(evidence["raw"], key=lambda r: r["ts"])
    for r in raw_sorted:
        lines.append(json.dumps(r, separators=(",", ":")))

    return "\n".join(lines)
