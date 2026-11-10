"""Numbered snapshots kept beside a transcript."""
from __future__ import annotations

import glob
import os
import re
import shutil
from dataclasses import dataclass

from .errors import PruneError
from .guard import check_live
from .transcript import load


def snapshot_paths(path: str) -> list[str]:
    found = [p for p in glob.glob(glob.escape(path) + ".snap*")
             if re.search(r"\.snap\d+$", p)]
    return sorted(found, key=lambda p: int(re.search(r"snap(\d+)$", p).group(1)))


def take_snapshot(path: str) -> tuple[int, str]:
    existing = snapshot_paths(path)
    n = 0 if not existing else int(re.search(r"snap(\d+)$", existing[-1]).group(1)) + 1
    dst = f"{path}.snap{n}"
    shutil.copy2(path, dst)
    return n, dst


def list_snapshots(path: str) -> list[tuple[str, int]]:
    """(snapshot path, size in bytes), oldest first."""
    return [(p, os.path.getsize(p)) for p in snapshot_paths(path)]


@dataclass
class Restored:
    source: str
    target: str
    saved: str | None   # where the file as it was before the restore went


def restore_snapshot(path: str, n: int, force: bool = False) -> Restored:
    src = f"{path}.snap{n}"
    if not os.path.exists(src):
        raise PruneError(f"no such snapshot: {src}")
    check_live(path, force)
    load(src)

    saved = None
    if os.path.exists(path):
        _, saved = take_snapshot(path)

    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
        shutil.copyfileobj(inp, out)
    shutil.copymode(path if os.path.exists(path) else src, tmp)
    os.replace(tmp, path)
    return Restored(source=src, target=path, saved=saved)
