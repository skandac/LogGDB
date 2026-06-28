"""
Phase 2 + 4: the evaluation harness -- the results table.

For every strategy, run it over every trace, score with ONE shared scorer, and
aggregate into a row: (precision, recall, tokens, context). Rows = strategies,
columns = metrics. This output IS the paper's results table.

Each strategy now returns a StrategyResult(span_set, prompt, conveys_context):
  - span_set -> precision/recall (the accuracy claim)
  - prompt   -> token count      (the cost)
  - conveys_context -> whether the evidence gives system-wide awareness, so the
    table doesn't misread DRILL_DOWN's small token premium over the bare spine as
    a loss (it buys context the spine lacks).

Storage: strategies that need aggregation (DRILL_DOWN) receive a store. We use
the DuckDB store here (columnar, in-process); swap to ClickHouseStore for the
real backend -- identical results, verified separately.
"""

import json
from loader import load_logs
from strategies import STRATEGIES
from serialize import count_tokens, tokenizer_mode
from store import DuckDBStore


def load_ground_truth(path: str) -> dict[str, set]:
    gt: dict[str, set] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            gt[d["trace_id"]] = set(d["true_cause_chain"])
    return gt


def score_one(got: set, true: set) -> tuple[float, float]:
    if not true:
        return (1.0, 1.0) if not got else (0.0, 1.0)
    if not got:
        return (1.0, 0.0)
    inter = got & true
    return len(inter) / len(got), len(inter) / len(true)


def evaluate(logs_path="../data/logs.jsonl", gt_path="../data/ground_truth.jsonl"):
    records, parent_of, by_trace = load_logs(logs_path)
    gt = load_ground_truth(gt_path)
    all_trace_ids = list(by_trace.keys())

    # one store, loaded once, shared across strategies that need aggregation
    store = DuckDBStore()
    store.load_jsonl(logs_path)

    rows = []
    for name, strategy in STRATEGIES.items():
        precisions, recalls = [], []
        total_tokens = 0
        conveys = False

        for trace_id, trace_records in by_trace.items():
            true = gt.get(trace_id, set())
            result = strategy(trace_records, parent_of, records, all_trace_ids, store)

            p, r = score_one(result.span_set, true)
            precisions.append(p)
            recalls.append(r)
            total_tokens += count_tokens(result.prompt)
            conveys = result.conveys_context  # same for all traces of a strategy

        n = len(by_trace)
        rows.append({
            "strategy": name,
            "precision": sum(precisions) / n,
            "recall": sum(recalls) / n,
            "tokens": total_tokens,
            "context": "yes" if conveys else "no",
        })

    _print_table(rows)
    return rows


def _print_table(rows):
    mode = tokenizer_mode()
    note = "" if mode == "tiktoken" else "  (APPROX tokens)"
    print(f"\nResults  [tokenizer: {mode}]{note}\n")
    print(f"{'strategy':<14}{'precision':>10}{'recall':>9}{'tokens':>9}{'context':>9}")
    print("-" * 51)
    order = {"trace_cause": 0, "drill_down": 1, "whole_trace": 2, "whole_logs": 3}
    for row in sorted(rows, key=lambda r: order.get(r["strategy"], 99)):
        print(f"{row['strategy']:<14}{row['precision']:>10.2f}{row['recall']:>9.2f}"
              f"{row['tokens']:>9}{row['context']:>9}")

    by = {r["strategy"]: r for r in rows}
    if "drill_down" in by and "whole_logs" in by:
        dd, wl = by["drill_down"], by["whole_logs"]
        if dd["tokens"]:
            factor = wl["tokens"] / dd["tokens"]
            print(f"\nheadline: DRILL_DOWN conveys system context at {factor:.1f}x fewer "
                  f"tokens than full-dump, same recall ({dd['recall']:.2f} vs {wl['recall']:.2f})")


if __name__ == "__main__":
    evaluate()