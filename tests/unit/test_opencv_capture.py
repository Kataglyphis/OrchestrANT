"""The OpenCV backend says what the driver agreed to: its pixel format and its own name, not DirectShow everywhere."""

from __future__ import annotations

import pytest


cv2 = pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")

from orchestrant.pipeline.capture import opencv  # noqa: E402
from orchestrant.pipeline.types import CameraConfig  # noqa: E402


class _FakeCapture:
    """cv2.VideoCapture as a V4L2 camera answers: 1280x720 at 30 as MJPG."""

    backend = "V4L2"

    def __init__(self, index: int) -> None:
        self.index = index
        self.asked: list[tuple[int, float]] = []
        self.answers = {
            cv2.CAP_PROP_FRAME_WIDTH: 1280.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 720.0,
            cv2.CAP_PROP_FPS: 30.0,
            cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*"MJPG")),
        }

    def isOpened(self) -> bool:  # noqa: N802 -- OpenCV's name
        return True

    def set(self, prop: int, value: float) -> bool:
        self.asked.append((prop, value))
        return True

    def get(self, prop: int) -> float:
        return self.answers.get(prop, 0.0)

    def getBackendName(self) -> str:  # noqa: N802 -- OpenCV's name
        if self.backend is None:
            msg = "no backend name in this build"
            raise cv2.error(msg)
        return self.backend


def _open(
    monkeypatch: pytest.MonkeyPatch, backend: str | None = "V4L2"
) -> opencv.OpenCVCapture:
    monkeypatch.setattr(_FakeCapture, "backend", backend)
    monkeypatch.setattr(opencv.cv2, "VideoCapture", _FakeCapture)
    capture = opencv.OpenCVCapture(CameraConfig(width=1920, height=1080, fps=30))
    assert capture.open()
    return capture


def test_the_negotiated_format_and_the_backend_are_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The log and the session info name MJPG and V4L2; the info used to say DirectShow on every OS."""
    info = _open(monkeypatch).get_info()
    assert info["fourcc"] == "MJPG"
    assert info["pipeline"] == "OpenCV V4L2 (device 0)"
    assert (info["width"], info["height"], info["fps"]) == (1280, 720, 30.0)


def test_a_build_without_backend_names_says_opencv_and_no_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build that cannot name its backend is not given a guessed one."""
    assert (
        _open(monkeypatch, backend=None).get_info()["pipeline"] == "OpenCV (device 0)"
    )


@pytest.mark.parametrize(
    ("code", "text"),
    [(0.0, ""), (float(0x47504A4D), "MJPG"), (float(0x56595559), "YUYV")],
)
def test_a_fourcc_reads_as_its_four_characters(code: float, text: str) -> None:
    """CAP_PROP_FOURCC packs four characters into a number, the first in the lowest byte."""
    assert opencv.fourcc_text(code) == text
