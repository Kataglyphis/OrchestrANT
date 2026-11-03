"""One kernel first: third_party/ANTfrastructure/docs/linux-accelerator-images.md#a-card-without-a-cuda-kernel."""

from __future__ import annotations

import ctypes
import sys
from typing import TYPE_CHECKING, Any

import numpy as np
import onnxruntime as ort


if TYPE_CHECKING:
    from collections.abc import Sequence


# A single float Relu: ONNX Runtime's own CUDA code, not a vendor library with its own kernels.
ONE_KERNEL = bytes.fromhex(
    "08083a330a0c0a0178120179220452656c751201705a0f0a0178120a0a08080112040a020801"
    "620f0a0179120a0a08080112040a0208014202100d"
)
NO_KERNEL = "cudaErrorNoKernelImageForDevice"
ORT_LOG_FATAL = 4


def _cuda_driver() -> str:
    return "nvcuda.dll" if sys.platform == "win32" else "libcuda.so.1"


def card_name(gpu: int, driver: str | None = None) -> str | None:
    """Name the card and its compute capability as the driver reports them, or None."""
    try:
        cuda = ctypes.CDLL(driver or _cuda_driver())
        device = ctypes.c_int()
        name = ctypes.create_string_buffer(256)
        major, minor = ctypes.c_int(), ctypes.c_int()
        answered = (
            cuda.cuInit(0) == 0
            and cuda.cuDeviceGet(ctypes.byref(device), gpu) == 0
            and cuda.cuDeviceGetName(name, len(name), device) == 0
            and cuda.cuDeviceComputeCapability(
                ctypes.byref(major), ctypes.byref(minor), device
            )
            == 0
        )
    except (OSError, AttributeError):
        return None
    if not answered:
        return None
    return f"{name.value.decode(errors='replace')} (compute capability {major.value}.{minor.value})"


def run_one_kernel(providers: Sequence[str | tuple[str, dict[Any, Any]]]) -> float:
    """Return the Relu of -1 on ``providers``; ONNX Runtime raises what stopped it."""
    options = ort.SessionOptions()
    # The message below says it better than ONNX Runtime's red line about a failed node.
    options.log_severity_level = ORT_LOG_FATAL
    session = ort.InferenceSession(
        ONE_KERNEL, sess_options=options, providers=providers, enable_fallback=False
    )
    outputs = session.run(None, {"x": np.array([-1.0], dtype=np.float32)})
    return float(np.asarray(outputs[0]).item())


def require_kernel(gpu: int) -> None:
    """Raise when the card has no kernel. CUDA reports every other failure when the real session starts."""
    try:
        run_one_kernel([("CUDAExecutionProvider", {"device_id": gpu})])
    # ONNX Runtime's Fail and its siblings share no base class but Exception.
    except Exception as exc:
        if NO_KERNEL not in str(exc):
            return
    else:
        return
    card = card_name(gpu) or "this NVIDIA GPU"
    message = (
        f"CUDAExecutionProvider cannot run on {card}: this ONNX Runtime has no CUDA kernel "
        "for it. --device cpu runs on the CPU"
    )
    raise RuntimeError(message)
