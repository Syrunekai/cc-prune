"""Local configuration from a .env file that is never committed.

The file is `.env` in the checkout (next to `cc-prune`), or, for an installed
package, `$XDG_CONFIG_HOME/ccprune/.env`. CC_PRUNE_ENV names another file.
A variable set in the environment wins over the file.

    CC_PRUNE_EXPORT_DIRS     a JSON array of {"name": ..., "path": ...}
    CC_PRUNE_EXPORT_DEFAULT  the name of the default folder; with a single
                             folder that folder is the default
    CC_PRUNE_USER_NAME       the speaker label of your messages in an export
    CC_PRUNE_ASSISTANT_NAME  the speaker label of Claude's messages
    CC_PRUNE_TIMEZONE        local, utc or an offset such as +2
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .errors import PruneError

DIRS = "CC_PRUNE_EXPORT_DIRS"
DEFAULT = "CC_PRUNE_EXPORT_DEFAULT"
USER_NAME = "CC_PRUNE_USER_NAME"
ASSISTANT_NAME = "CC_PRUNE_ASSISTANT_NAME"
TIMEZONE = "CC_PRUNE_TIMEZONE"

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def checkout_dir() -> str | None:
    """The checkout this package runs from, if it runs from one."""
    top = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return top if os.path.exists(os.path.join(top, "pyproject.toml")) else None


def env_path() -> str:
    if os.environ.get("CC_PRUNE_ENV"):
        return os.path.expanduser(os.environ["CC_PRUNE_ENV"])
    top = checkout_dir()
    if top:
        return os.path.join(top, ".env")
    home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(home, "ccprune", ".env")


# --------------------------------------------------------------------------
# the file
# --------------------------------------------------------------------------

@dataclass
class Entry:
    key: str
    value: str
    start: int      # first line of the entry
    end: int        # one past its last line


def _unquote(raw: str, lines: list[str], k: int) -> tuple[str, int]:
    """The value starting on line k (after the '='), and the line after it.
    Quoted values may span lines; double quotes take \\" \\\\ \\n escapes."""
    raw = raw.strip()
    if raw[:1] not in ("'", '"'):
        return re.sub(r"\s+#.*$", "", raw).strip(), k + 1
    quote, text, j = raw[0], raw[1:], k
    while True:
        pos = _closing(text, quote)
        if pos >= 0:
            body = text[:pos]
            break
        j += 1
        if j >= len(lines):
            raise PruneError(f"{env_path()}: line {k + 1}: the quote is never closed")
        text += "\n" + lines[j]
    if quote == '"':
        body = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)
    return body, j + 1


def _closing(text: str, quote: str) -> int:
    i = 0
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return -1


def parse(text: str) -> list[Entry]:
    lines = text.split("\n")
    out, k = [], 0
    while k < len(lines):
        m = _LINE.match(lines[k])
        if not m or lines[k].lstrip().startswith("#"):
            k += 1
            continue
        value, nxt = _unquote(m.group(2), lines, k)
        out.append(Entry(m.group(1), value, k, nxt))
        k = nxt
    return out


def read_env(path: str | None = None) -> dict[str, str]:
    """The file's values, with the environment's on top."""
    path = path or env_path()
    try:
        with open(path, encoding="utf-8") as fh:
            values = {e.key: e.value for e in parse(fh.read())}
    except FileNotFoundError:
        values = {}
    for key in (DIRS, DEFAULT, USER_NAME, ASSISTANT_NAME, TIMEZONE):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def _quote(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env(updates: dict[str, str | None], path: str | None = None) -> str:
    """Set (or, with None, remove) keys in the file, leaving every other line
    as it is. Returns the file's path."""
    path = path or env_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    lines = text.split("\n") if text else []
    entries = parse(text) if text else []
    for key, value in updates.items():
        new = [] if value is None else [f"{key}={_quote(value)}"]
        if new:
            new = new[0].split("\n")
        found = [e for e in entries if e.key == key]
        if found:
            e = found[-1]
            lines[e.start:e.end] = new
            entries = parse("\n".join(lines))
        elif new:
            related = [e for e in entries if e.key.startswith(key.rsplit("_", 1)[0])]
            if related:
                at = related[-1].end
            else:
                at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
            lines[at:at] = new
            entries = parse("\n".join(lines))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    out = "\n".join(lines)
    if out and not out.endswith("\n"):
        out += "\n"
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(out)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# export folders
# --------------------------------------------------------------------------

@dataclass
class Folder:
    name: str
    path: str

    @property
    def expanded(self) -> str:
        return os.path.expanduser(self.path)


@dataclass
class Folders:
    folders: list[Folder]
    default: str | None     # the name of the default folder, if there is one

    def get(self, name: str | None = None) -> Folder | None:
        """The folder of that name, or the default folder."""
        name = name or self.default
        return next((f for f in self.folders if f.name == name), None)


def parse_folders(raw: str | None) -> list[Folder]:
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PruneError(f"{DIRS} is not a JSON array: {exc}")
    if not isinstance(data, list):
        raise PruneError(f"{DIRS} must be a JSON array of {{\"name\": ..., \"path\": ...}}")
    out, names = [], set()
    for item in data:
        if not (isinstance(item, dict) and isinstance(item.get("name"), str)
                and isinstance(item.get("path"), str) and item["name"] and item["path"]):
            raise PruneError(f"{DIRS}: every entry needs a \"name\" and a \"path\": {item!r}")
        if item["name"] in names:
            raise PruneError(f"{DIRS}: the name {item['name']!r} is used twice")
        names.add(item["name"])
        out.append(Folder(item["name"], item["path"]))
    return out


def load_folders(values: dict[str, str] | None = None) -> Folders:
    values = read_env() if values is None else values
    folders = parse_folders(values.get(DIRS))
    default = (values.get(DEFAULT) or "").strip() or None
    if default and not any(f.name == default for f in folders):
        raise PruneError(f"{DEFAULT}={default!r} is not one of the folders in {DIRS}")
    if default is None and len(folders) == 1:
        default = folders[0].name
    return Folders(folders, default)


def save_folders(folders: Folders, path: str | None = None) -> str:
    items = [json.dumps({"name": f.name, "path": f.path}, ensure_ascii=False)
             for f in folders.folders]
    raw = "[\n  " + ",\n  ".join(items) + "\n]"
    return write_env({DIRS: raw if folders.folders else None,
                      DEFAULT: folders.default if folders.default else None}, path)
