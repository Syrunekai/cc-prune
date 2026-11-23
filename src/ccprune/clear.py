"""Clearing digested tool output from the records of a transcript."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from .errors import PruneError
from .transcript import (API_ATTACHMENTS, KEEP_PARAMS, PLACEHOLDER, STRUCTURAL, blocks,
                         failing_thinking, is_image_result, load, result_text,
                         thinking_fingerprint, thinking_positions)

DEFAULT_KEEP_TAIL = 0.15


@dataclass
class ClearOptions:
    results: int = 0
    inputs: int = 0
    commands: int = 0
    attachments: int = 0
    images: bool = False
    transformations: bool = False
    drop_failing_thinking: bool = False
    drop_all_thinking: bool = False
    keep_tail: float = DEFAULT_KEEP_TAIL

    @property
    def drops_thinking(self) -> bool:
        return self.drop_failing_thinking or self.drop_all_thinking

    def check(self) -> None:
        if not any([self.results, self.inputs, self.commands, self.attachments,
                    self.images, self.transformations, self.drops_thinking]):
            raise PruneError(
                "pick at least one bucket: --results N / --inputs N / --commands N / "
                "--attachments N / --images / --transformations / "
                "--drop-failing-thinking / --drop-all-thinking\n"
                "(N is a length threshold in characters; use 1 to clear all)")
        self.check_thinking()

    def check_thinking(self) -> None:
        if self.drop_failing_thinking and self.drop_all_thinking:
            raise PruneError("--drop-failing-thinking and --drop-all-thinking are "
                             "alternatives; pick one")


@dataclass
class ThinkingDrop:
    """The thinking blocks a thinking flag deletes."""
    deleted: int
    total: int
    first_record: int
    last_record: int
    models: list[str]
    newer: int      # kept thinking blocks after the last deleted record


@dataclass
class ClearOutcome:
    total: int          # records in the file the clear ran over
    protected: int      # records at the end that were left untouched
    freed: Counter      # characters removed, by bucket
    hits: Counter       # values cleared, by bucket
    thinking: list[str] # the thinking blocks the records carry afterwards
    drop: ThinkingDrop | None


def shrink_long_strings(obj, limit: int, stats: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in STRUCTURAL:
                continue
            if isinstance(v, str) and len(v) > limit:
                stats[0] += len(v)
                stats[1] += 1
                obj[k] = f"{PLACEHOLDER} {len(v)} chars"
            else:
                shrink_long_strings(v, limit, stats)
    elif isinstance(obj, list):
        for v in obj:
            shrink_long_strings(v, limit, stats)


def thinking_plan(recs: list[dict], opts: ClearOptions,
                  before: list[dict] | None = None) -> list[tuple[int, int]]:
    """The thinking blocks to delete. With `before`, the records as they were
    before a cut in the same edit, the failing blocks are found there, where
    the API's reports still line up with the blocks, and kept where they
    survived the cut."""
    if opts.drop_all_thinking:
        return thinking_positions(recs)
    if before is None:
        return failing_thinking(recs)
    failing = {_block_key(before[i], j) for i, j in failing_thinking(before)}
    return [(i, j) for i, j in thinking_positions(recs) if _block_key(recs[i], j) in failing]


def _block_key(rec: dict, j: int) -> tuple[str, str]:
    block = rec["message"]["content"][j]
    return rec.get("uuid") or "", json.dumps(block, ensure_ascii=False, sort_keys=True)


def describe_drop(recs: list[dict], pos: list[tuple[int, int]],
                  plan: list[tuple[int, int]]) -> ThinkingDrop | None:
    if not plan:
        return None
    lines = sorted({i for i, _ in plan})
    return ThinkingDrop(
        deleted=len(plan), total=len(pos),
        first_record=lines[0], last_record=lines[-1],
        models=sorted({(recs[i].get("message") or {}).get("model") or "?"
                       for i in lines}),
        newer=len([p for p in pos if p[0] > lines[-1]]))


def preview_thinking(path: str, opts: ClearOptions) -> ThinkingDrop | None:
    """What a thinking flag would delete from the file, or None when nothing."""
    recs = load(path)
    return describe_drop(recs, thinking_positions(recs), thinking_plan(recs, opts))


def clear_records(recs: list[dict], opts: ClearOptions,
                  before: list[dict] | None = None) -> ClearOutcome:
    """Clear the buckets `opts` asks for in `recs`, in place, leaving the last
    `opts.keep_tail` of the records alone. `before` is the transcript as it
    was before a cut made in the same edit (see thinking_plan)."""
    thinking = thinking_fingerprint(recs)
    cutoff = int(len(recs) * (1 - opts.keep_tail))
    freed, hits = Counter(), Counter()
    drop = None

    if opts.drops_thinking:
        pos = thinking_positions(recs)
        plan = thinking_plan(recs, opts, before)
        targets = set(plan)
        drop = describe_drop(recs, pos, plan)
        for i, j in pos:
            if (i, j) in targets:
                freed["thinking"] += len(json.dumps(recs[i]["message"]["content"][j],
                                                    ensure_ascii=False))
                hits["thinking"] += 1
        thinking = [fp for fp, p in zip(thinking, pos) if p not in targets]
        for i, j in sorted(targets, reverse=True):
            recs[i]["message"]["content"].pop(j)

    for i, rec in enumerate(recs):
        if i >= cutoff:
            break

        msg = rec.get("message")
        if opts.transformations and isinstance(msg, dict):
            logged = msg.pop("input_transformations", None)
            if logged:
                freed["transformations"] += len(json.dumps(logged, ensure_ascii=False))
                hits["transformations"] += 1

        if (opts.attachments and rec.get("type") == "attachment"
                and (rec.get("attachment") or {}).get("type") not in API_ATTACHMENTS):
            stats = [0, 0]
            shrink_long_strings(rec, opts.attachments, stats)
            freed["attachments"] += stats[0]
            hits["attachments"] += stats[1]

        for b in blocks(rec):
            kind = b.get("type")
            if kind == "tool_result":
                text = result_text(b)
                if text == PLACEHOLDER:
                    continue
                if is_image_result(b):
                    if opts.images:
                        freed["images"] += len(text)
                        hits["images"] += 1
                        b["content"] = PLACEHOLDER
                elif opts.results and len(text) > opts.results:
                    freed["results"] += len(text)
                    hits["results"] += 1
                    b["content"] = PLACEHOLDER
                    b.pop("is_error", None)
                    rec.pop("toolUseResult", None)
            elif kind == "tool_use":
                params = b.get("input")
                if not isinstance(params, dict):
                    continue
                if opts.inputs:
                    for key, val in params.items():
                        if (isinstance(val, str) and len(val) > opts.inputs
                                and key not in KEEP_PARAMS
                                and not val.startswith(PLACEHOLDER)):
                            freed["inputs"] += len(val)
                            hits["inputs"] += 1
                            params[key] = f"{PLACEHOLDER} {len(val)} chars"
                if opts.commands and b.get("name") == "Bash":
                    val = params.get("command")
                    if (isinstance(val, str) and len(val) > opts.commands
                            and not val.startswith(PLACEHOLDER)):
                        freed["commands"] += len(val)
                        hits["commands"] += 1
                        params["command"] = f"{PLACEHOLDER} {len(val)} chars"

    return ClearOutcome(total=len(recs), protected=len(recs) - cutoff, freed=freed,
                        hits=hits, thinking=thinking, drop=drop)
