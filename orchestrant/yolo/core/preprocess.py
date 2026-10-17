"""Preprocessing for the ONNX detector: letterbox, or a plain resize."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np


if TYPE_CHECKING:
    from collections.abc import Sequence

    import onnxruntime as ort


def wants_stretch(session: ort.InferenceSession) -> bool:
    """Whether the model asks for a plain resize instead of a letterbox.

    ModelANT's models say so in their ``kataglyphis.preprocess`` metadata; any
    other model keeps the letterbox.
    """
    meta = session.get_modelmeta().custom_metadata_map
    return meta.get("kataglyphis.preprocess") == "stretch"


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


def _to_blob(image: np.ndarray) -> np.ndarray:
    """BGR uint8 (H, W, 3) to an RGB float32 (1, 3, H, W) blob in [0, 1]."""
    blob = image[:, :, ::-1].astype(np.float32) / 255.0
    return blob.transpose(2, 0, 1)[np.newaxis, ...]


def preprocess(
    frame: np.ndarray,
    input_size: tuple[int, int] = (640, 640),
    *,
    stretch: bool = False,
) -> tuple[np.ndarray, float | tuple[float, float], int, int]:
    """Preprocess a frame for an ONNX detector.

    Letterboxes by default: resizes while preserving the aspect ratio and pads
    to input_size. With ``stretch`` it resizes each axis on its own instead.
    Either way the frame is converted from BGR to RGB and normalized to [0, 1].

    Args:
        frame: Input image in BGR format, shape (H, W, 3).
        input_size: Target size as (height, width). Defaults to (640, 640).
        stretch: Plain resize to input_size, no padding (see wants_stretch).

    Returns:
        Tuple of:
            - blob: Preprocessed image blob, shape (1, 3, H, W), float32.
            - scale: Scale factor applied during resize; an (x, y) pair when stretched.
            - pad_x: Horizontal padding added.
            - pad_y: Vertical padding added.
    """
    import cv2  # noqa: PLC0415 - only resizing needs OpenCV, which has no free-threaded wheel

    original_h, original_w = frame.shape[:2]

    if stretch:
        resized = cv2.resize(frame, (input_size[1], input_size[0]))
        return (
            _to_blob(resized),
            (input_size[1] / original_w, input_size[0] / original_h),
            0,
            0,
        )

    scale = min(input_size[0] / original_h, input_size[1] / original_w)
    new_w, new_h = int(original_w * scale), int(original_h * scale)

    resized = cv2.resize(frame, (new_w, new_h))

    padded = np.full((input_size[0], input_size[1], 3), 114, dtype=np.uint8)
    pad_x, pad_y = (input_size[1] - new_w) // 2, (input_size[0] - new_h) // 2
    padded[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

    return _to_blob(padded), scale, pad_x, pad_y
