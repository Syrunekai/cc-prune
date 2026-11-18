"""Numbered snapshots kept beside a transcript, with labels and pins."""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
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


def take_snapshot(path: str, label: str = "") -> tuple[int, str]:
    existing = snapshot_paths(path)
    n = 0 if not existing else snapshot_number(existing[-1]) + 1
    dst = f"{path}.snap{n}"
    shutil.copy2(path, dst)
    if label:
        meta = read_meta(path)
        meta.setdefault(str(n), {})["label"] = label
        meta[str(n)]["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_meta(path, meta)
    return n, dst


@dataclass
class SnapshotInfo:
    n: int
    path: str
    size: int
    label: str
    pinned: bool
    created: float      # when the snapshot file was made (change time)


def list_snapshots(path: str) -> list[SnapshotInfo]:
    """Every snapshot of the transcript, oldest first."""
    meta = read_meta(path)
    out = []
    for p in snapshot_paths(path):
        n = snapshot_number(p)
        m = meta.get(str(n), {})
        pinned = m["pinned"] if "pinned" in m else n == 0
        st = os.stat(p)
        out.append(SnapshotInfo(n=n, path=p, size=st.st_size, label=m.get("label", ""),
                                pinned=bool(pinned), created=st.st_ctime))
    return out


def set_pinned(path: str, n: int, pinned: bool) -> None:
    if not os.path.exists(f"{path}.snap{n}"):
        raise PruneError(f"no such snapshot: {path}.snap{n}")
    meta = read_meta(path)
    meta.setdefault(str(n), {})["pinned"] = pinned
    write_meta(path, meta)


def delete_snapshot(path: str, n: int) -> int:
    """Delete a snapshot and its label; returns the bytes freed."""
    snap = f"{path}.snap{n}"
    if not os.path.exists(snap):
        raise PruneError(f"no such snapshot: {snap}")
    size = os.path.getsize(snap)
    os.remove(snap)
    meta = read_meta(path)
    if meta.pop(str(n), None) is not None:
        write_meta(path, meta)
    return size


def snapshots_over_limit(path: str, limit: int, adding: int = 0) -> list[SnapshotInfo]:
    """The oldest unpinned snapshots that go once `adding` more exist and at
    most `limit` unpinned ones are kept. Nothing goes when `limit` is 0."""
    if limit <= 0:
        return []
    unpinned = [s for s in list_snapshots(path) if not s.pinned]
    excess = len(unpinned) + adding - limit
    return unpinned[:excess] if excess > 0 else []


def enforce_limit(path: str, limit: int | None = None) -> list[SnapshotInfo]:
    """Delete the snapshots beyond the limit; returns what was deleted."""
    gone = snapshots_over_limit(path, settings.MAX_SNAPSHOTS if limit is None else limit)
    for s in gone:
        delete_snapshot(path, s.n)
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
