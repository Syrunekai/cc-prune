"""Settings: the values you are most likely to want to change."""
from __future__ import annotations

import os

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
PROJECTS_ROOT = os.path.join(CONFIG_DIR, "projects")
SESSIONS_DIR = os.path.join(CONFIG_DIR, "sessions")

# seconds: a transcript under PROJECTS_ROOT edited more recently than this is
# treated as belonging to a running session (0 turns the check off)
LIVE_IF_EDITED_WITHIN = 30
