# CausalLog

A **causal-graph-indexed query layer** for LLM-driven root-cause analysis of
distributed-system logs.

Existing log query languages index by time or column. But long-horizon,
multi-service faults — heap leaks, retry-storm cascades — are linked by
*causality, not proximity*. That forces an LLM into a lose-lose: dump the raw
logs (context overflow) or window-aggregate (lose the causal thread). CausalLog
exposes operators that compile to columnar SQL + graph traversal and return a
**causally-complete, context-window-sized evidence set**.

The contribution is **not** an RCA agent — it is the query/retrieval layer
*underneath* one.

Advised by Prof. Emery Berger (PLASMA lab, UMass Amherst).

---

## Operators

| Operator | Question it answers | Mechanism |
|---|---|---|
| `TRACE_CAUSE` | Given the symptom, what is the cause? | Walk parent-span pointers from symptom to root |
| `SUSTAINED_DRIFT` | Where is the problem? (no labels) | Windowed least-squares slope over a metric series |
| `DRILL_DOWN` | How much detail per span? | Raw on the causal path, coarse summary everywhere else |

The operators compose: `SUSTAINED_DRIFT` finds *where* (the symptom span) without
needing a fault label, `TRACE_CAUSE` walks *why* (the causal chain), and
`DRILL_DOWN` decides *how much detail* each part of the timeline contributes —
producing a bounded evidence set that is the prompt an LLM receives.

---

## Architecture

```
loadgen ──HTTP──▶ gateway ──HTTP──▶ worker        (harness: generates logs)
                    │                  │
                    ▼                  ▼
              logs.jsonl  +  ground_truth.jsonl    (queryable substrate + answer key)
                    │
                    ▼
   ┌──────────────────────────────────────────────┐
   │  query layer                                  │
   │                                               │
   │  loader ─▶ SUSTAINED_DRIFT ─▶ TRACE_CAUSE     │
   │                                   │           │
   │                                   ▼           │
   │                              DRILL_DOWN ──▶ AggregationStore
   │                                               │   ├─ DuckDBStore (dev/test)
   │                                               │   └─ ClickHouseStore (prod)
   │                                   ▼           │
   │                          bounded evidence set │
   └──────────────────────────────────────────────┘
```

The causal graph is the **access path**, not a scorer. Causal linking is layered
and mostly *instrumented*, not inferred:

- **Layer 1 — trace lineage** (`parent_span_id`): deterministic, what the current
  operators use.
- **Layer 2 — happens-before / cross-request** (Phase 5): for faults that span
  multiple traces.
- **Layer 3 — thin inferred edges** (later): only where instrumentation is blind.

Operators query a `causal-link provider` interface, so the implementation can
swap from clean instrumented edges to inferred ones without changing the
operators.

---

## Status: Phases 0–4 complete

| Phase | What | State |
|---|---|---|
| 0 | Fault harness (2 services, trace propagation, leak gate, ground truth, siblings) | ✅ |
| 1 | `TRACE_CAUSE` + loader + scorer | ✅ |
| 2 | Strategy×metric eval harness (precision / recall / tokens) | ✅ |
| 3 | `SUSTAINED_DRIFT` (windowed slope + persistence) | ✅ |
| 4 | `DRILL_DOWN` + ClickHouse hybrid store | ✅ |
| 5 | Cascade fault + cross-request causality | next |
| 6 | Time compression + scale (exceed a context window) | — |
| 7 | Baseline showdown (real full-dump LLM vs. pruned set) | — |
| 8 | External validity (DeathStarBench / Train-Ticket) | — |

### Headline result (real data, tiktoken cl100k_base)

```
strategy       precision   recall   tokens  context
trace_cause         1.00     1.00     5468       no
drill_down          1.00     1.00     9306      yes
whole_trace         0.33     1.00    16382       no
whole_logs          0.01     1.00   982920      yes
```

`DRILL_DOWN` delivers a **causally complete evidence set with full system-wide
context at ~100x fewer tokens than a full log dump, at equal recall**. The key
property is not the raw multiplier but that DRILL_DOWN's evidence size stays
*constant* while the dump grows unboundedly with fleet size. Among strategies
that convey system-wide awareness (`context = yes`), DRILL_DOWN is the only cheap
one; `whole_logs` buys the same awareness at ~100x the cost and 0.01 precision.

---

## Repository layout

```
causallog/
├── harness/                  # Phase 0 — generates logs
│   ├── trace.py              # TraceContext, propagation across the wire
│   ├── emit.py               # JSON log writer -> data/logs.jsonl
│   ├── ground_truth.py       # answer-key writer -> data/ground_truth.jsonl
│   ├── gateway.py            # entry service (port 8000)
│   ├── worker.py             # downstream service (port 8001)
│   └── loadgen.py            # traffic driver (--leak, --n N)
├── data/                     # generated, gitignored
│   ├── logs.jsonl
│   └── ground_truth.jsonl
└── query/                    # Phase 1+ — consumes logs
    ├── loader.py             # load logs -> records / parent_of / by_trace
    ├── trace_cause.py        # TRACE_CAUSE + find_symptom
    ├── sustained_drift.py    # SUSTAINED_DRIFT + build_metric_series
    ├── drill_down.py         # DRILL_DOWN operator + serializer
    ├── store.py              # AggregationStore: DuckDBStore + ClickHouseStore
    ├── strategies.py         # retrieval strategies (rows of the results table)
    ├── serialize.py          # fixed prompt rendering + tiktoken counter
    ├── scorer.py             # Phase 1 precision/recall scorer
    ├── evaluate.py           # the strategy×metric results table
    ├── clickhouse_setup.sh   # Docker + schema DDL
    └── clickhouse_load.py    # load logs.jsonl into ClickHouse
```

---

## Quickstart

### 1. Generate logs (harness)

```bash
cd harness
rm -f ../data/*.jsonl
uvicorn worker:app  --port 8001     # terminal 1
uvicorn gateway:app --port 8000     # terminal 2
python loadgen.py --leak --n 30     # terminal 3 — leaky traces (climbing heap)
python loadgen.py --n 30            # clean traces (flat heap, negative cases)
```

### 2. Run the operators (query)

```bash
cd query
pip install duckdb tiktoken

# Phase 1: TRACE_CAUSE + scorer
python loader.py            # spans / traces / roots / dangling
python scorer.py            # precision=recall=1.0 on leaky, no-cause on clean

# Phase 3: SUSTAINED_DRIFT composing with TRACE_CAUSE (label-free)
python -c "
from loader import load_logs
from sustained_drift import build_metric_series, sustained_drift
from trace_cause import trace_cause
records, parent_of, by_trace = load_logs('../data/logs.jsonl')
symptom = sustained_drift(build_metric_series(records))
print('symptom:', symptom, 'chain:', trace_cause(symptom, parent_of))
"

# Phase 2+4: the results table
python evaluate.py
```

### 3. ClickHouse backend (optional, real columnar)

```bash
cd query
pip install clickhouse-connect

# stand up server + create the hybrid-schema table
./clickhouse_setup.sh

# load data
python clickhouse_load.py ../data/logs.jsonl

# tear down
docker stop causallog-ch && docker rm causallog-ch
```

The `evaluate.py` harness uses `DuckDBStore` by default (portable, no server).
`ClickHouseStore` is verified to produce identical aggregations; swap the two
lines in `evaluate.py` to run the eval through ClickHouse.

---

## Design decisions (locked)

1. **Build ON ClickHouse, don't fork.** Operators are a query-rewrite layer above
   columnar SQL; the causal graph is the access path. DuckDB for dev, ClickHouse
   at scale.
2. **Causal linking is layered, mostly instrumented.** Deterministic trace
   lineage first; inferred edges late and thin. Defuses the "graph discovery is
   inaccurate" critique — we barely infer.
3. **Two claims kept separate.** (a) Given good causal links, the query layer
   returns bounded, causally-complete evidence — *proven (Phases 1–4)*. (b) Good
   links can be produced from messy real logs — *harder, proven later (Phases
   5, 8)*.
4. **Eval before sophistication.** The results table (the "ruler") was built
   early and extended with each operator, so each phase is a *measured* result,
   not just working code.

---

## Schema

**Hybrid** (ClickHouse / DuckDB): typed columns for fast aggregation + a `raw`
JSON column for faithful, migration-proof drill-down.

```sql
CREATE TABLE logs (
    ts              Float64,
    service         String,
    trace_id        String,
    span_id         String,
    parent_span_id  Nullable(String),
    event_type      String,
    heap_mb         Nullable(Float64),
    raw             String          -- verbatim original log line
) ENGINE = MergeTree ORDER BY (trace_id, ts)
```

The typed columns are *derived from* the same parsed record that becomes `raw`,
so they cannot drift apart (single-source insert discipline).
```
