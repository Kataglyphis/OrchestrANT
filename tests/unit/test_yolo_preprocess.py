"""Unit tests for the detector's preprocessing: letterbox, and the plain resize ModelANT's models ask for."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from orchestrant.yolo.core.postprocess import DecodeConfig, postprocess
from orchestrant.yolo.core.preprocess import preprocess, wants_stretch


def _session(metadata: dict[str, str]) -> SimpleNamespace:
    meta = SimpleNamespace(custom_metadata_map=metadata)
    return SimpleNamespace(get_modelmeta=lambda: meta)


class TestWantsStretch:
    """The model's own metadata picks the preprocessing."""

    def test_stretch_metadata(self) -> None:
        """ModelANT's kataglyphis.preprocess=stretch asks for a plain resize."""
        session = _session({"kataglyphis.preprocess": "stretch"})
        assert wants_stretch(session)

    @pytest.mark.parametrize("metadata", [{}, {"kataglyphis.preprocess": "letterbox"}])
    def test_any_other_model_keeps_the_letterbox(
        self, metadata: dict[str, str]
    ) -> None:
        """A model without the key, a user's own YOLO export say, is letterboxed."""
        assert not wants_stretch(_session(metadata))


class TestStretch:
    """A plain resize: no padding, one scale per axis."""

    @pytest.fixture(autouse=True)
    def _opencv(self) -> None:
        pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")

    def test_fills_the_input_without_padding(self) -> None:
        """A 1280x720 frame becomes the whole 640x640 input, scaled 0.5 by 0.889."""
        frame = np.full((720, 1280, 3), 200, dtype=np.uint8)
        blob, scale, pad_x, pad_y = preprocess(frame, (640, 640), stretch=True)
        assert blob.shape == (1, 3, 640, 640)
        assert blob.dtype == np.float32
        assert np.allclose(blob, 200 / 255)
        assert scale == pytest.approx((0.5, 640 / 720))
        assert (pad_x, pad_y) == (0, 0)

    def test_letterbox_is_unchanged(self) -> None:
        """Without stretch, one scale and grey bars above and below."""
        frame = np.full((720, 1280, 3), 200, dtype=np.uint8)
        blob, scale, pad_x, pad_y = preprocess(frame, (640, 640))
        assert scale == 0.5
        assert (pad_x, pad_y) == (0, 140)
        assert np.allclose(blob[0, :, 0, 0], 114 / 255)

    def test_boxes_map_back_to_the_frame(self) -> None:
        """A box found where the stretched input shows an object decodes to the object in the frame."""
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[180:540, 200:600] = (0, 0, 255)
        blob, scale, pad_x, pad_y = preprocess(frame, (640, 640), stretch=True)
        red = np.argwhere(blob[0, 0] > 0.5)
        (y1, x1), (y2, x2) = red.min(axis=0), red.max(axis=0) + 1
        # Bilinear resampling may blur an edge by one pixel.
        assert np.allclose([x1, y1, x2, y2], [100, 160, 300, 480], atol=1)

        output = np.zeros((1, 300, 6), dtype=np.float32)
        output[0, 0] = [100, 160, 300, 480, 0.9, 15]
        config = DecodeConfig(
            scale=scale, pad_x=pad_x, pad_y=pad_y, input_size=(640, 640)
        )
        detections, _ = postprocess([output], config)
        assert detections[0]["bbox"] == [200, 180, 600, 540]
