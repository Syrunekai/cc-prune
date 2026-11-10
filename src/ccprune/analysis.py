"""Read-only analysis of a transcript: byte accounting, density and audit."""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field

from .errors import PruneError
from .transcript import blocks, is_image_result, load, result_text


@dataclass
class Inspection:
    file_bytes: int
    records: int
    buckets: Counter
    by_type: Counter
    counts: Counter


def bucket_sizes(recs: list[dict]) -> tuple[Counter, Counter, Counter]:
    buckets, by_type, counts = Counter(), Counter(), Counter()
    for r in recs:
        t = r.get("type", "?")
        counts[t] += 1
        by_type[t] += len(json.dumps(r, ensure_ascii=False))
        if t == "attachment":
            buckets["attachments"] += len(json.dumps(r, ensure_ascii=False))
        it = (r.get("message") or {}).get("input_transformations")
        if it:
            buckets["transformations"] += len(json.dumps(it, ensure_ascii=False))
        for b in blocks(r):
            k = b.get("type", "?")
            size = len(json.dumps(b, ensure_ascii=False))
            if k == "tool_result":
                buckets["images" if is_image_result(b) else "results"] += size
            elif k == "tool_use":
                buckets["inputs"] += size
            elif k in ("thinking", "redacted_thinking"):
                buckets["thinking"] += size
            else:
                buckets["text"] += size
    return buckets, by_type, counts


def inspect_file(path: str) -> Inspection:
    recs = load(path)
    buckets, by_type, counts = bucket_sizes(recs)
    return Inspection(file_bytes=os.path.getsize(path), records=len(recs),
                      buckets=buckets, by_type=by_type, counts=counts)


def density(before: int, after: int, megabytes: float) -> tuple[int, float]:
    """(tokens freed, characters per token) for a bucket cleared by `megabytes`."""
    freed = before - after
    if freed <= 0:
        raise PruneError("after must be smaller than before")
    return freed, megabytes * 1e6 / freed


@dataclass
class AuditEntry:
    line: int
    total_records: int
    status: str = "ok"          # ok, out_of_range or no_result
    chars: int = 0
    tool: str = "?"
    image: bool = False
    call_line: int | None = None
    params: dict = field(default_factory=dict)
    source: str | None = None   # the file the call read, when it read one
    exists: bool = False
    disk_bytes: int = 0
    rereads: list[int] = field(default_factory=list)
    basename: str = ""
    mentions: list[int] = field(default_factory=list)
    command: str | None = None
    said_line: int | None = None
    said: str = ""
    head: str = ""
    verdict: list[str] = field(default_factory=list)


def _all_text(rec) -> str:
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(rec)
    return "\n".join(out)


def audit_lines(path: str, lines: list[int]) -> list[AuditEntry]:
    """For each record index, whether clearing the tool result in it is safe."""
    recs = load(path)
    uses = {}
    for i, r in enumerate(recs):
        for b in blocks(r):
            if b.get("type") == "tool_use" and b.get("id"):
                uses[b["id"]] = (i, b.get("name", "?"), b.get("input", {}))

    entries = []
    for tgt in lines:
        if tgt >= len(recs):
            entries.append(AuditEntry(line=tgt, total_records=len(recs),
                                      status="out_of_range"))
            continue
        tr = next((b for b in blocks(recs[tgt])
                   if b.get("type") == "tool_result"), None)
        if tr is None:
            entries.append(AuditEntry(line=tgt, total_records=len(recs),
                                      status="no_result"))
            continue

        text = result_text(tr)
        uline, tool, params = uses.get(tr.get("tool_use_id"), (None, "?", {}))
        e = AuditEntry(line=tgt, total_records=len(recs), chars=len(text), tool=tool,
                       image=is_image_result(tr), call_line=uline, params=params,
                       head=text[:220])

        src = params.get("file_path") or params.get("path")
        if src:
            e.source = src
            e.exists = os.path.exists(src)
            e.disk_bytes = os.path.getsize(src) if e.exists else 0
            e.verdict.append("SAFE: re-readable" if e.exists else "RISK: file is gone")
            e.rereads = [i2 for (i2, _, p2) in uses.values()
                         if i2 > tgt and (p2.get("file_path") or p2.get("path")) == src]
            if e.rereads:
                e.verdict.append("SAFE: superseded")
            e.basename = os.path.basename(src)
            e.mentions = [i for i in range(tgt + 1, len(recs))
                          if e.basename in _all_text(recs[i])]
            if len(e.mentions) > 20:
                e.verdict.append("RISK: still an active topic")
            elif not e.mentions:
                e.verdict.append("SAFE: never referenced again")
        else:
            e.command = params.get("command")
            e.verdict.append("CHECK: output exists only here unless re-runnable")

        for j in range(tgt + 1, min(tgt + 6, len(recs))):
            said = " ".join(b.get("text", "") for b in blocks(recs[j])
                            if b.get("type") == "text").strip()
            if said:
                e.said_line, e.said = j, said[:340]
                break
        entries.append(e)
    return entries
