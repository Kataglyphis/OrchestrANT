"""The detection loop around a camera that stops delivering, and a model run that raises."""

# ruff: noqa: SLF001 -- the loop is the module's private core

from __future__ import annotations

import argparse
import importlib
from collections import deque
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest


pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime")

from orchestrant.pipeline.capture import watch
from orchestrant.pipeline.metrics.performance import PerformanceTracker
from orchestrant.pipeline.tracking.centroid import SimpleCentroidTracker
from orchestrant.yolo import loop
from orchestrant.yolo.core.detector import Detector


if TYPE_CHECKING:
    from orchestrant.yolo.core.postprocess import DecodeConfig


class _Camera:
    """Delivers ``frames`` frames, then fails every read."""

    def __init__(self, frames: int, *, stays_open: bool = True) -> None:
        self.frames = frames
        self.stays_open = stays_open
        self.reads = 0
        self.released = False

    def read(self) -> tuple[bool, np.ndarray | None]:
        self.reads += 1
        if self.frames > 0:
            self.frames -= 1
            return True, np.zeros((48, 64, 3), dtype=np.uint8)
        return False, None

    def is_opened(self) -> bool:
        return self.stays_open or self.frames > 0

    def release(self) -> None:
        self.released = True


class _Session:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.runs = 0

    def run(self, _names: object, _feed: object) -> list[np.ndarray]:
        self.runs += 1
        if self.error is not None:
            raise self.error
        output = np.zeros((1, 300, 6), dtype=np.float32)
        output[0, 0] = [8.0, 8.0, 40.0, 56.0, 0.9, 15.0]
        return [output]


class _Clock:
    """perf_counter and sleep on one fake timeline, so five seconds of failed reads cost none."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


def _context(
    camera: _Camera, session: _Session, layout: str | None = None
) -> loop.MonitorContext:
    quiet = SimpleNamespace(
        shutdown=lambda: None,
        get_stats=loop.SystemStats,
        get_process_stats=dict,
        update=lambda **_kwargs: {"energy_wh": 0.0},
    )
    return cast(
        "loop.MonitorContext",
        SimpleNamespace(
            args=argparse.Namespace(
                map=False,
                cpu_plot=False,
                conf=0.5,
                debug_boxes=False,
                debug_output=False,
                debug_detections=False,
                no_display=True,
            ),
            camera=camera,
            camera_info={"backend": "fake", "fps": 30.0},
            sys_monitor=quiet,
            detector=Detector(session, "images", (64, 48), True, layout),  # noqa: FBT003
            perf_tracker=PerformanceTracker(),
            tracker=SimpleCentroidTracker(),
            tracks={},
            cpu_history=deque(maxlen=2),
            log_buffer=deque(maxlen=2),
            power_monitor=quiet,
            hardware_info={},
            viewer=None,
            cpu_tdp_watts=45.0,
            describer=SimpleNamespace(
                offer=lambda _detections: None, stop=lambda: None
            ),
        ),
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """The frame watch's clock and sleep, faked together."""
    fake = _Clock()
    monkeypatch.setattr(watch.time, "perf_counter", fake.perf_counter)
    monkeypatch.setattr(watch.time, "sleep", fake.sleep)
    return fake


def test_a_camera_that_stops_delivering_ends_the_loop_with_an_error(
    clock: _Clock,
) -> None:
    """Frames stop while the device still claims to be open: the loop waits five seconds, then exits 1."""
    camera, session = _Camera(frames=3), _Session()
    assert loop._run_detection_loop(_context(camera, session)) == 1
    assert session.runs == 3
    assert camera.released
    assert 5.0 <= clock.now < 5.2
    # Every failed read paused; none of them spun.
    assert clock.sleeps == camera.reads - 3 - 1


def test_a_closed_camera_ends_the_loop_at_once(clock: _Clock) -> None:
    """A backend that reports itself closed is not worth five seconds of retries."""
    camera = _Camera(frames=2, stays_open=False)
    assert loop._run_detection_loop(_context(camera, _Session())) == 1
    assert camera.reads == 3
    assert clock.sleeps == 0


class _DeadLaunch(_Camera):
    """A closed camera whose backend knows why."""

    def failure_detail(self) -> str:
        return "gst-launch exited with code 1: no element v4l2src"


def test_a_lost_camera_says_what_its_backend_knows(clock: _Clock) -> None:
    """A gst-launch that died and an unplugged camera read alike without the backend's own words."""
    del clock
    with pytest.raises(
        watch.CameraLostError, match=r"^the camera delivered no frame for 0\.0 s$"
    ):
        watch.FrameWatch(_Camera(frames=0, stays_open=False)).read()
    with pytest.raises(
        watch.CameraLostError,
        match=r"^the camera delivered no frame for 0\.0 s \(gst-launch exited with code 1: no element v4l2src\)$",
    ):
        watch.FrameWatch(_DeadLaunch(frames=0, stays_open=False)).read()


def test_a_single_failed_read_is_retried_and_the_timer_starts_over(
    clock: _Clock,
) -> None:
    """One dropped frame costs a pause, not the session."""
    camera = _Camera(frames=0)
    frames = watch.FrameWatch(camera)
    assert frames.read() is None
    assert frames.failing_since == 0.0
    camera.frames = 1
    assert frames.read() is not None
    assert frames.failing_since is None
    clock.now += 100.0
    assert frames.read() is None
    assert frames.failing_since == clock.now - watch.CAMERA_RETRY_DELAY_SECONDS


def test_a_failing_model_run_is_not_reported_as_success(clock: _Clock) -> None:
    """An inference error mid-run ends the loop with exit code 1, after the cleanup."""
    del clock
    camera = _Camera(frames=5)
    session = _Session(error=RuntimeError("ORT: invalid input"))
    assert loop._run_detection_loop(_context(camera, session)) == 1
    assert session.runs == 1
    assert camera.released


def _count_guesses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per frame the guessing decoder was asked to decode."""
    decoder = importlib.import_module("orchestrant.yolo.core.postprocess")
    guess = decoder._decode_guessed
    guesses: list[int] = []

    def counted(outputs: list[np.ndarray], config: DecodeConfig) -> object:
        guesses.append(len(outputs))
        return guess(outputs, config)

    monkeypatch.setattr(decoder, "_decode_guessed", counted)
    return guesses


@pytest.mark.parametrize(
    ("layout", "guessed_frames"), [("xyxy_score_class", 0), (None, 3)]
)
def test_the_layout_chosen_at_start_decides_whether_a_frame_is_guessed(
    layout: str | None,
    guessed_frames: int,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that names its layout is never guessed; one that names none is guessed on every frame."""
    del clock
    guesses = _count_guesses(monkeypatch)
    seen: list[list[int]] = []
    ctx = _context(_Camera(frames=3), _Session(), layout)
    ctx.describer = cast(
        "loop.SceneDescriber",
        SimpleNamespace(
            offer=lambda found: seen.append([d["class_id"] for d in found]),
            stop=lambda: None,
        ),
    )
    loop._run_detection_loop(ctx)
    assert seen == [[15]] * 3
    assert len(guesses) == guessed_frames


def test_the_nominal_rate_comes_from_the_cameras_own_report() -> None:
    """Only a positive number counts; anything else leaves the measured rate in charge."""
    assert watch.nominal_fps({"fps": 30.0}) == 30.0
    assert watch.nominal_fps({"fps": 25}) == 25.0
    for unknown in ({}, {"fps": 0.0}, {"fps": -1}, {"fps": "30"}, {"fps": None}):
        assert watch.nominal_fps(unknown) == 0.0
