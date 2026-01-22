from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np


def infer_input_size(input_shape: Optional[List[object]]) -> Tuple[int, int]:
    """Infer (height, width) from an ONNX input shape."""

    if not input_shape or len(input_shape) < 4:
        return (640, 640)

    height = input_shape[-2]
    width = input_shape[-1]

    if isinstance(height, int) and isinstance(width, int):
        return (height, width)

    return (640, 640)


def preprocess(frame: np.ndarray, input_size: Tuple[int, int] = (640, 640)) -> tuple:
    """Preprocess frame for YOLOv10."""

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
