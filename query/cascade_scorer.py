"""
Phase 5, piece 4: cross-request scoring.

The cascade fault is scored on TWO levels, because they answer different questions
and one can mask the other:

  FULL-CHAIN : precision/recall against the entire true_cause_chain (16 spans:
               B's within-trace chain + each holder's within-trace chain).
               Answers "is the evidence set correct and complete?"

  HOLDER-ID  : precision/recall against ONLY the 3 holder compute spans.
               Answers "did the cross-request HOP specifically work?" -- the
               novel, hard part. Isolated so a strong within-trace result can't
               hide a weak cross-request hop. (13 of 16 spans are easy parent
               walks; only the 3 holders are the new inference. Full-chain alone
               would dilute a missed holder to near-invisibility.)

The cascade requires the WHOLE log (the cause spans 6 traces), so this scorer
runs the unified graph walk over all records, not per-trace.
"""

import json
from trace_cause import trace_cause_graph
from causal_link import ParentLinkProvider, ResourceLinkProviderAdapter, CompositeProvider
from resource_link import ResourceLinkProvider


def _pr(got: set, true: set) -> tuple[float, float]:
    """precision, recall for two sets. Empty-set conventions match the main scorer."""
    if not true:
        return (1.0, 1.0) if not got else (0.0, 1.0)
    if not got:
        return (1.0, 0.0)
    inter = got & true
    return len(inter) / len(got), len(inter) / len(true)


def score_cascade(logs_path="../data/logs.jsonl", gt_path="../data/ground_truth.jsonl"):
    all_records = [json.loads(l) for l in open(logs_path)]
    parent_of = {r["span_id"]: r["parent_span_id"] for r in all_records}

    # only cross-request ground-truth entries
    gts = [json.loads(l) for l in open(gt_path)]
    cascades = [g for g in gts if g.get("cross_request")]
    if not cascades:
        print("no cross-request ground truth found (run cascade.py first)")
        return

    # unified provider: parent edges + pool-overlap edges
    rp = ResourceLinkProvider(all_records)
    provider = CompositeProvider([
        ParentLinkProvider(parent_of),
        ResourceLinkProviderAdapter(rp),
    ])

    print("Phase 5 cross-request scoring\n")
    for gt in cascades:
        symptom = gt["symptom_span_id"]
        got = set(trace_cause_graph(symptom, provider))

        # --- full-chain ---
        full_true = set(gt["true_cause_chain"])
        fp, fr = _pr(got, full_true)

        # --- holder-id: restrict BOTH sides to the holder question ---
        # got_holders = which holder spans the walk found (intersect with all known
        # pool acquire spans so we score the HOP, not the chains hanging off it)
        holders_true = set(gt["holder_compute_spans"])
        pool_acquire_spans = {
            r["span_id"] for r in all_records
            if r.get("event_type") == "POOL_ACQUIRE"
        }
        got_holders = got & pool_acquire_spans     # holders the walk actually reached
        hp, hr = _pr(got_holders, holders_true)

        print(f"victim {symptom}  (pool {gt.get('pool_id')})")
        print(f"  FULL-CHAIN   precision={fp:.2f} recall={fr:.2f}  ({len(got)} spans vs {len(full_true)} true)")
        print(f"  HOLDER-ID    precision={hp:.2f} recall={hr:.2f}  "
              f"(found {sorted(got_holders)} vs true {sorted(holders_true)})")

        # the innocents check, stated explicitly
        innocents = pool_acquire_spans - holders_true - {symptom}
        leaked = innocents & got
        print(f"  innocents excluded: {not leaked}"
              + (f"  LEAKED {sorted(leaked)}" if leaked else "") + "\n")


if __name__ == "__main__":
    score_cascade()