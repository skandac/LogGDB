"""
Phase 1, piece 1: the loader.

Reads data/logs.jsonl and builds the index structures the rest of Phase 1
queries. The loader is deliberately DUMB: it indexes every record without
judgement. Excluding siblings (VALIDATE, DB_FETCH) is TRACE_CAUSE's job, not
the loader's — the loader just records the parent pointers faithfully.
"""

import json
from typing import Optional


def load_logs(path: str) -> tuple[dict, dict, dict]:
    """
    Returns three indexes built in a single pass:

      records[span_id]   -> the full log record
      parent_of[span_id] -> parent_span_id (or None for a root span)
      by_trace[trace_id] -> list of records in that trace

    Phase 0 assumption: 1 log line == 1 span.
    """
    records: dict[str, dict] = {}
    parent_of: dict[str, Optional[str]] = {}
    by_trace: dict[str, list] = {}

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)

            span_id = d["span_id"]
            records[span_id] = d
            parent_of[span_id] = d["parent_span_id"]
            by_trace.setdefault(d["trace_id"], []).append(d)

    return records, parent_of, by_trace


if __name__ == "__main__":
    records, parent_of, by_trace = load_logs("../data/logs.jsonl")
    print(f"spans:  {len(records)}")
    print(f"traces: {len(by_trace)}")

    roots = [s for s, p in parent_of.items() if p is None]
    dangling = [p for p in parent_of.values() if p is not None and p not in records]
    print(f"roots:  {len(roots)}  (expect 1 per trace)")
    print(f"dangling parents: {len(dangling)}  (expect 0)")
