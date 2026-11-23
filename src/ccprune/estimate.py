"""The context size the next request will count after an edit."""
from __future__ import annotations

import json
import os
from datetime import datetime

from .snapshots import read_meta, snapshot_number, snapshot_paths, snapshot_time
from .transcript import THINKING, blocks, load, rendered_chars
from .turns import Turns
from .usage import ESTIMATE_MARK, context_tokens

# weights that split a measured token count among the records it covers
WEIGHT_CHARS_PER_TOKEN = 2.0
SIGNATURE_CHARS_PER_TOKEN = 7.0
# system prompt and tool schemas, counted before the first record
OVERHEAD_TOKENS = 15000
# fraction the estimate is lowered by so it lands just under the real count
ESTIMATE_MARGIN = 0.025
MIN_ESTIMATE = 1000
# a segment's measured tokens may exceed its content weight by at most this
# factor when rescaling the records inside it; a larger ratio means content
# the weights cannot see, and spreading it over a few records over-corrects
MAX_SEGMENT_SCALE = 6.0


def iso_time(stamp) -> float:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def last_edit_time(path: str) -> float:
    """When this transcript was last rewritten by cc-prune: the newest snapshot."""
    meta = read_meta(path)
    return max((snapshot_time(path, snapshot_number(s), meta) for s in snapshot_paths(path)),
               default=0.0)


def content_key(rec: dict) -> str:
    return json.dumps([(rec.get("message") or {}).get("content"), rec.get("attachment")],
                      ensure_ascii=False, sort_keys=True)


def estimate_context(old: list[dict], new: list[dict], since: float,
                     margin: float = ESTIMATE_MARGIN) -> tuple[int | None, bool]:
    """Tokens the next request will count after an edit turned `old` into `new`.

    Each request made after the last edit (`since`) recorded the exact token
    count of everything before it, so the conversation splits into segments of
    known size. Only how a segment divides among its records is estimated: text
    by characters, thinking by signature length. Records before the first such
    request share that request's count with the system prompt and tool schemas.
    Thinking the edit invalidates (every block after the first changed record)
    counts as removed. Returns the estimate (None when the file holds no usage)
    and whether it rests on counts made after the last edit."""
    last = next((i for i in range(len(old) - 1, -1, -1) if context_tokens(old[i])), None)
    if last is None:
        return None, False
    turns = Turns(old)
    conv = turns.members(0, len(turns))

    # requests: the first record of each assistant message that carries usage
    points = {}
    for i in conv + [last]:
        r = old[i]
        if r.get("type") == "assistant" and context_tokens(r) and i <= last:
            key = (r.get("message") or {}).get("id") or r.get("uuid")
            if key not in points or i < points[key][0]:
                points[key] = (i, context_tokens(r), iso_time(r.get("timestamp")) > since
                               and not r.get(ESTIMATE_MARK))
    real = sorted((i, tok) for i, tok, fresh in points.values() if fresh)
    fresh = bool(real)
    # a count lower than the one before it means the content was edited in
    # between, so only the counts after the last drop describe this content
    drops = [k for k in range(1, len(real)) if real[k][1] < real[k - 1][1]]
    if drops:
        real = real[drops[-1]:]
    if not real:
        key = (old[last].get("message") or {}).get("id") or old[last].get("uuid")
        real = [points[key][:2]]
    # the first request's count covers the opening prompt and attachments,
    # which no edit touches, unless a drop shows records were removed after
    # it was made
    first = min(points.values())
    if not drops and first[0] < real[0][0] and first[1] < real[0][1]:
        real.insert(0, first[:2])
    bounds = [i for i, _ in real]
    totals = [real[0][1]] + [real[k][1] - real[k - 1][1] for k in range(1, len(real))]
    base = real[-1][1]
    conv = [i for i in conv if i < bounds[-1]]

    def seg(i: int) -> int:
        return sum(1 for b in bounds if b <= i)

    # thinking the last request still counted: all but the oldest it reported dropping
    dropped = sum(1 for e in (old[bounds[-1]].get("message") or {}).get("input_transformations") or []
                  if isinstance(e, dict) and e.get("type") == "thinking_dropped")
    sigs = [b.get("signature") or b.get("data") or "" for i in conv for b in blocks(old[i])
            if b.get("type") in THINKING]
    valid = set(sigs[dropped:])

    def weight(rec: dict, live: set) -> float:
        w = rendered_chars(rec) / WEIGHT_CHARS_PER_TOKEN
        for b in blocks(rec):
            sig = b.get("signature") or b.get("data") or ""
            if b.get("type") in THINKING and sig in live:
                w += len(sig) / SIGNATURE_CHARS_PER_TOKEN
        return w

    w_old = {i: weight(old[i], valid) for i in conv}
    seg_w = [0.0] * len(bounds)
    seg_w[0] = OVERHEAD_TOKENS
    for i in conv:
        seg_w[seg(i)] += w_old[i]
    scale = [t / w if w > 0 and t > 0 else 1.0 for t, w in zip(totals, seg_w)]
    if MAX_SEGMENT_SCALE:
        scale = [min(x, MAX_SEGMENT_SCALE) for x in scale]

    # the new conversation, up to the same request; thinking after the first
    # changed record no longer matches its signature
    old_uuids = {old[i].get("uuid") for i in conv}
    nturns = Turns(new)
    nconv = nturns.members(0, len(nturns))
    by_uuid = {old[i].get("uuid"): i for i in conv}
    first_change = len(nconv)
    for pos, j in enumerate(nconv):
        i = by_uuid.get(new[j].get("uuid"))
        if i is None or pos >= len(conv) or conv[pos] != i or content_key(old[i]) != content_key(new[j]):
            first_change = pos
            break
    kept_valid = {b.get("signature") or b.get("data") or ""
                  for pos, j in enumerate(nconv) if pos < first_change
                  for b in blocks(new[j]) if b.get("type") in THINKING} & valid

    w_new = {new[j].get("uuid"): weight(new[j], kept_valid) for j in nconv}
    # a record copied to a new place (carried attachments) costs what it did
    known = {r.get("uuid") for r in old if r.get("uuid")}
    copies = {}
    for j in nconv:
        if new[j].get("uuid") not in known:
            copies.setdefault(content_key(new[j]), []).append(new[j].get("uuid"))
    moved = set()
    for i in conv:
        if old[i].get("uuid") not in w_new and copies.get(content_key(old[i])):
            moved.add(old[i].get("uuid"))
            moved.add(copies[content_key(old[i])].pop())
    overall = base / (sum(seg_w) or 1)
    removed = sum(scale[seg(i)] * (w_old[i] - w_new.get(old[i].get("uuid"), 0.0))
                  for i in conv if old[i].get("uuid") not in moved)
    added = sum(overall * w for u, w in w_new.items() if u not in old_uuids and u not in moved)
    est = base - removed + added
    return max(int(est * (1 - margin)), MIN_ESTIMATE), fresh


def estimate_after(path: str, before: list[dict], new: list[dict],
                   since: float) -> tuple[int | None, bool]:
    """estimate_context from the newest version of the file whose counts are
    real: the file before this edit, or, when this edit follows another one
    with no request in between, the snapshot taken before that one. The flag
    says whether the estimate rests on real counts (anchored) or only on an
    earlier estimate (chained)."""
    est, fresh = estimate_context(before, new, since)
    if fresh or est is None:
        return est, fresh
    snaps = snapshot_paths(path)
    meta = read_meta(path)
    times = [snapshot_time(path, snapshot_number(s), meta) for s in snaps]
    tried = 0
    for k in range(len(snaps) - 1, -1, -1):
        if times[k] > since:
            continue
        older, ok = estimate_context(load(snaps[k]), new, times[k - 1] if k else 0.0)
        if ok:
            return older, True
        tried += 1
        if tried == 5:
            break
    return est, False
