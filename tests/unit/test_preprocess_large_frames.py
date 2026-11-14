"""preprocess() samples like ModelANT's nearest_floor on full-size frames, not only on the contract's tiny cases.

Rust and C++ index with integer maths (x * src // dst); this side hands the resize to OpenCV, whose
nearest-neighbour formula a model in plain Python disagreed with for 2304 and 4608 wide frames.
"""

from __future__ import annotations

import numpy as np
import pytest

from orchestrant.yolo.core.preprocess import _to_blob, preprocess


pytest.importorskip("cv2")

# (width, height): a 3 MP camera, 1080p, 720p and a portrait frame.
FRAMES = [(2304, 1296), (1920, 1080), (1280, 720), (480, 659)]
INPUT = (640, 640)
FILL = 114


def pattern(width: int, height: int) -> np.ndarray:
    """The (i * 37) mod 256 frame: neighbouring pixels and channels all differ, so a shifted column shows."""
    flat = np.arange(width * height * 3, dtype=np.uint64) * 37 % 256
    return flat.astype(np.uint8).reshape(height, width, 3)


def nearest_floor(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """ModelANT inference-v1's sampler: destination x takes source x * src // dst, rows the same."""
    src_h, src_w = frame.shape[:2]
    xs = np.arange(width) * src_w // width
    ys = np.arange(height) * src_h // height
    return frame[ys[:, None], xs[None, :]]


@pytest.mark.parametrize(("width", "height"), FRAMES)
def test_a_stretched_full_size_frame_is_sampled_by_nearest_floor(
    width: int, height: int
) -> None:
    """Each axis on its own: every destination pixel is the contract's source pixel."""
    frame = pattern(width, height)
    blob, _scale, _pad_x, _pad_y = preprocess(frame, INPUT, stretch=True)
    expected = _to_blob(nearest_floor(frame, INPUT[1], INPUT[0]))
    mismatched = np.argwhere(np.any(blob != expected, axis=(0, 1, 2)))
    assert not mismatched.size, f"columns off: {mismatched[:8].ravel().tolist()}"
    np.testing.assert_array_equal(blob, expected)


@pytest.mark.parametrize(("width", "height"), FRAMES)
def test_a_letterboxed_full_size_frame_is_sampled_by_nearest_floor(
    width: int, height: int
) -> None:
    """The aspect ratio kept, the rest padded grey: the sampled area is the contract's too."""
    frame = pattern(width, height)
    blob, _scale, pad_x, pad_y = preprocess(frame, INPUT)
    scale = min(INPUT[0] / height, INPUT[1] / width)
    new_w = min(max(int(width * scale + 0.5), 1), INPUT[1])
    new_h = min(max(int(height * scale + 0.5), 1), INPUT[0])
    padded = np.full((INPUT[0], INPUT[1], 3), FILL, dtype=np.uint8)
    padded[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = nearest_floor(
        frame, new_w, new_h
    )
    np.testing.assert_array_equal(blob, _to_blob(padded))
