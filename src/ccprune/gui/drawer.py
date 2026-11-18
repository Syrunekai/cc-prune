"""The bottom drawer: the by-turn timeline and the snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..snapshots import SnapshotInfo
from . import fmt, theme
from .widgets import RangeSlider, style_rows


@dataclass
class TurnInfo:
    n: int
    time: str
    chars: int
    clean: bool
    prompt: str


def item(text: str, color: str | None = None, right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if color:
        it.setForeground(QColor(color))
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class TimelineTab(QWidget):
    rangeChosen = Signal(int, int)      # the tips were let go on a range

    def __init__(self):
        super().__init__()
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.slider = RangeSlider()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Turn", "Time", "Chars", "Prompt"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        style_rows(self.table)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.hint)
        lay.addWidget(self.slider)
        lay.addWidget(self.table, 1)
        self.turns: list[TurnInfo] = []
        self.slider.rangeChanged.connect(lambda a, b: self._highlight(a, b))
        self.slider.rangeReleased.connect(self.rangeChosen)

    def set_turns(self, turns: list[TurnInfo], low: int, high: int) -> None:
        self.turns = turns
        n = len(turns)
        self.slider.setBounds(2, max(n - 1, 2))
        self.slider.setRange(low, high)
        self.table.setRowCount(n)
        for i, t in enumerate(turns):
            self.table.setItem(i, 0, item(str(t.n), right=True))
            self.table.setItem(i, 1, item(fmt.when(t.time)))
            self.table.setItem(i, 2, item(f"{t.chars:,}", right=True))
            self.table.setItem(i, 3, item(t.prompt if t.clean else "(a tool call is waiting here) " + t.prompt,
                                          None if t.clean else theme.WARN))
        self._highlight(low, high)

    def set_range(self, low: int, high: int) -> None:
        """Show a range chosen elsewhere (the fraction it resolves to)."""
        self.slider.setRange(low, high)
        self._highlight(low, high)

    def set_manual(self, manual: bool) -> None:
        self.slider.setEnabled(manual and len(self.turns) >= 3)
        self.hint.setText(
            "Drag the two tips to choose the turns to cut. Both start at the largest cut, turn 2 "
            "to the second to last. The cut moves to the nearest turns where it is possible."
            if manual else
            "The turn range method is off, so this only shows the range the drop fraction "
            "resolves to. Pick “By turn range” in the settings panel to set it by hand.")

    def _highlight(self, a: int, b: int) -> None:
        for i in range(self.table.rowCount()):
            cut = a <= i + 1 <= b
            for c in range(4):
                it = self.table.item(i, c)
                if it:
                    it.setBackground(QColor("#3a2d2d") if cut else QColor(0, 0, 0, 0))


class SnapshotsTab(QWidget):
    restoreRequested = Signal(int)
    deleteRequested = Signal(int)
    pinToggled = Signal(int, bool)

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Snapshot", "What it holds", "Size", "Taken", "Pinned"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        style_rows(self.table)
        self.total = QLabel()
        self.total.setObjectName("muted")
        self.restore = QPushButton("Restore")
        self.pin = QPushButton("Pin")
        self.delete = QPushButton("Delete")
        for b in (self.restore, self.pin, self.delete):
            b.setEnabled(False)
        bar = QHBoxLayout()
        bar.addWidget(self.total, 1)
        for b in (self.restore, self.pin, self.delete):
            bar.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)
        self.snaps: list[SnapshotInfo] = []
        self.table.itemSelectionChanged.connect(self._selected)
        self.restore.clicked.connect(lambda: self._emit(self.restoreRequested))
        self.delete.clicked.connect(lambda: self._emit(self.deleteRequested))
        self.pin.clicked.connect(self._toggle_pin)

    def set_snapshots(self, snaps: list[SnapshotInfo], current_size: int = 0) -> None:
        self.snaps = snaps
        self.table.setRowCount(len(snaps))
        for i, s in enumerate(snaps):
            taken = datetime.fromtimestamp(s.created).strftime("%Y-%m-%d %H:%M")
            self.table.setItem(i, 0, item(f"snap{s.n}"))
            self.table.setItem(i, 1, item(s.label or "", None if s.label else theme.MUTED))
            self.table.setItem(i, 2, item(fmt.size(s.size), right=True))
            self.table.setItem(i, 3, item(taken))
            self.table.setItem(i, 4, item("pinned" if s.pinned else "", theme.WARN))
        self.total.setText(f"{len(snaps)} snapshot(s), {fmt.size(sum(s.size for s in snaps))}"
                           if snaps else "No snapshots yet.")
        self._selected()

    def _current(self) -> SnapshotInfo | None:
        rows = self.table.selectionModel().selectedRows()
        return self.snaps[rows[0].row()] if rows else None

    def _selected(self) -> None:
        s = self._current()
        for b in (self.restore, self.pin, self.delete):
            b.setEnabled(s is not None)
        self.pin.setText("Unpin" if s and s.pinned else "Pin")

    def _emit(self, signal) -> None:
        if s := self._current():
            signal.emit(s.n)

    def _toggle_pin(self) -> None:
        if s := self._current():
            self.pinToggled.emit(s.n, not s.pinned)


class Drawer(QTabWidget):
    def __init__(self):
        super().__init__()
        self.timeline = TimelineTab()
        self.snapshots = SnapshotsTab()
        self.addTab(self.timeline, "Turn timeline")
        self.addTab(self.snapshots, "Snapshots")
