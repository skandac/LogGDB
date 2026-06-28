"""
Phase 1, piece 2: TRACE_CAUSE.

The whole operator is a walk up the parent chain. This is the core idea of the
project in miniature: the causal answer is NOT "everything in the trace" and NOT
"everything near the symptom in time" — it is exactly the spine from symptom to
root, following instrumented parent edges (Layer 1, deterministic trace lineage).

Why this excludes the innocent siblings for free:
  Trace e877b0e2 structure:

    b047fa6f REQUEST_START (root, parent=None)
     ├─ 4a0bfc54 VALIDATE       parent=b047fa6f   <- sibling of CALL_WORKER
     └─ bc298315 CALL_WORKER    parent=b047fa6f
         └─ e831cbe9 PROCESS_START  parent=bc298315
             ├─ 1193adf5 DB_FETCH       parent=e831cbe9  <- sibling of FAULT
             └─ 03d16a1b FAULT_INJECTED parent=e831cbe9  (symptom)

  Walking UP from the symptom only ever visits parents. VALIDATE and DB_FETCH
  are children-of-an-ancestor, never on the upward path, so they are never
  collected. The walk *structurally cannot* include a sibling — which is the
  guarantee we want the scorer to confirm via precision.
"""

from typing import Optional


def trace_cause(symptom_span_id: str, parent_of: dict[str, Optional[str]]) -> list[str]:
    """
    Walk from the symptom span up to the root, collecting span_ids in order.

    Returns [symptom, ..., root]. For the fault in e877b0e2 this is
    ["03d16a1b", "e831cbe9", "bc298315", "b047fa6f"] — exactly the 4-span
    Option-2 ground-truth chain.

    Stop condition is `parent is not None` — verified safe because every root's
    parent_span_id is real JSON null -> Python None (not the string "null").
    """
    chain: list[str] = [symptom_span_id]
    current = symptom_span_id

    while parent_of[current] is not None:   # stop when we reach a root span
        current = parent_of[current]        # step one edge up the causal spine
        chain.append(current)

    return chain


# Convenience: find the symptom span in a trace. Phase 0 marks the fault with
# event_type FAULT_INJECTED. A clean trace has none -> returns None -> the
# caller (scorer) treats that as "no cause", which is the correct negative case.
def find_symptom(trace_records: list[dict]) -> Optional[str]:
    for r in trace_records:
        if r["event_type"] == "FAULT_INJECTED":
            return r["span_id"]
    return None