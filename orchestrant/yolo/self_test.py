"""The self-test's input frame and what it reports it found, apart from the monitor loop."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
import onnxruntime as ort

from orchestrant.genai.engine import GenaiError
from orchestrant.paths import default_log_dir, missing_model_message, model_search_paths
from orchestrant.yolo.core.detector import Detector
from orchestrant.yolo.describe import self_test_report
from orchestrant.yolo.providers import provider_report
from orchestrant.yolo.startup import session_for


if TYPE_CHECKING:
    import argparse


def self_test_frame(args: argparse.Namespace) -> np.ndarray | None:
    """A black frame of the asked size, or the --self-test-image as OpenCV reads it (None when it cannot)."""
    if args.self_test_image is None:
        return np.zeros((args.height, args.width, 3), dtype=np.uint8)
    return cv2.imread(str(args.self_test_image))


def found_on(
    frame: np.ndarray, detections: list[dict[str, Any]], *, image: bool
) -> dict[str, Any]:
    """The blank frame's count, or for --self-test-image the golden's shape, which compare_detector_parity.py reads."""
    if not image:
        return {"detections_on_blank_frame": len(detections)}
    found = [
        {
            "class": int(item["class_id"]),
            "score": float(item["score"]),
            "box": [float(value) for value in item["bbox"]],
        }
        for item in detections
    ]
    shape = {
        "schema_version": 1,
        "width": int(frame.shape[1]),
        "height": int(frame.shape[0]),
    }
    return {
        "detections_on_image": len(found),
        "detected": {**shape, "detections": found},
    }


def run_self_test(args: argparse.Namespace) -> int:
    """One inference on a blank frame, printed as JSON: the install check CI runs with no camera or display."""
    model = Path(args.model)
    if not model.is_file():
        searched = ", ".join(str(path) for path in model_search_paths())
        failure = {
            "ok": False,
            "error": missing_model_message(model),
            "searched": searched,
        }
        sys.stdout.write(json.dumps(failure) + "\n")
        return 1
    try:
        detector = Detector.load(session_for(args, str(model)))
    except Exception as exc:
        sys.stdout.write(
            json.dumps({"ok": False, "device": args.device, "error": str(exc)}) + "\n"
        )
        return 1
    frame = self_test_frame(args)
    if frame is None:
        failure = {"ok": False, "error": f"Cannot read {args.self_test_image}"}
        sys.stdout.write(json.dumps(failure) + "\n")
        return 1
    found = detector.detect(frame, conf_threshold=args.conf)
    detections = found.detections
    report = {
        "ok": True,
        "python": platform.python_version(),
        "onnxruntime": ort.__version__,
        "opencv": cv2.__version__,
        **provider_report(detector.session),
        "model": str(model.resolve()),
        "input_size": list(detector.input_size),
        "preprocess": "stretch" if detector.stretch else "letterbox",
        "outputs": [list(output.shape) for output in found.outputs],
        **found_on(frame, detections, image=args.self_test_image is not None),
        "inference_ms": round(found.inference_ms, 2),
        "log_dir": str(default_log_dir()),
    }
    if args.describe:
        try:
            report["description"] = self_test_report(args, detections)
        except GenaiError as exc:
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
            return 1
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0
