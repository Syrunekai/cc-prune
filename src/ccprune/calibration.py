"""A log of the context each applied edit estimated, matched later with the
real count the API recorded for the first request after it."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from . import settings
from .estimate import iso_time
from .sessions import read_tail
from .transcript import load
from .usage import context_tokens

TAIL_BYTES = 16 * 1024 * 1024


def _file() -> str:
    return os.path.expanduser(settings.ESTIMATE_LOG)


def _read() -> list[dict]:
    out = []
    try:
        with open(_file(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return [e for e in out if isinstance(e, dict)]


def _write(entries: list[dict]) -> None:
    path = _file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in entries))
    os.replace(tmp, path)


def session_id_of(path: str) -> str:
    return os.path.basename(path).split(".")[0]


def record_estimate(path: str, estimate: int, before: int, label: str) -> None:
    """Add an entry for an edit that has just been written."""
    entry = {"session": session_id_of(path), "time": time.time(), "label": label,
             "before": before, "estimate": estimate, "real": None}
    entries = _read()
    entries.append(entry)
    _write(entries)


@dataclass
class Outcome:
    label: str
    time: float
    before: int
    estimate: int
    real: int

    @property
    def error(self) -> float:
        return (self.estimate - self.real) / self.real


def _first_real_after(path: str, since: float, until: float) -> int | None:
    """Input tokens of the first real request made after `since` and before
    `until`, read from the end of the transcript."""
    try:
        recs = read_tail(path, TAIL_BYTES)
        if os.path.getsize(path) <= TAIL_BYTES:
            recs = load(path)
    except OSError:
        return None
    for r in recs:
        if (r.get("type") == "assistant" and not r.get("ccPruneEstimate")
                and not r.get("isSidechain") and context_tokens(r)):
            t = iso_time(r.get("timestamp"))
            if since < t < until:
                return context_tokens(r)
    return None


def reconcile(path: str) -> list[Outcome]:
    """Fill in the real count for this session's open entries where the
    transcript now has one, and return every resolved entry of the session,
    oldest first. An entry is only matched with a request made before the
    next edit of the same session, so a later edit can't be blamed on it."""
    sid = session_id_of(path)
    entries = _read()
    mine = sorted((e for e in entries if e.get("session") == sid), key=lambda e: e["time"])
    changed = False
    for k, e in enumerate(mine):
        if e.get("real") is not None:
            continue
        until = mine[k + 1]["time"] if k + 1 < len(mine) else float("inf")
        real = _first_real_after(path, e["time"], until)
        if real:
            e["real"] = real
            changed = True
    if changed:
        _write(entries)
    return [Outcome(e.get("label", ""), e["time"], e.get("before", 0), e["estimate"], e["real"])
            for e in mine if e.get("real")]
