"""
cc-prune: surgical context recovery for Claude Code sessions.

Clears digested tool output from a session transcript without summarizing
anything, and cuts a stretch of whole turns out of the middle of one.

The engine lives in this package and returns plain data; `ccprune.cli` is one
front end over it.

    sessions     find_sessions
    clear / cut  plan_prune, then apply_plan   (cut, clear or both in one pass)
    snapshots    list_snapshots, restore_snapshot
    analysis     usage_rows, inspect_file, audit_lines, density
    export       read_transcript, render, write_export

Invariants held by every mutating operation except a cut:
  * no line is ever deleted           -> uuid / parentUuid chain intact
  * thinking blocks are never touched -> cryptographically signed
    (except the blocks a thinking flag is asked to delete)
  * tool_use ids and tool_result tool_use_ids are never touched -> pairing
  * every write is validated before replacing the original
  * every write takes a numbered snapshot first

A cut re-links the first kept turn onto the head and, when it purges, deletes
the cut records. It validates its own set: the resumed conversation is exactly
head + carried attachments + tail, every other parentUuid and every thinking
block is unchanged, and tool calls stay paired.

SCHEMA WARNING: the transcript format is undocumented and changes between
Claude Code versions. Everything here was reverse-engineered. Always keep
the snapshot until you have confirmed the session still resumes.
"""
from __future__ import annotations

__version__ = "0.5.0"

from .errors import PruneError
from .prune import Plan, apply_plan, plan_prune

__all__ = ["PruneError", "Plan", "apply_plan", "plan_prune", "__version__"]
