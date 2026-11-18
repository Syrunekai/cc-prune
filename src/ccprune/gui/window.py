"""The main window: sessions on the left, the preview in the middle, the
settings panel on the right, and a drawer at the bottom that starts hidden."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import settings as engine_settings
from ..calibration import reconcile
from ..export import default_filename, read_transcript, render, write_export
from ..favorites import load_favorites, set_favorite
from ..guard import live_reasons
from ..prune import apply_plan, load_transcript, plan_prune
from ..sessions import find_sessions, scan_session
from ..snapshots import delete_snapshot, list_snapshots, restore_snapshot, set_pinned
from ..transcript import first_text
from ..turns import Turns
from . import fmt
from .drawer import Drawer, TurnInfo
from .export_panel import ExportPanel, ExportSettings
from .preview import PreviewPanel
from .sessions import SessionRow, SessionsPanel, to_rows
from .settings_panel import SettingsPanel
from .state import load_section, load_settings, save_section, save_settings
from .workers import run_async

PREVIEW_DELAY_MS = 250
# characters of the transcript shown in the export preview
EXPORT_PREVIEW_CHARS = 80_000


def repo_dir() -> str:
    """The checkout that holds cc-prune, or '' when installed as a package."""
    here = os.path.dirname(os.path.abspath(__file__))
    top = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return top if os.path.exists(os.path.join(top, "cc-prune")) else ""


class MainWindow(QMainWindow):
    def __init__(self, root: str | None = None):
        super().__init__()
        self.setWindowTitle("cc-prune")
        self.resize(1480, 860)
        self.root = root or engine_settings.PROJECTS_ROOT
        self.favorites = load_favorites()
        self.row: SessionRow | None = None
        self.loaded = None
        self.turns: list[TurnInfo] = []
        self.history = []
        self.plan = None
        self.busy = False
        self.armed = False
        self._load_gen = self._plan_gen = 0
        self._fresh_selection = False

        self.sessions = SessionsPanel()
        self.preview = PreviewPanel()
        self.panel = SettingsPanel(load_settings())
        self.export_panel = ExportPanel(ExportSettings.from_dict(load_section("export")))
        self.tabs = QTabWidget()
        self.tabs.addTab(self.panel, "Prune")
        self.tabs.addTab(self.export_panel, "Export")
        self.tabs.setFixedWidth(384)
        self.drawer = Drawer()
        self.exporting = False
        self.last_export_dir = ""
        self._export_gen = 0

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.sessions)
        top.addWidget(self.preview)
        top.addWidget(self.tabs)
        top.setStretchFactor(0, 2)
        top.setStretchFactor(1, 3)
        top.setStretchFactor(2, 0)
        top.setCollapsible(2, False)
        top.setSizes([470, 600, 360])
        self.outer = QSplitter(Qt.Orientation.Vertical)
        self.outer.addWidget(top)
        self.outer.addWidget(self.drawer)
        self.outer.setStretchFactor(0, 1)
        self.outer.setCollapsible(0, False)
        self.drawer.hide()
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(8, 8, 8, 0)
        lay.addWidget(self.outer, 1)
        self.setCentralWidget(holder)

        self.drawer_button = QPushButton("Drawer  ▲")
        self.drawer_button.setCheckable(True)
        self.drawer_button.setToolTip("Show the by-turn timeline and the snapshots (Ctrl+D)")
        self.drawer_button.setShortcut("Ctrl+D")
        self.message = QLabel()
        self.message.setObjectName("muted")
        self.statusBar().addWidget(self.drawer_button)
        self.statusBar().addWidget(self.message, 1)

        self.debounce = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS)
        self.debounce.timeout.connect(self.run_preview)
        self.export_debounce = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS)
        self.export_debounce.timeout.connect(self.run_export_preview)
        self.tick = QTimer(self, interval=1000)
        self.tick.timeout.connect(self.on_tick)
        self.tick.start()

        self.sessions.selected.connect(self.on_selected)
        self.sessions.favoriteToggled.connect(self.on_favorite)
        self.sessions.refreshRequested.connect(self.refresh_sessions)
        self.panel.changed.connect(self.on_panel_changed)
        self.panel.applyClicked.connect(self.on_apply)
        self.panel.cancelClicked.connect(self.disarm)
        self.tabs.currentChanged.connect(self.on_mode)
        self.export_panel.changed.connect(self.on_export_changed)
        self.export_panel.exportClicked.connect(self.on_export)
        self.export_panel.open_folder.clicked.connect(self.open_export_folder)
        self.drawer_button.toggled.connect(self.toggle_drawer)
        self.drawer.timeline.rangeChosen.connect(self.panel.set_turn_range)
        self.drawer.snapshots.restoreRequested.connect(self.on_restore)
        self.drawer.snapshots.deleteRequested.connect(self.on_delete)
        self.drawer.snapshots.pinToggled.connect(self.on_pin)

        self.drawer.timeline.set_manual(self.panel.settings().method == "turns")
        self.refresh_sessions()

    # ---- helpers

    def say(self, text: str) -> None:
        self.message.setText(text)

    def toggle_drawer(self, on: bool) -> None:
        self.drawer.setVisible(on)
        self.drawer_button.setText("Drawer  ▼" if on else "Drawer  ▲")
        if on:
            total = max(self.outer.height(), 400)
            self.outer.setSizes([total - 300, 300])

    # ---- sessions

    def refresh_sessions(self) -> None:
        self.say("Scanning transcripts...")
        favorites = self.favorites

        def work():
            found = find_sessions(self.root, show_all=True, limit=0, sort="date")
            return to_rows(found.rows, favorites)

        def done(rows):
            self.sessions.set_rows(rows)
            self.say(f"{len(rows)} sessions")
            if self.row is None and not self.sessions.current_row():
                self.preview.show_message("Select a session on the left.")

        run_async(work, done, lambda m: self.say("Could not scan: " + m.splitlines()[-1]))

    def on_favorite(self, session_id: str, on: bool) -> None:
        self.favorites = set_favorite(session_id, on)
        for r in self.sessions.model.rows:
            if r.session_id == session_id:
                r.favorite = on
                self.sessions.update_row(r)

    def on_selected(self, row: SessionRow | None) -> None:
        self.row, self.plan, self.loaded = row, None, None
        self.armed = False
        self._fresh_selection = True
        self.export_panel.name.clear()
        self.export_panel.set_auto_name("")
        if row is None:
            self.preview.show_message("Select a session on the left.")
            self.update_apply()
            return
        self.load_session()

    def load_session(self) -> None:
        row = self.row
        self._load_gen += 1
        gen = self._load_gen
        self.plan, self.loaded = None, None
        self.preview.show_message("Reading the transcript...", row)
        self.update_apply()

        def work():
            loaded = load_transcript(row.path)
            t = Turns(loaded.records)
            infos = [TurnInfo(n=k + 1, time=str(loaded.records[t.first(k)].get("timestamp", "")),
                              chars=t.size[k], clean=t.clean[k],
                              prompt=" ".join(first_text(loaded.records[t.first(k)]).split())[:100])
                     for k in range(len(t))]
            return loaded, infos, list_snapshots(row.path), reconcile(row.path)

        def done(res):
            if gen != self._load_gen:
                return
            self.loaded, self.turns, snaps, self.history = res
            if self._fresh_selection:
                self.panel.reset_turn_range()
                self._fresh_selection = False
            self.panel.set_turns(len(self.turns))
            low, high = self.panel.settings().turn_range(len(self.turns))
            self.drawer.timeline.set_turns(self.turns, low, high)
            self.drawer.timeline.set_manual(self.panel.settings().method == "turns")
            self.drawer.snapshots.set_snapshots(snaps)
            self.schedule_preview()
            if self.exporting_mode():
                self.schedule_export_preview()

        def failed(msg):
            if gen == self._load_gen:
                self.preview.show_message("Could not read this transcript: " + msg.splitlines()[-1], row)

        run_async(work, done, failed)

    # ---- preview

    def on_panel_changed(self) -> None:
        s = self.panel.settings()
        save_settings(s)
        self.drawer.timeline.set_manual(s.method == "turns" and s.cut_on)
        if s.method == "turns" and self.turns:
            low, high = s.turn_range(len(self.turns))
            self.drawer.timeline.set_range(low, high)
        self.armed = False
        self.schedule_preview()

    def schedule_preview(self) -> None:
        self.plan = None
        self.armed = False
        self.update_command()
        if self.loaded is None:
            self.update_apply()
            return
        self._plan_gen += 1
        self.update_apply(updating=True)
        self.debounce.start()

    def update_command(self) -> None:
        if self.row is None:
            self.panel.set_command("")
            return
        self.panel.set_command(self.panel.settings().command(
            self.row.path, repo_dir(), len(self.turns), note=self.row.title[:60]))

    def run_preview(self) -> None:
        if self.loaded is None or self.row is None:
            return
        s, row, loaded = self.panel.settings(), self.row, self.loaded
        cut = s.cut_options(len(self.turns)) if s.cut_on else None
        clear = s.clear_options()
        if cut is None and clear is None:
            if not self.exporting_mode():
                self.preview.show_message("Nothing is selected: turn on the cut or pick something "
                                          "to clear.", row)
            self.update_apply()
            return
        self._plan_gen += 1
        gen = self._plan_gen

        def done(plan):
            if gen != self._plan_gen:
                return
            self.plan = plan
            if not self.exporting_mode():
                self.preview.show_plan(row, plan, self.history)
            if plan.cut and s.method == "fraction":
                self.drawer.timeline.set_range(plan.cut.a + 1, plan.cut.b)
            self.update_apply()

        def failed(msg):
            if gen != self._plan_gen:
                return
            self.plan = None
            if not self.exporting_mode():
                self.preview.show_message(msg.splitlines()[0] if msg.startswith(
                    ("pick", "--", "need", "no clean")) else msg, row)
            self.update_apply()

        run_async(lambda: plan_prune(row.path, cut=cut, clear=clear, reset_usage=s.reset_usage,
                                     loaded=loaded), done, failed)

    # ---- the apply button and the live guard

    def guard(self) -> tuple[list[str], int]:
        """Why this session can't be edited yet, and the seconds left when the
        only reason is a recent edit."""
        if self.row is None:
            return [], 0
        reasons = live_reasons(self.row.path)
        running = [r for r in reasons if r.startswith("registered")]
        wait = 0
        if reasons and not running:
            try:
                age = time.time() - os.path.getmtime(self.row.path)
            except OSError:
                age = 0
            wait = max(int(engine_settings.LIVE_IF_EDITED_WITHIN - age) + 1, 1)
        return running, wait

    def update_apply(self, updating: bool = False) -> None:
        p = self.panel
        if self.busy:
            return p.set_apply(False, "Applying...", "Writing the transcript.")
        if self.row is None:
            return p.set_apply(False, "Apply", "Select a session.")
        if updating or self.loaded is None:
            return p.set_apply(False, "Apply", "Working out the edit...")
        if self.plan is None:
            return p.set_apply(False, "Apply", "Fix the settings above.")
        if self.plan.is_noop:
            return p.set_apply(False, "Apply", "Nothing would change.")
        running, wait = self.guard()
        if running:
            return p.set_apply(False, "Apply", "This session is running: " + "; ".join(running)
                               + ". Exit it first.")
        if self.armed:
            return p.set_apply(False, f"Applying in {wait}s" if wait else "Applying...",
                               "Waiting for the live-session guard.", armed=True)
        if wait:
            return p.set_apply(True, "Apply when ready",
                               f"The file was edited moments ago; the guard clears in {wait}s.")
        p.set_apply(True, "Apply", self.estimate_status())

    def estimate_status(self) -> str:
        pl = self.plan
        return f"Estimate ~{fmt.tokens(pl.estimate)}" if pl and pl.estimate else ""

    def on_tick(self) -> None:
        if self.row is None:
            return
        running, wait = self.guard()
        live = live_reasons(self.row.path) if not self.busy else self.row.live
        if bool(live) != bool(self.row.live):
            self.row.live = live
            self.sessions.update_row(self.row)
        if self.armed and not wait and not running and self.plan is not None:
            self.armed = False
            self.do_apply()
            return
        if self.armed and running:
            self.armed = False
        if not self.busy and self.plan is not None:
            self.update_apply()

    def disarm(self) -> None:
        self.armed = False
        self.update_apply()

    def on_apply(self) -> None:
        if self.plan is None or self.busy:
            return
        running, wait = self.guard()
        if wait:
            self.armed = True
            self.update_apply()
            return
        self.do_apply()

    def do_apply(self) -> None:
        plan, row = self.plan, self.row
        if plan is None or row is None:
            return
        if plan.will_drop:
            names = "\n".join(f"  snap{s.n}  {fmt.size(s.size)}  {s.label}" for s in plan.will_drop)
            ask = QMessageBox.question(
                self, "Delete old snapshots?",
                f"Applying takes a new snapshot and deletes these, over the limit of "
                f"{engine_settings.MAX_SNAPSHOTS}:\n\n{names}\n\nThis cannot be undone.")
            if ask != QMessageBox.StandardButton.Yes:
                return
        self.busy = True
        self.update_apply()

        def done(applied):
            self.busy = False
            lines = [f"{plan.label}: snapshot snap{applied.snapshot}"]
            if applied.reset:
                lines.append(f"usage reset {fmt.tokens(applied.reset.last)} -> ~{fmt.tokens(applied.reset.estimate)} (estimate)")
            lines.append(f"size {fmt.size(plan.size_before)} -> {fmt.size(applied.size_after)}")
            for d in applied.dropped:
                lines.append(f"deleted snap{d.n} ({fmt.size(d.size)}) over the limit")
            lines.append("Resume it, send a real message and read the status bar.")
            self.preview.show_report(" | ".join(lines))
            self.after_change(row)

        def failed(msg):
            self.busy = False
            self.preview.show_report("NOT APPLIED: " + msg.strip())
            self.after_change(row)

        run_async(lambda: apply_plan(plan, force=False), done, failed)

    def after_change(self, row: SessionRow) -> None:
        """The file may have changed: read it and its row again."""
        def work():
            found = scan_session(row.path, False, False)
            return to_rows([found], self.favorites)[0] if found else None

        def done(new):
            if new is not None:
                self.sessions.update_row(new)
                if self.row and self.row.path == new.path:
                    self.row = new
            self.load_session()

        run_async(work, done, lambda m: self.load_session())

    # ---- snapshots

    def on_restore(self, n: int) -> None:
        row = self.row
        if row is None:
            return
        ask = QMessageBox.question(
            self, "Restore snapshot?",
            f"Restore snap{n} over the current transcript?\nThe current file is saved as a new "
            f"snapshot first, so nothing is lost.")
        if ask != QMessageBox.StandardButton.Yes:
            return

        def done(r):
            self.preview.show_report(f"restored snap{n}; the file as it was before is saved as "
                                     f"{os.path.basename(r.saved) if r.saved else 'nothing'}")
            self.after_change(row)

        run_async(lambda: restore_snapshot(row.path, n),
                  done, lambda m: self.preview.show_report("NOT RESTORED: " + m.strip()))

    def on_delete(self, n: int) -> None:
        row = self.row
        if row is None:
            return
        ask = QMessageBox.question(self, "Delete snapshot?",
                                   f"Delete snap{n}? It cannot be recovered.")
        if ask != QMessageBox.StandardButton.Yes:
            return
        freed = delete_snapshot(row.path, n)
        self.preview.show_report(f"deleted snap{n} ({fmt.size(freed)})")
        self.after_change(row)

    def on_pin(self, n: int, pinned: bool) -> None:
        if self.row is None:
            return
        set_pinned(self.row.path, n, pinned)
        self.drawer.snapshots.set_snapshots(list_snapshots(self.row.path))

    # ---- export

    def exporting_mode(self) -> bool:
        return self.tabs.currentWidget() is self.export_panel

    def on_mode(self, _index: int) -> None:
        if self.exporting_mode():
            self.export_panel.reload_config()
            self.schedule_export_preview()
        elif self.row is not None and self.loaded is not None:
            if self.plan is not None:
                self.preview.show_plan(self.row, self.plan, self.history)
            else:
                self.schedule_preview()

    def on_export_changed(self) -> None:
        save_section("export", self.export_panel.settings().to_dict())
        if self.exporting_mode():
            self.schedule_export_preview()

    def schedule_export_preview(self) -> None:
        self._export_gen += 1
        self.update_export_command()
        self.update_export_button(updating=self.loaded is not None)
        if self.row is None:
            self.preview.show_message("Select a session on the left.")
            return
        if self.loaded is None:
            return
        self.export_debounce.start()

    def run_export_preview(self) -> None:
        if self.loaded is None or self.row is None or not self.exporting_mode():
            return
        row, records = self.row, self.loaded.records
        try:
            opts = self.export_panel.options()
            opts.check()
        except Exception as exc:
            self.preview.show_message(str(exc), row)
            self.update_export_button()
            return
        gen = self._export_gen

        def work():
            t = read_transcript(row.path, opts, records=records)
            return t, render(t, opts, limit=EXPORT_PREVIEW_CHARS), default_filename(t)

        def done(res):
            if gen != self._export_gen or not self.exporting_mode():
                return
            t, text, name = res
            self.export_transcript = t
            self.export_panel.set_auto_name(name)
            target = self.export_panel.target()
            self.preview.show_export(row, len(t.shown), len(t.messages), t.trim_note,
                                     t.compactions, target, bool(target) and os.path.exists(target),
                                     text, bool(row.live), t.history, t.summary)
            self.update_export_command()
            self.update_export_button()

        def failed(msg):
            if gen == self._export_gen:
                self.preview.show_message("Could not read this session: " + msg.strip().splitlines()[-1], row)
                self.update_export_button()

        self.export_transcript = None
        run_async(work, done, failed)

    def update_export_command(self) -> None:
        p = self.export_panel
        if self.row is None or not p.target():
            p.set_command("")
            return
        p.set_command(p.settings().command(self.row.path, p.target(), p.named(),
                                           p.folders.default, custom_name=bool(p.name.text().strip()),
                                           summary=p.summary.isChecked()))

    def update_export_button(self, updating: bool = False) -> None:
        p = self.export_panel
        t = getattr(self, "export_transcript", None)
        if self.exporting:
            return p.set_export(False, "Exporting...")
        if self.row is None:
            return p.set_export(False, "Export", "Select a session.")
        if updating or self.loaded is None or t is None:
            return p.set_export(False, "Export", "Reading the session...")
        if not t.shown:
            return p.set_export(False, "Export", "This session has no messages to export.")
        target = p.target()
        if not target:
            return p.set_export(False, "Export", "Choose a folder.")
        exists = os.path.exists(target)
        p.set_export(True, "Overwrite…" if exists else "Export",
                     f"{os.path.basename(target)} exists." if exists else "")

    def on_export(self) -> None:
        p, row = self.export_panel, self.row
        target = p.target()
        if row is None or not target or self.exporting:
            return
        if os.path.exists(target):
            ask = QMessageBox.question(self, "Overwrite?",
                                       f"{target}\nalready exists. Overwrite it?")
            if ask != QMessageBox.StandardButton.Yes:
                return
        opts = p.options()
        self.exporting = True
        self.update_export_button()

        def work():
            t = read_transcript(row.path, opts)
            return write_export(t, target, opts, overwrite=True)

        def done(out):
            self.exporting = False
            self.last_export_dir = os.path.dirname(out.path)
            p.open_folder.setEnabled(True)
            self.preview.show_report(f"exported {out.messages} of {out.total} messages to "
                                     f"{out.path} ({out.size / 1024:.1f} KB)")
            self.update_export_button()
            p._folder_changed()

        def failed(msg):
            self.exporting = False
            self.preview.show_report("NOT EXPORTED: " + msg.strip())
            self.update_export_button()

        run_async(work, done, failed)

    def open_export_folder(self) -> None:
        if self.last_export_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_export_dir))
