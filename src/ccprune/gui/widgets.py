"""Controls: a stepped slider with an editable box, and a two-tip range slider."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QSizePolicy,
                               QSlider, QStyle, QStyledItemDelegate, QWidget)

from . import theme


class SliderBox(QWidget):
    """A slider that steps through lo..hi by `step`, beside a box that takes
    any value between box_lo and box_hi."""

    valueChanged = Signal(float)

    def __init__(self, lo: float = 0.05, hi: float = 0.95, step: float = 0.05,
                 box_lo: float | None = None, box_hi: float | None = None, parent=None):
        super().__init__(parent)
        self.lo, self.step = lo, step
        steps = round((hi - lo) / step)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, steps)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(2)
        self.slider.setPageStep(1)
        self.box = QDoubleSpinBox()
        self.box.setDecimals(2)
        self.box.setSingleStep(step)
        self.box.setRange(lo if box_lo is None else box_lo, hi if box_hi is None else box_hi)
        self.box.setFixedWidth(64)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.slider, 1)
        row.addWidget(self.box)
        self.slider.valueChanged.connect(self._from_slider)
        self.box.valueChanged.connect(self._from_box)
        self._value = self.box.value()

    def value(self) -> float:
        return self._value

    def setValue(self, x: float) -> None:
        self._set(x, emit=False)

    def _set(self, x: float, emit: bool) -> None:
        x = round(min(max(x, self.box.minimum()), self.box.maximum()), 2)
        self._value = x
        self.box.blockSignals(True)
        self.box.setValue(x)
        self.box.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setValue(min(max(round((x - self.lo) / self.step), 0), self.slider.maximum()))
        self.slider.blockSignals(False)
        if emit:
            self.valueChanged.emit(x)

    def _from_slider(self, idx: int) -> None:
        self._set(self.lo + idx * self.step, emit=True)

    def _from_box(self, x: float) -> None:
        self._set(x, emit=True)


class RangeSlider(QWidget):
    """Two tips on one track over the integers minimum..maximum. `rangeChanged`
    fires while a tip is dragged; `rangeReleased` when it is let go."""

    rangeChanged = Signal(int, int)
    rangeReleased = Signal(int, int)

    R = 8   # tip radius

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min, self._max = 1, 10
        self._low, self._high = 1, 10
        self._drag: str | None = None
        self._last = "low"
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        return QSize(300, 54)

    def bounds(self) -> tuple[int, int]:
        return self._min, self._max

    def range(self) -> tuple[int, int]:
        return self._low, self._high

    def setBounds(self, lo: int, hi: int) -> None:
        self._min, self._max = lo, max(hi, lo)
        self._low = min(max(self._low, self._min), self._max)
        self._high = min(max(self._high, self._low), self._max)
        self.update()

    def setRange(self, low: int, high: int) -> None:
        self._low = min(max(low, self._min), self._max)
        self._high = min(max(high, self._low), self._max)
        self.update()

    def _track(self) -> tuple[float, float, float]:
        left, right = self.R + 4, self.width() - self.R - 4
        return left, right, self.height() * 0.58

    def _x(self, value: int) -> float:
        left, right, _ = self._track()
        span = max(self._max - self._min, 1)
        return left + (right - left) * (value - self._min) / span

    def _value_at(self, x: float) -> int:
        left, right, _ = self._track()
        span = max(self._max - self._min, 1)
        frac = (x - left) / max(right - left, 1)
        return min(max(round(self._min + frac * span), self._min), self._max)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, y = self._track()
        on = self.isEnabled()
        muted = QColor(theme.MUTED if on else "#4a4f57")
        accent = QColor(theme.ACCENT if on else "#3a4a5e")

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#343840"))
        p.drawRoundedRect(QRectF(left, y - 3, right - left, 6), 3, 3)
        xl, xh = self._x(self._low), self._x(self._high)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(xl, y - 3, max(xh - xl, 0), 6), 3, 3)

        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 1, 7))
        p.setFont(small)
        p.setPen(QPen(muted))
        p.drawText(QRectF(left - self.R, y + 12, 40, 16), Qt.AlignmentFlag.AlignLeft, str(self._min))
        p.drawText(QRectF(right - 40 + self.R, y + 12, 40, 16), Qt.AlignmentFlag.AlignRight,
                   str(self._max))

        for name, value in (("low", self._low), ("high", self._high)):
            x = self._x(value)
            focused = self.hasFocus() and self._last == name
            p.setPen(QPen(QColor("#ffffff") if focused else QColor("#0f1113"), 2))
            p.setBrush(accent if on else QColor("#4a4f57"))
            p.drawEllipse(QPointF(x, y), self.R, self.R)
            p.setPen(QPen(QColor(theme.TEXT) if on else muted))
            p.drawText(QRectF(x - 20, y - self.R - 20, 40, 16), Qt.AlignmentFlag.AlignCenter, str(value))

    def _nearest(self, x: float) -> str:
        dl, dh = abs(x - self._x(self._low)), abs(x - self._x(self._high))
        if dl == dh:
            return "low" if x < self._x(self._low) else "high"
        return "low" if dl < dh else "high"

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        self._drag = self._last = self._nearest(event.position().x())
        self.setFocus()
        self._move(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._drag:
            self._move(event.position().x())

    def mouseReleaseEvent(self, _event) -> None:
        if self._drag:
            self._drag = None
            self.rangeReleased.emit(self._low, self._high)
            self.update()

    def _move(self, x: float) -> None:
        value = self._value_at(x)
        if self._drag == "low":
            self._low = min(value, self._high)
        else:
            self._high = max(value, self._low)
        self.rangeChanged.emit(self._low, self._high)
        self.update()

    def keyPressEvent(self, event) -> None:
        delta = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(event.key())
        if delta is None or not self.isEnabled():
            return super().keyPressEvent(event)
        if self._last == "low":
            self._low = min(max(self._low + delta, self._min), self._high)
        else:
            self._high = max(min(self._high + delta, self._max), self._low)
        self.rangeChanged.emit(self._low, self._high)
        self.rangeReleased.emit(self._low, self._high)
        self.update()


class RowDelegate(QStyledItemDelegate):
    """Paints hover and selection itself: a lighter row on hover, a muted blue
    row with a bar on its left edge when selected. The text keeps its own
    color in every state."""

    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self.hover_row = -1

    def paint(self, painter: QPainter, option, index) -> None:
        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.hover_row
        if selected or hovered:
            painter.fillRect(option.rect, QColor(theme.SELECTED if selected else theme.HOVER))
        if selected and index.column() == 0:
            painter.fillRect(option.rect.x(), option.rect.y(), 3, option.rect.height(),
                             QColor(theme.ACCENT))
        option.state = state & ~QStyle.StateFlag.State_Selected & ~QStyle.StateFlag.State_MouseOver \
            & ~QStyle.StateFlag.State_HasFocus
        if index.data(Qt.ItemDataRole.ForegroundRole) is None:
            option.palette.setColor(QPalette.ColorRole.Text, QColor(theme.TEXT))
        super().paint(painter, option, index)


class _HoverTracker(QObject):
    """Tells a RowDelegate which row the pointer is over."""

    def __init__(self, view: QAbstractItemView, delegate: RowDelegate):
        super().__init__(view)
        self.view, self.delegate = view, delegate

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            row = self.view.indexAt(event.position().toPoint()).row()
        elif kind in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            row = -1
        else:
            return False
        if row != self.delegate.hover_row:
            self.delegate.hover_row = row
            self.view.viewport().update()
        return False


def style_rows(view: QAbstractItemView) -> None:
    """Hover and selection drawn by RowDelegate, readable in every state."""
    delegate = RowDelegate(view)
    view.setItemDelegate(delegate)
    view.setMouseTracking(True)
    view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
    view.viewport().installEventFilter(_HoverTracker(view, delegate))
