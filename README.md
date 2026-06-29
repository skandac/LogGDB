# CausalLog

### Teaching a database to answer "why did this break?" — so an LLM doesn't have to read a million log lines to find out.

---

## The problem, in one scene

A service in your fleet starts failing. Somewhere in the last six hours, across
forty microservices, something went wrong — a slow memory leak, a connection pool
that got exhausted, a retry storm that cascaded. You have the logs. All
ten-billion lines of them.

You'd love to hand this to an LLM and say "find the root cause." But you can't:

- **Dump the raw logs** -> you blow past the context window by three orders of
  magnitude. Even a model with a million-token window chokes on a fleet's worth of
  logs.
- **Summarize / window-aggregate them first** -> you lose the *causal thread*. The
  one line that explains everything gets averaged away into "1.2M requests, 0.3%
  error rate."

This is a lose-lose, and it exists because **log query languages index by time and
column, but real faults are linked by *causality*.** A heap leak that surfaces as
a timeout six hours and four services later isn't *near* its cause in time or in
the log file. It's connected to it by a chain of cause and effect that no
`WHERE timestamp BETWEEN ...` can follow.

**CausalLog is the missing layer.** It's not the AI that diagnoses the bug — it's
the query engine *underneath* that AI, the thing that reaches into a mountain of
logs and pulls out exactly the spans that are causally connected to the failure,
sized to fit in a prompt. The LLM still does the reasoning. CausalLog just makes
sure the LLM is reading the right 9,000 tokens instead of the wrong 982,000.

> **The one-sentence version:** CausalLog is a *causal-graph-indexed query layer*
> for root-cause analysis. The causal graph is the access path — the way you find
> the relevant logs — not a thing you score or reason over. That distinction is
> the whole idea.

This project is research, advised by Prof. Emery Berger (PLASMA lab, UMass
Amherst). What follows is the story of building it, phase by phase.

---

## The mental model: logs as a graph, not a list

Every log line in CausalLog is a **span** — one unit of work, with an ID, a
parent ID, a timestamp, and what happened. The parent IDs chain spans into a
**trace** (one request's journey through the system):

```
REQUEST_START          (the root — a request arrives)
 |- VALIDATE            (a sibling — innocent)
 \- CALL_WORKER ------> PROCESS_START
                         |- DB_FETCH       (a sibling — innocent)
                         \- FAULT_INJECTED (the symptom — here's where it broke)
```

The key insight that runs through the whole project: **the cause of a failure is
the chain from the symptom up to the root — and *nothing else*.** Those innocent
siblings (VALIDATE, DB_FETCH) ran in the same request but didn't cause anything.
A good answer excludes them. A lazy answer ("here's the whole trace") includes
them and wastes the LLM's attention. Getting that exclusion *right* — and proving
it — is what each phase is really about.

---

## Phase 0 — Build a world where we know the truth

Before you can test "did we find the cause?", you need faults where you *already
know* the cause. So Phase 0 is a tiny two-service system (a gateway and a worker)
that generates realistic logs and, alongside them, an **answer key** — for every
injected fault, exactly which spans are truly on the causal path.

Crucially, every trace also contains those innocent siblings. They're not
decoration — they're the test. Any method that can't tell the difference between
"on the causal path" and "ran nearby" will get caught by them.

**Result:** a log generator and a ground-truth answer key, with the causal chain
crossing the network boundary correctly (the worker's spans know the gateway span
that called them).

---

## Phase 1 — `TRACE_CAUSE`: walk the chain, find the cause

The first operator. Given the symptom span, walk *up* the parent pointers to the
root, collecting spans as you go. That's it.

```
symptom (FAULT) -> its parent -> its parent -> ... -> root
```

Why this is elegant: walking *up* only ever visits ancestors. The innocent
siblings are *children of ancestors* — they're never on the upward path. So
they're excluded **for free, by the geometry of the walk**, not by any filtering
rule. The structure of the graph does the work.

We measured it two ways:
- **Recall:** did we get every span that's truly on the causal path? (Did we miss
  anything?)
- **Precision:** did we exclude the innocent siblings? (Did we drag in noise?)

A lazy method that returns the whole trace scores perfect recall but bad
precision. The siblings exist to punish exactly that.

**Result:** on a faulty trace, `TRACE_CAUSE` returns the exact causal chain —
**precision 1.0, recall 1.0.** On a clean trace, it correctly returns nothing.

---

## Phase 2 — Build the ruler before building more machine

A principle we held throughout: *measure before you sophisticate.* So before
adding fancier operators, we built the evaluation harness — a results table whose
rows are **retrieval strategies** and whose columns are **precision, recall, and
token count.**

That last column is the one that makes this a paper. The entire pitch is "fewer
tokens at equal accuracy," so the harness had to count tokens (with a real
tokenizer) for every strategy's output. We seeded the table with deliberately
lazy baselines — "dump the whole trace," "dump all the logs" — so that every later
operator could be measured *against* them on the same ruler.

**Result:** a strategy x metric table. One honest number per cell. The shape of the
table you'd put in the paper, with the real operator already winning.

---

## Phase 3 — `SUSTAINED_DRIFT`: find the symptom *without being told*

Phase 1 cheated a little: it started from a span literally labelled `FAULT`. Real
systems don't label their own bugs. So Phase 3 removes the cheat.

We added a metric to the logs — memory usage (`heap_mb`) — that **slowly climbs
when there's a leak** and stays flat when there isn't. But here's the catch: we
made it climb *through noise*. The numbers jitter. Some steps go *down* even while
the overall trend goes up:

```
92 -> 115 -> 126 -> 135 -> 116 -> 156 -> ...   (climbing, but jagged)
```

A naive "is it higher than last time?" detector breaks on every dip. So
`SUSTAINED_DRIFT` fits a **least-squares slope over a sliding window** — it asks
"is this *trending* up faster than noise?" — and only fires when the trend
**persists** across several consecutive windows. (A single lucky window can be
noise; a sustained run can't.)

This phase had a real scare: our first detector fired on *clean* data 95% of the
time — noise alone faked a trend. The fix wasn't to fudge a threshold; it was to
sweep the window size and persistence requirement and *measure* the false-positive
rate until we found a setting (window=12, persist=3) where clean data almost never
triggers and the real leak always does. **That sweep is itself a result** — it's
the evidence that the detector's settings were chosen, not guessed.

**Result:** `SUSTAINED_DRIFT` finds the leaking span from the metric trend alone —
no label — then hands it to `TRACE_CAUSE`. The two operators compose: *drift finds
where, cause finds why.*

---

## Phase 4 — `DRILL_DOWN`: full detail where it matters, a summary everywhere else

The causal chain is a list of span IDs. But an LLM reads *content*, not IDs. So
how much content do you include?

`DRILL_DOWN`'s answer: **raw, full detail for the spans on the causal path —
and a one-line coarse summary for everything else.** The LLM sees the whole system
at a glance ("FAULT_INJECTED: 30 spans, average memory 218 MB"), with high
resolution exactly where the cause lives. That's what "causally complete *and*
context-window-sized" means in practice.

The coarse summaries are **aggregations** ("count and average these columns across
this window"), which is precisely what a **columnar database** is built for. So
this phase stands up **ClickHouse** with a hybrid schema: typed columns for fast
aggregation, plus a verbatim copy of each raw log line for faithful drill-down.
(We tested all the logic against an in-process DuckDB twin so the columnar SQL was
verified before touching a server.)

Then we added `DRILL_DOWN` as a fourth row in the results table. The headline, on
real data with a real tokenizer:

```
strategy       precision   recall   tokens   gives full context?
trace_cause         1.00     1.00     5,468   no
drill_down          1.00     1.00     9,306   yes
whole_trace         0.33     1.00    16,382   no
whole_logs          0.01     1.00   982,920   yes
```

Read that bottom row: dumping all the logs gives the LLM full context — at
**982,920 tokens and 1% precision** (it's 99% noise). `DRILL_DOWN` gives the *same*
full situational awareness at **9,306 tokens** — about **100x cheaper, same
recall, 100x the precision.**

The honest framing isn't "100x faster." It's: *DRILL_DOWN's evidence stays a
constant size while the dump grows without bound as the fleet grows.* The 100x is
a symptom of that property. The property is the contribution.

**Result:** a four-strategy results table on a real columnar backend, showing
bounded, causally-complete, context-rich evidence at a ~100x token discount.

---

## Phase 5 — Cross-request causality: when the cause is in *another* request

This is the hard one, and the most important.

Everything so far followed `parent_span_id` — an edge that's *already in the
data*. But the nastiest distributed-systems faults cross *between* requests, where
no parent pointer exists. The canonical case: a **shared resource exhaustion.**

Imagine a connection pool with 3 slots. Three requests grab all three and hold
them. A fourth request (B) comes along, finds the pool empty, waits, and times
out. **B's failure was caused by the three holders — but they're completely
separate requests.** There is no `parent_span_id` linking B's timeout to them. The
only thing connecting them is that they touched the same pool at overlapping
times.

So we built two new things:

**1. A way to log the link.** The pool emits events — acquire, release, wait,
timeout — each tagged with the pool's ID. Now the connection is *in the data*, as
a join on pool ID + time, not a guess. (This matters: it means we're not
*inferring* causality statistically — which is error-prone and a known weak spot
in prior work — we're *reading* it from instrumented events.)

**2. The overlap rule — who actually caused B's timeout?** Not "whoever held a
slot at the exact instant B gave up" (too narrow — it misses someone who blocked B
for nine of its ten waiting seconds then released). The right rule is **interval
overlap**: a holder caused B's timeout if its hold *overlapped B's wait window*.

```
holder acquired BEFORE B gave up   AND   holder released AFTER B started waiting
```

This automatically excludes the *innocent past users* — requests that used the
pool and gave their slot back *before* B ever waited. They're the Phase-5 version
of the innocent siblings, and excluding them is the precision test.

**3. Teaching `TRACE_CAUSE` to walk a real graph.** A within-trace fault has one
cause-chain (a line). A cross-request fault has *many* causes (B has three
holders, each with its own chain) — that's a **tree**, not a line. So we
generalized the operator from a linear walk into a proper **graph traversal**
behind a clean interface:

> `predecessors(span)` -> the causal predecessors of a span, *whatever kind of edge
> they are.* For a normal span, that's its parent. For B's timeout, that's the
> three holders. `TRACE_CAUSE` just asks for predecessors and traverses — it never
> learns that pools exist.

This is the project's thesis made literally true: *the causal graph is the access
path.* Parent edges and pool edges live behind one interface; the operator walks
the unified graph. Adding a new kind of causal link later (inferred edges,
happens-before clocks) is a new provider — zero changes to the operator.

We verified it doesn't break the old behavior (on a within-trace fault, the graph
walk reproduces Phase 1's exact chain) *and* that it solves the new one. We scored
it on **two levels**, because they catch different failures:

- **Full-chain:** is the entire 16-span evidence set correct? (B's chain + all
  three holders' chains.)
- **Holder-ID:** did the *cross-request hop* specifically work — found all 3
  holders, excluded both innocents? (13 of the 16 spans are easy within-trace
  walks; this isolates the 3 that are the actually-novel part, so a strong easy
  result can't mask a weak hard one.)

**Result:** starting from B's timeout, the walk crosses from B's request, hops via
the pool to all three holders, and walks each holder's chain to its root.
**Full-chain and holder-ID both precision 1.0, recall 1.0. Innocents excluded.**
The query layer now handles faults whose cause lives in a different request
entirely.

---

## Where things stand

| Phase | What it adds | Status |
|---|---|---|
| 0 | Fault harness + ground-truth answer key | done |
| 1 | `TRACE_CAUSE` — walk the causal chain | done — precision = recall = 1.0 |
| 2 | Evaluation harness — the results table (the "ruler") | done |
| 3 | `SUSTAINED_DRIFT` — find the symptom with no label | done — tuned to ~0% false positives |
| 4 | `DRILL_DOWN` + ClickHouse — bounded, context-rich evidence | done — ~100x token reduction |
| 5 | Cross-request causality — faults across requests | done — both scores 1.0 |
| 6 | Scale + time compression (exceed a context window) | next |
| 7 | The showdown — real LLM, pruned evidence vs. full dump | — |
| 8 | External benchmarks (DeathStarBench / Train-Ticket) | — |

**A note on "where's the LLM?"** — by design, there isn't one yet. Phases 0–5
build and *prove* the retrieval layer: that the evidence set is correct
(precision/recall) and bounded (tokens). None of that needs a live model to
verify. The LLM enters in **Phase 7**, the showdown: feed a real model *both* the
pruned evidence and the full dump, and measure which one lets it actually fix the
bug. That's where "a query layer for LLMs to parse" gets its final proof. We built
the pruner first because you can't test "does pruning help the LLM" until you have
the pruning.

---

## The ideas worth remembering

1. **The causal graph is the *access path*, not the answer.** CausalLog finds the
   right logs; the LLM reasons over them. Keeping those jobs separate is what makes
   the contribution clean.
2. **Walking *up* excludes the innocent bystanders for free.** Precision falls out
   of the graph's geometry, not from filtering rules.
3. **Measure before you sophisticate.** The results table was built early and
   extended with every operator, so each phase is a *measured* result, not just
   working code.
4. **Prove the mechanism, then abstract it.** Cross-request linking was built and
   tested standalone *before* being unified behind the graph interface — so the
   interface was designed against two real edge types, not one real and one
   imagined.
5. **Read causality, don't guess it.** Cross-request links come from instrumented
   pool events joined on ID and time — deterministic — not from statistical
   inference, which is where prior systems get fragile.

---

## Running it

```bash
# 1. generate within-trace fault logs (heap leak)
cd harness
uvicorn worker:app  --port 8001
uvicorn gateway:app --port 8000
python loadgen.py --leak --n 30     # leaky traces
python loadgen.py --n 30            # clean traces

# 2. the within-trace results table (Phases 1-4)
cd ../query
pip install duckdb tiktoken
python evaluate.py

# 3. the cross-request cascade (Phase 5)
cd ../harness
rm -f ../data/*.jsonl
python cascade.py                   # generate the pool-exhaustion cascade
cd ../query
python cascade_scorer.py            # full-chain + holder-ID scores

# 4. optional: real ClickHouse backend
./clickhouse_setup.sh
python clickhouse_load.py ../data/logs.jsonl
```

---

*CausalLog is a research prototype. Advised by Prof. Emery Berger, PLASMA lab,
UMass Amherst.*
