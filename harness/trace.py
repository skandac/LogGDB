from dataclasses import dataclass, asdict
from uuid import uuid4
from typing import Optional

def _new_id() -> str:
    return uuid4().hex[:8]

@dataclass
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]

def new_root_context() -> TraceContext:
    return TraceContext(_new_id(), _new_id(), None)

def child_context(parent: TraceContext) -> TraceContext:
    return TraceContext(parent.trace_id, _new_id(), parent.span_id)

# --- propagation across the wire ---
# When gateway calls worker over HTTP, it sends its CURRENT span as headers.
# The worker reads them and builds a child whose parent points back at the
# gateway's span. This is the ONLY way the causal edge survives a network hop.

def to_headers(ctx):
    return {
        "x-trace-id": ctx.trace_id,
        "x-span-id": ctx.span_id,
        "x-parent-span-id": ctx.parent_span_id or "",   # add this
    }

def from_headers(headers):
    trace = headers.get("x-trace-id")
    span = headers.get("x-span-id")
    if not trace or not span:
        return None
    return TraceContext(
        trace_id=trace,
        span_id=span,
        parent_span_id=headers.get("x-parent-span-id") or None,  # now carries root
    )