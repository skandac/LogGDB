#!/usr/bin/env bash
# Phase 4: stand up ClickHouse and create the causallog schema.
# Run from anywhere. Idempotent-ish: re-running the docker step errors if the
# container exists (that's fine — stop/rm first, shown at the bottom).
set -euo pipefail

CH_HTTP=8123          # HTTP interface — clickhouse-connect talks to this
CH_NATIVE=9000        # native protocol (clickhouse-client)
CH_PW=causallog       # dev password; the default user has no network access without one

echo "==> starting ClickHouse container"
docker run -d \
  --name causallog-ch \
  --ulimit nofile=262144:262144 \
  -p ${CH_HTTP}:8123 \
  -p ${CH_NATIVE}:9000 \
  -e CLICKHOUSE_PASSWORD=${CH_PW} \
  -e CLICKHOUSE_DB=causallog \
  clickhouse/clickhouse-server

echo "==> waiting for server to accept queries"
for i in $(seq 1 30); do
  if curl -s "http://localhost:${CH_HTTP}/?password=${CH_PW}" --data-binary "SELECT 1" >/dev/null 2>&1; then
    echo "    up after ${i}s"; break
  fi
  sleep 1
done

echo "==> version"
echo 'SELECT version()' | curl -s "http://localhost:${CH_HTTP}/?password=${CH_PW}" --data-binary @-

echo "==> creating table causallog.logs (hybrid schema)"
# MergeTree is the workhorse engine. ORDER BY (trace_id, ts) clusters spans of a
# trace together and time-orders them — good for both the GROUP BY (by trace_id)
# and any time-range scans later. heap_mb is Nullable because only compute spans
# carry it. raw holds the verbatim JSON line (migration-proof drill-down).
curl -s "http://localhost:${CH_HTTP}/?password=${CH_PW}" --data-binary @- <<'SQL'
CREATE TABLE IF NOT EXISTS causallog.logs
(
    ts              Float64,
    service         String,
    trace_id        String,
    span_id         String,
    parent_span_id  Nullable(String),
    event_type      String,
    heap_mb         Nullable(Float64),
    raw             String
)
ENGINE = MergeTree
ORDER BY (trace_id, ts)
SQL

echo "==> done. table ready."
echo
echo "to load data:   python clickhouse_load.py ../data/logs.jsonl"
echo "to tear down:   docker stop causallog-ch && docker rm causallog-ch"
echo "to reset table: curl -s 'http://localhost:${CH_HTTP}/?password=${CH_PW}' --data-binary 'TRUNCATE TABLE causallog.logs'"