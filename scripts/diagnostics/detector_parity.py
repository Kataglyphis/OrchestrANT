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


def capture(session: Any, frame: Any, np: Any, pre: Any, post: Any) -> list[dict]:  # noqa: ANN401
    """Use the monitor's preprocessing and postprocessing twice in one session."""
    inputs = session.get_inputs()
    if len(inputs) != 1:
        msg = "Expected one detector input"
        raise ValueError(msg)
    size = pre.infer_input_size(inputs[0].shape)
    blob, scale, pad_x, pad_y = pre.preprocess(
        frame, input_size=size, stretch=pre.wants_stretch(session)
    )
    config = post.DecodeConfig(
        scale=scale, pad_x=pad_x, pad_y=pad_y, input_size=size, conf_threshold=0.25
    )
    height, width = frame.shape[:2]
    results = []
    for _ in range(2):
        outputs = session.run(None, {inputs[0].name: blob})
        outputs = [np.array(value, copy=True) for value in outputs]
        detections, classification = post.postprocess(outputs, config)
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


def main() -> int:
    """Run with the bundle's Python and write fresh diagnostic outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda", "migraphx"), required=True)
    parser.add_argument(
        "--gpu", type=gpu_index, default=0, help="visible GPU device index (default: 0)"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check_environment(os.environ)

    # Imports must resolve inside the package being exercised, never the checkout.
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415

    from orchestrant.smoke.ort_profile import bundle_paths  # noqa: PLC0415
    from orchestrant.yolo import providers  # noqa: PLC0415

    postprocess = importlib.import_module("orchestrant.yolo.core.postprocess")
    preprocess = importlib.import_module("orchestrant.yolo.core.preprocess")

    runtime, model = bundle_paths(ort.__file__)
    for module in (cv2, np, ort, providers, postprocess, preprocess):
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
        args.gpu,
        args.device,
        profile_prefix=str(args.output / "ort-profile"),
    )
    results = capture(session, frame, np, preprocess, postprocess)
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
