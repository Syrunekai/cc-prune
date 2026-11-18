"""The settings panel: always visible on the right, holds the values the
next edit will use, and the exact command that makes the same edit."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .state import PanelSettings
from .widgets import SliderBox

THINKING_LABELS = [("none", "Keep thinking"), ("failing", "Delete failing thinking"),
                   ("all", "Delete all thinking")]


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


class ThresholdRow(QWidget):
    """A checkbox with a character threshold: clear values longer than N."""

    changed = Signal()

    def __init__(self, text: str, tip: str):
        super().__init__()
        self.check = QCheckBox(text)
        self.n = QSpinBox()
        self.n.setRange(1, 1_000_000)
        self.n.setSingleStep(100)
        self.n.setSuffix(" chars")
        self.n.setFixedWidth(104)
        self.check.setToolTip(tip)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.check, 1)
        row.addWidget(self.n)
        self.check.toggled.connect(self._toggle)
        self.n.valueChanged.connect(lambda *_: self.changed.emit())
        self.n.setEnabled(False)

    def _toggle(self, on: bool) -> None:
        self.n.setEnabled(on)
        self.changed.emit()

    def set(self, on: bool, n: int) -> None:
        for w in (self.check, self.n):
            w.blockSignals(True)
        self.check.setChecked(on)
        self.n.setValue(max(n, 1))
        self.n.setEnabled(on)
        for w in (self.check, self.n):
            w.blockSignals(False)


class SettingsPanel(QWidget):
    changed = Signal()          # any value changed by the user
    applyClicked = Signal()
    cancelClicked = Signal()

    def __init__(self, settings: PanelSettings):
        super().__init__()
        self._s = settings.normalized()
        self._turns = 0

        # ---- cut
        self.cut_group = QGroupBox("Cut")
        self.cut_group.setCheckable(True)
        self.by_fraction = QRadioButton("By fraction")
        self.by_turns = QRadioButton("By turn range")
        self.by_turns.setToolTip("Set the range with the two tips in the drawer below "
                                 "(toggle it with the button at the bottom left)")
        self.drop = SliderBox(0.05, 0.95, 0.05, box_lo=0.01, box_hi=0.99)
        self.keep_head = QSpinBox()
        self.keep_head.setRange(1, 999)
        self.keep_head.setFixedWidth(64)
        self.range_label = QLabel()
        self.range_label.setWordWrap(True)
        self.purge = QCheckBox("Delete the cut records (purge)")
        self.note = QCheckBox("Leave a note where the turns were cut")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Drop", self.drop)
        form.addRow("Keep head", self.keep_head)
        box = QVBoxLayout(self.cut_group)
        method = QHBoxLayout()
        method.addWidget(self.by_fraction)
        method.addWidget(self.by_turns)
        box.addLayout(method)
        box.addLayout(form)
        box.addWidget(self.range_label)
        box.addWidget(self.purge)
        box.addWidget(self.note)

        # ---- clear
        clear = QGroupBox("Clear")
        self.keep_tail = SliderBox(0.05, 0.95, 0.05, box_lo=0.0, box_hi=1.0)
        self.results = ThresholdRow("Tool results longer than", "Replace long tool output with a placeholder")
        self.inputs = ThresholdRow("Tool inputs longer than", "Replace long tool call parameters (file bodies, edits)")
        self.commands = ThresholdRow("Bash commands longer than", "Replace long Bash commands")
        self.attachments = ThresholdRow("Attachments longer than", "Shrink long attachment text")
        self.images = QCheckBox("Images")
        self.transformations = QCheckBox("Input-transformations log")
        self.transformations.setToolTip("Claude Code's own log of thinking blocks it dropped (experimental)")
        self.thinking = QComboBox()
        for key, label in THINKING_LABELS:
            self.thinking.addItem(label, key)
        self.thinking_note = muted("Experimental: thinking is signed, deleting it is not undoable "
                                   "except from a snapshot.")
        form2 = QFormLayout()
        form2.setContentsMargins(0, 0, 0, 0)
        form2.addRow("Keep tail", self.keep_tail)
        cl = QVBoxLayout(clear)
        cl.addLayout(form2)
        for w in (self.results, self.inputs, self.commands, self.attachments,
                  self.images, self.transformations, self.thinking, self.thinking_note):
            cl.addWidget(w)

        # ---- options
        self.reset_usage = QCheckBox("Rewrite the recorded usage")
        self.reset_usage.setToolTip("Lower the usage stored in the file so a near-limit session can resume")

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 4, 0)
        col.addWidget(self.cut_group)
        col.addWidget(clear)
        col.addWidget(self.reset_usage)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)

        # ---- command and apply
        self.command = QPlainTextEdit()
        self.command.setReadOnly(True)
        self.command.setFixedHeight(84)
        self.command.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        mono = self.command.font()
        mono.setFamily("monospace")
        mono.setPointSizeF(max(mono.pointSizeF() - 1.5, 7))
        self.command.setFont(mono)
        self.copy = QPushButton("Copy command")
        self.copy.setEnabled(False)
        self.status = muted("")
        self.apply = QPushButton("Apply")
        self.apply.setObjectName("apply")
        self.apply.setEnabled(False)
        self.cancel = QPushButton("Cancel")
        self.cancel.hide()
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply, 1)
        buttons.addWidget(self.cancel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll, 1)
        lay.addWidget(muted("The same edit as a command:"))
        lay.addWidget(self.command)
        lay.addWidget(self.copy)
        lay.addWidget(self.status)
        lay.addLayout(buttons)

        self._load(self._s)
        self._connect()

    # ---- values

    def settings(self) -> PanelSettings:
        return self._s

    def _load(self, s: PanelSettings) -> None:
        widgets = [self.cut_group, self.by_fraction, self.by_turns, self.drop, self.keep_head,
                   self.purge, self.note, self.keep_tail, self.images, self.transformations,
                   self.thinking, self.reset_usage]
        for w in widgets:
            w.blockSignals(True)
        self.cut_group.setChecked(s.cut_on)
        (self.by_turns if s.method == "turns" else self.by_fraction).setChecked(True)
        self.drop.setValue(s.drop)
        self.keep_head.setValue(s.keep_head)
        self.purge.setChecked(s.purge)
        self.note.setChecked(s.note)
        self.keep_tail.setValue(s.keep_tail)
        self.results.set(s.results_on, s.results)
        self.inputs.set(s.inputs_on, s.inputs)
        self.commands.set(s.commands_on, s.commands)
        self.attachments.set(s.attachments_on, s.attachments)
        self.images.setChecked(s.images)
        self.transformations.setChecked(s.transformations)
        self.thinking.setCurrentIndex(max(self.thinking.findData(s.thinking), 0))
        self.reset_usage.setChecked(s.reset_usage)
        for w in widgets:
            w.blockSignals(False)
        self._refresh_enabled()

    def _connect(self) -> None:
        for sig in (self.cut_group.toggled, self.by_turns.toggled, self.drop.valueChanged,
                    self.keep_head.valueChanged, self.purge.toggled, self.note.toggled,
                    self.keep_tail.valueChanged, self.images.toggled,
                    self.transformations.toggled, self.thinking.currentIndexChanged,
                    self.reset_usage.toggled, self.results.changed, self.inputs.changed,
                    self.commands.changed, self.attachments.changed):
            sig.connect(self._edited)
        self.apply.clicked.connect(self.applyClicked)
        self.cancel.clicked.connect(self.cancelClicked)
        self.copy.clicked.connect(self._copy)

    def _edited(self, *_) -> None:
        s = replace(self._s)
        s.cut_on = self.cut_group.isChecked()
        s.method = "turns" if self.by_turns.isChecked() else "fraction"
        s.drop, s.keep_head = self.drop.value(), self.keep_head.value()
        s.purge, s.note = self.purge.isChecked(), self.note.isChecked()
        s.keep_tail = self.keep_tail.value()
        s.results_on, s.results = self.results.check.isChecked(), self.results.n.value()
        s.inputs_on, s.inputs = self.inputs.check.isChecked(), self.inputs.n.value()
        s.commands_on, s.commands = self.commands.check.isChecked(), self.commands.n.value()
        s.attachments_on, s.attachments = (self.attachments.check.isChecked(),
                                           self.attachments.n.value())
        s.images, s.transformations = self.images.isChecked(), self.transformations.isChecked()
        s.thinking = self.thinking.currentData()
        s.reset_usage = self.reset_usage.isChecked()
        self._s = s.normalized()
        self._refresh_enabled()
        self.changed.emit()

    def set_turn_range(self, a: int, b: int) -> None:
        """The range chosen in the drawer."""
        self._s = replace(self._s, from_turn=a, to_turn=b)
        self._refresh_enabled()
        self.changed.emit()

    def reset_turn_range(self) -> None:
        """Back to the largest cut; the range belongs to one session."""
        if self._s.from_turn or self._s.to_turn:
            self._s = replace(self._s, from_turn=0, to_turn=0)
            self._refresh_enabled()

    def set_turns(self, turns: int) -> None:
        self._turns = turns
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        s, on = self._s, self.cut_group.isChecked()
        frac = on and s.method == "fraction"
        for w in (self.by_fraction, self.by_turns, self.purge, self.note):
            w.setEnabled(on)
        self.drop.setEnabled(frac)
        self.keep_head.setEnabled(frac)
        if s.method == "turns":
            if self._turns >= 3:
                a, b = s.turn_range(self._turns)
                self.range_label.setText(f"Turns {a} to {b} of {self._turns}. Set the tips in the "
                                         f"drawer; they start at the largest cut.")
            else:
                self.range_label.setText("Pick a session to set a range.")
        else:
            self.range_label.setText("Both ends snap to turns where no tool call is left "
                                     "waiting for its result.")
        self.range_label.setObjectName("muted")
        self.range_label.setStyleSheet("" if on else "color: #5d626b;")
        self.thinking_note.setVisible(self.thinking.currentData() != "none")

    # ---- command and apply button

    def set_command(self, text: str) -> None:
        self.command.setPlainText(text)
        self.copy.setEnabled(bool(text))

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.command.toPlainText())
        self.copy.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy.setText("Copy command"))

    def set_apply(self, enabled: bool, text: str = "Apply", status: str = "",
                  armed: bool = False) -> None:
        self.apply.setEnabled(enabled)
        self.apply.setText(text)
        self.apply.setObjectName("armed" if armed else "apply")
        self.apply.style().unpolish(self.apply)
        self.apply.style().polish(self.apply)
        self.cancel.setVisible(armed)
        self.status.setText(status)
