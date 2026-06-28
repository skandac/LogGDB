"""
Phase 4: load logs.jsonl into ClickHouse with the NO-DRIFT discipline.

The hybrid schema stores each field both typed AND inside the `raw` JSON blob.
The only way they can disagree is if you derive them from different sources. So
we parse each line ONCE into `d`, then build BOTH the typed tuple and `raw` from
that same `d`. One source dict -> typed columns and raw can never drift apart.

Usage:
    python clickhouse_load.py ../data/logs.jsonl
"""

import sys
import json

CH_HOST = "localhost"
CH_PORT = 8123
CH_DB = "causallog"
CH_USER = "default"
CH_PASSWORD = "causallog"   # matches clickhouse_setup.sh

_COLS = ["ts", "service", "trace_id", "span_id",
         "parent_span_id", "event_type", "heap_mb", "raw"]


def load(path: str):
    import clickhouse_connect
    client = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, database=CH_DB,
        username=CH_USER, password=CH_PASSWORD,
    )

    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)                         # parse ONCE
            raw = json.dumps(d, separators=(",", ":"))   # blob from same dict
            rows.append([
                d["ts"],
                d["service"],
                d["trace_id"],
                d["span_id"],
                d.get("parent_span_id"),                 # None -> Nullable(String)
                d["event_type"],
                d.get("heap_mb"),                        # None on non-compute spans
                raw,
            ])

    # column-oriented insert; clickhouse-connect maps Python None to NULL.
    client.insert(CH_DB + ".logs", rows, column_names=_COLS)

    # verify
    n = client.query(f"SELECT count(*) FROM {CH_DB}.logs").result_rows[0][0]
    print(f"inserted {len(rows)} rows; table now holds {n}")

    # quick sanity: the aggregation DRILL_DOWN relies on
    sample = client.query(
        f"SELECT event_type, count(*), avg(heap_mb) "
        f"FROM {CH_DB}.logs GROUP BY event_type ORDER BY event_type"
    )
    print("event_type breakdown:")
    for row in sample.result_rows:
        print("  ", row)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../data/logs.jsonl"
    load(path)