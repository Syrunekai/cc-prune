"""The Export tab: where a transcript goes and what it holds."""
from __future__ import annotations

import os
import shlex
from dataclasses import asdict, dataclass, fields, replace

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import config
from ..errors import PruneError
from ..export import DEFAULT_ASSISTANT_NAME, DEFAULT_USER_NAME, ExportOptions
from .settings_panel import muted

OTHER = "\x00other"     # the combo entry of a one-off folder


@dataclass
class ExportSettings:
    raw: bool = False
    before_compaction: bool = False
    trim: str = "all"               # "all", "head" or "tail"
    count: int = 100
    timezone: str = ""              # empty: CC_PRUNE_TIMEZONE, else local
    user_name: str = ""             # empty: CC_PRUNE_USER_NAME, else the default
    assistant_name: str = ""
    folder: str = ""                # the named folder last used

    @classmethod
    def from_dict(cls, raw: dict) -> "ExportSettings":
        known = {f.name for f in fields(cls)}
        try:
            out = cls(**{k: v for k, v in (raw or {}).items() if k in known})
        except TypeError:
            return cls()
        if out.trim not in ("all", "head", "tail"):
            out.trim = "all"
        out.count = max(int(out.count or 1), 1)
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    def options(self, env: dict[str, str], summary: bool) -> ExportOptions:
        return ExportOptions(
            raw=self.raw, before_compaction=self.before_compaction, summary=summary,
            head=self.count if self.trim == "head" else 0,
            tail=self.count if self.trim == "tail" else 0,
            timezone=self.timezone or env.get(config.TIMEZONE) or "local",
            user_name=self.user_name or env.get(config.USER_NAME) or DEFAULT_USER_NAME,
            assistant_name=self.assistant_name or env.get(config.ASSISTANT_NAME)
            or DEFAULT_ASSISTANT_NAME)

    def command(self, path: str, dest: str, named: str | None, default: str | None,
                custom_name: bool = False, summary: bool | None = None) -> str:
        """The command line that writes the same file: `dest` is the full
        target path, used when the folder has no name or the file name is set."""
        args = ["export", '"$F"']
        if custom_name or not named:
            args.append(shlex.quote(dest))
        elif named == default:
            pass
        else:
            args += ["--to", shlex.quote(named)]
        if self.raw:
            args.append("--raw")
        if self.before_compaction:
            args.append("--history")
        if summary is not None and summary == self.before_compaction:
            args.append("--summary" if summary else "--no-summary")
        if self.trim != "all":
            args += [f"--{self.trim}", str(self.count)]
        if self.timezone:
            args += ["--timezone", shlex.quote(self.timezone)]
        if self.user_name:
            args += ["--user-name", shlex.quote(self.user_name)]
        if self.assistant_name:
            args += ["--assistant-name", shlex.quote(self.assistant_name)]
        top = config.checkout_dir()
        run = f'python3 {shlex.quote(os.path.join(top, "cc-prune"))}' if top else "cc-prune"
        return f"F={shlex.quote(path)} && {run} " + " ".join(args)


class ExportPanel(QWidget):
    changed = Signal()
    exportClicked = Signal()

    def __init__(self, settings: ExportSettings):
        super().__init__()
        self._s = settings
        self.env: dict[str, str] = {}
        self.folders = config.Folders([], None)
        self.other_dir = ""
        self.auto_name = ""
        self.config_error = ""

        # ---- where
        where = QGroupBox("Folder")
        self.folder = QComboBox()
        self.browse = QPushButton("Other…")
        self.browse.setToolTip("Export into a folder that has no name")
        self.save_folder = QPushButton("Save as named…")
        self.save_folder.setToolTip("Add this folder to the named folders in .env")
        self.make_default = QPushButton("Make default")
        self.folder_path = muted("")
        self.env_note = muted("")
        self.env_note.setTextInteractionFlags(self.env_note.textInteractionFlags())
        self.name = QLineEdit()
        self.name.setPlaceholderText("automatic")
        self.name.setToolTip("Leave empty for <date>-<title>.md")
        wl = QVBoxLayout(where)
        wl.addWidget(self.folder)
        wl.addWidget(self.folder_path)
        row = QHBoxLayout()
        row.addWidget(self.browse)
        row.addWidget(self.save_folder)
        row.addWidget(self.make_default)
        wl.addLayout(row)
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.addRow("File name", self.name)
        wl.addLayout(form)
        wl.addWidget(self.env_note)

        # ---- what
        what = QGroupBox("Content")
        self.raw = QCheckBox("Tool calls, results and thinking")
        self.raw.setToolTip("Off: only the conversation text. On: tool calls and results "
                            "(cut to 2,000 chars each), thinking, and image markers.")
        self.before = QCheckBox("History before compactions")
        self.before.setToolTip("Off: start at the latest compaction, what Claude works from now.\n"
                               "On: the whole conversation from the first message, each "
                               "compaction marked.")
        self.summary = QCheckBox("Compaction summary")
        self.summary.setToolTip("Include the summary each compaction wrote. Turning the history "
                                "on unchecks this and turning it off checks it; it is never "
                                "saved, so choose each time.")
        self.summary.setStyleSheet("margin-left: 22px;")
        self.trim_all = QRadioButton("All messages")
        self.trim_head = QRadioButton("First")
        self.trim_tail = QRadioButton("Last")
        self.count = QSpinBox()
        self.count.setRange(1, 1_000_000)
        self.count.setFixedWidth(84)
        trim = QHBoxLayout()
        trim.addWidget(self.trim_all)
        trim.addWidget(self.trim_head)
        trim.addWidget(self.trim_tail)
        trim.addWidget(self.count)
        trim.addStretch(1)
        ql = QVBoxLayout(what)
        ql.addWidget(self.raw)
        ql.addWidget(self.before)
        ql.addWidget(self.summary)
        ql.addLayout(trim)

        # ---- how
        how = QGroupBox("Labels and time")
        self.user_name = QLineEdit()
        self.assistant_name = QLineEdit()
        self.timezone = QLineEdit()
        self.timezone.setToolTip("local, utc, or an offset such as +2 or -5.5")
        hl = QFormLayout(how)
        hl.addRow("You", self.user_name)
        hl.addRow("Claude", self.assistant_name)
        hl.addRow("Time zone", self.timezone)

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 4, 0)
        for w in (where, what, how):
            col.addWidget(w)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)

        self.command = QPlainTextEdit()
        self.command.setReadOnly(True)
        self.command.setFixedHeight(70)
        mono = self.command.font()
        mono.setFamily("monospace")
        mono.setPointSizeF(max(mono.pointSizeF() - 1.5, 7))
        self.command.setFont(mono)
        self.copy = QPushButton("Copy command")
        self.status = muted("")
        self.export = QPushButton("Export")
        self.export.setObjectName("apply")
        self.open_folder = QPushButton("Open folder")
        self.open_folder.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.export, 1)
        buttons.addWidget(self.open_folder)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll, 1)
        lay.addWidget(muted("The same export as a command:"))
        lay.addWidget(self.command)
        lay.addWidget(self.copy)
        lay.addWidget(self.status)
        lay.addLayout(buttons)

        self.reload_config()
        self._load()
        self.before.toggled.connect(lambda on: self.summary.setChecked(not on))
        for sig in (self.raw.toggled, self.before.toggled, self.summary.toggled, self.trim_head.toggled,
                    self.trim_tail.toggled, self.trim_all.toggled, self.count.valueChanged,
                    self.user_name.textChanged, self.assistant_name.textChanged,
                    self.timezone.textChanged, self.name.textChanged):
            sig.connect(self._edited)
        self.folder.currentIndexChanged.connect(self._folder_changed)
        self.browse.clicked.connect(self._browse)
        self.save_folder.clicked.connect(self._save_named)
        self.make_default.clicked.connect(self._make_default)
        self.copy.clicked.connect(self._copy)
        self.export.clicked.connect(self.exportClicked)

    # ---- configuration

    def reload_config(self) -> None:
        """Read .env again and refill the folder list."""
        self.config_error = ""
        try:
            self.env = config.read_env()
            self.folders = config.load_folders(self.env)
        except PruneError as exc:
            self.env, self.folders = {}, config.Folders([], None)
            self.config_error = str(exc)
        path = config.env_path()
        here = path.replace(os.path.expanduser("~"), "~", 1)
        if self.config_error:
            self.env_note.setText(f"<span style='color:#e5605e'>{self.config_error}</span>")
        elif not self.folders.folders:
            self.env_note.setText(f"No named folders yet. Pick one with “Other…” and "
                                  f"save it, or set {config.DIRS} in {here}.")
        else:
            self.env_note.setText(f"Named folders live in {here}.")
        self._fill_folders()
        self._placeholders()

    def _placeholders(self) -> None:
        self.user_name.setPlaceholderText(self.env.get(config.USER_NAME) or DEFAULT_USER_NAME)
        self.assistant_name.setPlaceholderText(self.env.get(config.ASSISTANT_NAME)
                                               or DEFAULT_ASSISTANT_NAME)
        self.timezone.setPlaceholderText(self.env.get(config.TIMEZONE) or "local")

    def _fill_folders(self, select: str | None = None) -> None:
        want = select or self._s.folder or self.folders.default
        self.folder.blockSignals(True)
        self.folder.clear()
        for f in self.folders.folders:
            label = f.name + ("  (default)" if f.name == self.folders.default else "")
            self.folder.addItem(label, f.name)
        if self.other_dir:
            self.folder.addItem(f"Other: {self.other_dir}", OTHER)
        idx = self.folder.findData(OTHER if select == OTHER else want)
        if idx < 0 and self.folders.default:
            idx = self.folder.findData(self.folders.default)
        self.folder.setCurrentIndex(max(idx, 0))
        self.folder.blockSignals(False)
        self._folder_changed()

    def _load(self) -> None:
        s = self._s
        self.raw.setChecked(s.raw)
        self.before.setChecked(s.before_compaction)
        self.summary.setChecked(not s.before_compaction)
        {"all": self.trim_all, "head": self.trim_head, "tail": self.trim_tail}[s.trim].setChecked(True)
        self.count.setValue(s.count)
        self.count.setEnabled(s.trim != "all")
        self.user_name.setText(s.user_name)
        self.assistant_name.setText(s.assistant_name)
        self.timezone.setText(s.timezone)

    # ---- values

    def settings(self) -> ExportSettings:
        return self._s

    def options(self) -> ExportOptions:
        return self._s.options(self.env, self.summary.isChecked())

    def named(self) -> str | None:
        data = self.folder.currentData()
        return None if data in (None, OTHER) else data

    def directory(self) -> str:
        """The folder the file goes into, expanded; '' when none is chosen."""
        if self.folder.currentData() == OTHER:
            return self.other_dir
        f = self.folders.get(self.named()) if self.named() else None
        return f.expanded if f else ""

    def file_name(self) -> str:
        name = self.name.text().strip()
        if not name:
            return self.auto_name
        return name if name.endswith(".md") else name + ".md"

    def target(self) -> str:
        d = self.directory()
        return os.path.join(d, self.file_name()) if d and self.file_name() else ""

    def set_auto_name(self, name: str) -> None:
        self.auto_name = name
        self.name.setPlaceholderText(name or "automatic")

    def _edited(self, *_) -> None:
        trim = "head" if self.trim_head.isChecked() else "tail" if self.trim_tail.isChecked() else "all"
        self.count.setEnabled(trim != "all")
        self._s = replace(self._s, raw=self.raw.isChecked(),
                          before_compaction=self.before.isChecked(), trim=trim,
                          count=self.count.value(), user_name=self.user_name.text().strip(),
                          assistant_name=self.assistant_name.text().strip(),
                          timezone=self.timezone.text().strip())
        self.changed.emit()

    def _folder_changed(self, *_) -> None:
        named = self.named()
        if named:
            self._s = replace(self._s, folder=named)
        d = self.directory()
        here = d.replace(os.path.expanduser("~"), "~", 1) if d else ""
        missing = d and not os.path.isdir(d)
        self.folder_path.setText((here + ("  (will be created)" if missing else "")) if d
                                 else "No folder chosen.")
        self.save_folder.setEnabled(self.folder.currentData() == OTHER)
        self.make_default.setEnabled(bool(named) and named != self.folders.default)
        self.changed.emit()

    def _browse(self) -> None:
        start = self.directory() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Export folder", start)
        if chosen:
            self.other_dir = chosen
            self._fill_folders(select=OTHER)

    def _save_named(self) -> None:
        if not self.other_dir:
            return
        name, ok = QInputDialog.getText(self, "Save folder",
                                        "A name for this folder:",
                                        text=os.path.basename(self.other_dir.rstrip(os.sep)))
        name = name.strip()
        if not ok or not name:
            return
        if any(f.name == name for f in self.folders.folders):
            QMessageBox.warning(self, "Save folder", f"There is already a folder named {name!r}.")
            return
        home = os.path.expanduser("~")
        path = "~" + self.other_dir[len(home):] if self.other_dir.startswith(home + os.sep) \
            else self.other_dir
        self.save_folders(config.Folders(self.folders.folders + [config.Folder(name, path)],
                                         self.folders.default or name))
        self.other_dir = ""
        self._s = replace(self._s, folder=name)
        self.reload_config()

    def _make_default(self) -> None:
        if self.named():
            self.save_folders(config.Folders(self.folders.folders, self.named()))
            self.reload_config()

    def save_folders(self, folders: config.Folders) -> None:
        try:
            config.save_folders(folders)
        except (OSError, PruneError) as exc:
            QMessageBox.warning(self, "Save folder", f"Could not write {config.env_path()}: {exc}")

    # ---- command and button

    def set_command(self, text: str) -> None:
        self.command.setPlainText(text)
        self.copy.setEnabled(bool(text))

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.command.toPlainText())
        self.copy.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy.setText("Copy command"))

    def set_export(self, enabled: bool, text: str = "Export", status: str = "") -> None:
        self.export.setEnabled(enabled)
        self.export.setText(text)
        self.status.setText(status)
