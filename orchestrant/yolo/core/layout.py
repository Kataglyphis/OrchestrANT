"""The detector's output layouts: a model names one, and each is decoded by column position."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np


if TYPE_CHECKING:
    from collections.abc import Callable


# The names ModelANT writes into a model; AccelerANTgine's layout_from_metadata maps the same three.
XYXY_SCORE_CLASS = "xyxy_score_class"
CXCYWH_OBJECTNESS_SCORES = "cxcywh_objectness_scores"
CXCYWH_SCORES_BY_ANCHOR = "cxcywh_scores_by_anchor"

Decoded = tuple[np.ndarray, np.ndarray, np.ndarray]


def _rows(
    output: np.ndarray, layout: str, fewest: int, *, turned: bool = False
) -> np.ndarray:
    """One candidate per row from a [1, a, b] or [a, b] output; ``turned`` when its columns are the candidates."""
    matrix = output[0] if output.ndim == 3 and output.shape[0] == 1 else output
    if matrix.ndim == 2 and turned:
        matrix = matrix.T
    if matrix.ndim != 2 or matrix.shape[1] < fewest:
        message = (
            f"The model names its output {layout}, which has {fewest} or more values "
            f"per box; the output's shape is {list(output.shape)}"
        )
        raise ValueError(message)
    return matrix


def _corners(centres: np.ndarray) -> np.ndarray:
    """Rows of cx, cy, w, h as rows of x1, y1, x2, y2."""
    half = centres[:, 2:4] / 2
    return np.hstack([centres[:, :2] - half, centres[:, :2] + half])


def _best_class(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each row's best class, and that class's score."""
    class_ids = np.argmax(scores, axis=1)
    return class_ids, scores[np.arange(len(class_ids)), class_ids]


def decode_xyxy_score_class(output: np.ndarray) -> Decoded:
    """Rows of x1, y1, x2, y2, score, class: an end-to-end model's boxes, one per object."""
    rows = _rows(output, XYXY_SCORE_CLASS, 6)
    return rows[:, :4], rows[:, 4], rows[:, 5].astype(int)


def decode_cxcywh_objectness_scores(output: np.ndarray) -> Decoded:
    """Rows of cx, cy, w, h, objectness, then a score per class; a box scores objectness times its best class."""
    rows = _rows(output, CXCYWH_OBJECTNESS_SCORES, 6)
    class_ids, best = _best_class(rows[:, 5:])
    return _corners(rows[:, :4]), rows[:, 4] * best, class_ids


def decode_cxcywh_scores_by_anchor(output: np.ndarray) -> Decoded:
    """A [4 + classes, anchors] head: each column is cx, cy, w, h, then a score per class."""
    rows = _rows(output, CXCYWH_SCORES_BY_ANCHOR, 5, turned=True)
    class_ids, best = _best_class(rows[:, 4:])
    return _corners(rows[:, :4]), best, class_ids


DECODERS: dict[str, Callable[[np.ndarray], Decoded]] = {
    XYXY_SCORE_CLASS: decode_xyxy_score_class,
    CXCYWH_OBJECTNESS_SCORES: decode_cxcywh_objectness_scores,
    CXCYWH_SCORES_BY_ANCHOR: decode_cxcywh_scores_by_anchor,
}


def layout_from_metadata(value: str) -> str:
    """The layout a ``kataglyphis.output`` value names; a value that names none is an error."""
    if value not in DECODERS:
        message = (
            f"The model names its output layout {value!r} (kataglyphis.output); "
            f"this decoder knows {', '.join(DECODERS)}"
        )
        raise ValueError(message)
    return value
