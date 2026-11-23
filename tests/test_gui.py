"""The window, driven offscreen."""
from __future__ import annotations

import json
import os
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ccprune import settings  # noqa: E402
from ccprune.favorites import load_favorites  # noqa: E402
from ccprune.gui.state import PanelSettings, load_settings  # noqa: E402
from ccprune.gui.widgets import RangeSlider, SliderBox  # noqa: E402
from ccprune.gui.window import MainWindow  # noqa: E402
from ccprune.snapshots import list_snapshots  # noqa: E402
from conftest import write_session  # noqa: E402

A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"


@pytest.fixture(autouse=True)
def no_unexpected_dialogs(monkeypatch):
    """A modal dialog a test did not stub fails the test instead of waiting forever."""
    from PySide6.QtWidgets import QDialog, QInputDialog

    def refuse(*a, **k):
        raise AssertionError(f"unexpected modal dialog: {a[1:3] if len(a) > 2 else a}")
    for name in ("warning", "question", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(refuse))
    monkeypatch.setattr(QMessageBox, "exec", refuse)
    monkeypatch.setattr(QDialog, "exec", refuse)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(refuse))
    from ccprune.gui.window import MainWindow
    monkeypatch.setattr(MainWindow, "show_menu", lambda self, menu, pos: refuse(self, "menu"))


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def world(tmp_path, monkeypatch, app):
    root = tmp_path / "projects"
    monkeypatch.setattr(settings, "PROJECTS_ROOT", str(root))
    monkeypatch.setattr(settings, "SESSIONS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(settings, "GUI_FILE", str(tmp_path / "cfg" / "gui.json"))
    monkeypatch.setattr(settings, "FAVORITES_FILE", str(tmp_path / "cfg" / "fav.json"))
    monkeypatch.setattr(settings, "ESTIMATE_LOG", str(tmp_path / "data" / "est.jsonl"))
    monkeypatch.setattr(settings, "MAX_SNAPSHOTS", 0)
    monkeypatch.setattr(settings, "LIVE_IF_EDITED_WITHIN", 30)
    paths = {A: write_session(root, A, turns=10), B: write_session(root, B, project="-tmp-other", turns=6)}
    windows = []

    def make() -> MainWindow:
        w = MainWindow(root=str(root))
        windows.append(w)
        w.show()
        wait(app, lambda: len(w.sessions.model.rows) == 2)
        return w

    yield make, paths
    for w in windows:
        w.tick.stop()
        w.close()
    wait(app, lambda: False, 0.2)


def wait(app, cond, secs: float = 20.0) -> bool:
    end = time.time() + secs
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.01)
    return bool(cond())


def select(app, w: MainWindow, session: str) -> None:
    path = next(r.path for r in w.sessions.model.rows if r.session_id == session)
    w.sessions.select_path(path)
    assert wait(app, lambda: w.plan is not None), "no plan was made"


def test_starts_with_the_drawer_hidden_and_the_method_unchanged(world, app):
    make, _ = world
    w = make()
    assert not w.drawer.isVisible()
    assert w.panel.settings().method == "fraction"
    w.drawer_button.setChecked(True)
    assert w.drawer.isVisible()
    assert w.panel.settings().method == "fraction"
    assert not w.drawer.timeline.slider.isEnabled()
    w.drawer_button.setChecked(False)
    assert not w.drawer.isVisible()


def test_selecting_a_session_makes_a_plan_and_a_command(world, app):
    make, paths = world
    w = make()
    select(app, w, A)
    assert w.plan.cut is not None and w.plan.estimate
    cmd = w.panel.command.toPlainText()
    assert "clearcut" in cmd and paths[A] in cmd and "--drop 0.5" in cmd
    assert w.panel.apply.isEnabled()
    assert "Anchored" in w.preview.confidence.text() or "Chained" in w.preview.confidence.text()


def test_panel_values_change_the_plan_and_are_saved(world, app):
    make, _ = world
    w = make()
    select(app, w, A)
    first = (w.plan.cut.a, w.plan.cut.b)
    w.panel.drop.slider.setValue(17)            # 0.05 + 17 * 0.05 = 0.90
    assert w.panel.settings().drop == pytest.approx(0.9)
    assert wait(app, lambda: w.plan and (w.plan.cut.a, w.plan.cut.b) != first)
    assert "--drop 0.9" in w.panel.command.toPlainText()
    assert load_settings().drop == pytest.approx(0.9)


def test_slider_box_steps_and_takes_free_values(app):
    sb = SliderBox(0.05, 0.95, 0.05, box_lo=0.0, box_hi=1.0)
    seen = []
    sb.valueChanged.connect(seen.append)
    sb.slider.setValue(1)
    sb.slider.setValue(18)
    assert seen == [0.1, 0.95] and sb.box.value() == 0.95
    sb.box.setValue(0.5)
    assert sb.slider.value() == 9 and sb.value() == 0.5
    sb.box.setValue(0.07)
    assert sb.value() == 0.07 and sb.slider.value() == 0
    sb.box.setValue(0.0)
    assert sb.value() == 0.0
    sb.setValue(0.3)
    assert sb.slider.value() == 5 and seen[-1] == 0.0


def test_range_slider_starts_wide_and_the_tips_drag_in(app):
    rs = RangeSlider()
    rs.resize(400, 54)
    rs.setBounds(2, 18)
    rs.setRange(2, 18)
    rs.show()
    changed, released = [], []
    rs.rangeChanged.connect(lambda a, b: changed.append((a, b)))
    rs.rangeReleased.connect(lambda a, b: released.append((a, b)))
    assert rs.range() == (2, 18)
    y = int(rs.height() * 0.58)
    mid = QPoint(int(rs._x(10)), y)
    QTest.mousePress(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(rs._x(2)), y))
    QTest.mouseMove(rs, mid)
    QTest.mouseRelease(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, mid)
    assert rs.range() == (10, 18) and released[-1] == (10, 18)
    QTest.mousePress(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(rs._x(18)), y))
    QTest.mouseMove(rs, QPoint(int(rs._x(14)), y))
    QTest.mouseRelease(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(rs._x(14)), y))
    assert rs.range() == (10, 14)
    QTest.mousePress(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(rs._x(10)), y))
    QTest.mouseMove(rs, QPoint(int(rs._x(17)), y))
    QTest.mouseRelease(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(rs._x(17)), y))
    assert rs.range() == (14, 14), "a tip stops at the other one"
    rs.setEnabled(False)
    QTest.mousePress(rs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(rs._x(14)), y))
    assert rs.range() == (14, 14)


def test_the_turn_range_only_applies_once_the_method_is_selected(world, app):
    make, _ = world
    w = make()
    select(app, w, A)
    w.drawer_button.setChecked(True)
    assert not w.drawer.timeline.slider.isEnabled()
    w.panel.by_turns.setChecked(True)
    assert wait(app, lambda: w.plan and w.panel.settings().method == "turns" and w.plan.cut.a == 1)
    assert w.drawer.timeline.slider.isEnabled()
    low, high = w.drawer.timeline.slider.range()
    assert (low, high) == (2, len(w.turns) - 1), "both tips start at the largest cut"
    assert w.plan.cut.b == len(w.turns) - 1

    sl = w.drawer.timeline.slider
    sl.setFocus()
    sl._last = "high"
    for _ in range(3):
        QTest.keyClick(sl, Qt.Key.Key_Left)
    assert wait(app, lambda: w.plan and w.plan.cut.b == len(w.turns) - 4)
    assert "--from 2 --to %d" % (len(w.turns) - 4) in w.panel.command.toPlainText()
    assert w.panel.settings().to_turn == len(w.turns) - 4

    select_b = next(r.path for r in w.sessions.model.rows if r.session_id == B)
    w.sessions.select_path(select_b)
    assert wait(app, lambda: w.plan and w.plan.cut and len(w.plan.cut.turns) == 6 and w.plan.cut.b == 5)
    assert w.panel.settings().to_turn == 0, "the range belongs to one session"


def test_favorites_toggle_with_the_star_and_sort_first(world, app):
    make, _ = world
    w = make()
    order = [r.session_id for r in (w.sessions.proxy.data(w.sessions.proxy.index(i, 0), Qt.ItemDataRole.UserRole)
                                    for i in range(2))]
    star_row = order.index(B)
    index = w.sessions.proxy.index(star_row, 0)
    w.sessions.view.clicked.emit(index)
    assert load_favorites() == {B}
    assert wait(app, lambda: w.sessions.proxy.data(w.sessions.proxy.index(0, 0), Qt.ItemDataRole.UserRole).session_id == B)
    w.sessions.only_fav.setChecked(True)
    assert w.sessions.proxy.rowCount() == 1
    w.sessions.view.clicked.emit(w.sessions.proxy.index(0, 0))
    assert load_favorites() == set()


def test_apply_writes_one_labeled_snapshot_and_reloads(world, app):
    make, paths = world
    w = make()
    select(app, w, A)
    before = open(paths[A], "rb").read()
    w.panel.apply.click()
    assert wait(app, lambda: not w.busy and list_snapshots(paths[A]))
    snaps = list_snapshots(paths[A])
    assert len(snaps) == 1 and snaps[0].label.startswith("clearcut turns")
    assert open(f"{paths[A]}.snap0", "rb").read() == before
    assert open(paths[A], "rb").read() != before
    assert wait(app, lambda: "snapshot snap0" in w.preview.report.toPlainText())
    assert wait(app, lambda: w.loaded is not None and w.plan is not None)
    assert wait(app, lambda: w.drawer.snapshots.table.rowCount() == 1)
    assert json.loads(open(settings.ESTIMATE_LOG).readline())["session"] == A


def test_snapshot_pin_and_restore_from_the_drawer(world, app, monkeypatch):
    make, paths = world
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = make()
    select(app, w, A)
    before = open(paths[A], "rb").read()
    w.panel.apply.click()
    assert wait(app, lambda: not w.busy and list_snapshots(paths[A]))
    assert wait(app, lambda: w.drawer.snapshots.table.rowCount() == 1 and w.plan is not None)
    w.drawer.snapshots.table.selectRow(0)
    assert w.drawer.snapshots.pin.text() == "Unpin"        # snap0 is pinned from the start
    w.drawer.snapshots.pin.click()
    assert not list_snapshots(paths[A])[0].pinned
    time.sleep(0.05)
    os.utime(paths[A], (time.time() - 3600,) * 2)
    w.drawer.snapshots.restore.click()
    assert wait(app, lambda: open(paths[A], "rb").read() == before)
    assert len(list_snapshots(paths[A])) == 2


def test_a_session_registered_to_a_running_process_cannot_be_applied(world, app, tmp_path):
    make, paths = world
    pid = os.getpid()
    start = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19]
    reg = tmp_path / "registry"
    reg.mkdir()
    (reg / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": A, "procStart": start}))
    w = make()
    select(app, w, A)
    assert not w.panel.apply.isEnabled()
    assert "running" in w.panel.status.text()
    assert any(r.live for r in w.sessions.model.rows if r.session_id == A)


def test_a_recent_edit_arms_apply_and_it_runs_when_the_guard_clears(world, app, monkeypatch):
    make, paths = world
    monkeypatch.setattr(settings, "LIVE_IF_EDITED_WITHIN", 2)
    os.utime(paths[A], None)
    w = make()
    select(app, w, A)
    assert w.panel.apply.isEnabled() and w.panel.apply.text() == "Apply when ready"
    w.panel.apply.click()
    assert w.armed and w.panel.cancel.isVisibleTo(w.panel)
    assert not list_snapshots(paths[A])
    assert wait(app, lambda: list_snapshots(paths[A]) != [], 10)
    assert not w.armed


def test_cancel_disarms(world, app, monkeypatch):
    make, paths = world
    monkeypatch.setattr(settings, "LIVE_IF_EDITED_WITHIN", 30)
    os.utime(paths[A], None)
    w = make()
    select(app, w, A)
    w.panel.apply.click()
    assert w.armed
    w.panel.cancel.click()
    assert not w.armed and w.panel.apply.text() == "Apply when ready"
    assert not list_snapshots(paths[A])


def test_a_file_that_changes_after_the_preview_is_refused(world, app):
    make, paths = world
    w = make()
    select(app, w, A)
    before = open(paths[A], "rb").read()
    with open(paths[A], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "cost-state", "sessionId": A}) + "\n")
    changed = open(paths[A], "rb").read()
    os.utime(paths[A], (time.time() - 3600,) * 2)
    w.panel.apply.click()
    assert wait(app, lambda: "NOT APPLIED" in w.preview.report.toPlainText())
    assert "changed after it was read" in w.preview.report.toPlainText()
    assert open(paths[A], "rb").read() == changed != before
    assert list_snapshots(paths[A]) == []


def test_a_clear_only_edit_that_matches_nothing_is_not_applicable(world, app):
    make, paths = world
    w = make()
    select(app, w, A)
    w.panel.cut_group.setChecked(False)
    w.panel.results.check.setChecked(False)
    w.panel.inputs.check.setChecked(False)
    w.panel.images.setChecked(False)
    w.panel.transformations.setChecked(True)
    assert wait(app, lambda: w.plan is not None and w.plan.is_noop)
    assert not w.panel.apply.isEnabled() and "Nothing would change" in w.panel.status.text()


def test_nothing_selected_shows_a_message_and_no_command(world, app):
    make, _ = world
    w = make()
    select(app, w, A)
    for box in (w.panel.cut_group,):
        box.setChecked(False)
    w.panel.results.check.setChecked(False)
    w.panel.inputs.check.setChecked(False)
    w.panel.images.setChecked(False)
    assert wait(app, lambda: "Nothing is selected" in w.preview.details.toPlainText())
    assert w.panel.command.toPlainText() == "" and not w.panel.apply.isEnabled()


def test_rows_keep_their_text_color_when_selected_and_hover_covers_the_row(world, app, tmp_path):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent as E
    make, _ = world
    w = make()
    v = w.sessions.view
    v.setCurrentIndex(w.sessions.proxy.index(0, 1))
    d = v.itemDelegate()
    rect = v.visualRect(w.sessions.proxy.index(1, 1))
    move = QMouseEvent(E.Type.MouseMove, QPointF(rect.center()), QPointF(rect.center()),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(v.viewport(), move)
    assert d.hover_row == 1
    QApplication.sendEvent(v.viewport(), QEvent(QEvent.Type.Leave))
    assert d.hover_row == -1
    assert w.sessions.model.headerData(2, Qt.Orientation.Horizontal) == "Live"


# ---- the Export tab

@pytest.fixture
def export_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    out = tmp_path / "out"
    env.write_text(f"""CC_PRUNE_EXPORT_DIRS='[{{"name": "main", "path": "{out}"}}]'\n""")
    monkeypatch.setenv("CC_PRUNE_ENV", str(env))
    for key in ("CC_PRUNE_EXPORT_DIRS", "CC_PRUNE_EXPORT_DEFAULT", "CC_PRUNE_USER_NAME",
                "CC_PRUNE_ASSISTANT_NAME", "CC_PRUNE_TIMEZONE"):
        monkeypatch.delenv(key, raising=False)
    return env, out


def open_export(app, w, session):
    w.tabs.setCurrentWidget(w.export_panel)
    path = next(r.path for r in w.sessions.model.rows if r.session_id == session)
    w.sessions.select_path(path)
    assert wait(app, lambda: getattr(w, "export_transcript", None) is not None
                and w.row and w.row.session_id == session), "no export preview"
    return path


def test_export_tab_previews_and_writes_into_the_default_folder(world, export_env, app, monkeypatch):
    make, paths = world
    env, out = export_env
    w = make()
    open_export(app, w, A)
    p = w.export_panel
    assert p.named() == "main" and p.target().startswith(str(out))
    assert w.preview.pages.currentWidget() is w.preview.markdown
    assert "### Operator" in w.preview.markdown.toPlainText()
    assert w.preview.big.text() == "20 messages"
    assert p.command.toPlainText().endswith('export "$F"')
    assert not p.before.isChecked() and p.summary.isChecked()
    p.summary.setChecked(False)
    assert wait(app, lambda: p.command.toPlainText().endswith('export "$F" --no-summary'))
    p.before.setChecked(True)
    assert not p.summary.isChecked(), "turning the history on unchecks the summary"
    assert wait(app, lambda: p.command.toPlainText().endswith('export "$F" --history'))
    p.summary.setChecked(True)
    assert wait(app, lambda: p.command.toPlainText().endswith('export "$F" --history --summary'))
    p.before.setChecked(False)
    assert p.summary.isChecked(), "turning the history off checks the summary"
    from ccprune.gui.state import load_section
    assert "summary" not in load_section("export")
    assert wait(app, lambda: p.export.isEnabled())
    before = open(paths[A], "rb").read()
    p.export.click()
    assert wait(app, lambda: out.exists() and os.listdir(out))
    assert wait(app, lambda: not w.exporting and p.export.text() == "Overwrite…")
    assert open(paths[A], "rb").read() == before
    assert "exported 20 of 20 messages" in w.preview.report.toPlainText()

    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No))
    p.export.click()
    assert asked and not w.exporting


def test_export_options_change_the_preview_and_the_command(world, export_env, app):
    make, _ = world
    w = make()
    open_export(app, w, A)
    p = w.export_panel
    p.trim_tail.setChecked(True)
    p.count.setValue(3)
    p.raw.setChecked(True)
    assert wait(app, lambda: w.preview.big.text().startswith("3 messages of"))
    assert "--raw" in p.command.toPlainText() and "--tail 3" in p.command.toPlainText()
    p.name.setText("mine")
    assert wait(app, lambda: p.target().endswith("/mine.md"))
    assert p.target() in p.command.toPlainText()
    from ccprune.gui.state import load_section
    assert load_section("export")["trim"] == "tail"


def test_saving_a_folder_by_name_writes_the_env_file(world, export_env, app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    make, _ = world
    env, _ = export_env
    other = tmp_path / "elsewhere"
    other.mkdir()
    w = make()
    open_export(app, w, A)
    p = w.export_panel
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(other)))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("notes", True)))
    p.browse.click()
    assert p.folder.currentData() != "main" and p.target().startswith(str(other))
    assert p.save_folder.isEnabled()
    p.save_folder.click()
    text = env.read_text()
    assert '"name": "notes"' in text and "CC_PRUNE_EXPORT_DEFAULT='main'" in text
    assert p.named() == "notes"
    p.make_default.click()
    assert "CC_PRUNE_EXPORT_DEFAULT='notes'" in env.read_text()
    assert not p.make_default.isEnabled()


def test_a_live_session_can_still_be_exported(world, export_env, app, tmp_path):
    make, paths = world
    pid = os.getpid()
    start = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19]
    reg = tmp_path / "registry"
    reg.mkdir()
    (reg / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": A, "procStart": start}))
    w = make()
    open_export(app, w, A)
    assert w.export_panel.export.isEnabled()
    assert "live" in w.preview.confidence.text()


def test_switching_back_to_prune_shows_the_plan(world, export_env, app):
    make, _ = world
    w = make()
    select(app, w, A)
    w.tabs.setCurrentWidget(w.export_panel)
    assert wait(app, lambda: w.preview.pages.currentWidget() is w.preview.markdown)
    w.tabs.setCurrentWidget(w.panel)
    assert w.preview.pages.currentWidget() is w.preview.details
    assert "Cut" in w.preview.details.toPlainText()


def test_no_folder_configured_asks_for_one(world, app, tmp_path, monkeypatch):
    monkeypatch.setenv("CC_PRUNE_ENV", str(tmp_path / "none.env"))
    monkeypatch.delenv("CC_PRUNE_EXPORT_DIRS", raising=False)
    make, _ = world
    w = make()
    open_export(app, w, A)
    assert not w.export_panel.export.isEnabled()
    assert "Choose a folder" in w.export_panel.status.text()
    assert "No named folders yet" in w.export_panel.env_note.text()


def test_the_summary_choice_is_not_remembered(world, export_env, app):
    from ccprune.gui.state import save_section
    save_section("export", {"before_compaction": True, "summary": True})
    make, _ = world
    w = make()
    assert w.export_panel.before.isChecked() and not w.export_panel.summary.isChecked()
    save_section("export", {"before_compaction": False, "summary": False})
    w2 = make()
    assert w2.export_panel.summary.isChecked()


def test_tooltips_use_the_theme_colors_not_the_system_ones(app):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QToolTip
    from ccprune.gui import theme
    theme.apply_theme(app)
    pal = QToolTip.palette()
    assert pal.color(QPalette.ColorRole.ToolTipText).name() == theme.TEXT
    assert pal.color(QPalette.ColorRole.ToolTipBase).name() == theme.PANEL
    assert f"QToolTip {{ color: {theme.TEXT}; background-color: {theme.PANEL};" in app.styleSheet()


# ---- the session menu: rename, delete, backups

def test_right_click_offers_rename_delete_and_backups(world, app, monkeypatch):
    make, paths = world
    w = make()
    seen = []
    menus = []
    monkeypatch.setattr(w, "show_menu", lambda menu, pos: (menus.append(menu), seen.append(
        [(x.text(), x.isEnabled()) for x in menu.actions() if not x.isSeparator()])))
    row_b = next(i for i in range(w.sessions.proxy.rowCount())
                 if w.sessions.proxy.data(w.sessions.proxy.index(i, 0), Qt.ItemDataRole.UserRole).session_id == B)
    rect = w.sessions.view.visualRect(w.sessions.proxy.index(row_b, 1))
    w.sessions.view.customContextMenuRequested.emit(rect.center())
    assert seen == [[("Rename…", True), ("Manage backups…", True), ("Delete all backups…", False),
                     ("Delete session…", True)]]
    assert w.sessions.current_row().session_id == B, "right-click selects the row"
    acts = menus[0].actions()
    assert acts[-1].text() == "Delete session…" and acts[-2].isSeparator(), "Delete sits alone at the bottom"

    from ccprune.gui.theme import apply_theme
    apply_theme(app)
    menu = menus[0]
    menu.adjustSize()
    menu.ensurePolished()
    image = menu.grab().toImage()

    def red_share(action) -> float:
        """Of the entry's text pixels, the share that is red."""
        r = menu.actionGeometry(action)
        bg = image.pixelColor(r.right() - 2, r.center().y())
        text = red = 0
        for x in range(r.left(), r.right()):
            for y in range(r.top(), r.bottom()):
                c = image.pixelColor(x, y)
                if abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue()) > 120:
                    text += 1
                    red += c.red() > c.green() + 60 and c.red() > c.blue() + 60
        return red / text if text else 0.0
    shares = {a.text(): red_share(a) for a in acts if not a.isSeparator() and a.isEnabled()}
    assert shares["Delete session…"] > 0.6, shares
    assert all(v < 0.2 for k, v in shares.items() if k != "Delete session…"), shares


def test_rename_from_the_menu(world, app, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from ccprune.manage import custom_title
    make, paths = world
    w = make()
    row = next(r for r in w.sessions.model.rows if r.session_id == A)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Renamed here", True)))
    w.rename(row)
    assert custom_title(paths[A]) == "Renamed here"
    assert '"customTitle":"Renamed here"' in open(paths[A]).read().splitlines()[-1]
    assert wait(app, lambda: next(r for r in w.sessions.model.rows if r.session_id == A).title == "Renamed here")


def test_rename_refuses_a_running_session(world, app, monkeypatch):
    from ccprune.manage import custom_title
    make, paths = world
    w = make()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    os.utime(paths[A], None)
    w.rename(next(r for r in w.sessions.model.rows if r.session_id == A))
    assert "can't be renamed now" in warned[0] and "/rename" in warned[0]
    assert custom_title(paths[A]) is None


def test_delete_from_the_menu_removes_the_session(world, app, monkeypatch):
    import ccprune.gui.window as win
    make, paths = world
    w = make()
    select(app, w, A)
    w.panel.apply.click()
    assert wait(app, lambda: not w.busy and list_snapshots(paths[A]))
    os.utime(paths[A], (time.time() - 3600,) * 2)       # past the recent-edit guard
    asked = []
    monkeypatch.setattr(win, "confirm_delete", lambda parent, row, files: asked.append(
        (row.session_id, row.title, [a.kind for a in files.artifacts])) or True)
    w.delete(next(r for r in w.sessions.model.rows if r.session_id == A))
    assert wait(app, lambda: not os.path.exists(paths[A]))
    sid, title, kinds = asked[0]
    assert sid == A and title and "snapshot" in kinds and kinds[-1] == "transcript"
    assert not list_snapshots(paths[A])
    assert wait(app, lambda: [r.session_id for r in w.sessions.model.rows] == [B])
    assert w.row is None and not w.panel.apply.isEnabled()
    assert "deleted session" in w.preview.report.toPlainText()


def test_delete_cancelled_or_live_keeps_the_session(world, app, monkeypatch, tmp_path):
    import ccprune.gui.window as win
    make, paths = world
    w = make()
    monkeypatch.setattr(win, "confirm_delete", lambda *a: False)
    w.delete(next(r for r in w.sessions.model.rows if r.session_id == A))
    assert os.path.exists(paths[A])
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(win, "confirm_delete", lambda *a: True)
    os.utime(paths[A], None)
    w.delete(next(r for r in w.sessions.model.rows if r.session_id == A))
    assert os.path.exists(paths[A]) and "can't be deleted now" in warned[0]


def test_backups_dialog_deletes_several_and_renumbers(world, app, monkeypatch):
    import ccprune.gui.actions as act
    from ccprune.snapshots import take_snapshot
    make, paths = world
    for k in range(4):
        take_snapshot(paths[A], f"s{k}")
    w = make()
    row = next(r for r in w.sessions.model.rows if r.session_id == A)
    monkeypatch.setattr(act, "_ask", lambda *a, **k: True)
    d = act.BackupsDialog(w, row, w.preview.show_report)
    t = d.tab
    assert t.table.rowCount() == 4 and t.delete_all.isEnabled()
    t.table.selectRow(0)
    t.table.selectionModel().select(t.table.model().index(2, 0),
                                    t.table.selectionModel().SelectionFlag.Select
                                    | t.table.selectionModel().SelectionFlag.Rows)
    assert t.delete.text() == "Delete 2" and not t.restore.isEnabled()
    t.delete.click()
    assert [(s.n, s.label) for s in list_snapshots(paths[A])] == [(0, "s1"), (1, "s3")]
    assert t.table.rowCount() == 2 and d.changed
    t.delete_all.click()
    assert list_snapshots(paths[A]) == [] and t.table.rowCount() == 0
    d.close()


def test_delete_all_backups_from_the_menu(world, app, monkeypatch):
    import ccprune.gui.window as win
    from ccprune.snapshots import take_snapshot
    make, paths = world
    take_snapshot(paths[B], "x")
    take_snapshot(paths[B], "y")
    w = make()
    before = open(paths[B], "rb").read()
    monkeypatch.setattr(win, "_ask", lambda *a, **k: True)
    w.delete_backups(next(r for r in w.sessions.model.rows if r.session_id == B))
    assert list_snapshots(paths[B]) == [] and open(paths[B], "rb").read() == before
    assert wait(app, lambda: next(r for r in w.sessions.model.rows if r.session_id == B).snaps == 0)


def test_apply_says_why_an_edit_cannot_be_made(world, app, monkeypatch):
    import ccprune.gui.window as win
    from ccprune.errors import PruneError
    make, _ = world

    def refuse(*a, **k):
        raise PruneError("refusing: 40 blocks were reported dropped. Delete all thinking instead, "
                         "or leave thinking as it is.")
    monkeypatch.setattr(win, "plan_prune", refuse)
    w = make()
    w.sessions.select_path(next(r.path for r in w.sessions.model.rows if r.session_id == A))
    assert wait(app, lambda: w.plan_error)
    status = w.panel.status.text()
    assert status.startswith("Can't apply: refusing: 40 blocks") and "Delete all thinking" in status
    assert "can't be made" in w.preview.details.toPlainText()
