"""The GStreamer reader: whole frames from a pipe that hands over pieces, and pipelines it can slice."""

# ruff: noqa: SLF001 -- the reader's steps are private helpers, so the tests call them

from __future__ import annotations

import io
import itertools
import re
import subprocess  # nosec B404

import pytest

from orchestrant.pipeline.capture import gstreamer
from orchestrant.pipeline.capture.gstreamer import GStreamerSubprocessCapture
from orchestrant.pipeline.types import CameraConfig


WIDTH, HEIGHT = 64, 64
FRAME = WIDTH * HEIGHT * 3


class _Dribble(io.RawIOBase):
    """A pipe: every read hands over at most ``chunk`` bytes, as the kernel does with a frame."""

    def __init__(self, data: bytes, chunk: int) -> None:
        self._data = data
        self._chunk = chunk
        self._at = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        view = memoryview(buffer).cast("B")
        piece = self._data[self._at : self._at + min(self._chunk, len(view))]
        view[: len(piece)] = piece
        self._at += len(piece)
        return len(piece)


class _ReadOnly:
    """A stream without readinto, whose read is as short as a pipe's."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self, size: int) -> bytes:
        return self._stream.read(min(size, 5))


class _Process:
    def __init__(self, stdout: object, status: int | None = None) -> None:
        self.stdout = stdout
        self._status = status

    def poll(self) -> int | None:
        return self._status

    def terminate(self) -> None:
        self._status = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


def _capture() -> GStreamerSubprocessCapture:
    cap = GStreamerSubprocessCapture(CameraConfig(width=WIDTH, height=HEIGHT))
    cap.frame_size = FRAME
    return cap


def test_frames_arriving_in_pieces_are_read_whole_and_stay_aligned() -> None:
    """A read shorter than a frame is the normal case on a pipe, not a lost frame."""
    cap = _capture()
    frames = [
        bytes(i % 251 for i in range(FRAME)),
        bytes(i % 7 for i in range(FRAME)),
        bytes([7]) * FRAME,
    ]
    cap.process = _Process(_Dribble(b"".join(frames), 1000))
    for expected in frames:
        frame, ended = cap._read_one_frame(FRAME)
        assert not ended
        assert frame is not None
        assert frame.shape == (HEIGHT, WIDTH, 3)
        assert frame.tobytes() == expected
        # The first frame is read before open() allocates the reusable buffer, the rest into it.
        cap._allocate_frame_buffer()
    assert cap._read_one_frame(FRAME) == (None, True)


def test_a_pipe_closing_inside_a_frame_ends_the_stream() -> None:
    """Ten bytes of a second frame are an end, never a frame."""
    cap = _capture()
    cap.process = _Process(_Dribble(bytes(FRAME + 10), 4096))
    frame, ended = cap._read_one_frame(FRAME)
    assert frame is not None
    assert not ended
    assert cap._read_one_frame(FRAME) == (None, True)


def test_a_stream_without_readinto_is_read_whole_too() -> None:
    """The read() fallback loops like readinto does."""
    cap = _capture()
    expected = bytes(i % 251 for i in range(FRAME))
    cap.process = _Process(_ReadOnly(expected))
    frame, ended = cap._read_one_frame(FRAME)
    assert not ended
    assert frame is not None
    assert frame.tobytes() == expected


@pytest.mark.parametrize(
    ("pipeline_type", "include_size", "include_fps", "include_format"),
    list(
        itertools.product(
            ("strict", "flexible", "auto"), (True, False), (True, False), (True, False)
        )
    ),
)
def test_every_pipeline_ends_in_the_frames_the_reader_slices(
    *, pipeline_type: str, include_size: bool, include_fps: bool, include_format: bool
) -> None:
    """Whatever the source side relaxes, stdout carries BGR at the reader's width and height."""
    pipeline = _capture()._build_pipeline_string(
        "autovideosrc",
        640,
        480,
        30,
        pipeline_type,
        with_device=False,
        include_size=include_size,
        include_fps=include_fps,
        include_format=include_format,
    )
    tail = (
        r"videoconvert ! videoscale ! "
        r"video/x-raw,format=BGR,width=640,height=480(,framerate=30/1)? ! "
        r"fdsink fd=1 sync=false$"
    )
    assert re.search(tail, pipeline), pipeline
    assert pipeline.endswith("framerate=30/1 ! fdsink fd=1 sync=false") == include_fps


def test_gst_launch_writes_stderr_to_a_file_and_a_failed_start_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stderr pipe nobody drains stops gst-launch once it fills; a file never does."""
    seen: dict[str, object] = {}

    def popen(cmd: list[str], **kwargs: object) -> _Process:
        del cmd
        stderr = kwargs["stderr"]
        assert stderr is not subprocess.PIPE
        assert isinstance(stderr, io.IOBase)
        stderr.write(b"no element mfvideosrc\n" * 50)
        stderr.flush()
        seen.update(kwargs)
        return _Process(io.BytesIO(), status=1)

    monkeypatch.setattr(gstreamer.subprocess, "Popen", popen)
    monkeypatch.setattr(gstreamer.time, "sleep", lambda _seconds: None)
    cap = _capture()
    cap.gst_launch_path = "gst-launch-1.0"
    texts: list[str] = []
    stderr_text = cap._stderr_text
    monkeypatch.setattr(cap, "_stderr_text", lambda: texts.append(stderr_text()) or "")

    assert not cap._start_pipeline("mfvideosrc ! fdsink fd=1")
    assert texts == [("no element mfvideosrc\n" * 50)[:300]]
    stderr_log = seen["stderr"]
    assert isinstance(stderr_log, io.IOBase)
    assert stderr_log.closed
    assert cap._stderr_log is None
