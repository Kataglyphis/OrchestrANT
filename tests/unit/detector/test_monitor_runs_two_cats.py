"""yolo-monitor's real loop on two_cats.jpg through a still camera: the shipped model, the decode, the drawing, the viewer."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest


# The monitor imports OpenCV and ONNX Runtime, which the free-threaded leg does not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="the chain ONNX Runtime is GIL-only")

from orchestrant.pipeline.capture.still import StillCamera
from orchestrant.yolo import loop, monitor, startup


if TYPE_CHECKING:
    from pathlib import Path

FRAMES = 4
CAT = 15


class FakeViewer:
    """A viewer with its own event loop, as Flet's: run() holds the main thread until the loop closes it."""

    def __init__(self) -> None:
        """Nothing rendered, still open."""
        self.renders: list[int] = []
        self.closed = threading.Event()

    def is_open(self) -> bool:
        """Open until close()."""
        return not self.closed.is_set()

    def render(self, frame: Any, **telemetry: Any) -> None:  # noqa: ANN401
        """Count each frame with the detections it was told about."""
        assert frame is not None
        self.renders.append(int(telemetry["detections_count"]))

    def close(self) -> None:
        """The loop ended."""
        self.closed.set()

    def run(self) -> None:
        """Hold the main thread like a window does, until the detection thread closes it."""
        assert self.closed.wait(60), "the detection loop never closed the viewer"


def run_monitor(
    monkeypatch: pytest.MonkeyPatch,
    runtime: SimpleNamespace,
    model: Path,
    contracts: Path,
    viewer: FakeViewer | None,
) -> SimpleNamespace:
    """Run yolo-monitor for FRAMES frames of two_cats.jpg and return what each frame found."""
    image = runtime.cv2.imread(
        str(contracts / "two_cats.jpg"), runtime.cv2.IMREAD_COLOR
    )
    camera = StillCamera(image, frames=FRAMES)
    monkeypatch.setattr(startup, "CameraCapture", camera)
    if viewer is not None:
        monkeypatch.setattr(startup, "_init_viewer", lambda *_args: viewer)
    found: list[list[dict[str, Any]]] = []
    decode = loop.postprocess

    def recording(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        detections, classification = decode(*args, **kwargs)
        found.append(detections)
        return detections, classification

    monkeypatch.setattr(loop, "postprocess", recording)
    argv = ["--model", str(model), "--device", "cpu", "--ui", "opencv"]
    argv += ["--log-level", "ERROR"] + (["--no-display"] if viewer is None else [])
    code = monitor.run_yolo_monitor(argv)
    return SimpleNamespace(code=code, camera=camera, found=found)


def cats(detections: list[dict[str, Any]]) -> list[float]:
    """The scores of the cats that pass 0.5, highest first."""
    scores = (float(d["score"]) for d in detections if int(d["class_id"]) == CAT)
    return sorted((score for score in scores if score >= 0.5), reverse=True)


@pytest.mark.parametrize("shown", [False, True], ids=["no-display", "viewer"])
def test_the_monitor_finds_both_cats_in_every_frame_and_ends_clean(
    monkeypatch: pytest.MonkeyPatch,
    runtime: SimpleNamespace,
    model: Path,
    contracts: Path,
    shown: bool,  # noqa: FBT001 -- a parametrized case
) -> None:
    """The loop users run, end to end: FRAMES reads, both cats each time, every frame shown, exit 0."""
    viewer = FakeViewer() if shown else None
    run = run_monitor(monkeypatch, runtime, model, contracts, viewer)
    assert run.code == 0
    assert run.camera.reads == FRAMES
    assert run.camera.released
    assert len(run.found) == FRAMES
    for frame in run.found:
        assert len(cats(frame)) == 2, frame
    if viewer is not None:
        assert viewer.renders == [len(frame) for frame in run.found]
        assert viewer.closed.is_set()
