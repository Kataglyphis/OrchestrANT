"""Plain pytest timings of three hot paths, written as an orchestrant-bench report for bench-history."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import statistics
import sysconfig
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from orchestrant.benchmark.client import RESULT_FORMAT, write_report
from orchestrant.benchmark.provenance import tool_fingerprint
from orchestrant.dummy import SimpleMLPreprocessor
from orchestrant.genai.engine import GenaiError, GenerationOptions
from orchestrant.genai.server import parse_request
from orchestrant.yolo.core.postprocess import DecodeConfig, postprocess
from orchestrant.yolo.core.preprocess import preprocess


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

BENCHMARK = "orchestrant-hot-paths"
JSON_ENV = "ORCHESTRANT_BENCH_JSON"
# One file per interpreter flavour under the test reports the CI lane uploads; bench-history keys on the flavour.
FLAVOUR = "free-threaded" if sysconfig.get_config_var("Py_GIL_DISABLED") else "gil"
DEFAULT_JSON = (
    Path(__file__).resolve().parents[1]
    / "logs"
    / "test-results"
    / "bench"
    / f"{BENCHMARK}-{FLAVOUR}.json"
)
WARMUP = 3
ROUNDS = 30
FRAME_BGR = (10, 20, 30)
PLANTED = (
    (1000, 320.0, 320.0, 40.0, 20.0, 0, 0.9),
    (4000, 100.0, 200.0, 30.0, 30.0, 16, 0.8),
    (8000, 500.0, 400.0, 60.0, 50.0, 79, 0.7),
)


def _timed(call: Callable[[], Any]) -> tuple[list[float], Any]:
    """Wall milliseconds of ROUNDS calls after WARMUP, and the last result."""
    result = None
    for _ in range(WARMUP):
        result = call()
    samples = []
    for _ in range(ROUNDS):
        start = time.perf_counter_ns()
        result = call()
        samples.append((time.perf_counter_ns() - start) / 1e6)
    return samples, result


def _row(label: str, samples: list[float], **facts: object) -> dict[str, Any]:
    """One report entry; bench-history reads p50_ms and p95_ms, lower is better."""
    return {
        "label": label,
        "unit": "ms",
        "better": "lower",
        "rounds": len(samples),
        "p50_ms": statistics.median(samples),
        "p95_ms": statistics.quantiles(samples, n=20, method="inclusive")[-1],
        "min_ms": min(samples),
        "mean_ms": statistics.fmean(samples),
        **facts,
        "results": [{"wall_ms": s} for s in samples],
    }


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {"warmup": WARMUP, "rounds": ROUNDS, "timer": "perf_counter_ns"}
    write_report(str(path), BENCHMARK, config, rows, None, [__file__])


@pytest.fixture(scope="module")
def reports() -> Iterator[list[dict[str, Any]]]:
    """Collect every timing, then write one report where JSON_ENV points."""
    rows: list[dict[str, Any]] = []
    yield rows
    _write(Path(os.environ.get(JSON_ENV) or DEFAULT_JSON), rows)


def test_detector_preprocess(reports: list[dict[str, Any]]) -> None:
    """A 1280x720 BGR frame letterboxes to an RGB (1, 3, 640, 640) blob."""
    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if (free_threaded or platform.machine() == "riscv64") and importlib.util.find_spec(
        "cv2"
    ) is None:
        pytest.skip("OpenCV publishes no free-threaded or riscv64 wheel")
    frame = np.empty((720, 1280, 3), dtype=np.uint8)
    frame[:] = FRAME_BGR
    samples, (blob, scale, pad_x, pad_y) = _timed(lambda: preprocess(frame))
    np.testing.assert_equal(blob.shape, (1, 3, 640, 640))
    np.testing.assert_equal(blob.dtype, np.float32)
    np.testing.assert_equal((scale, pad_x, pad_y), (0.5, 0, 140))
    np.testing.assert_allclose(blob[0, :, 320, 320], np.array(FRAME_BGR[::-1]) / 255)
    np.testing.assert_allclose(blob[0, :, 0, 0], 114 / 255)
    reports.append(_row("detector_preprocess", samples, frame=[720, 1280, 3]))


def _yolo_output() -> np.ndarray:
    """A YOLOv8-shaped (1, 84, 8400) output: low-score anchors plus the PLANTED boxes."""
    rng = np.random.default_rng(0)
    anchors = np.zeros((8400, 84), dtype=np.float32)
    anchors[:, 0:2] = rng.uniform(100, 540, (8400, 2))
    anchors[:, 2:4] = rng.uniform(10, 60, (8400, 2))
    anchors[:, 4:] = rng.uniform(0, 0.1, (8400, 80))
    for index, cx, cy, w, h, class_id, score in PLANTED:
        anchors[index, :4] = (cx, cy, w, h)
        anchors[index, 4 + class_id] = score
    return anchors.T[np.newaxis, ...]


def test_detector_decode(reports: list[dict[str, Any]]) -> None:
    """The decode keeps exactly the planted boxes, mapped back through the letterbox."""
    outputs = [_yolo_output()]
    config = DecodeConfig(
        scale=0.5, pad_x=0, pad_y=140, input_size=(640, 640), conf_threshold=0.5
    )
    samples, (detections, classification) = _timed(lambda: postprocess(outputs, config))
    np.testing.assert_equal(classification, None)
    expected = [
        {
            "bbox": [
                int((cx - w / 2) / 0.5),
                int((cy - h / 2 - 140) / 0.5),
                int((cx + w / 2) / 0.5),
                int((cy + h / 2 - 140) / 0.5),
            ],
            "class_id": class_id,
        }
        for _, cx, cy, w, h, class_id, _ in PLANTED
    ]
    found = [{"bbox": d["bbox"], "class_id": d["class_id"]} for d in detections]
    np.testing.assert_equal(found, expected)
    np.testing.assert_allclose(
        [d["score"] for d in detections], [p[-1] for p in PLANTED], rtol=1e-6
    )
    reports.append(_row("detector_decode", samples, anchors=8400, classes=80))


def _chat_body(turns: int) -> bytes:
    messages = [{"role": "system", "content": "You answer briefly."}]
    for turn in range(turns):
        role = "user" if turn % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"turn {turn}: " + "lorem " * 40})
    body = {
        "model": "chain-genai",
        "messages": messages,
        "max_tokens": 64,
        "temperature": 0.2,
        "top_p": 0.9,
        "stream": True,
    }
    return json.dumps(body).encode()


def test_request_parsing(reports: list[dict[str, Any]]) -> None:
    """orchestrant-serve's body check accepts a 64-turn chat and refuses an unknown role."""
    raw = _chat_body(64)
    samples, (messages, options, body) = _timed(lambda: parse_request(raw))
    np.testing.assert_equal(len(messages), 65)
    np.testing.assert_equal(
        options,
        GenerationOptions(max_new_tokens=64, temperature=0.2, top_p=0.9, top_k=50),
    )
    np.testing.assert_equal(body["model"], "chain-genai")
    bad = json.loads(raw)
    bad["messages"][1]["role"] = "robot"
    with pytest.raises(GenaiError, match="role"):
        parse_request(json.dumps(bad).encode())
    reports.append(_row("serve_request_parse", samples, body_bytes=len(raw)))


def test_pipeline_benchmark() -> None:
    """The profiling demos' dummy pipeline still z-scores its features."""
    result = SimpleMLPreprocessor(10000).run_pipeline()
    normalized = np.asarray(result["normalized"])
    np.testing.assert_equal(normalized.shape, (10000, 3))
    np.testing.assert_allclose(normalized.mean(axis=0), 0, atol=1e-9)
    np.testing.assert_allclose(normalized.std(axis=0), 1, rtol=1e-9)


def test_report_envelope(tmp_path: Path) -> None:
    """The written file is the shared orchestrant-bench envelope bench-history reads."""
    path = tmp_path / "bench.json"
    _write(path, [_row("probe", [1.0, 2.0, 3.0, 4.0])])
    doc = json.loads(path.read_text(encoding="utf-8"))
    np.testing.assert_equal(
        sorted(doc), ["benchmark", "config", "provenance", "reports", "result_format"]
    )
    np.testing.assert_equal(doc["result_format"], RESULT_FORMAT)
    np.testing.assert_equal(doc["benchmark"], BENCHMARK)
    row = doc["reports"][0]
    np.testing.assert_equal(
        (row["label"], row["p50_ms"], row["better"]), ("probe", 2.5, "lower")
    )
    np.testing.assert_allclose(row["p95_ms"], 3.85)
    np.testing.assert_equal(
        doc["provenance"]["tool_sha256"], tool_fingerprint(__file__)
    )
