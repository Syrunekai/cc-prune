"""The values of the settings panel: saved between runs, turned into engine
options, and turned into the command line that does the same thing."""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict, dataclass, fields

from .. import settings
from ..clear import ClearOptions
from ..cut import CutOptions

METHODS = ("fraction", "turns")
THINKING = ("none", "failing", "all")
# the cut a turn range starts at: everything but the first and last turn
DEFAULT_FROM = 2


@dataclass
class PanelSettings:
    cut_on: bool = True
    method: str = "fraction"        # how the cut is chosen: "fraction" or "turns"
    drop: float = 0.5
    keep_head: int = 1
    from_turn: int = 0              # 0 until a range is chosen: then the whole middle
    to_turn: int = 0
    purge: bool = True
    note: bool = True
    keep_tail: float = 0.15
    results_on: bool = True
    results: int = 1
    inputs_on: bool = True
    inputs: int = 1
    commands_on: bool = False
    commands: int = 500
    attachments_on: bool = False
    attachments: int = 500
    images: bool = True
    transformations: bool = False
    thinking: str = "none"
    reset_usage: bool = True

    def normalized(self) -> "PanelSettings":
        out = PanelSettings(**{f.name: getattr(self, f.name) for f in fields(self)})
        if out.method not in METHODS:
            out.method = "fraction"
        if out.thinking not in THINKING:
            out.thinking = "none"
        out.drop = min(max(float(out.drop), 0.01), 0.99)
        out.keep_tail = min(max(float(out.keep_tail), 0.0), 1.0)
        out.keep_head = max(int(out.keep_head), 1)
        return out

    def turn_range(self, turns: int) -> tuple[int, int]:
        """The turns of a manual range, 1-based, both ends inclusive; the whole
        middle of a conversation of `turns` turns until one is chosen."""
        last = max(turns - 1, DEFAULT_FROM)
        a = self.from_turn or DEFAULT_FROM
        b = self.to_turn or last
        return min(a, last), min(max(b, a), last)

    def cut_options(self, turns: int = 0) -> CutOptions:
        if self.method == "turns":
            a, b = self.turn_range(turns)
            return CutOptions(from_turn=a, to_turn=b, purge=self.purge, note=self.note)
        return CutOptions(drop=self.drop, keep_head=self.keep_head, purge=self.purge,
                          note=self.note)

    def clear_options(self) -> ClearOptions | None:
        """What to clear, or None when nothing is selected."""
        opts = ClearOptions(
            results=self.results if self.results_on else 0,
            inputs=self.inputs if self.inputs_on else 0,
            commands=self.commands if self.commands_on else 0,
            attachments=self.attachments if self.attachments_on else 0,
            images=self.images, transformations=self.transformations,
            drop_failing_thinking=self.thinking == "failing",
            drop_all_thinking=self.thinking == "all", keep_tail=self.keep_tail)
        try:
            opts.check()
        except Exception:
            return None
        return opts

    def command(self, path: str, repo_dir: str, turns: int = 0, note: str = "") -> str:
        """The command line that makes this same edit, ready to paste."""
        clear = self.clear_options()
        cut = self.cut_on
        if not cut and clear is None:
            return ""
        verb = "clearcut" if cut and clear else "cut" if cut else "clear"
        args = [verb, '"$F"']
        if cut:
            if self.method == "turns":
                a, b = self.turn_range(turns)
                args += ["--keep-head", "1", "--from", str(a), "--to", str(b)]
            else:
                args += ["--keep-head", str(self.keep_head), "--drop", fmt(self.drop)]
            if verb == "cut" and self.purge:
                args.append("--purge")
            if verb == "clearcut" and not self.purge:
                args.append("--branch")
            if not self.note:
                args.append("--no-note")
        if clear:
            args += ["--keep-tail", fmt(self.keep_tail)]
            if verb == "clearcut":
                args += ["--results", str(clear.results), "--inputs", str(clear.inputs)]
                if not clear.images:
                    args.append("--no-images")
            else:
                if clear.results:
                    args += ["--results", str(clear.results)]
                if clear.inputs:
                    args += ["--inputs", str(clear.inputs)]
                if clear.images:
                    args.append("--images")
            if clear.commands:
                args += ["--commands", str(clear.commands)]
            if clear.attachments:
                args += ["--attachments", str(clear.attachments)]
            if clear.transformations:
                args.append("--transformations")
            if clear.drop_failing_thinking:
                args.append("--drop-failing-thinking")
            if clear.drop_all_thinking:
                args.append("--drop-all-thinking")
        if not self.reset_usage:
            args.append("--no-reset-usage")
        if cut or clear.drops_thinking:
            args.append("--yes")
        if repo_dir:
            line = (f"PRUNE_PATH={shlex.quote(repo_dir)} && F={shlex.quote(path)} && "
                    f'python3 "$PRUNE_PATH/cc_prune.py" ' + " ".join(args))
        else:
            line = f"F={shlex.quote(path)} && cc-prune " + " ".join(args)
        return f"{line}  # {note}" if note else line


def fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def _file() -> str:
    return os.path.expanduser(settings.GUI_FILE)


def load_settings() -> PanelSettings:
    try:
        with open(_file(), encoding="utf-8") as fh:
            raw = json.load(fh).get("panel", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        raw = {}
    known = {f.name for f in fields(PanelSettings)}
    try:
        return PanelSettings(**{k: v for k, v in raw.items() if k in known}).normalized()
    except TypeError:
        return PanelSettings()


def save_settings(panel: PanelSettings) -> None:
    path = _file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"panel": asdict(panel)}, fh, indent=1)
    os.replace(tmp, path)
