"""The token usage a transcript records, and rewriting it after an edit."""
from __future__ import annotations

from .errors import PruneError
from .transcript import load

USAGE_FIELDS = ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")
RESET_LAST_N = 5
# set on records whose usage cc-prune rewrote, so a later estimate skips them
ESTIMATE_MARK = "ccPruneEstimate"


def context_tokens(rec: dict) -> int | None:
    u = (rec.get("message") or {}).get("usage")
    if isinstance(u, dict) and ("input_tokens" in u or
                                "cache_read_input_tokens" in u):
        return (u.get("input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0))
    return None


def last_context(recs: list[dict]) -> int:
    """Input tokens of the newest request in the records, 0 when none recorded."""
    return next((c for c in map(context_tokens, reversed(recs)) if c), 0)


def reset_usage(recs: list[dict], tokens: int) -> list[tuple[int, int, int]]:
    """Scale the recorded usage of the last requests down to `tokens`."""
    idx = [i for i, r in enumerate(recs) if context_tokens(r)]
    changed = []
    for i in idx[-RESET_LAST_N:]:
        old = context_tokens(recs[i])
        if old <= tokens:
            continue
        u = recs[i]["message"]["usage"]
        for f in USAGE_FIELDS:
            u[f] = round(u.get(f, 0) * tokens / old)
        recs[i][ESTIMATE_MARK] = True
        changed.append((i, old, context_tokens(recs[i])))
    return changed


def usage_rows(path: str) -> list[tuple[int, int, dict]]:
    """(record index, input tokens, usage) for every request in the file."""
    rows = []
    for i, r in enumerate(load(path)):
        ctx = context_tokens(r)
        if ctx is not None:
            rows.append((i, ctx, r["message"]["usage"]))
    if not rows:
        raise PruneError("No usage fields found in this transcript.\n"
                         "Check /cost inside the session instead.")
    return rows
