"""The one flow behind cut, clear and clearcut: plan the edit in memory, then
apply it with a live-session check, validation, one snapshot and one write."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from .clear import ClearOptions, ClearOutcome, clear_records
from .cut import CutOptions, CutPlan, plan_cut, validate_cut
from .estimate import estimate_after, last_edit_time
from .guard import check_live
from .snapshots import take_snapshot
from .transcript import load, replace_file, stage_validated, validate
from .usage import last_context
from .usage import reset_usage as rewrite_usage


@dataclass
class Plan:
    """An edit worked out in memory. Nothing has been written."""
    path: str
    orig: list[dict]            # the records as read; never modified
    new: list[dict]             # the records to write
    size_before: int
    since: float                # when the transcript was last rewritten by cc-prune
    cut: CutPlan | None
    clear: ClearOutcome | None
    last_context: int           # input tokens of the newest recorded request
    estimate: int | None        # tokens the next request will count after the edit
    reset_usage: bool           # whether applying rewrites the recorded usage
    check: Callable[[list[dict]], str | None]   # validates the file as written


@dataclass
class UsageReset:
    last: int
    estimate: int
    records: int


@dataclass
class Applied:
    snapshot: int | None
    snapshot_path: str | None
    reset: UsageReset | None
    size_after: int


def plan_prune(path: str, cut: CutOptions | None = None, clear: ClearOptions | None = None,
               *, reset_usage: bool = True) -> Plan:
    """Plan a cut, a clear or both in one pass over the records. A cut runs
    first; the clear then runs over the records that are left."""
    if cut is None and clear is None:
        raise ValueError("plan_prune needs a cut, a clear or both")
    if cut:
        cut.check()
    if clear:
        clear.check()

    orig = load(path)
    since = last_edit_time(path)
    size_before = os.path.getsize(path)
    work = load(path) if clear else orig
    cut_plan = plan_cut(work, cut) if cut else None
    new = cut_plan.new if cut_plan else work
    outcome = clear_records(new, clear) if clear else None

    last_ctx = last_context(orig)
    estimate = None
    if cut_plan:
        estimate = estimate_after(path, orig, new, since) if last_ctx else None
    elif reset_usage and set(outcome.freed) - {"transformations"}:
        estimate = estimate_after(path, orig, new, since)

    if cut_plan:
        thinking = outcome.thinking if outcome else cut_plan.kept_thinking

        def check(loaded: list[dict]) -> str | None:
            return validate_cut(loaded, orig, cut_plan, thinking)
    else:
        uuids = [r.get("uuid") for r in orig]
        parents = [r.get("parentUuid") for r in orig]

        def check(loaded: list[dict]) -> str | None:
            return validate(loaded, uuids, parents, outcome.thinking)

    return Plan(path=path, orig=orig, new=new, size_before=size_before, since=since,
                cut=cut_plan, clear=outcome, last_context=last_ctx, estimate=estimate,
                reset_usage=reset_usage, check=check)


def apply_plan(plan: Plan, *, force: bool = False) -> Applied:
    """Write a plan: check the session is not live, rewrite the recorded usage,
    validate the result, snapshot the original and replace it. A plan applies
    once; it edits its records."""
    check_live(plan.path, force)
    reset = None
    if plan.estimate and plan.reset_usage and plan.estimate < plan.last_context:
        changed = rewrite_usage(plan.new, plan.estimate)
        reset = UsageReset(plan.last_context, plan.estimate, len(changed))
    staged = stage_validated(plan.new, plan.path, plan.check)
    n, snap = take_snapshot(plan.path)
    replace_file(staged, plan.path)
    return Applied(snapshot=n, snapshot_path=snap, reset=reset,
                   size_after=os.path.getsize(plan.path))
