"""Post-processing utilities for YOLO model outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

from kataglyphispythoninference.yolo.constants import CLASS_NAMES


if TYPE_CHECKING:
    from collections.abc import Sequence


def _softmax(scores: np.ndarray) -> np.ndarray:
    shifted = scores - np.max(scores)
    exp_scores = np.exp(shifted)
    return exp_scores / np.sum(exp_scores)


def _xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    x, y, w, h = boxes.T
    x1 = x - w / 2
    y1 = y - h / 2
    x2 = x + w / 2
    y2 = y + h / 2
    return np.stack([x1, y1, x2, y2], axis=1)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


def _squeeze_to_2d(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    data = np.squeeze(data)
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    return data


def _looks_like_xywh(boxes: np.ndarray) -> bool:
    if boxes.size == 0:
        return False
    x2_lt_x1 = np.mean(boxes[:, 2] < boxes[:, 0])
    y2_lt_y1 = np.mean(boxes[:, 3] < boxes[:, 1])
    return bool((x2_lt_x1 > 0.3) or (y2_lt_y1 > 0.3))


def _decode_classification(output: np.ndarray) -> dict | None:
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


def _decode_triplet_outputs(
    outputs: Sequence[np.ndarray],
    input_size: tuple[int, int],
    scale: float,
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    debug_boxes: bool,
) -> list[dict] | None:
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

    height, width = input_size
    if np.max(boxes) <= 1.5:
        boxes = boxes * np.array([width, height, width, height], dtype=np.float32)

    if _looks_like_xywh(boxes):
        boxes = _xywh_to_xyxy(boxes)

    if debug_boxes:
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

    detections: list[dict] = []
    for box, score, class_id in zip(boxes, scores, class_ids, strict=False):
        if score < conf_threshold:
            continue
        x1, y1, x2, y2 = box
        x1 = (x1 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        x2 = (x2 - pad_x) / scale
        y2 = (y2 - pad_y) / scale
        detections.append(
            {
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "score": float(score),
                "class_id": int(class_id),
            }
        )
    return detections


def _decode_pair_outputs(
    outputs: Sequence[np.ndarray],
    input_size: tuple[int, int],
    scale: float,
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    debug_boxes: bool,
) -> list[dict] | None:
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
        logger.info(
            "Decoded boxes (first 3): {}",
            boxes[:3].round(2).tolist(),
        )
        logger.info(
            "Decoded scores/classes (first 3): {}",
            list(
                zip(
                    confs[:3].round(3).tolist(),
                    class_ids[:3].tolist(),
                    strict=False,
                )
            ),
        )

    detections: list[dict] = []
    for box, score, class_id in zip(boxes, confs, class_ids, strict=False):
        if score < conf_threshold:
            continue
        x1, y1, x2, y2 = box
        x1 = (x1 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        x2 = (x2 - pad_x) / scale
        y2 = (y2 - pad_y) / scale
        detections.append(
            {
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "score": float(score),
                "class_id": int(class_id),
            }
        )
    return detections


def _decode_generic_output(
    output: np.ndarray,
    input_size: tuple[int, int],
    scale: float,
    pad_x: int,
    pad_y: int,
    conf_threshold: float,
    debug_boxes: bool,
) -> list[dict]:
    detections: list[dict] = []
    data = output
    if data.ndim == 3:
        if data.shape[0] == 1:
            data = data[0]
        elif data.shape[0] > 1 and data.shape[-1] == 1:
            data = np.squeeze(data, axis=-1)

    if data.ndim == 2 and data.shape[0] < data.shape[1] and data.shape[1] >= 6:
        data = data.T

    if data.ndim != 2 or data.shape[1] < 6:
        return detections

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

    if np.max(boxes) <= 1.5:
        boxes = boxes * np.array([width, height, width, height], dtype=np.float32)

    if channels != 6 or _looks_like_xywh(boxes):
        boxes = _xywh_to_xyxy(boxes)

    if debug_boxes:
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

    for box, score, class_id in zip(boxes, scores, class_ids, strict=False):
        if score < conf_threshold:
            continue
        x1, y1, x2, y2 = box
        x1 = (x1 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        x2 = (x2 - pad_x) / scale
        y2 = (y2 - pad_y) / scale
        detections.append(
            {
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "score": float(score),
                "class_id": int(class_id),
            }
        )
    return detections


def postprocess(
    outputs: Sequence[np.ndarray],
    scale: float,
    pad_x: int,
    pad_y: int,
    input_size: tuple[int, int],
    *,
    conf_threshold: float = 0.5,
    debug_output: bool = False,
    debug_boxes: bool = False,
) -> tuple[list, dict | None]:
    """Parse model outputs for detection or classification models."""
    detections: list[dict] = []
    classification: dict | None = None

    if outputs is None or len(outputs) == 0:
        return detections, classification

    if debug_output:
        logger.info("Model outputs: {}", [np.asarray(out).shape for out in outputs])

    output = _squeeze_to_2d(outputs[0])

    if output.ndim == 0:
        return detections, classification

    classification = _decode_classification(output)
    if classification is not None:
        return detections, classification

    triplet = _decode_triplet_outputs(
        outputs,
        input_size,
        scale,
        pad_x,
        pad_y,
        conf_threshold,
        debug_boxes,
    )
    if triplet is not None:
        return triplet, classification

    pair = _decode_pair_outputs(
        outputs,
        input_size,
        scale,
        pad_x,
        pad_y,
        conf_threshold,
        debug_boxes,
    )
    if pair is not None:
        return pair, classification

    detections = _decode_generic_output(
        output,
        input_size,
        scale,
        pad_x,
        pad_y,
        conf_threshold,
        debug_boxes,
    )

    return detections, classification
