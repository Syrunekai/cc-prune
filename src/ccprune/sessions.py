"""Listing the session transcripts on disk without knowing their ids."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .errors import PruneError
from .usage import context_tokens

HEAD_RECORDS = 300
TAIL_BYTES = 262144


def read_head(path: str, limit: int = HEAD_RECORDS) -> list[dict]:
    objs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            if n >= limit:
                break
            try:
                objs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [o for o in objs if isinstance(o, dict)]


def read_tail(path: str, size: int = TAIL_BYTES) -> list[dict]:
    total = os.path.getsize(path)
    with open(path, "rb") as fh:
        if total > size:
            fh.seek(total - size)
            fh.readline()
        data = fh.read()
    objs = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            objs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [o for o in objs if isinstance(o, dict)]


def user_text(rec: dict) -> str:
    if rec.get("type") != "user" or rec.get("isMeta"):
        return ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return ""
    content = content.strip()
    return "" if content.startswith("<local-command-") else content


def session_title(path: str, objs: list[dict]) -> str | None:
    best = None
    for o in objs:
        t = o.get("type")
        if t == "custom-title":
            cand = (3, (o.get("customTitle") or "").strip())
        elif t == "ai-title":
            cand = (2, (o.get("aiTitle") or "").strip())
        elif t == "summary":
            cand = (1, (o.get("summary") or "").strip())
        else:
            continue
        if cand[1] and (best is None or cand[0] >= best[0]):
            best = cand
    sidecar = os.path.join(os.path.splitext(path)[0], "custom-title.json")
    try:
        with open(sidecar, encoding="utf-8") as fh:
            custom = (json.load(fh).get("customTitle") or "").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        custom = ""
    return custom or (best[1] if best else None)


def scan_session(path: str, want_last: bool, want_count: bool) -> dict | None:
    try:
        head = read_head(path)
    except OSError:
        return None
    if not any("sessionId" in o or o.get("type") in ("user", "assistant")
               for o in head):
        return None
    try:
        tail = read_tail(path)
    except OSError:
        tail = []

    cwd = next((o["cwd"] for o in head
                if isinstance(o.get("cwd"), str) and o["cwd"]), None)
    if cwd is None:
        proj = os.path.basename(os.path.dirname(path))
        cwd = proj.replace("-", "/") if proj.startswith("-") else proj

    title = session_title(path, head + tail)
    first_user = next((t for t in map(user_text, head) if t), "")
    last_user = next((t for t in map(user_text, reversed(tail)) if t), "")
    about = title or (last_user if want_last else first_user) or first_user

    stamps = [o["timestamp"] for o in head + tail
              if isinstance(o.get("timestamp"), str)]
    ctx = next((c for c in map(context_tokens, reversed(tail)) if c), None)

    count = None
    if want_count:
        count = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (isinstance(o, dict) and o.get("type") in ("user", "assistant")
                        and not o.get("isMeta")):
                    count += 1

    return {
        "path": path,
        "session_id": os.path.basename(path).split(".")[0],
        "cwd": cwd,
        "title": title,
        "about": about,
        "start": next((o["timestamp"] for o in head
                       if isinstance(o.get("timestamp"), str)), None),
        "end": stamps[-1] if stamps else None,
        "size": os.path.getsize(path),
        "context_tokens": ctx,
        "messages": count,
    }


def gather_sessions(root: str, deep: bool) -> list[str]:
    root = os.path.expanduser(root)
    if os.path.isfile(root):
        return [root]
    found = []
    for dirpath, _dirs, names in os.walk(root):
        depth = len(os.path.relpath(dirpath, root).split(os.sep)) \
            if dirpath != root else 0
        if not deep and depth > 1:
            continue
        found.extend(os.path.join(dirpath, n) for n in names
                     if n.endswith(".jsonl"))
    return found


def to_local(iso: str | None, tz: timezone) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
    except (ValueError, AttributeError):
        return None


@dataclass
class SessionList:
    rows: list[dict]    # the sessions to show, after the limit
    total: int          # sessions that matched, before the limit
    scanned: int        # transcript files looked at


def find_sessions(root: str, *, deep: bool = False, last: bool = False,
                  count: bool = False, project: str | None = None,
                  grep: str | None = None, since: str | None = None,
                  until: str | None = None, tz: timezone = timezone.utc,
                  sort: str = "date", reverse: bool = False, limit: int = 50,
                  show_all: bool = False,
                  on_progress: Callable[[int, int], None] | None = None) -> SessionList:
    """Scan the transcripts under `root`, keep the ones that match and sort them.
    `on_progress(done, total)` is called every 100 files."""
    files = gather_sessions(root, deep)
    if not files:
        raise PruneError(f"no .jsonl session files found under: {root}")

    rows = []
    for n, path in enumerate(files, 1):
        if on_progress and n % 100 == 0:
            on_progress(n, len(files))
        rec = scan_session(path, last, count)
        if rec:
            rows.append(rec)

    def keep(r: dict) -> bool:
        if project and project.lower() not in (r["cwd"] or "").lower():
            return False
        if grep:
            hay = " ".join([r["about"] or "", r["title"] or "",
                            r["cwd"] or "", r["session_id"]]).lower()
            if grep.lower() not in hay:
                return False
        start = to_local(r["start"], tz)
        day = start.strftime("%Y-%m-%d") if start else None
        if since and (day is None or day < since):
            return False
        if until and (day is None or day > until):
            return False
        return True

    rows = [r for r in rows if keep(r)]

    if sort == "size":
        rows.sort(key=lambda r: r["size"], reverse=not reverse)
    elif sort == "ctx":
        rows.sort(key=lambda r: r["context_tokens"] or 0, reverse=not reverse)
    elif sort == "project":
        rows.sort(key=lambda r: ((r["cwd"] or "").lower(), r["start"] or ""),
                  reverse=reverse)
    else:
        rows.sort(key=lambda r: r["start"] or "", reverse=not reverse)

    total = len(rows)
    if not show_all and limit and total > limit:
        rows = rows[:limit]
    return SessionList(rows=rows, total=total, scanned=len(files))
