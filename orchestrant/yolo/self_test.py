"""The self-test's input frame and what it reports it found, apart from the monitor loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import cv2
import numpy as np


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
