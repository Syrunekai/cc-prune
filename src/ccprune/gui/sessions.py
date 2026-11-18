"""The sessions list: favorites first, then the most recently used."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLineEdit,
                               QPushButton, QTableView, QVBoxLayout, QWidget)

from ..guard import live_reasons
from ..snapshots import snapshot_paths
from . import fmt, theme
from .widgets import style_rows

COLUMNS = ["", "Session", "Live", "Project", "Last used", "Context", "Size", "Snaps"]
LIVE_COL = 2


@dataclass
class SessionRow:
    path: str
    session_id: str
    title: str
    project: str
    last: str | None
    context: int | None
    size: int
    snaps: int = 0
    snap_bytes: int = 0
    live: list[str] = field(default_factory=list)
    favorite: bool = False


def to_rows(found: list[dict], favorites: set[str]) -> list[SessionRow]:
    home = os.path.expanduser("~")
    rows = []
    for r in found:
        snaps = snapshot_paths(r["path"])
        project = r["cwd"] or ""
        if project.startswith(home):
            project = "~" + project[len(home):]
        rows.append(SessionRow(
            path=r["path"], session_id=r["session_id"],
            title=" ".join((r["title"] or r["about"] or r["session_id"]).split())[:120],
            project=project, last=r["end"], context=r["context_tokens"], size=r["size"],
            snaps=len(snaps), snap_bytes=sum(os.path.getsize(s) for s in snaps),
            live=live_reasons(r["path"]), favorite=r["session_id"] in favorites))
    return rows


class SessionsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[SessionRow] = []

    def set_rows(self, rows: list[SessionRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, col = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return [("★" if r.favorite else "☆"), r.title, "● live" if r.live else "",
                    r.project, fmt.when(r.last),
                    fmt.tokens(r.context), fmt.size(r.size),
                    str(r.snaps) if r.snaps else ""][col]
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 0:
                return QColor(theme.WARN if r.favorite else theme.MUTED)
            if r.live:
                return QColor(theme.BAD)
            if col in (3, 4, 6, 7):
                return QColor(theme.MUTED)
            return QColor(theme.TEXT)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (0, 5, 6, 7):
            return int(Qt.AlignmentFlag.AlignCenter if col in (0, 7)
                       else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = [r.title, f"{r.path}\nsession {r.session_id}"]
            if r.live:
                tip.append("LIVE, can't be edited now: " + "; ".join(r.live))
            if r.snaps:
                tip.append(f"{r.snaps} snapshot(s), {fmt.size(r.snap_bytes)}")
            if col == 0:
                return "Click to " + ("remove from" if r.favorite else "add to") + " favorites"
            return "\n".join(tip)
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None


class _Proxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.only_favorites = False

    def set_filter(self, text: str, only_favorites: bool) -> None:
        self.text, self.only_favorites = text.lower(), only_favorites
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            self.endFilterChange()
        else:
            self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent) -> bool:
        r: SessionRow = self.sourceModel().rows[row]
        if self.only_favorites and not r.favorite:
            return False
        hay = f"{r.title} {r.project} {r.session_id}".lower()
        return all(word in hay for word in self.text.split())

    def lessThan(self, a, b) -> bool:
        ra, rb = self.sourceModel().rows[a.row()], self.sourceModel().rows[b.row()]
        return (not ra.favorite, "" if ra.last is None else _neg(ra.last)) < \
               (not rb.favorite, "" if rb.last is None else _neg(rb.last))


def _neg(iso: str) -> tuple:
    return tuple(-ord(c) for c in iso)


class SessionsPanel(QWidget):
    selected = Signal(object)           # a SessionRow, or None
    favoriteToggled = Signal(str, bool)
    refreshRequested = Signal()

    def __init__(self):
        super().__init__()
        self.model = SessionsModel()
        self.proxy = _Proxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.setDynamicSortFilter(True)
        self.proxy.sort(0)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by title, project or id")
        self.search.setClearButtonEnabled(True)
        self.only_fav = QCheckBox("★ only")
        self.refresh = QPushButton("↻")
        self.refresh.setToolTip("Rescan the transcripts")
        self.refresh.setFixedWidth(34)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setShowGrid(False)
        self.view.verticalHeader().hide()
        self.view.setWordWrap(False)
        self.view.setAlternatingRowColors(True)
        head = self.view.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.view.setColumnWidth(0, 30)
        self.view.setColumnHidden(3, True)
        style_rows(self.view)

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.only_fav)
        top.addWidget(self.refresh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)

        self.search.textChanged.connect(self._filter)
        self.only_fav.toggled.connect(self._filter)
        self.refresh.clicked.connect(self.refreshRequested)
        self.view.clicked.connect(self._clicked)
        self.view.selectionModel().currentRowChanged.connect(self._current)
        self._path: str | None = None

    def _filter(self, *_):
        self.proxy.set_filter(self.search.text(), self.only_fav.isChecked())

    def _row_at(self, proxy_index) -> SessionRow | None:
        return self.proxy.data(proxy_index, Qt.ItemDataRole.UserRole) if proxy_index.isValid() else None

    def _clicked(self, index) -> None:
        if index.column() == 0 and (r := self._row_at(index)):
            self.favoriteToggled.emit(r.session_id, not r.favorite)

    def _current(self, current, _previous) -> None:
        r = self._row_at(current)
        self._path = r.path if r else None
        self.selected.emit(r)

    def set_rows(self, rows: list[SessionRow]) -> None:
        keep = self._path
        self.view.selectionModel().blockSignals(True)
        self.model.set_rows(rows)
        self.view.selectionModel().blockSignals(False)
        if keep:
            self.select_path(keep, emit=False)

    def select_path(self, path: str, emit: bool = True) -> None:
        for i, r in enumerate(self.model.rows):
            if r.path == path:
                idx = self.proxy.mapFromSource(self.model.index(i, 1))
                if idx.isValid():
                    sm = self.view.selectionModel()
                    sm.blockSignals(not emit)
                    self.view.setCurrentIndex(idx)
                    sm.blockSignals(False)
                return

    def current_row(self) -> SessionRow | None:
        for r in self.model.rows:
            if r.path == self._path:
                return r
        return None

    def update_row(self, row: SessionRow) -> None:
        for i, r in enumerate(self.model.rows):
            if r.path == row.path:
                self.model.rows[i] = row
                self.model.dataChanged.emit(self.model.index(i, 0), self.model.index(i, len(COLUMNS) - 1))
                self.proxy.invalidate()
                return
