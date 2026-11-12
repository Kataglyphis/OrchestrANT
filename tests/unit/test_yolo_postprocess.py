"""Unit tests for YOLO output post-processing (pure numpy, no model needed)."""

from __future__ import annotations

import numpy as np

from orchestrant.yolo.core.postprocess import (
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

    def test_per_axis_scale_is_undone(self) -> None:
        """A plain resize scales x and y by their own factors, with no padding."""
        boxes = np.array([[64.0, 64.0, 320.0, 320.0]])
        dets = _unscale_and_collect(
            boxes, np.array([1.0]), np.array([15]), (0.5, 0.8), 0, 0, 0.5
        )
        assert dets[0]["bbox"] == [128, 80, 640, 400]


class TestPostprocessEntry:
    """The public postprocess() dispatcher."""

    def _config(self) -> DecodeConfig:
        return DecodeConfig(scale=1.0, pad_x=0, pad_y=0, input_size=(640, 640))

    def test_empty_outputs_return_nothing(self) -> None:
        """No model outputs -> no detections, no classification."""
        detections, classification = postprocess([], self._config())
        assert detections == []
        assert classification is None

    def test_end_to_end_output_maps_back_per_axis(self) -> None:
        """A [1, N, 6] xyxy/score/class output on a stretched 1280x720 frame lands in frame pixels."""
        output = np.zeros((1, 300, 6), dtype=np.float32)
        output[0, 0] = [100.0, 160.0, 300.0, 480.0, 0.9, 15.0]
        output[0, 1] = [10.0, 10.0, 20.0, 20.0, 0.1, 0.0]
        config = DecodeConfig(
            scale=(640 / 1280, 640 / 720), pad_x=0, pad_y=0, input_size=(640, 640)
        )
        detections, classification = postprocess([output], config)
        assert classification is None
        assert len(detections) == 1
        assert detections[0]["class_id"] == 15
        assert detections[0]["bbox"] == [200, 180, 600, 540]

    def test_objectness_scales_the_class_score_of_an_85_channel_output(self) -> None:
        """A YOLOv5-style row's score is objectness times its best class score; more rows than channels keeps the layout."""
        output = np.zeros((1, 100, 85), dtype=np.float32)
        output[0, 0, :5] = [320.0, 320.0, 100.0, 100.0, 0.5]
        output[0, 0, 5 + 3] = 0.8
        output[0, 1, :5] = [100.0, 100.0, 20.0, 20.0, 0.2]
        output[0, 1, 5 + 1] = 0.9
        config = DecodeConfig(
            scale=1.0, pad_x=0, pad_y=0, input_size=(640, 640), conf_threshold=0.3
        )
        detections, _ = postprocess([output], config)
        assert len(detections) == 1
        assert detections[0]["class_id"] == 3
        assert np.isclose(detections[0]["score"], 0.4)
        assert detections[0]["bbox"] == [270, 270, 370, 370]

    def test_a_raw_head_gives_one_box_per_object_not_one_per_anchor(self) -> None:
        """A YOLOv8 head is [1, 4 + classes, anchors]: three anchors on one cat came back as three cats."""
        output = np.zeros((1, 84, 8400), dtype=np.float32)
        # Three anchors on one object, the middle one the surest.
        for anchor, (cx, score) in enumerate(
            [(318.0, 0.6), (320.0, 0.9), (323.0, 0.7)]
        ):
            output[0, :4, anchor] = [cx, 320.0, 100.0, 100.0]
            output[0, 4 + 15, anchor] = score
        # Another class over the same place, and the same class somewhere else: both are objects of their own.
        output[0, :4, 3] = [320.0, 320.0, 100.0, 100.0]
        output[0, 4 + 16, 3] = 0.8
        output[0, :4, 4] = [100.0, 100.0, 40.0, 40.0]
        output[0, 4 + 15, 4] = 0.5
        config = DecodeConfig(
            scale=1.0, pad_x=0, pad_y=0, input_size=(640, 640), conf_threshold=0.3
        )
        detections, _ = postprocess([output], config)
        assert [(d["class_id"], round(d["score"], 2)) for d in detections] == [
            (15, 0.9),
            (16, 0.8),
            (15, 0.5),
        ]
        assert detections[0]["bbox"] == [270, 270, 370, 370]

    def test_an_end_to_end_output_keeps_every_row(self) -> None:
        """Six columns are one row per object already; two objects in one place stay two."""
        output = np.array(
            [
                [
                    [200.0, 180.0, 600.0, 540.0, 0.9, 15.0],
                    [200.0, 180.0, 600.0, 540.0, 0.8, 15.0],
                ]
            ],
            dtype=np.float32,
        )
        config = DecodeConfig(scale=1.0, pad_x=0, pad_y=0, input_size=(640, 640))
        detections, _ = postprocess([output], config)
        assert len(detections) == 2

    def test_boxes_are_cut_to_the_picture_and_rows_that_are_no_numbers_are_dropped(
        self,
    ) -> None:
        """Rust and C++ cut boxes to the frame; a NaN row used to stop the loop with a ValueError."""
        output = np.array(
            [
                [
                    [-20.0, -10.0, 300.0, 200.0, 0.9, 15.0],
                    [500.0, 400.0, 700.0, 660.0, 0.8, 15.0],
                    [10.0, 10.0, float("nan"), 50.0, 0.7, 15.0],
                    [10.0, 10.0, 50.0, 50.0, float("nan"), 15.0],
                ]
            ],
            dtype=np.float32,
        )
        config = DecodeConfig(
            scale=1.0,
            pad_x=0,
            pad_y=0,
            input_size=(640, 640),
            conf_threshold=0.3,
            layout="xyxy_score_class",
            frame_size=(640, 480),
        )
        detections, _ = postprocess([output], config)
        assert [d["bbox"] for d in detections] == [
            [0, 0, 300, 200],
            [500, 400, 640, 480],
        ]
        # Without a frame size nothing is cut: older callers keep their boxes.
        config.frame_size = None
        detections, _ = postprocess([output], config)
        assert detections[0]["bbox"] == [-20, -10, 300, 200]

    def test_a_dynamic_end_to_end_output_decodes_at_any_row_count(self) -> None:
        """One to five rows of six values are detections, not class scores and not a turned raw head."""
        rows = np.array(
            [[10.0 * n, 20.0, 10.0 * n + 50.0, 120.0, 0.9, 15.0] for n in range(1, 6)],
            dtype=np.float32,
        )
        for count in range(1, 6):
            detections, classification = postprocess(
                [rows[np.newaxis, :count]], self._config()
            )
            assert classification is None, count
            assert [d["class_id"] for d in detections] == [15] * count
            assert detections[0]["bbox"] == [10, 20, 60, 120]

    def test_a_dynamic_output_without_rows_is_no_detection(self) -> None:
        """An end-to-end model that saw nothing returns zero rows; that is an empty list, not an error."""
        empty = np.zeros((1, 0, 6), dtype=np.float32)
        assert postprocess([empty], self._config()) == ([], None)
        triplet = [
            np.zeros((1, 0, 4), dtype=np.float32),
            np.zeros((1, 0), dtype=np.float32),
            np.zeros((1, 0), dtype=np.float32),
        ]
        detections, classification = postprocess(triplet, self._config())
        assert detections == []
        assert classification is None

    def test_classification_output_takes_priority(self) -> None:
        """A logit vector routes to the classification result."""
        logits = np.array([[0.1, 9.0, 0.2]], dtype=np.float32)
        detections, classification = postprocess([logits], self._config())
        assert detections == []
        assert classification is not None
        assert classification["class_id"] == 1
