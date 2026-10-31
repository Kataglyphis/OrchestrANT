"""Profile execution-provider placement on the packaged detection model."""

from __future__ import annotations

import collections
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrant.paths import MODEL_NAME


if TYPE_CHECKING:
    from collections.abc import Iterable


PROVIDERS = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "migraphx": "MIGraphXExecutionProvider",
}


def operator_counts(events: object) -> collections.Counter[str]:
    """Count model Node events by provider; other trace records prove nothing."""
    if not isinstance(events, list):
        msg = "ORT profile must contain a list of events"
        raise TypeError(msg)
    counts: collections.Counter[str] = collections.Counter()
    for event in events:
        if not isinstance(event, dict) or event.get("cat") != "Node":
            continue
        args = event.get("args")
        if not isinstance(args, dict):
            continue
        provider, operator = args.get("provider"), args.get("op_name")
        if (
            isinstance(provider, str)
            and isinstance(operator, str)
            and operator.strip()
            and not operator.strip().casefold().startswith("memcpy")
        ):
            counts[provider] += 1
    return counts


def bundle_paths(runtime_module: str) -> tuple[Path, Path]:
    """Require the interpreter, ORT module and model to belong to one bundle."""
    runtime = Path(sys.prefix).resolve()
    data = os.environ.get("ORCHESTRANT_DATA_DIR", "")
    expected_data = runtime.parent / "share" / "orchestrant"
    if not data or Path(data).resolve() != expected_data.resolve():
        msg = (
            "Run this profile through the installed bundle's orchestrant-smoke launcher"
        )
        raise RuntimeError(msg)
    if not Path(runtime_module).resolve().is_relative_to(runtime):
        msg = "ONNX Runtime is outside the bundle's Python runtime"
        raise RuntimeError(msg)
    model = expected_data / "models" / MODEL_NAME
    if not model.is_file() or not model.resolve().is_relative_to(
        expected_data.resolve()
    ):
        msg = f"The bundled model is missing or outside the bundle: {model}"
        raise RuntimeError(msg)
    return runtime, model.resolve()


def _inputs(entries: Iterable[Any], numpy: Any) -> dict[str, Any]:  # noqa: ANN401 -- optional module is loaded lazily
    result = {}
    for entry in entries:
        if entry.type != "tensor(float)":
            msg = f"Unsupported model input type for {entry.name}: {entry.type}"
            raise ValueError(msg)
        shape = [dim if isinstance(dim, int) and dim > 0 else 1 for dim in entry.shape]
        result[entry.name] = numpy.zeros(shape, dtype=numpy.float32)
    if not result:
        msg = "The bundled model has no inputs"
        raise ValueError(msg)
    return result


def profile_model(
    ort: Any,  # noqa: ANN401 -- optional module
    numpy: Any,  # noqa: ANN401 -- optional module
    model: Path,
    device: str,
    gpu: int,
) -> dict[str, Any]:
    """Run one inference and require at least one node on the requested provider."""
    provider = PROVIDERS[device]
    if gpu < 0:
        msg = "GPU device index must be non-negative"
        raise ValueError(msg)
    if provider not in ort.get_available_providers():
        msg = f"ONNX Runtime does not provide {provider}"
        raise RuntimeError(msg)
    from orchestrant.yolo.providers import create_session

    with tempfile.TemporaryDirectory(prefix="orchestrant-ort-profile-") as directory:
        session = create_session(
            str(model), gpu, device, profile_prefix=str(Path(directory) / "profile")
        )
        if provider not in session.get_providers():
            msg = f"ONNX Runtime did not activate {provider}"
            raise RuntimeError(msg)
        session.run(None, _inputs(session.get_inputs(), numpy))
        events = json.loads(Path(session.end_profiling()).read_text(encoding="utf-8"))
    counts = operator_counts(events)
    if not counts[provider]:
        msg = f"No model operators ran on {provider}: {dict(counts)}"
        raise RuntimeError(msg)
    return {
        "device": device,
        "device_id": gpu,
        "model": str(model),
        "providers": dict(counts),
    }


def run_bundle_profile(device: str, gpu: int) -> dict[str, Any]:
    """Profile the bundle's model using its own installed ONNX Runtime."""
    import numpy as np
    import onnxruntime

    runtime, model = bundle_paths(onnxruntime.__file__)
    report = profile_model(onnxruntime, np, model, device, gpu)
    report.update(
        {
            "runtime": str(runtime),
            "onnxruntime": onnxruntime.__version__,
            "onnxruntime_module": onnxruntime.__file__,
            "profile_module": __file__,
        }
    )
    return report
