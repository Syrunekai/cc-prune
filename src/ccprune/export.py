"""Export a Claude Code session as a readable markdown transcript.

Only the conversation the session actually holds is exported: the parentUuid
walk from the newest main-thread record, continued across compactions through
logicalParentUuid. Records a branch-mode cut left unreachable, and sidechains,
are not part of it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo

from .errors import PruneError
from .sessions import session_title
from .transcript import load

DEFAULT_USER_NAME = "Operator"
DEFAULT_ASSISTANT_NAME = "Machine"
TOOL_SPEAKER = "Tool"
SUMMARY_SPEAKER = "Compaction summary"
# characters of a tool input, tool result or thinking block kept with --raw
RAW_LIMIT = 2000
THINKING_LIMIT = 1000


@dataclass
class ExportOptions:
    raw: bool = False               # include tool calls, tool results, thinking and images
    head: int = 0                   # keep only the first N messages (0 = all)
    tail: int = 0                   # keep only the last N messages (0 = all)
    before_compaction: bool = False  # True: the whole history, compactions marked;
                                     # False: the latest compaction's summary and what follows
    summary: bool | None = None     # include compaction summaries; None: only without the history

    @property
    def with_summary(self) -> bool:
        return (not self.before_compaction) if self.summary is None else self.summary
    timezone: str = "local"         # "local", "utc" or an offset such as "+2" or "-5.5"
    user_name: str = DEFAULT_USER_NAME
    assistant_name: str = DEFAULT_ASSISTANT_NAME
    title: str = ""                 # the heading; the session title when empty

    def check(self) -> None:
        if self.head and self.tail:
            raise PruneError("pick --head or --tail, not both")
        if self.head < 0 or self.tail < 0:
            raise PruneError("--head and --tail take a positive number")
        parse_timezone(self.timezone)


@dataclass
class Message:
    speaker: str
    time: datetime | None
    text: str
    compacted_before: bool = False  # the first message after a compaction


@dataclass
class Transcript:
    path: str
    session_id: str
    title: str | None               # the session's own title, if it has one
    messages: list[Message]         # every message of the conversation
    shown: list[Message]            # the messages after --head / --tail
    compactions: int                # compactions in the whole session
    history: bool                   # whether the history before them is included
    summary: bool                   # whether compaction summaries are in it
    tz: tzinfo

    @property
    def trim_note(self) -> str:
        if len(self.shown) == len(self.messages):
            return ""
        first = self.shown and self.messages and self.shown[0] is self.messages[0]
        return f"{'first' if first else 'last'} {len(self.shown)} of {len(self.messages)} messages"


@dataclass
class Exported:
    path: str
    messages: int
    total: int
    size: int


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------

def parse_timezone(text: str | None) -> tzinfo:
    s = (text or "local").strip().lower()
    if s == "local":
        return datetime.now().astimezone().tzinfo
    if s in ("utc", "0", "z"):
        return timezone.utc
    try:
        return timezone(timedelta(hours=float(s)))
    except (ValueError, OverflowError):
        raise PruneError(f"unknown timezone {text!r}: use local, utc or an offset such as +2 or -5.5")


def tz_label(dt: datetime) -> str:
    offset = dt.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    h, m = divmod(abs(minutes), 60)
    sign = "+" if minutes > 0 else "-"
    return f"UTC{sign}{h}:{m:02d}" if m else f"UTC{sign}{h}"


def parse_time(stamp, tz: tzinfo) -> datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# the conversation
# --------------------------------------------------------------------------

def conversation(recs: list[dict], before_compaction: bool = True) -> tuple[list[int], set[int]]:
    """Record indexes of the conversation, oldest first, and the indexes of
    the compact boundaries it crosses."""
    by = {r["uuid"]: i for i, r in enumerate(recs) if r.get("uuid")}
    leaf = next((i for i in range(len(recs) - 1, -1, -1)
                 if recs[i].get("uuid") and not recs[i].get("isSidechain")), None)
    if leaf is None:
        return [], set()
    chain, seen, boundaries, i = [], set(), set(), leaf
    while i is not None and i not in seen:
        seen.add(i)
        chain.append(i)
        rec = recs[i]
        parent = rec.get("parentUuid")
        if not parent and rec.get("logicalParentUuid"):
            boundaries.add(i)
            if not before_compaction:
                break
            parent = rec.get("logicalParentUuid")
        i = by.get(parent) if parent else None
    return chain[::-1], boundaries


def block_text(block: dict, raw: bool) -> tuple[str, bool]:
    """A content block as text, and whether it is a tool block."""
    kind = block.get("type")
    if kind == "text":
        return block.get("text", ""), False
    if not raw:
        return "", kind in ("tool_use", "tool_result")
    if kind == "tool_use":
        body = json.dumps(block.get("input", {}), indent=2, ensure_ascii=False)[:RAW_LIMIT]
        return f"[Tool: {block.get('name', '?')}]\n```json\n{body}\n```", True
    if kind == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            parts = []
            for sub in content:
                if isinstance(sub, dict) and sub.get("type") == "text":
                    parts.append(sub.get("text", ""))
                elif isinstance(sub, dict) and sub.get("type") == "image":
                    parts.append("[Image]")
            content = "\n".join(parts)
        return f"[Tool Result]\n{str(content)[:RAW_LIMIT]}", True
    if kind == "thinking":
        text = block.get("thinking", "")
        return (f"[Thinking]\n{text[:THINKING_LIMIT]}" if text else ""), False
    if kind == "image":
        return "[Image]", False
    return f"[{kind}]", False


def record_text(rec: dict, raw: bool) -> tuple[str, bool]:
    """The readable text of a record, and whether it holds only tool blocks."""
    content = (rec.get("message") or {}).get("content", rec.get("content", ""))
    if isinstance(content, str):
        return content.strip(), False
    if not isinstance(content, list):
        return "", False
    parts, tools, other = [], 0, 0
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            other += 1
        elif isinstance(block, dict):
            text, is_tool = block_text(block, raw)
            tools += is_tool
            other += not is_tool and bool(text)
            if text:
                parts.append(text)
    return "\n".join(parts).strip(), bool(tools) and not other


def read_transcript(path: str, opts: ExportOptions | None = None,
                    records: list[dict] | None = None) -> Transcript:
    """Read a transcript into messages. With `records` the file is not read again."""
    opts = opts or ExportOptions()
    opts.check()
    tz = parse_timezone(opts.timezone)
    recs = load(path) if records is None else records
    chain, boundaries = conversation(recs, opts.before_compaction)
    compactions = len(boundaries) if opts.before_compaction else len(conversation(recs, True)[1])
    messages: list[Message] = []
    pending_compaction = False
    for i in chain:
        rec = recs[i]
        if i in boundaries:
            pending_compaction = True
            continue
        kind = rec.get("type")
        if rec.get("isCompactSummary"):
            if opts.with_summary:
                text, _ = record_text(rec, False)
                if text:
                    messages.append(Message(SUMMARY_SPEAKER, parse_time(rec.get("timestamp"), tz),
                                            text, compacted_before=pending_compaction and bool(messages)))
                    pending_compaction = False
            continue
        if kind not in ("user", "assistant") or rec.get("isMeta"):
            continue
        text, only_tools = record_text(rec, opts.raw)
        if not text:
            continue
        if kind == "assistant":
            speaker = opts.assistant_name
        else:
            speaker = TOOL_SPEAKER if only_tools else opts.user_name
        messages.append(Message(speaker, parse_time(rec.get("timestamp"), tz), text,
                                compacted_before=pending_compaction and bool(messages)))
        pending_compaction = False

    shown = messages
    if opts.head:
        shown = messages[:opts.head]
    elif opts.tail:
        shown = messages[-opts.tail:]
    session_id = next((r.get("sessionId") for r in recs if r.get("sessionId")), None) \
        or os.path.basename(path).split(".")[0]
    return Transcript(path=path, session_id=session_id, title=session_title(path, recs),
                      messages=messages, shown=shown, compactions=compactions,
                      history=opts.before_compaction,
                      summary=any(m.speaker == SUMMARY_SPEAKER for m in messages), tz=tz)


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------

def slugify(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:maxlen].rstrip("-")


def default_filename(t: Transcript) -> str:
    """<yymmdd-hhmm of the first shown message>-<title or session id>.md"""
    first = next((m.time for m in t.shown if m.time), None)
    stamp = first.strftime("%y%m%d-%H%M") if first else "unknown"
    return f"{stamp}-{slugify(t.title or '') or t.session_id[:8]}.md"


def date_range(t: Transcript) -> str:
    days = [m.time.strftime("%Y-%m-%d") for m in t.shown if m.time]
    if not days:
        return "unknown"
    return days[0] if days[0] == days[-1] else f"{days[0]} to {days[-1]}"


def render(t: Transcript, opts: ExportOptions | None = None, limit: int = 0) -> str:
    """The transcript as markdown. With `limit`, stop after about that many
    characters (for a preview)."""
    opts = opts or ExportOptions()
    out = [f"# {opts.title or t.title or 'Session Transcript'}\n\n"]
    if t.title and opts.title and opts.title != t.title:
        out.append(f"**Session title:** {t.title}\n\n")
    out.append(f"**Session:** {date_range(t)}\n\n")
    out.append(f"**Extracted:** {datetime.now(t.tz).strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    out.append(f"**Total messages:** {len(t.messages)}\n\n")
    if t.compactions:
        if t.history:
            note = "marked below" + (", each with its summary" if t.summary else "")
        else:
            note = "this transcript starts at the latest one" + (", with its summary" if t.summary else "")
        out.append(f"**Compactions:** {t.compactions}, {note}\n\n")
    if t.trim_note:
        out.append(f"**Showing:** {t.trim_note}\n\n")
    out.append("---\n\n")
    size = sum(len(s) for s in out)
    day = None
    for m in t.shown:
        if limit and size > limit:
            out.append(f"\n… {len(t.shown) - t.shown.index(m)} more messages\n")
            break
        part = []
        if m.compacted_before:
            part.append("\n*— the conversation was compacted here —*\n\n---\n\n")
        if m.time and m.time.strftime("%Y-%m-%d") != day:
            day = m.time.strftime("%Y-%m-%d")
            part.append(f"\n## {day}\n\n")
        stamp = f" — {m.time.strftime('%Y-%m-%d %H:%M:%S')} {tz_label(m.time)}" if m.time else ""
        part.append(f"### {m.speaker}{stamp}\n\n{m.text}\n\n---\n\n")
        text = "".join(part)
        size += len(text)
        out.append(text)
    return "".join(out)


def resolve_destination(t: Transcript, dest: str) -> str:
    """A directory gets the default file name; anything else is the file."""
    dest = os.path.expanduser(dest)
    if os.path.isdir(dest) or dest.endswith(os.sep):
        return os.path.join(dest, default_filename(t))
    return dest if dest.endswith(".md") else dest + ".md"


def write_export(t: Transcript, path: str, opts: ExportOptions | None = None,
                 overwrite: bool = False) -> Exported:
    if not t.shown:
        raise PruneError("this session has no messages to export")
    if os.path.exists(path) and not overwrite:
        raise PruneError(f"{path} already exists; pass --force to overwrite it")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = render(t, opts)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return Exported(path=path, messages=len(t.shown), total=len(t.messages),
                    size=os.path.getsize(path))


def export_session(path: str, dest: str, opts: ExportOptions | None = None,
                   overwrite: bool = False) -> Exported:
    opts = opts or ExportOptions()
    t = read_transcript(path, opts)
    return write_export(t, resolve_destination(t, dest), opts, overwrite)
