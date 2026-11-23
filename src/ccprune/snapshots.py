"""Numbered snapshots kept beside a transcript, with labels and pins.

Snapshots are numbered 0 to N-1 with no gaps, oldest first: deleting any of
them renumbers the rest. Each snapshot's label, pin and creation time live in
the transcript's .snapmeta.json, keyed by number, and move with it.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import settings
from .errors import PruneError
from .guard import check_live
from .transcript import load


def snapshot_paths(path: str) -> list[str]:
    found = [p for p in glob.glob(glob.escape(path) + ".snap*")
             if re.search(r"\.snap\d+$", p)]
    return sorted(found, key=lambda p: int(re.search(r"snap(\d+)$", p).group(1)))


def snapshot_number(snap: str) -> int:
    return int(re.search(r"snap(\d+)$", snap).group(1))


def meta_path(path: str) -> str:
    return path + ".snapmeta.json"


def read_meta(path: str) -> dict[str, dict]:
    try:
        with open(meta_path(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_meta(path: str, meta: dict[str, dict]) -> None:
    if not meta:
        try:
            os.remove(meta_path(path))
        except OSError:
            pass
        return
    tmp = meta_path(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    os.replace(tmp, meta_path(path))


def snapshot_time(path: str, n: int, meta: dict[str, dict] | None = None) -> float:
    """When snapshot n was taken: the recorded time, else the file's change time."""
    m = (read_meta(path) if meta is None else meta).get(str(n), {})
    if isinstance(m.get("time"), (int, float)):
        return float(m["time"])
    return os.stat(f"{path}.snap{n}").st_ctime


def take_snapshot(path: str, label: str = "") -> tuple[int, str]:
    existing = snapshot_paths(path)
    if [snapshot_number(p) for p in existing] != list(range(len(existing))):
        renumber(path)
    n = len(existing)
    dst = f"{path}.snap{n}"
    shutil.copy2(path, dst)
    meta = read_meta(path)
    entry = meta.setdefault(str(n), {})
    entry["time"] = time.time()
    entry["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if label:
        entry["label"] = label
    write_meta(path, meta)
    return n, dst


@dataclass
class SnapshotInfo:
    n: int
    path: str
    size: int
    label: str
    pinned: bool
    created: float      # when the snapshot was taken


def list_snapshots(path: str) -> list[SnapshotInfo]:
    """Every snapshot of the transcript, oldest first."""
    meta = read_meta(path)
    out = []
    for p in snapshot_paths(path):
        n = snapshot_number(p)
        m = meta.get(str(n), {})
        pinned = m["pinned"] if "pinned" in m else n == 0
        out.append(SnapshotInfo(n=n, path=p, size=os.path.getsize(p), label=m.get("label", ""),
                                pinned=bool(pinned), created=snapshot_time(path, n, meta)))
    return out


def set_pinned(path: str, n: int, pinned: bool) -> None:
    if not os.path.exists(f"{path}.snap{n}"):
        raise PruneError(f"no such snapshot: {path}.snap{n}")
    meta = read_meta(path)
    meta.setdefault(str(n), {})["pinned"] = pinned
    write_meta(path, meta)


def renumber(path: str) -> dict[int, int]:
    """Number the snapshots 0 to N-1 in their current order, moving their
    labels, pins and times with them. Returns old number -> new number."""
    snaps = snapshot_paths(path)
    meta = read_meta(path)
    moves = {snapshot_number(p): k for k, p in enumerate(snaps)}
    new_meta = {}
    for old, new in moves.items():
        entry = dict(meta.get(str(old), {}))
        entry.setdefault("pinned", old == 0)
        entry.setdefault("time", snapshot_time(path, old, meta))
        new_meta[str(new)] = entry
    for old, new in sorted(moves.items()):
        if old != new:
            os.rename(f"{path}.snap{old}", f"{path}.snap{new}")
    if any(old != new for old, new in moves.items()) or new_meta != meta:
        write_meta(path, new_meta)
    return moves


def delete_snapshots(path: str, numbers) -> int:
    """Delete these snapshots, then renumber the rest from 0. Returns the
    bytes freed."""
    numbers = sorted(set(numbers))
    for n in numbers:
        if not os.path.exists(f"{path}.snap{n}"):
            raise PruneError(f"no such snapshot: {path}.snap{n}")
    meta = read_meta(path)
    for n in snapshot_paths(path):
        k = snapshot_number(n)
        meta.setdefault(str(k), {}).setdefault("time", snapshot_time(path, k, meta))
        meta[str(k)].setdefault("pinned", k == 0)
    freed = 0
    for n in numbers:
        snap = f"{path}.snap{n}"
        freed += os.path.getsize(snap)
        os.remove(snap)
        meta.pop(str(n), None)
    write_meta(path, meta)
    renumber(path)
    return freed


def delete_snapshot(path: str, n: int) -> int:
    """Delete one snapshot and renumber the rest; returns the bytes freed."""
    return delete_snapshots(path, [n])


def delete_all_snapshots(path: str) -> int:
    """Delete every snapshot and the metadata file; returns the bytes freed."""
    freed = 0
    for p in snapshot_paths(path):
        freed += os.path.getsize(p)
        os.remove(p)
    write_meta(path, {})
    return freed


def snapshots_over_limit(path: str, limit: int, adding: int = 0) -> list[SnapshotInfo]:
    """The oldest unpinned snapshots that go once `adding` more exist and at
    most `limit` unpinned ones are kept. Nothing goes when `limit` is 0."""
    if limit <= 0:
        return []
    unpinned = [s for s in list_snapshots(path) if not s.pinned]
    excess = len(unpinned) + adding - limit
    return unpinned[:excess] if excess > 0 else []


def enforce_limit(path: str, limit: int | None = None) -> list[SnapshotInfo]:
    """Delete the snapshots beyond the limit; returns what was deleted, with
    the numbers they had."""
    gone = snapshots_over_limit(path, settings.MAX_SNAPSHOTS if limit is None else limit)
    if gone:
        delete_snapshots(path, [s.n for s in gone])
    return gone


@dataclass
class Restored:
    source: str
    target: str
    saved: str | None   # where the file as it was before the restore went


def restore_snapshot(path: str, n: int, force: bool = False,
                     keep_current: bool = True) -> Restored:
    src = f"{path}.snap{n}"
    if not os.path.exists(src):
        raise PruneError(f"no such snapshot: {src}")
    check_live(path, force)
    load(src)

    saved = None
    if keep_current and os.path.exists(path):
        _, saved = take_snapshot(path, f"before restore of snap{n}")

    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
        shutil.copyfileobj(inp, out)
    shutil.copymode(path if os.path.exists(path) else src, tmp)
    os.replace(tmp, path)
    return Restored(source=src, target=path, saved=saved)
