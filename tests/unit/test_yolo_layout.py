"""The detector's named output layouts: chosen once from the model's metadata, decoded by column position."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, NoReturn

import numpy as np
import pytest
from loguru import logger

from orchestrant.yolo.core import layout
from orchestrant.yolo.core.postprocess import DecodeConfig, output_layout, postprocess


if TYPE_CHECKING:
    from collections.abc import Iterator


# The package re-exports the function under the module's name, so the module comes from the import system.
decoder = importlib.import_module("orchestrant.yolo.core.postprocess")

NAMES = (
    layout.XYXY_SCORE_CLASS,
    layout.CXCYWH_OBJECTNESS_SCORES,
    layout.CXCYWH_SCORES_BY_ANCHOR,
)
RAW = (layout.CXCYWH_OBJECTNESS_SCORES, layout.CXCYWH_SCORES_BY_ANCHOR)
GUESSING = (
    "_decode_guessed",
    "_decode_classification",
    "_decode_triplet_outputs",
    "_decode_pair_outputs",
    "_decode_generic_output",
    "_decode_scores_and_boxes",
    "_normalize_output_data",
    "_prepare_boxes",
    "_looks_like_xywh",
    "_squeeze_to_2d",
    "_softmax",
    "_sigmoid",
)
Candidate = tuple[tuple[float, float, float, float], int, float]


def _session(metadata: dict[str, str]) -> SimpleNamespace:
    meta = SimpleNamespace(custom_metadata_map=metadata)
    return SimpleNamespace(get_modelmeta=lambda: meta)


def _config(
    name: str | None,
    scale: float | tuple[float, float] = 1.0,
    *,
    debug_boxes: bool = False,
) -> DecodeConfig:
    return DecodeConfig(
        scale=scale,
        pad_x=0,
        pad_y=0,
        input_size=(640, 640),
        conf_threshold=0.3,
        debug_boxes=debug_boxes,
        layout=name,
    )


def _found(detections: list[dict]) -> list[tuple[int, float, list[int]]]:
    return [(d["class_id"], round(d["score"], 2), d["bbox"]) for d in detections]


def _tensor(name: str, candidates: list[Candidate], classes: int = 3) -> np.ndarray:
    """Centre boxes with one class score each, as the tensor a model of that layout gives."""
    rows = np.zeros((len(candidates), 4 + classes), dtype=np.float32)
    for row, (box, class_id, score) in zip(rows, candidates, strict=True):
        row[:4] = box
        row[4 + class_id] = score
    if name == layout.CXCYWH_SCORES_BY_ANCHOR:
        return rows.T[np.newaxis]
    if name == layout.CXCYWH_OBJECTNESS_SCORES:
        return np.insert(rows, 4, 1.0, axis=1)[np.newaxis]
    corners = [
        [x - w / 2, y - h / 2, x + w / 2, y + h / 2, score, class_id]
        for (x, y, w, h), class_id, score in candidates
    ]
    return np.array(corners, dtype=np.float32).reshape(1, len(candidates), 6)


@pytest.fixture
def logged_warnings() -> Iterator[list[str]]:
    """Every warning the code under test logs."""
    messages: list[str] = []
    sink = logger.add(
        lambda message: messages.append(message.record["message"]), level="WARNING"
    )
    yield messages
    logger.remove(sink)


@pytest.fixture
def no_guessing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every function of the guessing decoder fails the test that reaches it."""

    def guessed(*_args: object, **_kwargs: object) -> NoReturn:
        message = "the guessing decoder ran"
        raise AssertionError(message)

    for name in GUESSING:
        monkeypatch.setattr(decoder, name, guessed)


class TestTheModelNamesItsLayout:
    """``kataglyphis.output`` is read once, when the session opens."""

    @pytest.mark.parametrize("name", NAMES)
    def test_a_known_name_in_the_metadata_is_the_layout(
        self, name: str, logged_warnings: list[str]
    ) -> None:
        """The three names AccelerANTgine maps are the three this decoder takes."""
        assert output_layout(_session({"kataglyphis.output": name})) == name
        assert logged_warnings == []

    def test_the_layouts_are_exactly_the_three_shared_names(self) -> None:
        """A fourth name would be one the C++ detector does not know."""
        assert set(layout.DECODERS) == {
            "xyxy_score_class",
            "cxcywh_objectness_scores",
            "cxcywh_scores_by_anchor",
        }

    @pytest.mark.parametrize("value", ["yolo_v99", "", "XYXY_SCORE_CLASS"])
    def test_an_unknown_name_is_an_error_that_names_the_value(self, value: str) -> None:
        """A model that names a layout nobody decodes must not start and guess."""
        with pytest.raises(ValueError, match=repr(value)) as error:
            output_layout(_session({"kataglyphis.output": value}))
        assert "kataglyphis.output" in str(error.value)

    def test_a_model_without_the_key_is_guessed_and_says_so_once(
        self, logged_warnings: list[str]
    ) -> None:
        """A YOLO export of the user's own has no metadata: it still decodes, and the log says how."""
        session = _session({"kataglyphis.preprocess": "stretch"})
        assert output_layout(session) is None
        assert len(logged_warnings) == 1
        assert "guessed" in logged_warnings[0]


class TestXyxyScoreClass:
    """An end-to-end model's rows: x1, y1, x2, y2, score, class."""

    def test_rows_decode_to_frame_boxes_by_column_position(self) -> None:
        """The third row's corners are the wrong way round; the guess would read every row as a centre box."""
        output = np.array(
            [
                [
                    [100.0, 160.0, 300.0, 480.0, 0.9, 15.0],
                    [10.0, 10.0, 20.0, 20.0, 0.1, 0.0],
                    [40.0, 80.0, 20.0, 60.0, 0.7, 3.0],
                ]
            ],
            dtype=np.float32,
        )
        config = _config(layout.XYXY_SCORE_CLASS, (640 / 1280, 640 / 720))
        detections, classification = postprocess([output], config)
        assert classification is None
        assert _found(detections) == [
            (15, 0.9, [200, 180, 600, 540]),
            (3, 0.7, [80, 90, 40, 67]),
        ]

    def test_small_coordinates_stay_input_pixels(self) -> None:
        """The guess multiplies boxes whose largest value is under 1.5 by the input size."""
        output = np.array([[[0.5, 0.5, 1.5, 1.5, 0.9, 2.0]]], dtype=np.float32)
        detections, _ = postprocess([output], _config(layout.XYXY_SCORE_CLASS))
        assert _found(detections) == [(2, 0.9, [0, 0, 1, 1])]

    def test_two_objects_in_one_place_stay_two(self) -> None:
        """One row is one object already, so no row is suppressed."""
        output = np.array(
            [
                [
                    [200.0, 180.0, 600.0, 540.0, 0.9, 15.0],
                    [200.0, 180.0, 600.0, 540.0, 0.8, 15.0],
                ]
            ],
            dtype=np.float32,
        )
        detections, _ = postprocess([output], _config(layout.XYXY_SCORE_CLASS))
        assert _found(detections) == [
            (15, 0.9, [200, 180, 600, 540]),
            (15, 0.8, [200, 180, 600, 540]),
        ]


class TestTheRawLayouts:
    """Raw heads: centre boxes with a score per class, one answer per anchor."""

    def test_objectness_rows_score_objectness_times_the_best_class(self) -> None:
        """Rows of cx, cy, w, h, objectness and three class scores; eight columns, far from the guess's 85."""
        output = np.array(
            [
                [
                    [320.0, 320.0, 100.0, 100.0, 0.5, 0.1, 0.8, 0.2],
                    [100.0, 200.0, 40.0, 60.0, 0.9, 0.9, 0.0, 0.1],
                    [500.0, 500.0, 50.0, 50.0, 0.2, 0.0, 0.0, 0.9],
                ]
            ],
            dtype=np.float32,
        )
        config = _config(layout.CXCYWH_OBJECTNESS_SCORES)
        detections, classification = postprocess([output], config)
        assert classification is None
        assert _found(detections) == [
            (0, 0.81, [80, 170, 120, 230]),
            (1, 0.4, [270, 270, 370, 370]),
        ]

    def test_anchor_columns_are_the_candidates(self) -> None:
        """Seven channels by three anchors: more rows than columns, which the guess would not turn."""
        output = np.array(
            [
                [
                    [320.0, 100.0, 500.0],
                    [320.0, 200.0, 500.0],
                    [100.0, 40.0, 50.0],
                    [100.0, 60.0, 50.0],
                    [0.1, 0.9, 0.0],
                    [0.8, 0.0, 0.0],
                    [0.2, 0.1, 0.2],
                ]
            ],
            dtype=np.float32,
        )
        config = _config(layout.CXCYWH_SCORES_BY_ANCHOR)
        detections, classification = postprocess([output], config)
        assert classification is None
        assert _found(detections) == [
            (0, 0.9, [80, 170, 120, 230]),
            (1, 0.8, [270, 270, 370, 370]),
        ]

    @pytest.mark.parametrize("name", RAW)
    def test_one_object_seen_by_three_anchors_is_one_box(self, name: str) -> None:
        """Another class in the same place and the same class elsewhere are objects of their own."""
        output = _tensor(
            name,
            [
                ((318.0, 320.0, 100.0, 100.0), 1, 0.6),
                ((320.0, 320.0, 100.0, 100.0), 1, 0.9),
                ((323.0, 320.0, 100.0, 100.0), 1, 0.7),
                ((320.0, 320.0, 100.0, 100.0), 2, 0.8),
                ((100.0, 100.0, 40.0, 40.0), 1, 0.5),
            ],
        )
        detections, _ = postprocess([output], _config(name))
        assert _found(detections) == [
            (1, 0.9, [270, 270, 370, 370]),
            (2, 0.8, [270, 270, 370, 370]),
            (1, 0.5, [80, 80, 120, 120]),
        ]


class TestANamedLayoutIsNeverGuessed:
    """The per-frame path of a named layout looks at shapes and positions only."""

    @pytest.mark.parametrize("name", NAMES)
    @pytest.mark.usefixtures("no_guessing")
    def test_a_named_layout_never_runs_the_guessing_decoder(self, name: str) -> None:
        """Every guessing function raises here, and the frame still decodes."""
        output = _tensor(name, [((320.0, 320.0, 100.0, 100.0), 1, 0.9)])
        detections, classification = postprocess([output], _config(name))
        assert classification is None
        assert _found(detections) == [(1, 0.9, [270, 270, 370, 370])]

    @pytest.mark.usefixtures("no_guessing")
    def test_a_model_that_names_no_layout_is_still_guessed(self) -> None:
        """Without a name the old decoder runs, which the same trap proves."""
        output = _tensor(
            layout.XYXY_SCORE_CLASS, [((320.0, 320.0, 100.0, 100.0), 1, 0.9)]
        )
        with pytest.raises(AssertionError, match="the guessing decoder ran"):
            postprocess([output], _config(None))

    @pytest.mark.parametrize("name", NAMES)
    def test_an_unbatched_output_decodes_like_a_batched_one(self, name: str) -> None:
        """[rows, values] without the leading 1 is the same matrix."""
        output = _tensor(name, [((320.0, 320.0, 100.0, 100.0), 1, 0.9)])
        batched, _ = postprocess([output], _config(name))
        unbatched, _ = postprocess([output[0]], _config(name))
        assert _found(unbatched) == _found(batched) == [(1, 0.9, [270, 270, 370, 370])]

    @pytest.mark.parametrize("name", NAMES)
    def test_an_output_without_candidates_is_no_detection(self, name: str) -> None:
        """A dynamic model that saw nothing returns no rows; that is an empty list, not an error."""
        assert postprocess([_tensor(name, [])], _config(name)) == ([], None)

    @pytest.mark.parametrize(
        ("name", "shape"),
        [
            (layout.XYXY_SCORE_CLASS, (1, 300, 5)),
            (layout.XYXY_SCORE_CLASS, (300,)),
            (layout.XYXY_SCORE_CLASS, (2, 300, 6)),
            (layout.CXCYWH_OBJECTNESS_SCORES, (1, 100, 5)),
            (layout.CXCYWH_SCORES_BY_ANCHOR, (1, 4, 8400)),
        ],
    )
    def test_an_output_too_small_for_its_layout_is_an_error_that_names_both(
        self, name: str, shape: tuple[int, ...]
    ) -> None:
        """Reading a column that is not there must not turn into a guess."""
        output = np.zeros(shape, dtype=np.float32)
        with pytest.raises(ValueError, match=name) as error:
            postprocess([output], _config(name))
        assert str(list(shape)) in str(error.value)

    def test_debug_boxes_logs_what_a_named_layout_decoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--debug-boxes shows the boxes before they are mapped back to the frame."""
        logged: list[list[float]] = []
        monkeypatch.setattr(
            decoder,
            "_log_debug_boxes",
            lambda boxes, _scores, _class_ids: logged.extend(boxes.tolist()),
        )
        output = _tensor(
            layout.XYXY_SCORE_CLASS, [((320.0, 320.0, 100.0, 100.0), 1, 0.9)]
        )
        config = _config(layout.XYXY_SCORE_CLASS, 0.5, debug_boxes=True)
        detections, _ = postprocess([output], config)
        assert logged == [[270.0, 270.0, 370.0, 370.0]]
        assert _found(detections) == [(1, 0.9, [540, 540, 740, 740])]
