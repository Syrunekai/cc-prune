"""Telling whether a transcript belongs to a running Claude Code session."""
from __future__ import annotations

import glob
import json
import os
import re
import time

from . import settings
from .errors import PruneError

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def session_ids_for(real: str) -> set[str]:
    """Session ids a transcript path belongs to: its own file name, plus any
    directory between the project folder and the file named after a session
    (subagent logs live under <session-id>/subagents/)."""
    root = os.path.realpath(os.path.expanduser(settings.PROJECTS_ROOT))
    parts = os.path.relpath(real, root).split(os.sep)
    names = [os.path.basename(real).split(".")[0]] + parts[1:-1]
    return {n for n in names if UUID_RE.match(n)}


def process_running(pid: int, started) -> bool:
    """Whether the registered process is still running. On Linux the start time
    must also match, so a reused pid is not mistaken for the session; without
    /proc only existence can be checked; on Windows nothing can, so assume yes."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[1].split()[19] == str(started)
    except (OSError, IndexError):
        pass
    if os.name == "posix" and not os.path.isdir("/proc"):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    return os.name != "posix"


def live_reasons(path: str) -> list[str]:
    """Why this transcript looks like it belongs to a running session."""
    real = os.path.realpath(path)
    root = os.path.realpath(os.path.expanduser(settings.PROJECTS_ROOT))
    if not real.startswith(root + os.sep):
        return []
    reasons = []
    ids = session_ids_for(real)
    for f in glob.glob(os.path.join(os.path.expanduser(settings.SESSIONS_DIR), "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        pid = d.get("pid")
        if (d.get("sessionId") in ids and isinstance(pid, int)
                and process_running(pid, d.get("procStart"))):
            reasons.append(f"registered to running pid {pid}")
    if settings.LIVE_IF_EDITED_WITHIN > 0:
        try:
            age = time.time() - os.path.getmtime(real)
        except OSError:
            age = None
        if age is not None and age < settings.LIVE_IF_EDITED_WITHIN:
            reasons.append(f"edited {max(age, 0):.0f}s ago "
                           f"(LIVE_IF_EDITED_WITHIN = {settings.LIVE_IF_EDITED_WITHIN}s)")
    return reasons


def check_live(path: str, force: bool = False) -> None:
    """Raise when the transcript looks live and `force` is not set."""
    reasons = live_reasons(path)
    if reasons and not force:
        raise PruneError("this session looks live: " + "; ".join(reasons) + ".\n"
                         "Exit it first, or pass --force to edit it anyway.")
