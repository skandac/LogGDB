import httpx
from fastapi import FastAPI, Query
from trace import new_root_context, child_context, to_headers
from emit import emit

app = FastAPI(title="gateway")
WORKER = "http://127.0.0.1:8001/process"

@app.get("/handle")
async def handle(leak: bool = Query(False), heap_mb: float = Query(None)):
    
    root = new_root_context()                      # brand-new request = root
    emit(root, "gateway", "REQUEST_START", "gateway received request")

    # SIBLING: validate — innocent, child of root
    validate_ctx = child_context(root)
    emit(validate_ctx, "gateway", "VALIDATE", "request validated")

    # call_worker — child of root; its span is what we send across the wire
    call_ctx = child_context(root)
    emit(call_ctx, "gateway", "CALL_WORKER", "calling worker")
    async with httpx.AsyncClient() as client:
        # to_headers(call_ctx): worker's first span will be a CHILD of call_ctx
        r = await client.get(WORKER, params={"leak": leak, "heap_mb": heap_mb},
                             headers=to_headers(call_ctx))
    return {"trace_id": root.trace_id, "worker_response": r.json()}