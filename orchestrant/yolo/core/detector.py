"""One model's preprocess, run and decode, wired once for the monitor loop, the self-test and the golden."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from orchestrant.yolo.core.postprocess import DecodeConfig, output_layout, postprocess
from orchestrant.yolo.core.preprocess import infer_input_size, preprocess, wants_stretch


if TYPE_CHECKING:
    import onnxruntime as ort


@dataclass(frozen=True, slots=True)
class Detected:
    """One frame's result: what was found, a classifier's answer, the raw outputs and the model's time alone."""

    detections: list[dict[str, Any]]
    classification: dict[str, Any] | None
    outputs: list[np.ndarray]
    inference_ms: float


@dataclass(frozen=True, slots=True)
class Detector:
    """A session with what its model asks for, read once: a drifted stretch flag or layout in one caller is gone."""

    session: Any
    input_name: str
    input_size: tuple[int, int]
    stretch: bool
    layout: str | None

    @classmethod
    def load(cls, session: ort.InferenceSession) -> Detector:
        """Read the input, the preprocessing and the output layout; an unknown layout raises here, before any frame."""
        model_input = session.get_inputs()[0]
        return cls(
            session=session,
            input_name=model_input.name,
            input_size=infer_input_size(model_input.shape),
            stretch=wants_stretch(session),
            layout=output_layout(session),
        )

    def detect(
        self,
        frame: np.ndarray,
        *,
        conf_threshold: float,
        debug_boxes: bool = False,
        debug_output: bool = False,
    ) -> Detected:
        """Preprocess `frame`, run the model once and decode its outputs back onto the frame."""
        blob, scale, pad_x, pad_y = preprocess(
            frame, input_size=self.input_size, stretch=self.stretch
        )
        started = time.perf_counter()
        raw = self.session.run(None, {self.input_name: blob})
        inference_ms = (time.perf_counter() - started) * 1000
        outputs = [np.asarray(output) for output in raw]
        config = DecodeConfig(
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            input_size=self.input_size,
            conf_threshold=conf_threshold,
            debug_boxes=debug_boxes,
            layout=self.layout,
            frame_size=(frame.shape[1], frame.shape[0]),
        )
        detections, classification = postprocess(
            outputs, config, debug_output=debug_output
        )
        return Detected(detections, classification, outputs, inference_ms)
