"""The newest value of a stream with its number, for any number of readers; no cv2, so the free-threaded leg drives it."""

from __future__ import annotations

import threading
from typing import Generic, TypeVar


_T = TypeVar("_T")


# A TypeVar, not class LatestSlot[T]: the package still installs on Python 3.11.
class LatestSlot(Generic[_T]):  # noqa: UP046
    """One slot, not a queue: a queue hands each value to one reader only, and the oldest first."""

    def __init__(self) -> None:
        """Empty and open; the first value gets number 1."""
        self._changed = threading.Condition()
        self._value: _T | None = None
        self._seq = 0
        self._closed = False

    def publish(self, value: _T) -> int:
        """Replace the value and wake every waiting reader; returns the value's number."""
        with self._changed:
            self._value = value
            self._seq += 1
            self._changed.notify_all()
            return self._seq

    def latest(self) -> _T | None:
        """The newest value, or None before the first one."""
        with self._changed:
            return self._value

    @property
    def published(self) -> int:
        """How many values were published, which is the newest one's number."""
        with self._changed:
            return self._seq

    @property
    def closed(self) -> bool:
        """Whether close() was called."""
        with self._changed:
            return self._closed

    def next(self, seen: int, timeout: float) -> tuple[int, _T | None]:
        """Wait up to `timeout` s for a value newer than number `seen`.

        Returns the newest value with its number, or `(seen, None)` when none came or the slot closed.
        """
        with self._changed:
            self._changed.wait_for(lambda: self._seq != seen or self._closed, timeout)
            if self._seq == seen:
                return seen, None
            return self._seq, self._value

    def close(self) -> None:
        """Wake every waiting reader for good; a value published later still counts."""
        with self._changed:
            self._closed = True
            self._changed.notify_all()
