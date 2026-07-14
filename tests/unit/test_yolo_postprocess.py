"""Unit tests for YOLO output post-processing (pure numpy, no model needed)."""

from __future__ import annotations

import numpy as np

from orchestr_ant_ion.yolo.core.postprocess import (
    DecodeConfig,
    _decode_classification,
    _looks_like_xywh,
    _prepare_boxes,
    _sigmoid,
    _softmax,
    _squeeze_to_2d,
    _unscale_and_collect,
    _xywh_to_xyxy,
    postprocess,
)


class TestMathHelpers:
    """Numeric primitives used by every decode path."""

    def test_softmax_is_a_probability_distribution(self) -> None:
        """Softmax output sums to 1 and preserves the argmax."""
        logits = np.array([1.0, 3.0, 0.5], dtype=np.float32)
        probs = _softmax(logits)
        assert np.isclose(probs.sum(), 1.0)
        assert int(np.argmax(probs)) == 1
        assert np.all(probs > 0)

    def test_sigmoid_midpoint_and_monotonicity(self) -> None:
        """Sigmoid maps 0 to 0.5 and is strictly increasing."""
        values = np.array([-2.0, 0.0, 2.0])
        out = _sigmoid(values)
        assert np.isclose(out[1], 0.5)
        assert out[0] < out[1] < out[2]

    def test_xywh_to_xyxy_known_box(self) -> None:
        """A centered 10x20 box converts to the expected corners."""
        boxes = np.array([[50.0, 40.0, 10.0, 20.0]])
        out = _xywh_to_xyxy(boxes)
        assert np.allclose(out, [[45.0, 30.0, 55.0, 50.0]])

    def test_squeeze_to_2d_drops_batch_dim(self) -> None:
        """A (1, N, M) tensor squeezes to (N, M)."""
        arr = np.zeros((1, 5, 6))
        assert _squeeze_to_2d(arr).shape == (5, 6)


class TestFormatHeuristics:
    """Box-format detection and preparation."""

    def test_xyxy_boxes_are_not_flagged_as_xywh(self) -> None:
        """Well-formed corner boxes (x2>x1, y2>y1) must not be converted."""
        boxes = np.array([[10.0, 10.0, 50.0, 60.0], [0.0, 0.0, 5.0, 5.0]])
        assert not _looks_like_xywh(boxes)

    def test_center_boxes_are_flagged_as_xywh(self) -> None:
        """Small w/h columns produce x2<x1 patterns typical of xywh."""
        boxes = np.array([[100.0, 100.0, 20.0, 10.0], [200.0, 150.0, 30.0, 12.0]])
        assert _looks_like_xywh(boxes)

    def test_empty_boxes_are_not_xywh(self) -> None:
        """The empty array short-circuits to False."""
        assert not _looks_like_xywh(np.empty((0, 4)))

    def test_prepare_boxes_scales_normalized_coordinates(self) -> None:
        """Boxes in [0, 1] are scaled up to pixel space."""
        boxes = np.array([[0.1, 0.2, 0.5, 0.6]], dtype=np.float32)
        out = _prepare_boxes(boxes, (100, 200), convert_xywh=False)
        assert np.allclose(out, [[20.0, 20.0, 100.0, 60.0]])


class TestClassificationDecode:
    """Classification-vs-detection discrimination."""

    def test_logit_vector_decodes_to_class(self) -> None:
        """A 1-D logit vector is softmaxed and argmaxed into a class dict."""
        logits = np.array([0.1, 5.0, -1.0, 0.3], dtype=np.float32)
        result = _decode_classification(logits)
        assert result is not None
        assert result["class_id"] == 1
        assert 0.0 < result["score"] <= 1.0

    def test_detection_matrix_is_not_classification(self) -> None:
        """A (N, 6) detection matrix must return None."""
        output = np.random.default_rng(0).random((100, 6)).astype(np.float32)
        assert _decode_classification(output) is None


class TestUnscaleAndCollect:
    """Letterbox unscaling and confidence gating."""

    def test_threshold_filters_low_scores(self) -> None:
        """Detections under conf_threshold are dropped."""
        boxes = np.array([[10.0, 10.0, 20.0, 20.0], [30.0, 30.0, 40.0, 40.0]])
        scores = np.array([0.9, 0.1])
        class_ids = np.array([0, 0])
        dets = _unscale_and_collect(boxes, scores, class_ids, 1.0, 0, 0, 0.5)
        assert len(dets) == 1
        assert dets[0]["score"] == 0.9

    def test_padding_and_scale_are_undone(self) -> None:
        """Bbox coordinates map back to the original image frame."""
        boxes = np.array([[110.0, 60.0, 210.0, 160.0]])
        scores = np.array([1.0])
        class_ids = np.array([2])
        dets = _unscale_and_collect(boxes, scores, class_ids, 2.0, 10, 60, 0.5)
        assert dets[0]["bbox"] == [50, 0, 100, 50]
        assert dets[0]["class_id"] == 2


class TestPostprocessEntry:
    """The public postprocess() dispatcher."""

    def _config(self) -> DecodeConfig:
        return DecodeConfig(scale=1.0, pad_x=0, pad_y=0, input_size=(640, 640))

    def test_empty_outputs_return_nothing(self) -> None:
        """No model outputs -> no detections, no classification."""
        detections, classification = postprocess([], self._config())
        assert detections == []
        assert classification is None

    def test_classification_output_takes_priority(self) -> None:
        """A logit vector routes to the classification result."""
        logits = np.array([[0.1, 9.0, 0.2]], dtype=np.float32)
        detections, classification = postprocess([logits], self._config())
        assert detections == []
        assert classification is not None
        assert classification["class_id"] == 1
