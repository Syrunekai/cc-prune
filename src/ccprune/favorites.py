"""Favorite sessions, kept by session id outside the repository."""
from __future__ import annotations

import json
import os

from . import settings


def _file() -> str:
    return os.path.expanduser(settings.FAVORITES_FILE)


def load_favorites() -> set[str]:
    try:
        with open(_file(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return set()
    ids = data.get("favorites") if isinstance(data, dict) else None
    return {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()


def save_favorites(ids: set[str]) -> None:
    path = _file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"favorites": sorted(ids)}, fh, indent=1)
    os.replace(tmp, path)


def set_favorite(session_id: str, on: bool) -> set[str]:
    """Mark or unmark a session; returns the full set afterwards."""
    ids = load_favorites()
    if on:
        ids.add(session_id)
    else:
        ids.discard(session_id)
    save_favorites(ids)
    return ids
