"""Shared non-modal progress/cancel channel for Blender services and Qt UI."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    kind: str
    task_id: str
    title: str
    label: str
    value: int
    cancellable: bool
    cancelled: bool = False


class ProgressCancelled(RuntimeError):
    pass


_listener = None
_active = {}


def set_listener(listener):
    global _listener
    _listener = listener


def cancel_task(task_id):
    reporter = _active.get(str(task_id or ""))
    if reporter is not None:
        reporter.cancel()
        return True
    return False


class ProgressReporter:
    def __init__(self, title, label="", cancellable=True):
        self.task_id = uuid4().hex
        self.title = str(title)
        self.label = str(label or title)
        self.value = 0
        self.cancellable = bool(cancellable)
        self.cancelled = False
        self.closed = False

    def _emit(self, kind):
        if _listener is not None:
            _listener(ProgressEvent(
                kind, self.task_id, self.title, self.label,
                max(0, min(int(self.value), 100)), self.cancellable, self.cancelled,
            ))

    def begin(self):
        _active[self.task_id] = self
        self._emit("BEGIN")
        return self

    def update(self, value, label=None):
        if label is not None:
            self.label = str(label)
        self.value = max(0, min(int(value), 100))
        self._emit("UPDATE")
        self.check_cancelled()

    def cancel(self):
        if self.cancellable:
            self.cancelled = True

    def check_cancelled(self):
        if self.cancelled:
            raise ProgressCancelled("{} canceled".format(self.title))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.value = 100 if not self.cancelled else self.value
        self._emit("END")
        _active.pop(self.task_id, None)


@contextmanager
def progress_scope(title, label="", cancellable=True):
    reporter = ProgressReporter(title, label, cancellable).begin()
    try:
        yield reporter
    finally:
        reporter.close()
