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
