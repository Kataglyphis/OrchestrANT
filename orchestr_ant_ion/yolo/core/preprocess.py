"""Preprocessing utilities for YOLO inference."""

from __future__ import annotations

import cv2
import numpy as np
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


def infer_input_size(input_shape: Sequence[object] | None) -> tuple[int, int]:
    """Infer (height, width) from an ONNX input shape.

    Args:
        input_shape: Shape array from ONNX model input, typically [batch, channels, height, width].

    Returns:
        Tuple of (height, width). Defaults to (640, 640) if shape cannot be inferred.
    """
    if not input_shape or len(input_shape) < 4:
        return (640, 640)

    height = input_shape[-2]
    width = input_shape[-1]

    if isinstance(height, int) and isinstance(width, int):
        return (height, width)

    return (640, 640)


def preprocess(
    frame: np.ndarray, input_size: tuple[int, int] = (640, 640)
) -> tuple[np.ndarray, float, int, int]:
    """Preprocess frame for YOLOv10 inference.

    Resizes frame while preserving aspect ratio, pads to input_size,
    converts BGR to RGB, and normalizes to [0, 1].

    Args:
        frame: Input image in BGR format, shape (H, W, 3).
        input_size: Target size as (height, width). Defaults to (640, 640).

    Returns:
        Tuple of:
            - blob: Preprocessed image blob, shape (1, 3, H, W), float32.
            - scale: Scale factor applied during resize.
            - pad_x: Horizontal padding added.
            - pad_y: Vertical padding added.
    """
    original_h, original_w = frame.shape[:2]

    scale = min(input_size[0] / original_h, input_size[1] / original_w)
    new_w, new_h = int(original_w * scale), int(original_h * scale)

    resized = cv2.resize(frame, (new_w, new_h))

    padded = np.full((input_size[0], input_size[1], 3), 114, dtype=np.uint8)
    pad_x, pad_y = (input_size[1] - new_w) // 2, (input_size[0] - new_h) // 2
    padded[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

    blob = padded[:, :, ::-1].astype(np.float32) / 255.0
    blob = blob.transpose(2, 0, 1)[np.newaxis, ...]

    return blob, scale, pad_x, pad_y
