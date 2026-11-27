"""The monitor's defaults are written once: the camera's in CameraConfig, the score threshold in constants.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrant.pipeline import constants
from orchestrant.pipeline.types import CameraConfig
from orchestrant.yolo.cli import parse_args
from orchestrant.yolo.core.postprocess import DecodeConfig


ROOT = Path(__file__).resolve().parents[2]


def test_parse_args_offers_the_camera_config_and_the_one_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second copy of 1920, 1080 and 30 drifted from the first before."""
    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("ORCHESTRANT_MODEL", str(model))
    args = parse_args([])
    camera = CameraConfig()
    assert (args.width, args.height, args.fps) == (
        camera.width,
        camera.height,
        camera.fps,
    )
    assert (
        args.conf
        == constants.DEFAULT_SCORE_THRESHOLD
        == DecodeConfig(1.0, 0, 0, (640, 640)).conf_threshold
    )


@pytest.mark.source_tree
def test_every_public_constant_is_imported_somewhere() -> None:
    """A constant nobody reads is a second default waiting to drift; two such sat in constants.py."""
    public = {name for name in vars(constants) if name.isupper()}
    imported: set[str] = set()
    for path in sorted(ROOT.glob("orchestrant/**/*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "orchestrant.pipeline.constants"
            ):
                imported.update(alias.name for alias in node.names)
    assert public - imported == set()


def test_no_power_estimate_without_a_source_or_a_named_tdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner decision 2026-11-14: a desktop 45 W guess read several times too high on a Pi, so nothing is shown."""
    pytest.importorskip("cv2", reason="start-up imports OpenCV")
    from orchestrant.yolo import startup  # noqa: PLC0415

    monkeypatch.delenv("KATAGLYPHIS_CPU_TDP_WATTS", raising=False)
    assert startup._cpu_tdp_watts() == 0.0  # noqa: SLF001
    monkeypatch.setenv("KATAGLYPHIS_CPU_TDP_WATTS", "8")
    assert startup._cpu_tdp_watts() == 8.0  # noqa: SLF001


def test_the_loop_reads_typed_flags_taken_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_context hands the loop LoopFlags, not the Namespace; every field comes from its flag."""
    pytest.importorskip("cv2", reason="the loop imports OpenCV")
    from orchestrant.yolo.loop import LoopFlags  # noqa: PLC0415

    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("ORCHESTRANT_MODEL", str(model))
    argv = [
        "--no-display",
        "--ui",
        "opencv",
        "--map",
        "--map-size",
        "300",
        "--conf",
        "0.4",
    ]
    argv += ["--cpu-plot", "--debug-boxes", "--debug-output", "--debug-detections"]
    flags = LoopFlags.from_args(parse_args(argv))
    assert flags == LoopFlags(
        conf=0.4,
        no_display=True,
        ui="opencv",
        map=True,
        map_size=300,
        cpu_plot=True,
        debug_boxes=True,
        debug_output=True,
        debug_detections=True,
    )


def test_the_describe_engine_decodes_on_one_thread_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Owner decision 2026-11-14: on a Pi 5 a description on all cores left the detector a fifth of its speed."""
    from orchestrant.yolo import describe  # noqa: PLC0415

    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("ORCHESTRANT_MODEL", str(model))
    seen = []
    monkeypatch.setattr(describe, "GenaiEngine", lambda *a, **k: seen.append((a, k)))
    describe.open_engine(parse_args(["--describe", "--describe-model", str(tmp_path)]))
    describe.open_engine(
        parse_args(
            ["--describe", "--describe-model", str(tmp_path), "--describe-threads", "3"]
        )
    )
    assert [k["threads"] for _a, k in seen] == [1, 3]
