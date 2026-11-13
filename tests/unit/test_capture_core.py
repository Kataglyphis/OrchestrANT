"""The capture wrapper: which backend opens, and that a named one is not replaced."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrant.pipeline.capture import core
from orchestrant.pipeline.types import CameraConfig, CaptureBackend


if TYPE_CHECKING:
    import pytest


class _Backend:
    """A capture backend that opens or not, and counts how often it was built."""

    built = 0

    def __init__(self, config: CameraConfig, *, opens: bool) -> None:
        del config
        type(self).built += 1
        self._opens = opens

    def open(self) -> bool:
        return self._opens


def _backends(
    monkeypatch: pytest.MonkeyPatch, *, gstreamer: bool, opencv: bool
) -> dict[str, type[_Backend]]:
    """Both backends replaced by fakes that open as told."""
    made = {
        "gstreamer": type("_Gst", (_Backend,), {"built": 0}),
        "opencv": type("_Cv", (_Backend,), {"built": 0}),
    }
    monkeypatch.setattr(
        core,
        "GStreamerSubprocessCapture",
        lambda config: made["gstreamer"](config, opens=gstreamer),
    )
    monkeypatch.setattr(
        core, "OpenCVCapture", lambda config: made["opencv"](config, opens=opencv)
    )
    return made


def test_a_gstreamer_that_fails_is_not_replaced_by_opencv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenCV in its place hid that the backend the user asked for does not work."""
    made = _backends(monkeypatch, gstreamer=False, opencv=True)
    camera = core.CameraCapture(CameraConfig(backend=CaptureBackend.GSTREAMER))
    assert not camera.open()
    assert (made["gstreamer"].built, made["opencv"].built) == (1, 0)
    assert not camera.is_opened()
    assert camera.backend_name == ""
    assert camera.read() == (False, None)


def test_each_backend_opens_under_its_own_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each backend the config names is the one that opens."""
    made = _backends(monkeypatch, gstreamer=True, opencv=True)
    gst = core.CameraCapture(CameraConfig(backend=CaptureBackend.GSTREAMER))
    assert gst.open()
    assert gst.backend_name == "GStreamer (subprocess)"
    cv = core.CameraCapture(CameraConfig())
    assert cv.open()
    assert cv.backend_name == "OpenCV"
    assert (made["gstreamer"].built, made["opencv"].built) == (1, 1)


def test_an_opencv_that_fails_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default backend has nothing behind it either."""
    made = _backends(monkeypatch, gstreamer=True, opencv=False)
    assert not core.CameraCapture(CameraConfig()).open()
    assert made["gstreamer"].built == 0
