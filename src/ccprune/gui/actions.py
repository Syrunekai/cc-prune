"""Session and snapshot actions: rename, delete, and the backups manager."""
from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QVBoxLayout, QWidget)

from ..manage import SessionFiles, custom_title
from ..snapshots import (delete_all_snapshots, delete_snapshots, list_snapshots, restore_snapshot,
                         set_pinned)
from . import fmt
from .drawer import SnapshotsTab
from .sessions import SessionRow
from .workers import run_async


def _ask(parent: QWidget, title: str, text: str, informative: str = "", detail: str = "",
         action: str = "Delete") -> bool:
    """A warning with a destructive button and Cancel as the default."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    box.setTextFormat(Qt.TextFormat.RichText)
    if informative:
        box.setInformativeText(informative)
    if detail:
        box.setDetailedText(detail)
    go = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is go


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------

class SnapshotController(QObject):
    """Wires a SnapshotsTab to one transcript's snapshots."""

    changed = Signal()              # the snapshots, or the transcript, changed

    def __init__(self, tab: SnapshotsTab, parent: QWidget, path: Callable[[], str | None],
                 report: Callable[[str], None]):
        super().__init__(parent)
        self.tab, self.parent, self.path, self.report = tab, parent, path, report
        tab.deleteRequested.connect(self.delete)
        tab.deleteAllRequested.connect(self.delete_all)
        tab.pinToggled.connect(self.pin)
        tab.restoreRequested.connect(self.restore)

    def refresh(self, keep_selection: bool = False) -> None:
        p = self.path()
        self.tab.set_snapshots(list_snapshots(p) if p and os.path.exists(p) else [],
                               keep_selection)

    def delete(self, numbers: list[int]) -> None:
        p = self.path()
        if not p or not numbers:
            return
        names = ", ".join(f"snap{n}" for n in numbers)
        note = ("<br><br>snap0 is the transcript as it was before cc-prune first changed it."
                if 0 in numbers else "")
        if not _ask(self.parent, "Delete snapshots?",
                    f"Delete {esc(names)}? {'It' if len(numbers) == 1 else 'They'} cannot be "
                    f"recovered.{note}",
                    "The snapshots that remain are renumbered from snap0, oldest first."):
            return
        freed = delete_snapshots(p, numbers)
        self.report(f"deleted {names} ({fmt.size(freed)}); the rest are renumbered from snap0")
        self.refresh()
        self.changed.emit()

    def delete_all(self) -> None:
        p = self.path()
        snaps = list_snapshots(p) if p else []
        if not snaps:
            return
        pinned = sum(s.pinned for s in snaps)
        if not _ask(self.parent, "Delete all snapshots?",
                    f"Delete all {len(snaps)} snapshot(s), {fmt.size(sum(s.size for s in snaps))}?",
                    f"This includes {pinned} pinned snapshot(s) and snap0, the transcript as it was "
                    f"before cc-prune first changed it. They cannot be recovered; the transcript "
                    f"itself is not touched." if pinned else
                    "They cannot be recovered; the transcript itself is not touched.",
                    action="Delete all"):
            return
        freed = delete_all_snapshots(p)
        self.report(f"deleted all {len(snaps)} snapshot(s) ({fmt.size(freed)})")
        self.refresh()
        self.changed.emit()

    def pin(self, n: int, pinned: bool) -> None:
        p = self.path()
        if p:
            set_pinned(p, n, pinned)
            self.refresh(keep_selection=True)

    def restore(self, n: int) -> None:
        p = self.path()
        if not p:
            return
        ask = QMessageBox.question(
            self.parent, "Restore snapshot?",
            f"Restore snap{n} over the current transcript?\nThe current file is saved as a new "
            f"snapshot first, so nothing is lost.")
        if ask != QMessageBox.StandardButton.Yes:
            return

        def done(r):
            self.report(f"restored snap{n}; the file as it was before is saved as "
                        f"{os.path.basename(r.saved) if r.saved else 'nothing'}")
            self.refresh()
            self.changed.emit()

        run_async(lambda: restore_snapshot(p, n), done,
                  lambda m: self.report("NOT RESTORED: " + m.strip()))


class BackupsDialog(QDialog):
    """Every snapshot of one session, to delete, pin or restore."""

    def __init__(self, parent: QWidget, row: SessionRow, report: Callable[[str], None]):
        super().__init__(parent)
        self.setWindowTitle(f"Backups: {row.title}")
        self.resize(760, 420)
        self.tab = SnapshotsTab()
        self.controller = SnapshotController(self.tab, self, lambda: row.path, report)
        self.changed = False
        self.controller.changed.connect(lambda: setattr(self, "changed", True))
        head = QLabel(f"<b>{esc(row.title)}</b><br><span style='color:#8b919b'>session "
                      f"{row.session_id}</span>")
        hint = QLabel("Select several with Ctrl or Shift. Deleting renumbers the rest from snap0.")
        hint.setObjectName("muted")
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(self.tab, 1)
        lay.addWidget(hint)
        lay.addWidget(close)
        self.controller.refresh()


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def ask_title(parent: QWidget, row: SessionRow) -> str | None:
    """The new title, or None when cancelled."""
    text, ok = QInputDialog.getText(
        parent, "Rename session", f"Session {row.session_id}\n\nNew title:",
        QLineEdit.EchoMode.Normal, custom_title(row.path) or row.title)
    return text if ok and text.strip() else None


def confirm_delete(parent: QWidget, row: SessionRow, files: SessionFiles) -> bool:
    snaps = sum(a.kind == "snapshot" for a in files.artifacts)
    parts = [f"{len(files.artifacts)} item(s), {fmt.size(files.size)}"]
    if snaps:
        parts.append(f"{snaps} snapshot(s)")
    if any(a.kind == "session folder" for a in files.artifacts):
        parts.append("its folder (subagent transcripts, tool results, title)")
    home = os.path.expanduser("~")
    detail = "\n".join(f"{a.kind:<15} {fmt.size(a.size):>8}  {a.path.replace(home, '~', 1)}"
                       for a in files.artifacts)
    if files.kept:
        detail += "\n\nKept (they record another session):\n" + "\n".join(
            k.replace(home, "~", 1) for k in files.kept)
    return _ask(parent, "Delete session?",
                f"<b>{esc(row.title)}</b><br>Session {row.session_id}",
                "Deletes the transcript and everything kept for it: " + ", ".join(parts)
                + ". This cannot be undone.", detail, action="Delete session")
