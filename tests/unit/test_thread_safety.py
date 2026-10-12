"""The shared state the Cython modules keep, driven from many threads at once.

Every leg runs this; the 3.14t leg and the compiled cp314t wheel run it with the GIL off.
ORCHESTRANT_REQUIRE_COMPILED=1 (scripts/linux/ci_packaging.sh's wheel step) also requires
each module under test to be the wheel's extension, not the .py source.
"""

# ruff: noqa: SLF001 -- the races live in private helpers, so the tests call them

from __future__ import annotations

import collections
import importlib
import importlib.machinery
import io
import os
import pkgutil
import sys
import sysconfig
import threading
import time
import warnings
from queue import Queue
from typing import TYPE_CHECKING

import numpy as np
import pytest

import orchestrant
from orchestrant.benchmark import gateway, openai_api
from orchestrant.monitoring import gpu as gpu_module
from orchestrant.pipeline.capture.gstreamer import GStreamerSubprocessCapture
from orchestrant.pipeline.logging import log_buffer_lines
from orchestrant.pipeline.types import CameraConfig


if TYPE_CHECKING:
    from collections.abc import Callable


THREADS = 8
FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
UNDER_TEST = (
    gateway,
    openai_api,
    sys.modules[GStreamerSubprocessCapture.__module__],
    sys.modules[log_buffer_lines.__module__],
)


def _together(target: Callable[[], object]) -> None:
    """Run target on THREADS threads released at once; re-raise the first failure."""
    barrier = threading.Barrier(THREADS)
    errors: list[BaseException] = []

    def run() -> None:
        barrier.wait()
        try:
            target()
        except BaseException as exc:  # reported to the test thread below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


def test_the_modules_under_test_are_compiled_when_required() -> None:
    """The wheel run must exercise the extensions, never the .py the checkout holds."""
    if os.environ.get("ORCHESTRANT_REQUIRE_COMPILED") != "1":
        pytest.skip("a source run; the wheel run sets ORCHESTRANT_REQUIRE_COMPILED=1")
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    sources = [m.__name__ for m in UNDER_TEST if not str(m.__file__).endswith(suffixes)]
    assert not sources, f"imported from source, not the compiled wheel: {sources}"


def test_every_gateway_reply_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process-wide reply tally loses no count to concurrent replies."""
    monkeypatch.setattr(gateway, "_SERVED", collections.Counter())
    per_thread = 4000
    headers = {"X-Gw-Lane": "npu"}

    def reply() -> None:
        for _ in range(per_thread):
            gateway.note_reply("http://127.0.0.1:9080/v1", {"model": "chat"}, headers)

    _together(reply)
    replies = gateway.served("http://127.0.0.1:9080")[0]["replies"]
    assert replies == THREADS * per_thread


def test_concurrent_first_callers_share_one_gpu_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Racing first callers open exactly one GPU probe and all get it."""
    opened: list[object] = []

    class SlowProbe:
        available = True

        def __init__(self) -> None:
            time.sleep(0.05)  # a driver context takes its time; the race window
            opened.append(self)

    monkeypatch.setattr(gpu_module, "GPUProbe", SlowProbe)
    monkeypatch.setattr(openai_api, "_gpu_probe", None)
    seen: list[object] = []
    _together(lambda: seen.append(openai_api.gpu_probe()))
    assert len(opened) == 1
    assert all(probe is opened[0] for probe in seen)


def _capture(width: int = 64, height: int = 64) -> GStreamerSubprocessCapture:
    cap = GStreamerSubprocessCapture(CameraConfig(width=width, height=height))
    cap.frame_size = width * height * 3
    return cap


class _FakeProcess:
    """A gst-launch stand-in: stdout serves frames until terminate()."""

    def __init__(self, stdout: io.RawIOBase | io.BytesIO) -> None:
        self.stdout = stdout
        self.stderr = None
        self.ended = False

    def poll(self) -> int | None:
        status = 0 if self.ended else None
        time.sleep(
            0.002
        )  # release() lands between this answer and the caller's next step
        return status

    def terminate(self) -> None:
        self.ended = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.ended = True


class _EndlessFrames(io.RawIOBase):
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        view = memoryview(buffer).cast("B")
        view[:] = b"\x07" * len(view)
        return len(view)


def test_a_queued_frame_is_not_overwritten_by_the_next_read() -> None:
    """A frame read into the reusable buffer survives the read after it."""
    cap = _capture()
    first, second = bytes([1]) * cap.frame_size, bytes([2]) * cap.frame_size
    cap.process = _FakeProcess(io.BytesIO(first + second))
    cap._allocate_frame_buffer()
    frame, _ = cap._read_one_frame(cap.frame_size)
    cap._read_one_frame(cap.frame_size)
    assert frame is not None
    assert frame.tobytes() == first


def test_release_while_the_reader_runs_raises_nothing_on_the_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release() on the caller's thread never crashes the running reader thread."""
    errors: list[BaseException | None] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: errors.append(args.exc_value)
    )
    for _ in range(50):
        cap = _capture()
        cap.process = _FakeProcess(_EndlessFrames())
        cap._allocate_frame_buffer()
        cap.running = True
        reader = threading.Thread(target=cap._frame_reader)
        reader.start()
        cap.release()
        reader.join(timeout=5)
        assert not reader.is_alive()
    assert not errors, errors[0]


def test_offering_frames_from_many_threads_never_blocks_or_overflows() -> None:
    """The drop-oldest offer neither raises Full nor grows past maxsize."""
    cap = _capture()
    cap.frame_queue = Queue(maxsize=2)
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    def offer() -> None:
        for _ in range(2000):
            cap._offer_frame(frame)

    _together(offer)
    assert cap.frame_queue.qsize() <= 2


def test_streams_sharing_one_capture_never_raise_on_an_empty_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two streams on one FrameCapture: the loser of a race gets None, not queue.Empty."""
    pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
    from orchestrant.streaming import (  # noqa: PLC0415 -- needs cv2
        capture as streaming_capture,
    )

    class Quiet:
        def __getattr__(self, _name: str) -> Callable[..., None]:
            return lambda *_a, **_k: None

    monkeypatch.setattr(streaming_capture, "logger", Quiet())
    cap = object.__new__(streaming_capture.FrameCapture)
    cap.frame_queue = Queue(maxsize=10)
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    def stream() -> None:
        for _ in range(2000):
            cap.frame_queue.put(frame)
            cap.get_frame()
            cap.get_frame()

    _together(stream)
    assert cap.frame_queue.qsize() <= THREADS


def test_the_log_buffer_reads_while_sinks_append() -> None:
    """Reading the log deque while another thread appends never raises."""
    buffer: collections.deque[str] = collections.deque(maxlen=200)
    stop = threading.Event()

    def append() -> None:
        n = 0
        while not stop.is_set():
            buffer.append(f"line {n}")
            n += 1

    writer = threading.Thread(target=append)
    writer.start()
    try:
        for _ in range(3000):
            assert len(log_buffer_lines(buffer)) <= 200
    finally:
        stop.set()
        writer.join()


@pytest.mark.skipif(not FREE_THREADED, reason="a GIL build has the GIL on by design")
def test_the_gil_stays_off_after_importing_every_module() -> None:
    """Every importable module (bodies run) leaves the free-threaded interpreter GIL-free."""
    imported = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for info in pkgutil.walk_packages(orchestrant.__path__, "orchestrant."):
            if info.name.endswith("__main__"):
                continue
            try:
                importlib.import_module(info.name)
            except ImportError:
                continue  # an optional dependency without a cp314t wheel (cv2, wx)
            imported.append(info.name)
    offenders = [
        str(w.message) for w in caught if "global interpreter lock" in str(w.message)
    ]
    assert len(imported) > 20, imported
    assert not offenders, offenders
    assert sys._is_gil_enabled() is False
