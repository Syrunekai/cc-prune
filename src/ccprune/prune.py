"""The one flow behind cut, clear and clearcut: plan the edit in memory, then
apply it with a live-session check, validation, one snapshot and one write."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Callable

from . import settings
from .calibration import record_estimate
from .clear import ClearOptions, ClearOutcome, clear_records
from .cut import CutOptions, CutPlan, plan_cut, validate_cut
from .errors import PruneError
from .estimate import estimate_after, last_edit_time
from .guard import check_live
from .snapshots import SnapshotInfo, enforce_limit, snapshots_over_limit, take_snapshot
from .transcript import load, replace_file, stage_validated, validate
from .usage import last_context
from .usage import reset_usage as rewrite_usage


def file_stamp(path: str) -> tuple[int, int]:
    """Size and modification time: what a transcript looked like when read."""
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


@dataclass
class LoadedTranscript:
    """A transcript read once, so many plans can be made from it without
    reading the file again. `records` is never modified by planning."""
    path: str
    records: list[dict]
    stamp: tuple[int, int]


def load_transcript(path: str) -> LoadedTranscript:
    stamp = file_stamp(path)
    return LoadedTranscript(path=path, records=load(path), stamp=stamp)


@dataclass
class Plan:
    """An edit worked out in memory. Nothing has been written."""
    path: str
    orig: list[dict]            # the records as read; never modified
    new: list[dict]             # the records to write
    stamp: tuple[int, int]      # the file's size and mtime when it was read
    size_before: int
    since: float                # when the transcript was last rewritten by cc-prune
    label: str                  # names the snapshot this edit takes
    cut: CutPlan | None
    clear: ClearOutcome | None
    last_context: int           # input tokens of the newest recorded request
    estimate: int | None        # tokens the next request will count after the edit
    estimate_anchored: bool     # whether the estimate rests on real counts
    reset_usage: bool           # whether applying rewrites the recorded usage
    will_drop: list[SnapshotInfo]   # snapshots applying would delete under the limit
    check: Callable[[list[dict]], str | None]   # validates the file as written

    @property
    def is_noop(self) -> bool:
        """A clear that matched nothing leaves the file as it is."""
        return self.cut is None and self.clear is not None and not self.clear.freed


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
    noop: bool = False
    dropped: list[SnapshotInfo] = field(default_factory=list)


def plan_label(cut: CutPlan | None, clear: ClearOutcome | None) -> str:
    if cut and clear:
        return f"clearcut turns {cut.a + 1}-{cut.b}"
    if cut:
        return f"cut turns {cut.a + 1}-{cut.b}"
    return "clear"


def plan_prune(path: str, cut: CutOptions | None = None, clear: ClearOptions | None = None,
               *, reset_usage: bool = True, loaded: LoadedTranscript | None = None) -> Plan:
    """Plan a cut, a clear or both in one pass over the records. A cut runs
    first; the clear then runs over the records that are left. With `loaded`
    the transcript is not read again."""
    if cut is None and clear is None:
        raise ValueError("plan_prune needs a cut, a clear or both")
    if cut:
        cut.check()
    if clear:
        clear.check()

    if loaded is not None:
        stamp, orig = loaded.stamp, loaded.records
        work = copy.deepcopy(orig) if clear else orig
    else:
        stamp = file_stamp(path)
        orig = load(path)
        work = load(path) if clear else orig
    since = last_edit_time(path)
    cut_plan = plan_cut(work, cut) if cut else None
    new = cut_plan.new if cut_plan else work
    outcome = clear_records(new, clear, before=orig if cut_plan else None) if clear else None

    last_ctx = last_context(orig)
    estimate, anchored = None, False
    if cut_plan:
        if last_ctx:
            estimate, anchored = estimate_after(path, orig, new, since)
    elif reset_usage and set(outcome.freed) - {"transformations"}:
        estimate, anchored = estimate_after(path, orig, new, since)

    if cut_plan:
        thinking = outcome.thinking if outcome else cut_plan.kept_thinking

        def check(loaded_recs: list[dict]) -> str | None:
            return validate_cut(loaded_recs, orig, cut_plan, thinking)
    else:
        uuids = [r.get("uuid") for r in orig]
        parents = [r.get("parentUuid") for r in orig]

        def check(loaded_recs: list[dict]) -> str | None:
            return validate(loaded_recs, uuids, parents, outcome.thinking)

    return Plan(path=path, orig=orig, new=new, stamp=stamp, size_before=stamp[0],
                since=since, label=plan_label(cut_plan, outcome), cut=cut_plan,
                clear=outcome, last_context=last_ctx, estimate=estimate,
                estimate_anchored=anchored, reset_usage=reset_usage,
                will_drop=snapshots_over_limit(path, settings.MAX_SNAPSHOTS, adding=1),
                check=check)


def apply_plan(plan: Plan, *, force: bool = False) -> Applied:
    """Write a plan: check the session is not live and the file is as it was
    read, rewrite the recorded usage, validate the result, snapshot the
    original and replace it. A plan applies once; it edits its records. A
    plan that changes nothing writes nothing and takes no snapshot."""
    check_live(plan.path, force)
    if file_stamp(plan.path) != plan.stamp:
        raise PruneError("the transcript changed after it was read. Read it again "
                         "and make a new plan; nothing was written.")
    if plan.is_noop:
        return Applied(snapshot=None, snapshot_path=None, reset=None,
                       size_after=plan.size_before, noop=True)

    reset = None
    if plan.estimate and plan.reset_usage and plan.estimate < plan.last_context:
        changed = rewrite_usage(plan.new, plan.estimate)
        reset = UsageReset(plan.last_context, plan.estimate, len(changed))
    staged = stage_validated(plan.new, plan.path, plan.check)
    n, snap = take_snapshot(plan.path, plan.label)
    replace_file(staged, plan.path)
    if plan.estimate:
        try:
            record_estimate(plan.path, plan.estimate, plan.last_context, plan.label)
        except OSError:
            pass
    return Applied(snapshot=n, snapshot_path=snap, reset=reset,
                   size_after=os.path.getsize(plan.path), dropped=enforce_limit(plan.path))
