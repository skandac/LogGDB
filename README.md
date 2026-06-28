# causallog

A fault harness that generates **causally-linked logs**, plus an answer key, so
you can build and score a tool that recovers root cause from flat logs alone.

## Layout

```
harness/   Phase 0 — services that GENERATE logs
data/      generated artifacts (gitignored)
query/     Phase 1+ — TRACE_CAUSE and the eval rig (empty for now)
```

## How it works

Each request gets a **TraceContext** (`harness/trace.py`): a stable `trace_id`
plus per-hop `span_id` / `parent_span_id`. The gateway mints a root context and
forwards a child context to the worker via headers, so the flat log stream in
`data/logs.jsonl` can be reassembled into a causal tree.

- **`gateway`** (port 8000) — entry point. Never faults; its errors are
  *symptoms* propagated up from downstream.
- **`worker`** (port 8001) — does the work and probabilistically injects a
  fault. When it does, it logs the error *and* writes the truth to
  `data/ground_truth.jsonl` keyed by `trace_id`.

The query phase only ever reads `logs.jsonl`. `ground_truth.jsonl` is used
solely to score how well it recovered the real cause.

## Run it

```bash
# terminal 1
python -m harness.worker
# terminal 2
python -m harness.gateway
# terminal 3 — drive traffic
python -m harness.loadgen --requests 200 --concurrency 8
```

Then inspect the output:

```bash
wc -l data/logs.jsonl data/ground_truth.jsonl
```

No third-party dependencies — Phase 0 is pure standard library.
