"""Cutting a stretch of whole turns out of the middle of a conversation."""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass

from .errors import PruneError
from .transcript import blocks, first_text, thinking_fingerprint
from .turns import Turns, descendants, is_carried, tool_ids

CUT_NOTE = "[cc-prune: {n} earlier turns were cut from the conversation here]"
CUT_NOTE_RE = re.compile(r"\[cc-prune: (\d+) earlier turns were cut from the conversation here\]"
                         r"(?:\n\n)?")


@dataclass
class CutOptions:
    drop: float = 0
    keep_head: int = 5
    from_turn: int = 0
    to_turn: int = 0
    purge: bool = False
    note: bool = True

    def check(self) -> None:
        if not (self.drop or self.from_turn or self.to_turn):
            raise PruneError("pick --drop F (with --keep-head N) or --from A --to B; "
                             "--list shows the turns")


@dataclass
class CutPlan:
    turns: Turns
    a: int                      # first cut turn, 0-based
    b: int                      # first kept turn after the cut, 0-based
    cut_records: int
    cut_chars: int
    total_chars: int
    earlier: int                # turns earlier cuts already removed, noted inside the cut
    first_cut: str              # start of the first cut prompt
    first_kept: str             # start of the first kept prompt
    carried: Counter            # attachment kinds copied over, by kind
    moved_from: tuple[int, int] | None   # --from/--to as asked, when it had to move
    purge: bool
    purged: set[int]            # indexes of the records the cut deletes
    copies: list[dict]          # carried attachments, as new records
    tail_first: int             # index of the first kept record after the cut
    new: list[dict]             # the records after the cut
    expect: list                # uuids of the conversation a resume must load
    kept_uuids: list            # uuids of the records the resume may load
    kept_thinking: list[str]    # thinking blocks that survive the cut


def noted_turns(text: str) -> int:
    """Turns earlier cuts recorded in the notes inside `text`."""
    return sum(int(m.group(1)) for m in CUT_NOTE_RE.finditer(text))


def add_note(rec: dict, n: int) -> None:
    """Put the cut note at the start of the prompt, merging a note already there."""
    msg = rec["message"]
    if isinstance(msg.get("content"), str):
        text = msg["content"]
        msg["content"] = f"{CUT_NOTE.format(n=n + noted_turns(text))}\n\n{CUT_NOTE_RE.sub('', text)}"
        return
    for b in msg["content"]:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            b["text"] = f"{CUT_NOTE.format(n=n + noted_turns(text))}\n\n{CUT_NOTE_RE.sub('', text)}"
            return
    msg["content"].insert(0, {"type": "text", "text": CUT_NOTE.format(n=n)})


def cut_range(turns: Turns, opts: CutOptions) -> tuple[int, int]:
    """Turns a..b-1 (0-based) to cut, both ends moved onto clean boundaries."""
    n = len(turns)
    clean = [t for t in range(1, n) if turns.clean[t]]
    if opts.from_turn or opts.to_turn:
        if not (opts.from_turn and opts.to_turn):
            raise PruneError("--from and --to go together")
        if not 2 <= opts.from_turn <= opts.to_turn <= n - 1:
            raise PruneError(f"need 2 <= --from <= --to <= {n - 1} "
                             f"(turn 1 and the last turn always stay; see --list)")
        a = next((t for t in clean if t >= opts.from_turn - 1), None)
        b = next((t for t in reversed(clean) if t <= opts.to_turn), None)
    else:
        if not 0 < opts.drop < 1:
            raise PruneError("--drop is a fraction between 0 and 1")
        if not 1 <= opts.keep_head < n - 1:
            raise PruneError(f"--keep-head must be between 1 and {n - 2} for this "
                             f"conversation ({n} turns)")
        a = next((t for t in clean if t >= opts.keep_head), None)
        b = None
        if a is not None:
            target = opts.drop * sum(turns.size[a:])
            cands = [t for t in clean if t > a]
            if cands:
                b = min(cands, key=lambda t: abs(sum(turns.size[a:t]) - target))
    if a is None or b is None or b <= a:
        raise PruneError("no clean cut fits that range: a tool call is still waiting for "
                         "its result at every boundary in it. See --list for clean turns")
    return a, b


def plan_cut(recs: list[dict], opts: CutOptions) -> CutPlan:
    """Work out a cut without changing `recs`. The plan's `new` list holds the
    records after the cut; it shares record objects with `recs` except the
    first kept prompt, which is copied to carry the note."""
    turns = Turns(recs)
    a, b = cut_range(turns, opts)

    cut = turns.members(a, b)
    head_last = turns.chain[turns.bounds[a] - 1]
    tail_first = turns.first(b)
    kept = turns.members(0, a) + turns.members(b, len(turns))

    carried = [i for i in cut if is_carried(recs[i])]
    # turns removed by earlier cuts whose notes sit in this stretch
    earlier = sum(noted_turns(first_text(recs[turns.first(t)])) for t in range(a, b))

    purged = descendants(recs, set(turns.chain[turns.bounds[a]:turns.bounds[b]]),
                         {tail_first}) if opts.purge else set()
    copies, parent = [], recs[head_last]["uuid"]
    for i in carried:
        c = json.loads(json.dumps(recs[i]))
        c["uuid"], c["parentUuid"] = str(uuid.uuid4()), parent
        parent = c["uuid"]
        copies.append(c)

    new = []
    for i, r in enumerate(recs):
        if i in purged:
            continue
        if i == tail_first:
            new.extend(copies)
            r = json.loads(json.dumps(r))
            r["parentUuid"] = parent
            if opts.note:
                add_note(r, b - a + earlier)
        new.append(r)

    def prompt(t: int) -> str:
        return " ".join(first_text(recs[turns.first(t)]).split())[:70]

    moved = None
    if opts.from_turn and (a + 1, b) != (opts.from_turn, opts.to_turn):
        moved = (opts.from_turn, opts.to_turn)

    return CutPlan(
        turns=turns, a=a, b=b, cut_records=len(cut),
        cut_chars=sum(turns.size[a:b]), total_chars=sum(turns.size),
        earlier=earlier, first_cut=prompt(a), first_kept=prompt(b),
        carried=Counter((recs[i].get("attachment") or {}).get("type") for i in carried),
        moved_from=moved, purge=opts.purge, purged=purged, copies=copies,
        tail_first=tail_first, new=new,
        expect=[recs[i]["uuid"] for i in turns.chain[:turns.bounds[a]]]
        + [c["uuid"] for c in copies]
        + [recs[i]["uuid"] for i in turns.chain[turns.bounds[b]:]],
        kept_uuids=[recs[i].get("uuid") for i in kept],
        kept_thinking=thinking_fingerprint([r for i, r in enumerate(recs)
                                            if i not in purged]))


def validate_cut(new: list[dict], old: list[dict], plan: CutPlan,
                 thinking: list[str]) -> str | None:
    """The problem with a cut's result, or None when it holds. `new` is the
    file as written, `old` the records before the cut and `thinking` the
    thinking blocks the written file must carry."""
    copy_ids = [c["uuid"] for c in plan.copies]
    originals = [r for r in new if r.get("uuid") not in copy_ids]
    survivors = [r for i, r in enumerate(old) if i not in plan.purged]
    if len(originals) != len(survivors):
        return f"record count changed: expected {len(survivors)}, got {len(originals)}"
    for r, s in zip(originals, survivors):
        if r.get("uuid") != s.get("uuid"):
            return "uuid order changed"
        if (r.get("parentUuid") != s.get("parentUuid")
                and r.get("uuid") != old[plan.tail_first]["uuid"]):
            return f"parentUuid of {r.get('uuid')} changed"
    if [r.get("uuid") for r in new if r.get("uuid") in copy_ids] != copy_ids:
        return "carried attachments missing or out of order"
    if thinking_fingerprint(new) != thinking:
        return "thinking blocks altered -- the API would reject this"
    turns = Turns(new)
    if [new[i]["uuid"] for i in turns.chain] != plan.expect:
        return "the resumed conversation would not be head + carried + tail"
    conv = [i for t in range(len(turns)) for i in turns.members(t, t + 1)]
    if sorted(new[i].get("uuid") for i in conv if new[i].get("uuid") not in copy_ids) \
            != sorted(plan.kept_uuids):
        return "the resumed conversation would load records outside head and tail"
    uses, results = tool_ids(new, conv)
    if not results <= uses:
        return "a kept tool_result lost its tool_use"
    messages = [r for r in new if not str(r.get("type", "")).startswith("api-request")]
    all_uses = {b["id"] for r in messages for b in blocks(r)
                if b.get("type") == "tool_use" and b.get("id")}
    all_results = {b.get("tool_use_id") for r in messages for b in blocks(r)
                   if b.get("type") == "tool_result"}
    if not all_results <= all_uses:
        return "tool_use / tool_result pairing broken"
    return None
