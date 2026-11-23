"""Renaming and deleting sessions.

Claude Code's /rename appends `{"type":"custom-title","customTitle":...,"sessionId":...}`
to the transcript, where claude --resume reads it, and writes the same title to
`<project>/<session id>/custom-title.json`. A running Claude Code appends its
in-memory title again after every reply, so a running session is not renamed
from outside.

Deleting a session removes everything Claude Code and cc-prune keep for it:

    projects/<project>/<id>.jsonl          the transcript
    projects/<project>/<id>.jsonl.snap*    cc-prune snapshots, and .snapmeta.json
    projects/<project>/<id>/               subagent transcripts, tool results, the title
    file-history/<id>/  session-env/<id>/
    teams/session-<id8>/  tasks/session-<id8>/  jobs/<id8>/
                                           named by the first 8 characters of the id; kept
                                           when they record another session's id, or when
                                           another transcript shares those 8 characters
    cc-prune's favorite and estimate-log entries for the id

history.jsonl, the prompt history every Claude Code process appends to, is not
rewritten.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
from dataclasses import dataclass, field

from . import settings
from .errors import PruneError
from .guard import check_live
from .snapshots import meta_path, snapshot_paths

TITLE_FILE = "custom-title.json"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def session_id(path: str) -> str:
    return os.path.basename(path)[:-len(".jsonl")] if path.endswith(".jsonl") \
        else os.path.basename(path)


def session_dir(path: str) -> str:
    return path[:-len(".jsonl")] if path.endswith(".jsonl") else path + ".d"


def claude_dir() -> str:
    return os.path.expanduser(settings.CONFIG_DIR)


# --------------------------------------------------------------------------
# rename
# --------------------------------------------------------------------------

def custom_title(path: str) -> str | None:
    """The title the session was renamed to, if it was."""
    from .sessions import read_head, read_tail, session_title
    try:
        head, tail = read_head(path), read_tail(path)
    except OSError:
        return None
    pick = lambda recs: [o for o in recs if o.get("type") == "custom-title"]
    return session_title(path, pick(head), pick(tail))


def rename_session(path: str, title: str, force: bool = False) -> str:
    """Set the session's title as Claude Code's /rename does: append a
    custom-title record to the transcript and write custom-title.json. The
    transcript keeps its modification time. Refuses a running session, which
    would write its own title back after its next reply."""
    if not os.path.isfile(path):
        raise PruneError(f"no such transcript: {path}")
    title = " ".join(title.split())
    if not title:
        raise PruneError("the title is empty")
    check_live(path, force)
    sid = session_id(path)
    if custom_title(path) != title:
        st = os.stat(path)
        record = json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid},
                            ensure_ascii=False, separators=(",", ":"))
        with open(path, "rb+") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell():
                fh.seek(-1, os.SEEK_END)
                lead = b"" if fh.read(1) == b"\n" else b"\n"
            else:
                lead = b""
            fh.write(lead + record.encode("utf-8") + b"\n")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.makedirs(session_dir(path), exist_ok=True)
    target = os.path.join(session_dir(path), TITLE_FILE)
    tmp = target + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"customTitle": title}, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, target)
    return title


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------

@dataclass
class Artifact:
    kind: str
    path: str
    size: int
    is_dir: bool


@dataclass
class SessionFiles:
    session_id: str
    transcript: str
    artifacts: list[Artifact]                 # everything removed, the transcript last
    kept: list[str] = field(default_factory=list)   # prefix folders that are not this session's

    @property
    def size(self) -> int:
        return sum(a.size for a in self.artifacts)


def _size(path: str) -> int:
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _artifact(kind: str, path: str) -> Artifact:
    return Artifact(kind, path, _size(path), os.path.isdir(path) and not os.path.islink(path))


def _ids_in(folder: str) -> set[str]:
    """Session ids recorded in the small JSON files of a folder."""
    found = set()
    for name in ("config.json", "state.json"):
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                found.update(UUID.findall(fh.read(1_000_000)))
        except OSError:
            pass
    return found


def _all_session_ids() -> set[str]:
    root = os.path.join(claude_dir(), "projects")
    return {os.path.basename(p)[:-len(".jsonl")]
            for p in glob.glob(os.path.join(glob.escape(root), "*", "*.jsonl"))}


def session_files(path: str) -> SessionFiles:
    """Everything delete_session would remove for this transcript."""
    if not os.path.isfile(path):
        raise PruneError(f"no such transcript: {path}")
    sid = session_id(path)
    home = claude_dir()
    items: list[Artifact] = []
    for snap in snapshot_paths(path):
        items.append(_artifact("snapshot", snap))
    for extra in (meta_path(path), path + ".tmp"):
        if os.path.lexists(extra):
            items.append(_artifact("snapshot data", extra))
    if os.path.lexists(session_dir(path)):
        items.append(_artifact("session folder", session_dir(path)))
    for kind in ("file-history", "session-env"):
        p = os.path.join(home, kind, sid)
        if os.path.lexists(p):
            items.append(_artifact(kind, p))

    kept = []
    short = sid[:8]
    others = None
    for kind, p in (("teams", os.path.join(home, "teams", f"session-{short}")),
                    ("tasks", os.path.join(home, "tasks", f"session-{short}")),
                    ("jobs", os.path.join(home, "jobs", short))):
        if not os.path.lexists(p):
            continue
        ids = {i for i in _ids_in(p) if i.startswith(short)}
        if sid in ids:
            mine = True
        elif ids:
            mine = False
        else:
            if others is None:
                others = {i for i in _all_session_ids() if i.startswith(short) and i != sid}
            mine = not others
        if mine:
            items.append(_artifact(kind, p))
        else:
            kept.append(p)
    items.append(_artifact("transcript", path))
    return SessionFiles(session_id=sid, transcript=path, artifacts=items, kept=kept)


def _inside(path: str, root: str) -> bool:
    real = os.path.realpath(os.path.dirname(path))
    return real == root or real.startswith(root + os.sep)


@dataclass
class Deleted:
    session_id: str
    removed: list[Artifact]
    size: int


def delete_session(path: str, force: bool = False) -> Deleted:
    """Delete the session and everything kept for it. Refuses a session that
    is running, like every edit does."""
    check_live(path, force)
    files = session_files(path)
    root = os.path.realpath(claude_dir())
    proj = os.path.realpath(os.path.dirname(path))
    for a in files.artifacts:
        if not (_inside(a.path, root) or _inside(a.path, proj)):
            raise PruneError(f"refusing to delete {a.path}: it is outside {root}")
    removed = []
    for a in files.artifacts:
        if os.path.islink(a.path) or not os.path.isdir(a.path):
            os.remove(a.path)
        else:
            shutil.rmtree(a.path)
        removed.append(a)
    _forget(files.session_id)
    return Deleted(files.session_id, removed, sum(a.size for a in removed))


def _forget(sid: str) -> None:
    """Drop the id from cc-prune's favorites and estimate log."""
    from .favorites import load_favorites, save_favorites
    try:
        favs = load_favorites()
        if sid in favs:
            favs.discard(sid)
            save_favorites(favs)
    except OSError:
        pass
    log = os.path.expanduser(settings.ESTIMATE_LOG)
    try:
        with open(log, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    keep = []
    for line in lines:
        try:
            if json.loads(line).get("session") == sid:
                continue
        except (json.JSONDecodeError, AttributeError):
            pass
        keep.append(line)
    if len(keep) != len(lines):
        tmp = log + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        os.replace(tmp, log)
