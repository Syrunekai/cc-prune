"""Run engine calls off the interface thread."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..errors import PruneError


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable, signals: _Signals):
        super().__init__()
        self.fn, self.signals = fn, signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn()
        except PruneError as exc:
            self.signals.failed.emit(str(exc))
        except SystemExit as exc:
            self.signals.failed.emit(str(exc))
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=4))
        else:
            self.signals.done.emit(result)


_alive: set[_Signals] = set()


def run_async(fn: Callable, on_done: Callable, on_failed: Callable | None = None) -> None:
    """Call `fn` on a worker thread; `on_done(result)` or `on_failed(message)`
    then runs on the interface thread."""
    signals = _Signals()
    _alive.add(signals)

    def finish(slot: Callable | None):
        def call(value):
            _alive.discard(signals)
            if slot:
                slot(value)
        return call

    signals.done.connect(finish(on_done))
    signals.failed.connect(finish(on_failed))
    QThreadPool.globalInstance().start(_Task(fn, signals))
