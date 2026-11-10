"""The small editing commands: reset-usage and strip-commands."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .errors import PruneError
from .guard import check_live
from .snapshots import take_snapshot
from .transcript import (NAME_RE, PLACEHOLDER, STDOUT_RE, THINKING, chain_fingerprint, load,
                         replace_file, stage_validated, validate)
from .usage import reset_usage


@dataclass
class UsageResetResult:
    snapshot: int
    snapshot_path: str
    changed: list[tuple[int, int, int]]     # (record index, old tokens, new tokens)


def reset_usage_file(path: str, tokens: int, force: bool = False) -> UsageResetResult:
    """Rewrite the recorded usage of the last requests down to `tokens`."""
    if tokens < 1:
        raise PruneError("--tokens must be a positive number")
    check_live(path, force)
    recs = load(path)
    uuids, parents, thinking = chain_fingerprint(recs)

    changed = reset_usage(recs, tokens)
    if not changed:
        raise PruneError("nothing to reset: no recent request is above that figure")
    staged = stage_validated(recs, path,
                             lambda loaded: validate(loaded, uuids, parents, thinking))
    n, snap = take_snapshot(path)
    replace_file(staged, path)
    return UsageResetResult(snapshot=n, snapshot_path=snap, changed=changed)


@dataclass
class StripPlan:
    path: str
    recs: list[dict]
    hits: list[tuple]       # (record index, container, key, command name)
    chars: Counter          # characters of stdout, by command name


@dataclass
class Stripped:
    snapshot: int
    snapshot_path: str
    count: int
    freed: int
    keep_last: int


def _strings(rec: dict):
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") in THINKING:
                return
            for k, v in o.items():
                if isinstance(v, str):
                    yield o, k, v
                else:
                    yield from walk(v)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, str):
                    yield o, i, v
                else:
                    yield from walk(v)
    yield from walk(rec)


def plan_strip(path: str, commands: list[str], orphans: bool) -> StripPlan:
    """Find the local-command-stdout blocks of the named commands (or of every
    command, with `orphans`)."""
    recs = load(path)
    wanted = {c.lstrip("/").lower() for c in commands}

    hits = []
    for i, rec in enumerate(recs):
        for cont, key, val in _strings(rec):
            if not STDOUT_RE.search(val):
                continue
            m = NAME_RE.search(val)
            named = m.group(1).lower() if m else "<orphan>"
            if orphans or named in wanted:
                hits.append((i, cont, key, named))

    chars = Counter()
    for i, cont, key, named in hits:
        chars[named] += len(STDOUT_RE.search(cont[key]).group(2))
    return StripPlan(path=path, recs=recs, hits=hits, chars=chars)


def apply_strip(plan: StripPlan, keep_last: int = 1, force: bool = False) -> Stripped:
    """Replace the matched stdout blocks with a placeholder, sparing the last
    `keep_last` of each command."""
    check_live(plan.path, force)
    recs, hits = plan.recs, plan.hits

    spare = set()
    if keep_last:
        for name in {h[3] for h in hits}:
            for h in [x for x in hits if x[3] == name][-keep_last:]:
                spare.add((h[0], h[2]))

    uuids, parents, thinking = chain_fingerprint(recs)

    freed = count = 0
    for i, cont, key, named in hits:
        if (i, key) in spare:
            continue
        before = cont[key]
        after, k = STDOUT_RE.subn(
            lambda m: m.group(1) + f"{PLACEHOLDER} {len(m.group(2))} chars"
            + m.group(3), before)
        if k:
            freed += len(before) - len(after)
            count += k
            cont[key] = after

    staged = stage_validated(recs, plan.path,
                             lambda loaded: validate(loaded, uuids, parents, thinking))
    n, snap = take_snapshot(plan.path)
    replace_file(staged, plan.path)
    return Stripped(snapshot=n, snapshot_path=snap, count=count, freed=freed,
                    keep_last=keep_last)
