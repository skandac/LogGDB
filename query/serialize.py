"""
Phase 2, piece 2: serialization + token measurement.

The token axis is only meaningful if EVERY strategy's evidence is rendered the
SAME way before counting. So this module owns the single rendering function and
the single tokenizer. No strategy gets to serialize itself — that would let one
strategy look cheaper by formatting differently. One renderer, one tokenizer,
applied uniformly = honest token comparison.

Rendering choice: emit the fields an LLM would actually need to reason about a
span, sorted by timestamp so the evidence reads as a coherent timeline. This is a
fixed contract; if you change it, you change it for all strategies at once.
"""

import json
from functools import lru_cache

# Fields handed to the model. Keep this stable — it defines the token cost.
_PROMPT_FIELDS = ("ts", "service", "event_type", "span_id", "parent_span_id", "message")


def evidence_to_prompt(span_ids: set[str], records: dict[str, dict]) -> str:
    """
    Render a set of span_ids into the exact text an LLM would receive.

    Sorted by timestamp so causally-ordered evidence reads as a timeline. Only
    the prompt fields are included (not internal bookkeeping). Deterministic:
    same span set -> same string -> same token count, every run.
    """
    spans = [records[s] for s in span_ids if s in records]
    spans.sort(key=lambda r: r["ts"])

    lines = []
    for r in spans:
        slim = {k: r.get(k) for k in _PROMPT_FIELDS}
        lines.append(json.dumps(slim, separators=(",", ":")))
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _encoder():
    """
    Load tiktoken cl100k_base once. If the BPE vocab can't be fetched (offline /
    locked-down network), fall back to a char/4 approximation so the harness still
    runs and the TABLE STRUCTURE is verifiable. On your machine with internet the
    real encoder loads and you get exact counts.

    Returns (mode, callable) where mode is 'tiktoken' or 'approx'.
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return ("tiktoken", lambda s: len(enc.encode(s)))
    except Exception:
        # ~4 chars per token is the standard rough English/JSON estimate.
        return ("approx", lambda s: (len(s) + 3) // 4)


def count_tokens(text: str) -> int:
    """Token count under the fixed encoding (or approximation if vocab missing)."""
    _, fn = _encoder()
    return fn(text)


def tokenizer_mode() -> str:
    """'tiktoken' (exact) or 'approx' (fallback) — printed in the report header
    so a result is never silently mistaken for an exact count."""
    mode, _ = _encoder()
    return mode