from fastapi import FastAPI, Request, Query
from trace import from_headers, child_context, new_root_context
from emit import emit
from ground_truth import record

app = FastAPI(title="worker")

@app.get("/process")
async def process(request: Request, leak: bool = Query(False), heap_mb: float = Query(None)):
    # Rebuild parent from headers; if none, this is a fresh root.
    parent = from_headers(request.headers)
    process_ctx = child_context(parent) if parent else new_root_context()
    emit(process_ctx, "worker", "PROCESS_START", "worker received work")

    # SIBLING 1: db_fetch — innocent, child of process
    db_ctx = child_context(process_ctx)
    emit(db_ctx, "worker", "DB_FETCH", "fetched config rows")

    # SIBLING 2: compute — child of process, where the leak lives
    compute_ctx = child_context(process_ctx)
    if leak:
        emit(compute_ctx, "worker", "FAULT_INJECTED", "compute path faulted", heap_mb=heap_mb)
        record(
    trace_id=compute_ctx.trace_id,
    fault="heap_leak",
    root_cause_service="worker",
    root_cause_span_id=compute_ctx.span_id,
    symptom_span_id=compute_ctx.span_id,
    true_cause_chain=[
        compute_ctx.span_id,           # a2fb3360  fault
        process_ctx.span_id,           # 422cddca  process
        process_ctx.parent_span_id,    # e58e585a  call_worker
        parent.parent_span_id,         # 3062d368  gateway root ← NEW
    ],
)
    else:
        emit(compute_ctx, "worker", "COMPUTE_OK", "compute path clean", heap_mb=heap_mb)

    return {"trace_id": process_ctx.trace_id, "leak": leak}