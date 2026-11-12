"""Post-processing utilities for YOLO model outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

from orchestrant.pipeline.constants import POSTPROCESS_DEFAULT_CONF_THRESHOLD
from orchestrant.yolo.core.constants import CLASS_NAMES
from orchestrant.yolo.core.layout import (
    DECODERS,
    XYXY_SCORE_CLASS,
    layout_from_metadata,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    import onnxruntime as ort


@dataclass
class DecodeConfig:
    """Configuration for post-processing model outputs.

    ``scale`` is one factor for a letterbox, an (x, y) pair for a plain resize.
    ``layout`` is the name the model gave its output (see output_layout); None guesses it per frame.
    ``frame_size`` is the picture's (width, height); with it every box is cut to the picture.
    """

    scale: float | tuple[float, float]
    pad_x: int
    pad_y: int
    input_size: tuple[int, int]
    conf_threshold: float = POSTPROCESS_DEFAULT_CONF_THRESHOLD
    debug_boxes: bool = False
    layout: str | None = None
    frame_size: tuple[int, int] | None = None


def output_layout(session: ort.InferenceSession) -> str | None:
    """The layout the model names in its ``kataglyphis.output`` metadata; None when it names none."""
    named = session.get_modelmeta().custom_metadata_map.get("kataglyphis.output")
    if named is None:
        logger.warning(
            "The model names no output layout (kataglyphis.output): it is guessed from every frame"
        )
        return None
    return layout_from_metadata(named)


def _softmax(scores: np.ndarray) -> np.ndarray:
    """Apply softmax normalization to a score array.

    Args:
        scores: Raw score array (any shape).

    Returns:
        Normalized probability array with same shape.
    """
    shifted = scores - np.max(scores)
    exp_scores = np.exp(shifted)
    return exp_scores / np.sum(exp_scores)


def _xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    """Convert bounding boxes from center format to corner format.

    Args:
        boxes: Array of shape (N, 4) with [cx, cy, w, h] format.

    Returns:
        Array of shape (N, 4) with [x1, y1, x2, y2] format.
    """
    x, y, w, h = boxes.T
    x1 = x - w / 2
    y1 = y - h / 2
    x2 = x + w / 2
    y2 = y + h / 2
    return np.stack([x1, y1, x2, y2], axis=1)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    """Apply sigmoid activation to values.

    Args:
        values: Input array (any shape).

    Returns:
        Sigmoid-activated array with same shape.
    """
    return 1.0 / (1.0 + np.exp(-values))


def _squeeze_to_2d(arr: np.ndarray) -> np.ndarray:
    """Reduce array dimensions to 2D by removing singleton dimensions.

    Args:
        arr: Input array with potential singleton dimensions.

    Returns:
        2D array with singleton dimensions removed.
    """
    data = np.asarray(arr)
    data = np.squeeze(data)
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    return data


def _looks_like_xywh(boxes: np.ndarray) -> bool:
    """Detect if boxes are in center format (xywh) vs corner format (xyxy).

    Heuristic: if many boxes have x2 < x1 or y2 < y1, they're likely xywh.

    Args:
        boxes: Array of shape (N, 4).

    Returns:
        True if boxes appear to be in center format.
    """
    if boxes.size == 0:
        return False
    x2_lt_x1 = np.mean(boxes[:, 2] < boxes[:, 0])
    y2_lt_y1 = np.mean(boxes[:, 3] < boxes[:, 1])
    return bool((x2_lt_x1 > 0.3) or (y2_lt_y1 > 0.3))


def _decode_classification(output: np.ndarray) -> dict | None:
    """Decode classification model output to class prediction.

    Args:
        output: Model output array.

    Returns:
        Dictionary with class_id, score, and label, or None if not classification.
    """
    if output.ndim == 1 or (output.ndim == 2 and output.shape[0] == 1):
        scores = output if output.ndim == 1 else output[0]
        scores = scores.astype(np.float32)
        if np.any(scores < 0) or np.any(scores > 1):
            scores = _softmax(scores)
        class_id = int(np.argmax(scores))
        score = float(scores[class_id])
        label = CLASS_NAMES[class_id] if class_id < len(CLASS_NAMES) else str(class_id)
        return {
            "class_id": class_id,
            "score": score,
            "label": label,
        }
    return None


def _log_debug_boxes(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
) -> None:
    """Log the first 3 decoded boxes and their scores for debugging.

    Args:
        boxes: Bounding box array.
        scores: Confidence score array.
        class_ids: Class ID array.
    """
    logger.info(
        "Decoded boxes (first 3): {}",
        boxes[:3].round(2).tolist(),
    )
    logger.info(
        "Decoded scores/classes (first 3): {}",
        list(
            zip(
                scores[:3].round(3).tolist(),
                class_ids[:3].tolist(),
                strict=False,
            )
        ),
    )


def _unscale_and_collect(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    scale: float | tuple[float, float],
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
) -> list[dict]:
    """Unscale bounding boxes and collect detections above the threshold.

    Args:
        boxes: Bounding boxes in input image coordinates.
        scores: Confidence scores for each detection.
        class_ids: Class IDs for each detection.
        scale: Scale factor applied during preprocessing; (x, y) when per axis.
        pad_x: Horizontal padding applied during preprocessing.
        pad_y: Vertical padding applied during preprocessing.
        conf_threshold: Minimum confidence threshold.

    Returns:
        List of detection dictionaries with bbox, score, and class_id.
    """
    scale_x, scale_y = scale if isinstance(scale, tuple) else (scale, scale)
    detections: list[dict] = []
    for box, score, class_id in zip(boxes, scores, class_ids, strict=False):
        # A NaN passes every comparison and then cannot become a pixel; the other detectors drop such rows too.
        if (
            not (np.isfinite(score) and np.all(np.isfinite(box)))
            or score < conf_threshold
        ):
            continue
        x1, y1, x2, y2 = box
        x1 = (x1 - pad_x) / scale_x
        y1 = (y1 - pad_y) / scale_y
        x2 = (x2 - pad_x) / scale_x
        y2 = (y2 - pad_y) / scale_y
        detections.append(
            {
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "score": float(score),
                "class_id": int(class_id),
            }
        )
    return detections


def _prepare_boxes(
    boxes: np.ndarray,
    input_size: tuple[int, int],
    *,
    convert_xywh: bool = True,
) -> np.ndarray:
    """Scale boxes to pixel coordinates and optionally convert format.

    Args:
        boxes: Raw bounding boxes.
        input_size: (height, width) of input image.
        convert_xywh: Whether to convert from xywh to xyxy format.

    Returns:
        Processed bounding boxes in pixel coordinates.
    """
    height, width = input_size
    # An empty result has no largest value to test, and nothing to scale.
    if boxes.size and np.max(boxes) <= 1.5:
        boxes = boxes * np.array([width, height, width, height], dtype=np.float32)

    if convert_xywh and _looks_like_xywh(boxes):
        boxes = _xywh_to_xyxy(boxes)

    return boxes


def _decode_triplet_outputs(
    outputs: Sequence[np.ndarray],
    input_size: tuple[int, int],
    scale: float | tuple[float, float],
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    *,
    debug_boxes: bool,
) -> list[dict] | None:
    """Decode triplet output format (boxes, scores, class_ids).

    Args:
        outputs: Model output tensors.
        input_size: Input image dimensions.
        scale: Preprocessing scale factor.
        pad_x: Horizontal padding.
        pad_y: Vertical padding.
        conf_threshold: Confidence threshold.
        debug_boxes: Whether to log debug info.

    Returns:
        List of detections or None if not triplet format.
    """
    if len(outputs) < 3:
        return None

    boxes = _squeeze_to_2d(outputs[0])
    scores = _squeeze_to_2d(outputs[1])
    class_ids = _squeeze_to_2d(outputs[2])

    if not (boxes.ndim == 2 and boxes.shape[-1] == 4):
        return None

    if scores.ndim > 1:
        scores = scores.reshape(-1)
    if class_ids.ndim > 1:
        class_ids = class_ids.reshape(-1)

    boxes = _prepare_boxes(boxes, input_size, convert_xywh=True)

    if debug_boxes:
        _log_debug_boxes(boxes, scores, class_ids)

    return _unscale_and_collect(
        boxes, scores, class_ids, scale, pad_x, pad_y, conf_threshold
    )


def _decode_pair_outputs(
    outputs: Sequence[np.ndarray],
    input_size: tuple[int, int],
    scale: float | tuple[float, float],
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    *,
    debug_boxes: bool,
) -> list[dict] | None:
    """Decode pair output format (scores, boxes).

    Args:
        outputs: Model output tensors.
        input_size: Input image dimensions.
        scale: Preprocessing scale factor.
        pad_x: Horizontal padding.
        pad_y: Vertical padding.
        conf_threshold: Confidence threshold.
        debug_boxes: Whether to log debug info.

    Returns:
        List of detections or None if not pair format.
    """
    if len(outputs) < 2:
        return None

    scores = _squeeze_to_2d(outputs[0])
    boxes = _squeeze_to_2d(outputs[1])

    if not (scores.ndim == 2 and boxes.ndim == 2 and boxes.shape[1] == 4):
        return None

    height, width = input_size
    probs = _softmax(scores) if scores.shape[1] > 1 else scores
    class_ids = np.argmax(probs, axis=1)
    confs = probs[np.arange(len(class_ids)), class_ids]

    if np.min(boxes) < 0.0 or np.max(boxes) > 1.5:
        boxes = _sigmoid(boxes)
    boxes = boxes * np.array([width, height, width, height], dtype=np.float32)
    boxes = _xywh_to_xyxy(boxes)

    if debug_boxes:
        _log_debug_boxes(boxes, confs, class_ids)

    return _unscale_and_collect(
        boxes, confs, class_ids, scale, pad_x, pad_y, conf_threshold
    )


# The overlap above which the weaker of two boxes of one class is the same object again.
_NMS_IOU = 0.45


def _suppress_overlaps(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    conf_threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Keep the best box of each object: a raw head answers once per anchor, dozens of times for one object."""
    confident = scores >= conf_threshold
    boxes, scores, class_ids = boxes[confident], scores[confident], class_ids[confident]
    areas = np.maximum(boxes[:, 2] - boxes[:, 0], 0) * np.maximum(
        boxes[:, 3] - boxes[:, 1], 0
    )
    order = np.argsort(-scores, kind="stable")
    kept: list[int] = []
    while order.size:
        best, rest = int(order[0]), order[1:]
        kept.append(best)
        width = np.minimum(boxes[best, 2], boxes[rest, 2]) - np.maximum(
            boxes[best, 0], boxes[rest, 0]
        )
        height = np.minimum(boxes[best, 3], boxes[rest, 3]) - np.maximum(
            boxes[best, 1], boxes[rest, 1]
        )
        overlap = np.maximum(width, 0) * np.maximum(height, 0)
        union = areas[best] + areas[rest] - overlap
        same_object = (class_ids[rest] == class_ids[best]) & (
            overlap > _NMS_IOU * union
        )
        order = rest[~same_object]
    return boxes[kept], scores[kept], class_ids[kept]


def _normalize_output_data(output: np.ndarray) -> np.ndarray:
    """Normalize output data to standard 2D format.

    Args:
        output: Raw model output.

    Returns:
        Normalized 2D array.
    """
    data = output
    if data.ndim == 3:
        if data.shape[0] == 1:
            data = data[0]
        elif data.shape[0] > 1 and data.shape[-1] == 1:
            data = np.squeeze(data, axis=-1)

    # Six columns are end-to-end rows already; turning two to five of them would drop every detection.
    if data.ndim == 2 and data.shape[0] < data.shape[1] and data.shape[1] > 6:
        data = data.T
    return data


def _decode_scores_and_boxes(
    data: np.ndarray,
    input_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Extract boxes, scores, and class IDs from normalized output.

    Args:
        data: Normalized 2D output data.
        input_size: Input image dimensions.

    Returns:
        Tuple of (boxes, scores, class_ids) or None if invalid.
    """
    if data.ndim != 2 or data.shape[1] < 6:
        return None

    channels = data.shape[1]
    height, width = input_size

    if channels == 6:
        boxes = data[:, :4]
        scores = data[:, 4]
        class_ids = data[:, 5].astype(int)
    else:
        boxes = data[:, :4]
        if channels >= 85:
            obj = data[:, 4]
            class_scores = data[:, 5:]
            class_ids = np.argmax(class_scores, axis=1)
            scores = obj * class_scores[np.arange(len(class_ids)), class_ids]
        else:
            class_scores = data[:, 4:]
            class_ids = np.argmax(class_scores, axis=1)
            scores = class_scores[np.arange(len(class_ids)), class_ids]

    # An empty result has no largest value to test, and nothing to scale.
    if boxes.size and np.max(boxes) <= 1.5:
        boxes = boxes * np.array([width, height, width, height], dtype=np.float32)

    if channels != 6 or _looks_like_xywh(boxes):
        boxes = _xywh_to_xyxy(boxes)

    return boxes, scores, class_ids


def _decode_generic_output(
    output: np.ndarray,
    input_size: tuple[int, int],
    scale: float | tuple[float, float],
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    *,
    debug_boxes: bool,
) -> list[dict]:
    """Decode generic YOLO output format.

    Args:
        output: Raw model output.
        input_size: Input image dimensions.
        scale: Preprocessing scale factor.
        pad_x: Horizontal padding.
        pad_y: Vertical padding.
        conf_threshold: Confidence threshold.
        debug_boxes: Whether to log debug info.

    Returns:
        List of detections.
    """
    data = _normalize_output_data(output)
    decoded = _decode_scores_and_boxes(data, input_size)
    if decoded is None:
        return []
    boxes, scores, class_ids = decoded
    # Six columns are an end-to-end model's rows, one per object already.
    if data.shape[1] != 6:
        boxes, scores, class_ids = _suppress_overlaps(
            boxes, scores, class_ids, conf_threshold
        )

    if debug_boxes:
        _log_debug_boxes(boxes, scores, class_ids)

    return _unscale_and_collect(
        boxes, scores, class_ids, scale, pad_x, pad_y, conf_threshold
    )


def _usable_corner_rows(
    boxes: np.ndarray, scores: np.ndarray, class_ids: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop the rows no detector of the family keeps: a class without a name, or corners in the wrong order."""
    named = (class_ids >= 0) & (class_ids < len(CLASS_NAMES))
    ordered = (boxes[:, 2] >= boxes[:, 0]) & (boxes[:, 3] >= boxes[:, 1])
    keep = named & ordered
    return boxes[keep], scores[keep], class_ids[keep]


def _decode_named(layout: str, output: np.ndarray, config: DecodeConfig) -> list[dict]:
    """Decode the first output by the layout the model named, reading columns by position."""
    boxes, scores, class_ids = DECODERS[layout](output)
    if layout == XYXY_SCORE_CLASS:
        boxes, scores, class_ids = _usable_corner_rows(boxes, scores, class_ids)
    # An end-to-end model's rows are one per object already; a raw head answers once per anchor.
    if layout != XYXY_SCORE_CLASS:
        boxes, scores, class_ids = _suppress_overlaps(
            boxes, scores, class_ids, config.conf_threshold
        )

    if config.debug_boxes:
        _log_debug_boxes(boxes, scores, class_ids)

    return _unscale_and_collect(
        boxes,
        scores,
        class_ids,
        config.scale,
        config.pad_x,
        config.pad_y,
        config.conf_threshold,
    )


def _decode_guessed(
    outputs: Sequence[np.ndarray], config: DecodeConfig
) -> tuple[list, dict | None]:
    """Decode a model that names no layout: the form is guessed from each frame's shapes and values."""
    detections: list[dict] = []
    classification: dict | None = None

    raw = np.asarray(outputs[0])
    # One end-to-end row, [1, 1, 6], squeezes to the shape of six class scores; its rank tells them apart.
    one_row = raw.ndim == 3 and raw.shape[:2] == (1, 1) and raw.shape[2] == 6
    output = raw[0] if one_row else _squeeze_to_2d(raw)

    if output.ndim == 0:
        return detections, classification

    classification = None if one_row else _decode_classification(output)
    if classification is not None:
        return detections, classification

    triplet = _decode_triplet_outputs(
        outputs,
        config.input_size,
        config.scale,
        config.pad_x,
        config.pad_y,
        config.conf_threshold,
        debug_boxes=config.debug_boxes,
    )
    if triplet is not None:
        return triplet, classification

    pair = _decode_pair_outputs(
        outputs,
        config.input_size,
        config.scale,
        config.pad_x,
        config.pad_y,
        config.conf_threshold,
        debug_boxes=config.debug_boxes,
    )
    if pair is not None:
        return pair, classification

    detections = _decode_generic_output(
        output,
        config.input_size,
        config.scale,
        config.pad_x,
        config.pad_y,
        config.conf_threshold,
        debug_boxes=config.debug_boxes,
    )

    return detections, classification


def _cut_to_frame(
    detections: list[dict], frame_size: tuple[int, int] | None
) -> list[dict]:
    """Cut every box to the picture, as the Rust and C++ detectors do: a model may reach past the edge."""
    if frame_size is None:
        return detections
    width, height = frame_size
    for detection in detections:
        x1, y1, x2, y2 = detection["bbox"]
        detection["bbox"] = [
            min(max(x1, 0), width),
            min(max(y1, 0), height),
            min(max(x2, 0), width),
            min(max(y2, 0), height),
        ]
    return detections


def postprocess(
    outputs: Sequence[np.ndarray],
    config: DecodeConfig,
    *,
    debug_output: bool = False,
) -> tuple[list, dict | None]:
    """Parse model outputs for detection or classification models.

    Args:
        outputs: Model output tensors.
        config: Decoding configuration.
        debug_output: Whether to log output shapes.

    Returns:
        Tuple of (detections list, classification dict or None).
    """
    if outputs is None or len(outputs) == 0:
        return [], None

    if debug_output:
        logger.info("Model outputs: {}", [np.asarray(out).shape for out in outputs])

    if config.layout is not None:
        detections = _decode_named(config.layout, np.asarray(outputs[0]), config)
        return _cut_to_frame(detections, config.frame_size), None
    detections, classification = _decode_guessed(outputs, config)
    return _cut_to_frame(detections, config.frame_size), classification
