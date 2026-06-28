import json, time, os
from typing import Optional
from trace import TraceContext

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LOG_PATH = os.path.join(_DATA_DIR, "logs.jsonl")

def emit(ctx: TraceContext, service: str, event_type: str, message: str,
         heap_mb: Optional[float] = None):
    record = {
        "ts": time.time(),
        "service": service,
        "trace_id": ctx.trace_id,
        "span_id": ctx.span_id,
        "parent_span_id": ctx.parent_span_id,
        "event_type": event_type,
        "message": message,
    }
    if heap_mb is not None:          # only leaky/clean compute spans carry it
        record["heap_mb"] = heap_mb
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")