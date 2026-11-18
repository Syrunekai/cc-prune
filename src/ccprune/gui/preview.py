"""The middle of the window: what the edit will do, and what it did."""
from __future__ import annotations

import html
import os
import time

from PySide6.QtWidgets import (QLabel, QPlainTextEdit, QStackedWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..calibration import Outcome
from ..prune import Plan
from . import fmt, theme
from .sessions import SessionRow


def esc(text) -> str:
    return html.escape(str(text))


def line(label: str, text: str, color: str | None = None) -> str:
    value = f'<span style="color:{color}">{text}</span>' if color else text
    return f'<tr><td style="color:{theme.MUTED}; padding-right:14px; white-space:nowrap">{label}</td><td>{value}</td></tr>'


class PreviewPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.title = QLabel()
        self.title.setStyleSheet("font-size:15px; font-weight:600")
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        self.big = QLabel()
        self.big.setObjectName("big")
        self.confidence = QLabel()
        self.confidence.setWordWrap(True)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.markdown = QPlainTextEdit()
        self.markdown.setReadOnly(True)
        mono = self.markdown.font()
        mono.setFamily("monospace")
        self.markdown.setFont(mono)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.details)
        self.pages.addWidget(self.markdown)
        self.report_title = QLabel("Report")
        self.report_title.setObjectName("muted")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(150)
        mono = self.report.font()
        mono.setFamily("monospace")
        self.report.setFont(mono)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        for w in (self.title, self.sub, self.big, self.confidence):
            lay.addWidget(w)
        lay.addWidget(self.pages, 1)
        lay.addWidget(self.report_title)
        lay.addWidget(self.report)
        self.show_message("Select a session on the left.")

    def show_message(self, text: str, row: SessionRow | None = None) -> None:
        self.pages.setCurrentWidget(self.details)
        self.big.setStyleSheet("")
        self._header(row)
        self.big.setText("")
        self.confidence.setText("")
        self.details.setHtml(f'<p style="color:{theme.MUTED}">{esc(text)}</p>')

    def _header(self, row: SessionRow | None) -> None:
        if row is None:
            self.title.setText("cc-prune")
            self.sub.setText("")
            return
        self.title.setText(row.title)
        bits = [row.project, fmt.size(row.size), f"recorded {fmt.tokens(row.context)} tokens",
                f"last used {fmt.when(row.last)}"]
        if row.snaps:
            bits.append(f"{row.snaps} snapshot(s), {fmt.size(row.snap_bytes)}")
        self.sub.setText("  ·  ".join(b for b in bits if b))

    def show_plan(self, row: SessionRow, plan: Plan, history: list[Outcome]) -> None:
        self.pages.setCurrentWidget(self.details)
        self._header(row)
        if plan.estimate:
            self.big.setText(f"~{fmt.tokens(plan.estimate)} tokens")
            self.big.setStyleSheet("")
            if plan.estimate_anchored:
                self.confidence.setText(
                    f'<span style="color:{theme.GOOD}">Anchored</span> on real request counts. '
                    f'Across 36 real before/after pairs this method came out between -7% and +4%.')
            else:
                self.confidence.setText(
                    f'<span style="color:{theme.WARN}">Chained</span>: no real request since the '
                    f'last edit, so this rests on an earlier estimate and can be well off. Send one '
                    f'message and read the real count before cutting again.')
        else:
            self.big.setText("no estimate")
            self.confidence.setText("The file records no usage to estimate from." if not plan.last_context
                                    else "This edit does not change the recorded context.")

        rows = []
        if plan.cut:
            c = plan.cut
            n = len(c.turns)
            rows.append(line("Cut", f"turns <b>{c.a + 1} to {c.b}</b> of {n}: {c.cut_records} records, "
                                    f"{c.cut_chars:,} chars ({c.cut_chars / max(c.total_chars, 1):.0%} of the conversation)"))
            rows.append(line("Keep", f"turns 1 to {c.a} and {c.b + 1} to {n}"))
            if c.moved_from:
                rows.append(line("", f"asked for {c.moved_from[0]} to {c.moved_from[1]}; moved to the "
                                     f"nearest turns where a cut is possible", theme.WARN))
            if c.earlier:
                rows.append(line("", f"plus {c.earlier} turns earlier cuts already removed "
                                     f"(the note will say {c.b - c.a + c.earlier})", theme.MUTED))
            if c.carried:
                rows.append(line("Carried", ", ".join(f"{k} x{v}" for k, v in c.carried.items())))
            rows.append(line("Mode", "purge: the cut records are deleted from the file" if c.purge
                             else "branch: the cut records stay in the file, unreachable"))
        if plan.clear:
            o = plan.clear
            if o.freed:
                rows.append(line("Clear", "<br>".join(
                    f"{k}: {o.hits[k]} values, {o.freed[k] / 1e6:.2f} MB" for k in o.freed)))
            else:
                rows.append(line("Clear", "nothing matches these thresholds", theme.MUTED))
            rows.append(line("Protected", f"the last {o.protected} of {o.total} records"))
        rows.append(line("Records", f"{len(plan.orig):,} now, {len(plan.new):,} after"))
        if plan.is_noop:
            rows.append(line("Result", "nothing would change; no snapshot is taken and the file "
                                       "is not written", theme.WARN))
        else:
            rows.append(line("Snapshot", f"one is taken first: “{esc(plan.label)}”"))
        for s in plan.will_drop:
            rows.append(line("", f"will delete snap{s.n} ({fmt.size(s.size)}"
                                 f"{', ' + esc(s.label) if s.label else ''}) over the snapshot limit",
                             theme.BAD))
        if history:
            rows.append(line("Estimator", "<br>".join(
                f"{esc(o.label)}: estimated {fmt.tokens(o.estimate)}, real {fmt.tokens(o.real)} "
                f'<span style="color:{theme.GOOD if abs(o.error) < 0.1 else theme.WARN}">'
                f"({o.error:+.0%})</span>" for o in history[-4:])))
        self.details.setHtml(f"<table cellspacing='4'>{''.join(rows)}</table>")

    def show_export(self, row: SessionRow, shown: int, total: int, note: str, compactions: int,
                    target: str, exists: bool, text: str, live: bool,
                    history: bool = False, summary: bool = False) -> None:
        self._header(row)
        self.pages.setCurrentWidget(self.markdown)
        self.big.setText(f"{shown:,} messages" + (f" of {total:,}" if shown != total else ""))
        self.big.setStyleSheet("")
        bits = []
        if target:
            home = os.path.expanduser("~")
            where = target.replace(home, "~", 1)
            bits.append(f"→ {esc(where)}" + (f' <span style="color:{theme.WARN}">(exists, '
                                              f'will ask before overwriting)</span>' if exists else ""))
        else:
            bits.append(f'<span style="color:{theme.WARN}">Choose a folder in the Export tab.</span>')
        if compactions:
            bits.append(f"{compactions} compaction(s) in this session: "
                        + (("the whole history, compactions marked"
                            + (", with their summaries" if summary else "")) if history
                           else "starting at the latest" + (", with its summary" if summary else "")))
        if live:
            bits.append(f'<span style="color:{theme.MUTED}">The session is live: the export holds '
                        f'the file as it is now.</span>')
        self.confidence.setText("<br>".join(bits))
        self.markdown.setPlainText(text)

    def show_report(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.report.appendPlainText(f"[{stamp}] {text}")
