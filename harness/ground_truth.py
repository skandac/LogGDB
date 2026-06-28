"""Answer-key writer.

When the harness injects a fault it records the truth here, keyed by trace_id:
which span caused the failure and what the failure was. The query phase never
reads this file to make its decision — it's only used to score how well
TRACE_CAUSE recovered the real cause from the logs alone.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
GT_PATH = os.path.join(_DATA_DIR, "ground_truth.jsonl")

_lock = threading.Lock()


def record(
    trace_id: str,
    fault: str,
    root_cause_service: str,
    root_cause_span_id: str,
    symptom_span_id: str,            # where TRACE_CAUSE starts from (the FAULT span)
    true_cause_chain: list[str],     # the spans that ARE on the causal path
    detail: Optional[str] = None,
    **fields: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "trace_id": trace_id,
        "fault": fault,
        "symptom_span_id": symptom_span_id,
        "root_cause_service": root_cause_service,
        "root_cause_span_id": root_cause_span_id,
        "true_cause_chain": true_cause_chain,   # e.g. [compute, process, call_worker, root]
        "detail": detail,
    }
    entry.update(fields)

    line = json.dumps(entry, separators=(",", ":")) + "\n"
    os.makedirs(_DATA_DIR, exist_ok=True)
    with _lock:
        with open(GT_PATH, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
    return entry
