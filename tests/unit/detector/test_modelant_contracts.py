"""ModelANT's inference-v1 cases through the production preprocessing, and the two-cats golden on the CPU provider.

Not covered: GPU providers, the packaged bundle's own ORT, and preprocessing outside these four cases.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from orchestrant.yolo.cli import parse_args
from orchestrant.yolo.core.preprocess import preprocess


if TYPE_CHECKING:
    from types import ModuleType, SimpleNamespace


GOLDEN = Path(__file__).with_name("two_cats_golden.json")
CASES = (
    "rgb_stretch_red_green",
    "rgba_strided_offset",
    "letterbox_odd_padding",
    "letterbox_rounded_content",
)
CAT = 15


def contract(contracts: Path) -> dict[str, Any]:
    """The pinned inference-v1.json."""
    return json.loads((contracts / "inference-v1.json").read_text(encoding="utf-8"))


def bgr_frame(case: dict[str, Any]) -> np.ndarray:
    """The case's strided, offset RGB or RGBA bytes as the BGR frame the monitor captures."""
    channels = len(case["pixel_format"])
    data = np.asarray(case["data"], dtype=np.uint8)
    rows = [
        data[start : start + case["width"] * channels].reshape(case["width"], channels)
        for start in range(
            case["plane_offset_bytes"],
            case["plane_offset_bytes"] + case["height"] * case["row_stride_bytes"],
            case["row_stride_bytes"],
        )
    ]
    return np.ascontiguousarray(np.stack(rows)[:, :, 2::-1])


def test_the_contract_holds_exactly_the_replayed_cases(contracts: Path) -> None:
    """A new ModelANT case must join CASES, so no case goes unreplayed."""
    spec = contract(contracts)
    assert spec["schema_version"] == 1
    assert tuple(case["name"] for case in spec["cases"]) == CASES


@pytest.mark.parametrize("name", CASES)
def test_inference_v1_case(
    name: str, contracts: Path, runtime: SimpleNamespace
) -> None:
    """The production preprocessing yields the case's NCHW tensor and mapping."""
    assert runtime.cv2 is not None
    case = next(c for c in contract(contracts)["cases"] if c["name"] == name)
    blob, scale, pad_x, pad_y = preprocess(
        bgr_frame(case),
        (case["input_height"], case["input_width"]),
        stretch=case["mode"] == "stretch",
    )
    assert blob.dtype == np.float32
    np.testing.assert_allclose(blob.ravel(), case["expected_nchw"], atol=1e-7, rtol=0)
    mapping = case["mapping"]
    scale_x, scale_y = scale if isinstance(scale, tuple) else (scale, scale)
    assert (pad_x, pad_y) == (mapping["pad_x"], mapping["pad_y"])
    assert (scale_x, scale_y) == pytest.approx((mapping["scale_x"], mapping["scale_y"]))
    assert round(scale_x * case["width"]) == mapping["content_width"]
    assert round(scale_y * case["height"]) == mapping["content_height"]


def providers() -> ModuleType:
    """The provider module, imported late because the free-threaded leg has no ONNX Runtime."""
    return importlib.import_module("orchestrant.yolo.providers")


def detect(
    model: Path, image: np.ndarray, runtime: SimpleNamespace, diagnostic: ModuleType
) -> dict[str, Any]:
    """One CPU run of the monitor's pre- and postprocessing, as compare_detector_parity.py reads it."""
    assert runtime.ort is not None
    session = providers().create_session(str(model), 0, "cpu")
    pre = importlib.import_module("orchestrant.yolo.core.preprocess")
    post = importlib.import_module("orchestrant.yolo.core.postprocess")
    return diagnostic.capture(session, image, np, pre, post)[0]


def test_two_cats_match_the_golden(
    model: Path,
    contracts: Path,
    runtime: SimpleNamespace,
    diagnostic: ModuleType,
    comparator: ModuleType,
    tmp_path: Path,
) -> None:
    """Both cats at IoU >= 0.90 and score within 0.05 of the recorded run, with nothing added or lost."""
    image = runtime.cv2.imread(
        str(contracts / "two_cats.jpg"), runtime.cv2.IMREAD_COLOR
    )
    fresh = tmp_path / "two_cats.json"
    fresh.write_text(
        json.dumps(detect(model, image, runtime, diagnostic)), encoding="utf-8"
    )
    golden = comparator.load(GOLDEN)
    found = comparator.load(fresh)
    cats = [item for item in found["detections"] if item["class"] == CAT]
    assert len(cats) >= 2, found
    comparator.compare(golden, found)


def test_shipped_thread_default_reaches_the_session_and_zero_keeps_ort(
    model: Path, runtime: SimpleNamespace
) -> None:
    """No setting takes the shipped count; an explicit 0 leaves ORT's own default."""
    assert runtime.ort is not None
    module = providers()
    usable = module.shipped_intra_op_threads()
    shipped = module.create_session(str(model), 0, "cpu")
    assert shipped.get_session_options().intra_op_num_threads == usable
    ort_default = module.create_session(str(model), 0, "cpu", intra_op_threads=0)
    assert ort_default.get_session_options().intra_op_num_threads == 0


def test_threads_from_config_and_cli_reach_intra_op_num_threads(
    model: Path, runtime: SimpleNamespace, tmp_path: Path
) -> None:
    """[onnxruntime] intra_op_threads, and --threads over it, end up in the real session's options."""
    assert runtime.ort is not None
    config = tmp_path / "yolo.toml"
    config.write_text("[onnxruntime]\nintra_op_threads = 3\n", encoding="utf-8")
    for argv, expected in (
        (["--config", str(config)], 3),
        (["--config", str(config), "--threads", "2"], 2),
    ):
        args = parse_args(["--model", str(model), "--device", "cpu", *argv])
        session = providers().create_session(
            args.model, args.gpu, args.device, intra_op_threads=args.intra_op_threads
        )
        assert session.get_session_options().intra_op_num_threads == expected
