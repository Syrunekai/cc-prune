"""Settings: the values you are most likely to want to change."""
from __future__ import annotations

import os

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
PROJECTS_ROOT = os.path.join(CONFIG_DIR, "projects")
SESSIONS_DIR = os.path.join(CONFIG_DIR, "sessions")

# seconds: a transcript under PROJECTS_ROOT edited more recently than this is
# treated as belonging to a running session (0 turns the check off)
LIVE_IF_EDITED_WITHIN = 30

# snapshots kept per transcript: once an edit succeeds, the oldest unpinned
# snapshots beyond this many are deleted (0 keeps them all). Snapshot 0, the
# pristine file, counts as pinned unless it is unpinned by hand.
MAX_SNAPSHOTS = 0

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
DATA_HOME = os.environ.get("XDG_DATA_HOME") or "~/.local/share"

# favorite sessions, by session id
FAVORITES_FILE = os.path.join(CONFIG_HOME, "ccprune", "favorites.json")
# the window's own settings (the values of its settings panel)
GUI_FILE = os.path.join(CONFIG_HOME, "ccprune", "gui.json")
# one line per applied edit: the context it estimated, and later the real one
ESTIMATE_LOG = os.path.join(DATA_HOME, "ccprune", "estimates.jsonl")
