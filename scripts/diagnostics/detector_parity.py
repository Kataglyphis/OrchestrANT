#!/usr/bin/env python3
"""Capture two real-image detector runs from one installed-bundle session.

The files are diagnostic inputs to the shared parity and ORT profile verifiers;
their existence alone does not verify or promote a package.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import operator
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Mapping


# ModelANT contracts/fixtures/NOTICE records this MIT-licensed fixture's origin.
FIXTURE_SHA256 = "6d375ac20fadfd400b3033084f8b0581dcc145f7c14e3b24b97975e379c52999"


def gpu_index(value: str) -> int:
    """Parse a non-negative index in the provider's visible device list."""
    message = "GPU index must be a non-negative integer"
    try:
        index = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(message) from error
    if index < 0:
        raise argparse.ArgumentTypeError(message)
    return index


def check_environment(environ: Mapping[str, str]) -> None:
    """Reject environment overrides that can alter imports or GPU execution."""
    unsafe = {"PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_AUDIT", "ORCHESTRANT_MODEL"}
    unexpected = sorted(
        key
        for key, value in environ.items()
        if value and (key in unsafe or key.startswith(("ORT_", "MIGRAPHX_")))
    )
    if unexpected:
        msg = f"Unset diagnostic environment overrides: {', '.join(unexpected)}"
        raise ValueError(msg)


def check_hash(path: Path, expected: str) -> None:
    """Reject missing, linked, or changed input bytes."""
    if path.is_symlink() or not path.is_file():
        msg = f"Input is missing or linked: {path}"
        raise ValueError(msg)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected:
        msg = f"Input SHA-256 mismatch: {path}"
        raise ValueError(msg)


def _decoder(session: Any, frame: Any, np: Any, pre: Any, post: Any) -> Any:  # noqa: ANN401
    """The bundle's wiring before core.detector existed: preprocess once, then run and decode."""
    inputs = session.get_inputs()
    if len(inputs) != 1:
        msg = "Expected one detector input"
        raise ValueError(msg)
    size = pre.infer_input_size(inputs[0].shape)
    blob, scale, pad_x, pad_y = pre.preprocess(
        frame, input_size=size, stretch=pre.wants_stretch(session)
    )
    # A bundle built before the model named its layout has neither field: there the decoder still guesses.
    named = (
        {
            "layout": post.output_layout(session),
            "frame_size": (frame.shape[1], frame.shape[0]),
        }
        if hasattr(post, "output_layout")
        else {}
    )
    config = post.DecodeConfig(
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        input_size=size,
        conf_threshold=0.25,
        **named,
    )

    def run() -> tuple[list, Any]:
        outputs = session.run(None, {inputs[0].name: blob})
        outputs = [np.array(value, copy=True) for value in outputs]
        return post.postprocess(outputs, config)

    return run


def capture(
    session: Any,  # noqa: ANN401
    frame: Any,  # noqa: ANN401
    np: Any,  # noqa: ANN401
    pre: Any,  # noqa: ANN401
    post: Any,  # noqa: ANN401
    detector: Any = None,  # noqa: ANN401
) -> list[dict]:
    """Run the monitor's detector twice in one session; the bundle's core.detector when it has one."""
    if detector is not None:
        wired = detector.Detector.load(session)

        def run() -> tuple[list, Any]:
            found = wired.detect(frame, conf_threshold=0.25)
            return found.detections, found.classification

    else:
        run = _decoder(session, frame, np, pre, post)
    height, width = frame.shape[:2]
    results = []
    for _ in range(2):
        detections, classification = run()
        if classification is not None or not detections:
            msg = "Detector returned no detections or classification output"
            raise ValueError(msg)
        items = [
            {
                "class": operator.index(item["class_id"]),
                "score": float(item["score"]),
                "box": [float(value) for value in item["bbox"]],
            }
            for item in detections
        ]
        results.append(
            {
                "schema_version": 1,
                "width": int(width),
                "height": int(height),
                "detections": items,
            }
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line; the device names are the production provider's."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument(
        "--device", choices=("cpu", "cuda", "migraphx", "directml"), required=True
    )
    parser.add_argument(
        "--gpu",
        type=gpu_index,
        help="visible GPU device index (default: the DirectML package's vendor adapter, otherwise 0)",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def adapter_index(device: str, requested: int | None) -> int:
    """Resolve an unset index as the monitor does; older bundles keep working off DirectML."""
    if requested is not None:
        return requested
    if device != "directml":
        return 0
    return importlib.import_module("orchestrant.yolo.cli").default_gpu(device)


def main() -> int:
    """Run with the bundle's Python and write fresh diagnostic outputs."""
    args = build_parser().parse_args()
    check_environment(os.environ)

    # Imports must resolve inside the package being exercised, never the checkout.
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415

    from orchestrant.smoke.ort_profile import bundle_paths  # noqa: PLC0415
    from orchestrant.yolo import providers  # noqa: PLC0415

    postprocess = importlib.import_module("orchestrant.yolo.core.postprocess")
    preprocess = importlib.import_module("orchestrant.yolo.core.preprocess")
    # A bundle from before core.detector keeps its own wiring; one that has it is run through it.
    try:
        detector = importlib.import_module("orchestrant.yolo.core.detector")
    except ImportError:
        detector = None

    runtime, model = bundle_paths(ort.__file__)
    for module in (cv2, np, ort, providers, postprocess, preprocess, detector):
        if module is None:
            continue
        if not Path(module.__file__).resolve().is_relative_to(runtime):
            msg = f"Module outside bundle: {module.__file__}"
            raise RuntimeError(msg)
    check_hash(args.fixture, FIXTURE_SHA256)
    check_hash(model, args.model_sha256)
    frame = cv2.imread(str(args.fixture), cv2.IMREAD_COLOR)
    if frame is None:
        msg = "Cannot decode fixture"
        raise ValueError(msg)
    args.output.mkdir(parents=False, exist_ok=False)
    session = providers.create_session(
        str(model),
        adapter_index(args.device, args.gpu),
        args.device,
        profile_prefix=str(args.output / "ort-profile"),
    )
    results = capture(session, frame, np, preprocess, postprocess, detector)
    profile = Path(session.end_profiling())
    if not profile.is_file() or not profile.resolve().is_relative_to(
        args.output.resolve()
    ):
        msg = "ORT did not write a profile inside the output directory"
        raise RuntimeError(msg)
    encoded = [json.dumps(result, allow_nan=False) + "\n" for result in results]
    for index, result in enumerate(encoded, 1):
        (args.output / f"detection-{index}.json").write_text(result, encoding="utf-8")
    (args.output / "profile-name.txt").write_text(profile.name + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
