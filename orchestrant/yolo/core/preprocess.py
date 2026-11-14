"""Preprocessing for the ONNX detector: letterbox, or a plain resize."""

from __future__ import annotations

import functools
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


# value / 255 for every byte: one lookup per pixel gives the floats the division gave.
_UNIT_FLOATS = np.arange(256, dtype=np.float32) / np.float32(255.0)


def _to_blob(image: np.ndarray) -> np.ndarray:
    """BGR uint8 (H, W, 3) to an RGB float32 (1, 3, H, W) blob in [0, 1]."""
    height, width = image.shape[:2]
    # Written plane by plane into its final layout: a transposed view makes ONNX Runtime copy it again.
    blob = np.empty((1, 3, height, width), dtype=np.float32)
    for channel in range(3):
        np.take(
            _UNIT_FLOATS, image[:, :, 2 - channel], out=blob[0, channel], mode="clip"
        )
    return blob


@functools.lru_cache(maxsize=8)
def _sample_maps(
    src_w: int, src_h: int, dst_w: int, dst_h: int
) -> tuple[np.ndarray, np.ndarray]:
    """Whole source coordinates x * src // dst, ModelANT's nearest_floor, as float maps for cv2.remap."""
    xs = (np.arange(dst_w) * src_w // dst_w).astype(np.float32)
    ys = (np.arange(dst_h) * src_h // dst_h).astype(np.float32)
    map_x = np.broadcast_to(xs, (dst_h, dst_w)).copy()
    map_y = np.broadcast_to(ys[:, None], (dst_h, dst_w)).copy()
    map_x.setflags(write=False)
    map_y.setflags(write=False)
    return map_x, map_y


def _nearest_floor(frame: np.ndarray, dst_w: int, dst_h: int) -> np.ndarray:
    """Sample like Rust and C++ do; cv2.resize's own nearest picks other columns for a 2304 wide frame."""
    import cv2  # noqa: PLC0415 - only resizing needs OpenCV, which has no free-threaded wheel

    map_x, map_y = _sample_maps(frame.shape[1], frame.shape[0], dst_w, dst_h)
    # Whole-number coordinates, so nearest is exact; about 0.2 ms over cv2.resize at 1080p on a Pi 5.
    return cv2.remap(frame, map_x, map_y, cv2.INTER_NEAREST)


def preprocess(
    frame: np.ndarray,
    input_size: tuple[int, int] = (640, 640),
    *,
    stretch: bool = False,
) -> tuple[np.ndarray, float | tuple[float, float], int, int]:
    """Preprocess a frame for an ONNX detector.

    Letterboxes by default: resizes while preserving the aspect ratio and pads
    to input_size. With ``stretch`` it resizes each axis on its own instead.
    Either way it samples the nearest source pixel (ModelANT inference-v1's
    ``nearest_floor``), converts BGR to RGB and normalizes to [0, 1].

    Args:
        frame: Input image in BGR format, shape (H, W, 3).
        input_size: Target size as (height, width). Defaults to (640, 640).
        stretch: Plain resize to input_size, no padding (see wants_stretch).

    Returns:
        Tuple of:
            - blob: Preprocessed image blob, shape (1, 3, H, W), float32.
            - scale: Scale factor applied during resize; an (x, y) pair when the axes differ.
            - pad_x: Horizontal padding added.
            - pad_y: Vertical padding added.
    """
    original_h, original_w = frame.shape[:2]

    if stretch:
        resized = _nearest_floor(frame, input_size[1], input_size[0])
        return (
            _to_blob(resized),
            (input_size[1] / original_w, input_size[0] / original_h),
            0,
            0,
        )

    scale = min(input_size[0] / original_h, input_size[1] / original_w)
    # ModelANT's inference-v1 rounds half up; truncating drops a row, round() rounds half to even.
    new_w = min(max(int(original_w * scale + 0.5), 1), input_size[1])
    new_h = min(max(int(original_h * scale + 0.5), 1), input_size[0])

    resized = _nearest_floor(frame, new_w, new_h)

    padded = np.full((input_size[0], input_size[1], 3), 114, dtype=np.uint8)
    pad_x, pad_y = (input_size[1] - new_w) // 2, (input_size[0] - new_h) // 2
    padded[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

    # Rounding can leave the axes unequal; one factor stays the common case.
    scales = (new_w / original_w, new_h / original_h)
    return (
        _to_blob(padded),
        scales[0] if scales[0] == scales[1] else scales,
        pad_x,
        pad_y,
    )
