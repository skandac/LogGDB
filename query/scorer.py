"""
Phase 1, piece 3: the scorer.

This is the ruler. It exists to catch a LAZY TRACE_CAUSE. A lazy implementation
that returns the whole trace would score recall=1.0 (it contains all true spans)
but precision<1.0 (it also drags in VALIDATE and DB_FETCH). Precision is the
entire reason the harness injects innocent siblings — so the scorer MUST measure
it, not just recall.

Metrics, per leaky trace, as sets of span_ids:
  got  = set(trace_cause(symptom, parent_of))
  true = set(true_cause_chain from ground truth)

  recall    = |got ∩ true| / |true|   -> did we get every true span?
  precision = |got ∩ true| / |got|    -> did we avoid pulling in innocents?

Negative case: a clean trace has no FAULT_INJECTED, so find_symptom returns None,
so we assert TRACE_CAUSE is never run and "no cause" is reported.
"""

import json
from loader import load_logs
from trace_cause import trace_cause, find_symptom


def load_ground_truth(path: str) -> dict[str, dict]:
    """trace_id -> ground-truth record (has symptom_span_id, true_cause_chain)."""
    gt: dict[str, dict] = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            gt[d["trace_id"]] = d
    return gt


def score():
    records, parent_of, by_trace = load_logs("../data/logs.jsonl")
    gt = load_ground_truth("../data/ground_truth.jsonl")

    for trace_id, trace_records in by_trace.items():
        symptom = find_symptom(trace_records)

        # ----- negative case: clean trace, no symptom -> must report no cause
        if symptom is None:
            assert trace_id not in gt, f"{trace_id}: clean trace but has ground truth!"
            print(f"{trace_id}  CLEAN   -> no cause (correct)")
            continue

        # ----- positive case: run the operator and score it
        got = trace_cause(symptom, parent_of)
        true = gt[trace_id]["true_cause_chain"]

        got_set, true_set = set(got), set(true)
        inter = got_set & true_set
        recall = len(inter) / len(true_set)
        precision = len(inter) / len(got_set)

        # the precision check is the meaningful one: did siblings leak in?
        leaked = got_set - true_set
        print(f"{trace_id}  LEAKY   recall={recall:.2f} precision={precision:.2f}"
              f"  chain_len={len(got)}"
              + (f"  LEAKED={leaked}" if leaked else ""))

        assert recall == 1.0,    f"{trace_id}: missed true spans {true_set - got_set}"
        assert precision == 1.0, f"{trace_id}: pulled in innocents {leaked}"

    print("\nall assertions passed.")


if __name__ == "__main__":
    score()