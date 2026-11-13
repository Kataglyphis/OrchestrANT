"""The GStreamer capture: whole frames from a pipe that hands over pieces, and which pipelines it starts, in which order."""

# ruff: noqa: SLF001 -- the reader's steps are private helpers, so the tests call them

from __future__ import annotations

import io
import itertools
import re
import subprocess  # nosec B404
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from orchestrant.pipeline.capture import gstreamer
from orchestrant.pipeline.capture.gstreamer import GStreamerSubprocessCapture
from orchestrant.pipeline.types import CameraConfig


if TYPE_CHECKING:
    from typing import IO


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
    """gst-launch: ended with ``status``, or running when it is None."""

    def __init__(self, stdout: object, status: int | None = None) -> None:
        self.stdout = stdout
        self._status = status
        self.waits: list[float | None] = []

    def poll(self) -> int | None:
        return self._status

    def terminate(self) -> None:
        self._status = 0

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self._status is None:
            raise subprocess.TimeoutExpired(
                cmd="gst-launch-1.0", timeout=timeout or 0.0
            )
        return self._status


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


def _file(value: object) -> IO[bytes]:
    """The file a Popen argument holds; on Windows a TemporaryFile is a wrapper around one, not an io.IOBase."""
    assert value is not subprocess.PIPE
    assert hasattr(value, "fileno")
    return cast("IO[bytes]", value)


def test_gst_launch_writes_stderr_to_a_file_and_a_failed_start_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stderr pipe nobody drains stops gst-launch once it fills; a file never does."""
    seen: dict[str, object] = {}

    def popen(cmd: list[str], **kwargs: object) -> _Process:
        del cmd
        stderr = _file(kwargs["stderr"])
        stderr.write(b"no element mfvideosrc\n" * 50)
        stderr.flush()
        seen.update(kwargs)
        return _Process(io.BytesIO(), status=1)

    monkeypatch.setattr(gstreamer.subprocess, "Popen", popen)
    cap = _capture()
    cap.gst_launch_path = "gst-launch-1.0"
    texts: list[str] = []
    stderr_text = cap._stderr_text
    monkeypatch.setattr(cap, "_stderr_text", lambda: texts.append(stderr_text()) or "")

    assert not cap._start_pipeline("mfvideosrc ! fdsink fd=1")
    assert texts == [("no element mfvideosrc\n" * 50)[:300]]
    assert _file(seen["stderr"]).closed
    assert cap._stderr_log is None


def test_each_platform_tries_its_own_sources_and_autovideosrc_last() -> None:
    """Windows keeps its three; Linux asks v4l2 for the numbered device, then libcamera where GStreamer has it."""
    assert gstreamer.camera_sources("Windows", 1, lambda _name: True) == [
        "mfvideosrc device-index=1",
        "ksvideosrc device-index=1",
        "dshowvideosrc",
        "autovideosrc",
    ]
    asked: list[str] = []
    assert gstreamer.camera_sources(
        "Linux", 2, lambda name: asked.append(name) or True
    ) == [
        "v4l2src device=/dev/video2",
        "libcamerasrc",
        "autovideosrc",
    ]
    assert asked == ["libcamerasrc"]
    assert gstreamer.camera_sources("Linux", 0, lambda _name: False) == [
        "v4l2src device=/dev/video0",
        "autovideosrc",
    ]
    assert gstreamer.camera_sources("Darwin", 0, lambda _name: True) == ["autovideosrc"]


def _dead_launches(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every gst-launch ends at once with status 1; the commands started, in order."""
    started: list[list[str]] = []

    def popen(cmd: list[str], **kwargs: object) -> _Process:
        del kwargs
        started.append(cmd)
        return _Process(io.BytesIO(), status=1)

    def no_sleep(_seconds: float) -> None:
        pytest.fail("a pipeline that ended is not waited for")

    monkeypatch.setattr(gstreamer.subprocess, "Popen", popen)
    monkeypatch.setattr(gstreamer.time, "sleep", no_sleep)
    return started


def test_on_linux_open_tries_the_numbered_v4l2_device_first_and_no_windows_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thirty tries of sources Linux does not have came before the first useful one, a second each."""
    started = _dead_launches(monkeypatch)
    monkeypatch.setattr(gstreamer.platform, "system", lambda: "Linux")
    cap = GStreamerSubprocessCapture(
        CameraConfig(width=WIDTH, height=HEIGHT, device_index=2)
    )
    cap.gst_launch_path = "gst-launch-1.0"
    monkeypatch.setattr(cap, "_has_element", lambda _name: False)

    assert not cap.open()

    sources = [" ".join(cmd[2:]).split(" ! ")[0] for cmd in started]
    specs = len(gstreamer.PIPELINE_SPECS)
    one_round = ["v4l2src device=/dev/video2"] * specs + ["autovideosrc"] * specs
    # The asked size, then the fallback size.
    assert sources == one_round * 2
    assert all(cmd[:2] == ["gst-launch-1.0", "-q"] for cmd in started)


def test_on_windows_open_starts_the_pipelines_it_always_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows order and its device argument are unchanged, byte for byte."""
    started = _dead_launches(monkeypatch)
    monkeypatch.setattr(gstreamer.platform, "system", lambda: "Windows")
    cap = GStreamerSubprocessCapture(
        CameraConfig(width=WIDTH, height=HEIGHT, device_index=1)
    )
    cap.gst_launch_path = "gst-launch-1.0"

    assert not cap.open()

    pipelines = [" ".join(cmd[2:]) for cmd in started]
    specs = len(gstreamer.PIPELINE_SPECS)
    assert pipelines[0] == (
        "mfvideosrc device-index=1 ! video/x-raw,width=64,height=64,framerate=30/1,format=BGR ! "
        "videoconvert ! videoscale ! video/x-raw,format=BGR,width=64,height=64,framerate=30/1 ! "
        "fdsink fd=1 sync=false"
    )
    assert [p.split(" ! ")[0] for p in pipelines[::specs]] == [
        "mfvideosrc device-index=1",
        "ksvideosrc device-index=1",
        "dshowvideosrc",
        "autovideosrc",
    ] * 2


def test_a_source_without_a_camera_number_says_which_camera_it_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--camera 1 on autovideosrc opened the default camera without a word."""
    _dead_launches(monkeypatch)
    said: list[str] = []
    sink = gstreamer.logger.add(
        lambda message: said.append(str(message)), level="WARNING"
    )
    try:
        for index in (0, 1):
            cap = GStreamerSubprocessCapture(
                CameraConfig(width=WIDTH, height=HEIGHT, device_index=index)
            )
            cap.gst_launch_path = "gst-launch-1.0"
            said.clear()
            assert not cap._try_pipelines(
                ["v4l2src device=/dev/video1", "autovideosrc"],
                WIDTH,
                HEIGHT,
                30,
                float("inf"),
            )
            named = [line for line in said if "takes no camera number" in line]
            assert len(named) == index, said
            assert all(
                "autovideosrc" in line and "not camera 1" in line for line in named
            )
    finally:
        gstreamer.logger.remove(sink)


def test_a_running_pipeline_is_waited_for_once_and_then_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gst-launch that is still there after the start-up wait is asked for its first frame."""
    processes: list[_Process] = []

    def popen(cmd: list[str], **kwargs: object) -> _Process:
        del cmd, kwargs
        processes.append(_Process(io.BytesIO(bytes(FRAME)), status=None))
        return processes[-1]

    monkeypatch.setattr(gstreamer.subprocess, "Popen", popen)
    cap = _capture()
    cap.gst_launch_path = "gst-launch-1.0"
    try:
        assert cap._start_pipeline("autovideosrc ! fdsink fd=1")
        assert processes[0].waits == [gstreamer.GST_PROCESS_STARTUP_DELAY]
        ok, frame = cap.read()
        assert ok
        assert frame is not None
        assert frame.shape == (HEIGHT, WIDTH, 3)
    finally:
        cap.release()


def test_a_gst_launch_that_ended_says_its_exit_code_and_its_last_words() -> None:
    """A run that dies after minutes names its reason at the end of stderr, behind the start-up warnings."""
    cap = _capture()
    assert cap.failure_detail() == ""
    cap.process = cast("subprocess.Popen[bytes]", _Process(io.BytesIO(), status=None))
    assert cap.failure_detail() == ""
    cap.process = cast("subprocess.Popen[bytes]", _Process(io.BytesIO(), status=3))
    assert cap.failure_detail() == "gst-launch exited with code 3"
    cap._stderr_log = io.BytesIO(
        b"WARNING: slow\n" * 200
        + b"ERROR: Device '/dev/video0' failed during initialization\n"
    )
    detail = cap.failure_detail()
    assert detail.startswith("gst-launch exited with code 3: ")
    assert detail.endswith("ERROR: Device '/dev/video0' failed during initialization")
    assert len(detail) <= len("gst-launch exited with code 3: ") + 300
    # The start of a long log is what a failed start shows; the end is what a lost camera shows.
    assert cap._stderr_text().startswith("WARNING: slow")


def test_the_element_question_goes_to_the_gst_inspect_beside_gst_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source libcamerasrc is tried only where this GStreamer has it; a gst-inspect that cannot run means no."""
    asked: list[list[str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        asked.append(cmd)
        if kwargs.get("timeout") != gstreamer.GST_INSPECT_TIMEOUT:
            pytest.fail("the question has no time limit")
        if cmd[2] == "broken":
            raise FileNotFoundError
        return subprocess.CompletedProcess(cmd, 0 if cmd[2] == "libcamerasrc" else 1)

    monkeypatch.setattr(gstreamer.subprocess, "run", run)
    cap = _capture()
    cap.gst_launch_path = None
    assert not cap._has_element("libcamerasrc")
    assert asked == []
    cap.gst_launch_path = "/opt/gst/bin/gst-launch-1.0"
    assert cap._has_element("libcamerasrc")
    assert not cap._has_element("nosuchsrc")
    assert not cap._has_element("broken")
    assert [Path(cmd[0]).as_posix() for cmd in asked] == [
        "/opt/gst/bin/gst-inspect-1.0"
    ] * 3
    assert asked[0][1:] == ["--exists", "libcamerasrc"]
