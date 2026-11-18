"""Short text for numbers and times."""
from __future__ import annotations

from datetime import datetime


def tokens(n: int | float | None) -> str:
    if not n:
        return "?"
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1000:.0f}k"


def size(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit in ("B", "K") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


def when(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return "?"
    now = datetime.now().astimezone()
    if t.date() == now.date():
        return t.strftime("today %H:%M")
    if (now.date() - t.date()).days < 7:
        return t.strftime("%a %H:%M")
    return t.strftime("%Y-%m-%d")
