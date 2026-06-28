"""
Phase 2 + 4: retrieval strategies, extended.

A STRATEGY is a row in the results table. Originally a strategy returned just a
span set and the harness derived the prompt from it. Phase 4's DRILL_DOWN breaks
that: it returns STRUCTURED evidence (raw causal path + coarse summary), so its
token cost is NOT derivable from a flat span set. So a strategy now returns a
StrategyResult with TWO fields:

    span_set : the spans it claims are causal   -> scored for precision/recall
    prompt   : the exact text an LLM receives   -> counted for tokens

For the simple strategies the prompt is derived from the span set (same as
before). For DRILL_DOWN the two diverge: precision/recall come from the raw path
spans only, while the prompt includes the coarse summary too. This separation is
the honest way to score DRILL_DOWN -- it keeps TRACE_CAUSE's exact accuracy while
its token line reflects the full rendered evidence.

Rows, in order of increasing context:
  trace_cause : causal spine only. Bounded, causally complete, no system context.
  drill_down  : causal spine RAW + a system-wide coarse census. Same accuracy as
                trace_cause, a few more tokens, but conveys situational awareness
                the bare spine lacks. Its win is awareness-per-token vs the dumps.
  whole_trace : every span in the trace. Recall ok, precision down (siblings).
  whole_logs  : dump everything. The full-dump baseline; catastrophic tokens.
"""

from dataclasses import dataclass
from typing import Optional, Callable

from trace_cause import trace_cause, find_symptom
from serialize import evidence_to_prompt as flat_evidence_to_prompt
from drill_down import drill_down, evidence_to_prompt as drill_evidence_to_prompt


@dataclass
class StrategyResult:
    span_set: set          # for precision/recall
    prompt: str            # for token count
    conveys_context: bool  # does this strategy give system-wide situational awareness?


# ---- simple strategies: return a span set; harness renders the flat prompt ----
# These keep their original span-set logic; we wrap them so the prompt is the
# flat rendering of exactly those spans (unchanged behavior from Phase 2).

def _flat_result(span_set: set, records: dict, conveys: bool) -> StrategyResult:
    prompt = flat_evidence_to_prompt(span_set, records)
    return StrategyResult(span_set=span_set, prompt=prompt, conveys_context=conveys)


def trace_cause_strategy(trace_records, parent_of, records, all_trace_ids, store):
    symptom = find_symptom(trace_records)
    span_set = set() if symptom is None else set(trace_cause(symptom, parent_of))
    # bare spine: accurate, but the LLM sees nothing about the rest of the system
    return _flat_result(span_set, records, conveys=False)


def whole_trace_strategy(trace_records, parent_of, records, all_trace_ids, store):
    span_set = {r["span_id"] for r in trace_records}
    return _flat_result(span_set, records, conveys=False)


def whole_logs_strategy(trace_records, parent_of, records, all_trace_ids, store):
    span_set = set(parent_of.keys())
    # dumping everything trivially conveys all context -- at ruinous token cost
    return _flat_result(span_set, records, conveys=True)


# ---- DRILL_DOWN: structured evidence; its own prompt ----

def drill_down_strategy(trace_records, parent_of, records, all_trace_ids, store):
    """
    span_set (for accuracy) = the raw causal path only -- identical to TRACE_CAUSE,
    so precision/recall match the bare spine. The coarse summary is CONTEXT, not a
    causal claim, so it does not enter precision/recall.

    prompt (for tokens) = the full rendered evidence: coarse system-wide census +
    raw path. A few tokens more than the bare spine, but it conveys context the
    spine lacks -- and orders of magnitude cheaper than dumping logs for the same
    awareness.
    """
    symptom = find_symptom(trace_records)
    path = [] if symptom is None else trace_cause(symptom, parent_of)
    span_set = set(path)

    if not path:
        # clean trace: no causal path. Empty evidence, empty prompt -- matches the
        # negative-case convention (no cause to report).
        return StrategyResult(span_set=set(), prompt="", conveys_context=True)

    evidence = drill_down(path, all_trace_ids, store)
    prompt = drill_evidence_to_prompt(evidence)
    return StrategyResult(span_set=span_set, prompt=prompt, conveys_context=True)


STRATEGIES: dict[str, Callable] = {
    "trace_cause": trace_cause_strategy,
    "drill_down":  drill_down_strategy,
    "whole_trace": whole_trace_strategy,
    "whole_logs":  whole_logs_strategy,
}