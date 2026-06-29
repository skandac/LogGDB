"""
Phase 5, piece 2: ResourceLinkProvider (standalone, tested in isolation).

The cross-request causal edge. Within-trace causality lives in parent_span_id;
this provider supplies the OTHER kind — a victim blocked on an exhausted pool is
caused by whoever HELD that pool's slots during the victim's wait. There is no
parent pointer between them; the only link is the shared pool_id + time overlap.

This is built and tested ALONE first (the hybrid plan): prove the join is correct
before unifying it with parent edges behind one CausalLinkProvider. It deliberately
does NOT touch TRACE_CAUSE yet.

THE OVERLAP CONDITION (causal blame = duration of contention, not an instant):
  A holder H caused victim V's pool timeout iff H's hold interval overlaps V's
  wait interval:
        acquire_H < timeout_V   AND   release_H > wait_V
  - acquire_H < timeout_V : H grabbed a slot before V gave up        (recall side)
  - release_H > wait_V     : H still held it after V started waiting   (precision side)
  A holder that never released (no RELEASE event) overlaps by definition (+inf).
  The precision side is what excludes innocent PAST users who released before V
  ever waited.
"""

from typing import Optional


def _pool_intervals(all_records: list) -> dict:
    """
    From all log records, reconstruct each (pool_id, holder span) hold interval.

    Returns: pool_id -> list of {span_id, trace_id, acquire, release}
    A span that ACQUIREd but never RELEASEd gets release=None (treated as +inf).
    Keyed off ACQUIRE events; RELEASE events are matched by (pool_id, span_id).
    """
    pools: dict[str, dict[str, dict]] = {}   # pool_id -> span_id -> interval

    for r in all_records:
        if "pool_id" not in r:
            continue
        pid = r["pool_id"]
        sid = r["span_id"]
        et = r["event_type"]
        pools.setdefault(pid, {})
        if et == "POOL_ACQUIRE":
            pools[pid].setdefault(sid, {
                "span_id": sid, "trace_id": r["trace_id"],
                "acquire": r["ts"], "release": None,
            })["acquire"] = r["ts"]
        elif et == "POOL_RELEASE":
            # match the acquire for this span; if acquire not seen yet, stub it
            iv = pools[pid].setdefault(sid, {
                "span_id": sid, "trace_id": r["trace_id"],
                "acquire": None, "release": None,
            })
            iv["release"] = r["ts"]

    return {pid: list(spans.values()) for pid, spans in pools.items()}


def _victim_window(all_records: list, symptom_span_id: str) -> Optional[tuple]:
    """
    For a victim symptom span (its POOL_TIMEOUT), find (pool_id, wait_ts, timeout_ts).

    The victim emits POOL_WAIT then POOL_TIMEOUT on the same span_id + pool_id.
    Returns None if this span isn't a pool-timeout victim (not a cross-request case).
    """
    wait_ts = timeout_ts = pool_id = None
    for r in all_records:
        if r["span_id"] != symptom_span_id or "pool_id" not in r:
            continue
        if r["event_type"] == "POOL_WAIT":
            wait_ts = r["ts"]; pool_id = r["pool_id"]
        elif r["event_type"] == "POOL_TIMEOUT":
            timeout_ts = r["ts"]; pool_id = r["pool_id"]
    if wait_ts is None or timeout_ts is None:
        return None
    return pool_id, wait_ts, timeout_ts


class ResourceLinkProvider:
    """
    Given the full log, answers: which spans are the cross-request cause of a
    given victim symptom span? The pool-overlap join, and nothing else.

    Takes the RAW list of log records (not a span_id-keyed dict), because a single
    span emits MULTIPLE pool events (ACQUIRE then RELEASE) — deduping by span_id
    would drop events. The join is over the event stream, not over spans.
    """

    def __init__(self, all_records: list[dict]):
        self.all_records = all_records
        self.intervals = _pool_intervals(all_records)

    def holders_for(self, symptom_span_id: str) -> list[str]:
        """
        Return the holder span_ids whose hold overlapped the victim's wait window.
        Empty list if the symptom isn't a pool-timeout victim, or if (impossibly)
        no holder overlaps.
        """
        win = _victim_window(self.all_records, symptom_span_id)
        if win is None:
            return []
        pool_id, wait_ts, timeout_ts = win

        holders = []
        for iv in self.intervals.get(pool_id, []):
            if iv["span_id"] == symptom_span_id:
                continue                       # the victim is not its own holder
            acq = iv["acquire"]
            rel = iv["release"]
            if acq is None:
                continue                       # no acquire -> not a holder
            rel_eff = rel if rel is not None else float("inf")
            # overlap: acquired before victim gave up AND released after victim began waiting
            if acq < timeout_ts and rel_eff > wait_ts:
                holders.append(iv["span_id"])
        return holders