"""
Phase 4, piece 1: the storage interface + backends.

DRILL_DOWN needs exactly two operations from storage, and they are OPPOSITES:

  summarize(trace_ids)  -> coarse aggregate stats per event_type
        touches MANY rows, reads FEW columns (event_type, heap_mb).
        This is the columnar win: scan two columns across the whole window,
        skip message/parent/etc. GROUP BY is what ClickHouse exists for.

  fetch_raw(span_ids)   -> full original records for specific spans
        touches FEW rows, reads ALL columns. The causal-path detail the LLM
        actually reads. We return the verbatim record from the `raw` column.

The operator codes against this INTERFACE, never against ClickHouse SQL directly.
That's decision #2 from the handoff (operators query a provider interface; the
implementation swaps). Two backends below produce IDENTICAL results:

  DuckDBStore     - columnar SQL, in-process, no server. Used for dev + tests.
  ClickHouseStore - same SQL semantics, real columnar engine. Used at scale.

If the DuckDB-backed operator passes its tests, the ClickHouse one is a
connection swap, because the queries are written to be portable.

HYBRID SCHEMA (both backends):
  typed columns: ts, service, trace_id, span_id, parent_span_id, event_type, heap_mb
  raw column:    the full original JSON log line, verbatim
  -> aggregation reads typed columns (fast); raw-fetch reads `raw` (faithful,
     migration-proof as the log schema grows in Phase 5/6).

NO-DRIFT INSERT DISCIPLINE (critical): the typed columns are DERIVED from the
same parsed dict that becomes `raw`. They are written in one operation from one
source dict, so the typed columns and the JSON can never disagree. See load_*.
"""

import json
from abc import ABC, abstractmethod
from typing import Optional


# Columns we pull out of the JSON into typed storage. heap_mb is optional per row.
_TYPED = ("ts", "service", "trace_id", "span_id", "parent_span_id", "event_type", "heap_mb")


class AggregationStore(ABC):
    """The interface DRILL_DOWN talks to. Two methods, both opposites."""

    @abstractmethod
    def summarize(self, trace_ids: list[str]) -> list[dict]:
        """Coarse aggregate over the given traces, grouped by event_type.
        Returns rows like {event_type, n, avg_heap}. The columnar GROUP BY."""

    @abstractmethod
    def fetch_raw(self, span_ids: list[str]) -> list[dict]:
        """Full verbatim records for specific spans. The point lookup."""


class DuckDBStore(AggregationStore):
    """
    Dev/test backend. Real columnar SQL, runs in-process. The SQL here is written
    to also be valid (or near-valid) ClickHouse, so ClickHouseStore can mirror it.
    """

    def __init__(self):
        import duckdb
        self.con = duckdb.connect(":memory:")
        # hybrid schema: typed columns + raw JSON text
        self.con.execute("""
            CREATE TABLE logs (
                ts DOUBLE,
                service VARCHAR,
                trace_id VARCHAR,
                span_id VARCHAR,
                parent_span_id VARCHAR,
                event_type VARCHAR,
                heap_mb DOUBLE,
                raw VARCHAR
            )
        """)

    def load_jsonl(self, path: str):
        """Insert with the no-drift discipline: parse each line ONCE, derive the
        typed columns AND the raw blob from that same dict, insert together."""
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)                      # parse once
                raw = json.dumps(d, separators=(",", ":"))  # verbatim-ish blob
                rows.append((
                    d["ts"], d["service"], d["trace_id"], d["span_id"],
                    d.get("parent_span_id"), d["event_type"], d.get("heap_mb"),
                    raw,                                   # same dict -> can't drift
                ))
        self.con.executemany(
            "INSERT INTO logs VALUES (?,?,?,?,?,?,?,?)", rows
        )

    def summarize(self, trace_ids: list[str]) -> list[dict]:
        if not trace_ids:
            return []
        placeholders = ",".join("?" * len(trace_ids))
        q = f"""
            SELECT event_type,
                   count(*)        AS n,
                   avg(heap_mb)    AS avg_heap
            FROM logs
            WHERE trace_id IN ({placeholders})
            GROUP BY event_type
            ORDER BY event_type
        """
        cur = self.con.execute(q, trace_ids)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def fetch_raw(self, span_ids: list[str]) -> list[dict]:
        if not span_ids:
            return []
        placeholders = ",".join("?" * len(span_ids))
        q = f"SELECT raw FROM logs WHERE span_id IN ({placeholders})"
        cur = self.con.execute(q, span_ids)
        return [json.loads(r[0]) for r in cur.fetchall()]


class ClickHouseStore(AggregationStore):
    """
    Production backend. SAME SQL SEMANTICS as DuckDBStore. You run this against a
    ClickHouse server (see clickhouse_setup.sh / DDL). Wired with clickhouse-connect.

    Not run in the dev sandbox (no server there). Because the queries mirror the
    DuckDB ones, behavior is identical; swap is just the connection.
    """

    def __init__(self, host="localhost", port=8123, database="causallog",
                 username="default", password="causallog"):
        import clickhouse_connect
        self.client = clickhouse_connect.get_client(
            host=host, port=port, database=database,
            username=username, password=password,
        )

    def summarize(self, trace_ids: list[str]) -> list[dict]:
        if not trace_ids:
            return []
        # ClickHouse: parameter binding via {name:Type}. Mirror of DuckDB query.
        q = """
            SELECT event_type,
                   count(*)     AS n,
                   avg(heap_mb) AS avg_heap
            FROM logs
            WHERE trace_id IN {ids:Array(String)}
            GROUP BY event_type
            ORDER BY event_type
        """
        res = self.client.query(q, parameters={"ids": trace_ids})
        return [dict(zip(res.column_names, row)) for row in res.result_rows]

    def fetch_raw(self, span_ids: list[str]) -> list[dict]:
        if not span_ids:
            return []
        q = "SELECT raw FROM logs WHERE span_id IN {ids:Array(String)}"
        res = self.client.query(q, parameters={"ids": span_ids})
        return [json.loads(row[0]) for row in res.result_rows]