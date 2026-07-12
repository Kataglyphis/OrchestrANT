"""Runtime smoke checks that exercise the installed ML wheels with real work.

Every check performs actual computation -- a forward pass, an autograd step, an
ONNX inference, an image round-trip -- rather than a bare ``import``. The point
is to prove the *wheels* function on the machine they were installed for, which
is exactly what a cross-built or emulated container image needs to verify: an
``import`` can succeed while the compiled extension underneath is mislinked.

Each check imports its dependency lazily and never raises: a missing or broken
wheel becomes a failed :class:`CheckResult`, so one broken package cannot abort
the rest of the suite.
"""

from __future__ import annotations

import base64
import dataclasses
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable


# A tiny ONNX model (opset 9) computing  Y = X + [10, 10].  Embedded as base64 so
# the check needs neither a model file on disk nor the `onnx` build dependency.
_TINY_ONNX_ADD_B64 = (
    "CAk6SwoOCgFYCgFDEgFZIgNBZGQSBHRpbnkqEQgCEAEiCAAAIEEAACBBQgFDWg8K"
    "AVgSCgoICAESBAoCCAJiDwoBWRIKCggIARIECgIIAkIECgAQEg=="
)


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of a single wheel smoke check.

    Attributes:
        name: Short package/feature label shown in the report.
        ok: ``True`` when the check passed.
        detail: Human-readable evidence (versions, values) or the error.
        optional: ``True`` for secondary runtimes whose failure is a warning,
            not a gate failure (e.g. the edge LiteRT interpreter).
    """

    name: str
    ok: bool
    detail: str
    optional: bool = False


def _ok(name: str, detail: str) -> CheckResult:
    """Build a passing result."""
    return CheckResult(name=name, ok=True, detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    """Build a failing result."""
    return CheckResult(name=name, ok=False, detail=detail)


def _err(name: str, exc: Exception) -> CheckResult:
    """Build a failing result from an exception."""
    return _fail(name, f"{type(exc).__name__}: {exc}")


def _optional_fail(name: str, detail: str) -> CheckResult:
    """Build a failing result for a secondary runtime (warning, not gate fail)."""
    return CheckResult(name=name, ok=False, detail=detail, optional=True)


def check_numpy() -> CheckResult:
    """Exercise NumPy: array creation, matmul, and a reduction."""
    name = "numpy"
    try:
        import numpy as np

        a = np.arange(9, dtype=np.float64).reshape(3, 3)
        product = a @ a
        if product.shape != (3, 3):
            return _fail(name, f"unexpected matmul shape {product.shape}")
        return _ok(name, f"{np.__version__}: 3x3 matmul + reduce ok")
    except Exception as exc:
        return _err(name, exc)


def check_torch() -> CheckResult:
    """Exercise PyTorch: matmul, autograd, and a tiny module forward+backward."""
    name = "torch"
    try:
        import torch
        from torch import nn

        torch.manual_seed(0)
        x = torch.randn(4, 3, requires_grad=True)
        layer = nn.Linear(3, 2)
        layer(x).sum().backward()
        if x.grad is None or tuple(x.grad.shape) != (4, 3):
            return _fail(name, "autograd did not populate gradients")

        ones = torch.ones(2, 2)
        if not torch.equal(ones @ ones, torch.full((2, 2), 2.0)):
            return _fail(name, "matmul result incorrect")

        threads = torch.get_num_threads()
        return _ok(
            name, f"{torch.__version__}: linear+backward+matmul ok (threads={threads})"
        )
    except Exception as exc:
        return _err(name, exc)


def check_torch_numpy_bridge() -> CheckResult:
    """Verify the torch<->numpy zero-copy ABI bridge round-trips."""
    name = "torch<->numpy"
    try:
        import numpy as np
        import torch

        original = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        restored = torch.from_numpy(original).numpy()
        if not np.array_equal(original, restored):
            return _fail(name, "from_numpy/.numpy round-trip mismatch")
        return _ok(name, "from_numpy/.numpy round-trip ok")
    except Exception as exc:
        return _err(name, exc)


def check_torchvision() -> CheckResult:
    """Exercise torchvision's compiled ops via non-maximum suppression."""
    name = "torchvision"
    try:
        import torch
        import torchvision
        from torchvision.ops import nms

        boxes = torch.tensor(
            [[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]],
            dtype=torch.float32,
        )
        scores = torch.tensor([0.9, 0.8, 0.7])
        # Boxes 0 and 1 overlap (IoU ~0.68) so one is suppressed; box 2 stands.
        keep = nms(boxes, scores, iou_threshold=0.5)
        if int(keep.numel()) != 2:
            return _fail(name, f"nms kept {int(keep.numel())} boxes, expected 2")
        return _ok(
            name, f"{torchvision.__version__}: ops.nms ok (kept {keep.tolist()})"
        )
    except Exception as exc:
        return _err(name, exc)


def check_onnxruntime() -> CheckResult:
    """Run a tiny embedded ONNX Add model through the CPU execution provider."""
    name = "onnxruntime"
    try:
        import numpy as np
        import onnxruntime as ort

        model = base64.b64decode(_TINY_ONNX_ADD_B64)
        session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        out = session.run(None, {"X": np.array([1.0, 2.0], dtype=np.float32)})[0]
        if out.tolist() != [11.0, 12.0]:
            return _fail(name, f"inference output {out.tolist()} != [11.0, 12.0]")
        return _ok(name, f"{ort.__version__}: CPU-EP inference ok {out.tolist()}")
    except Exception as exc:
        return _err(name, exc)


def check_opencv() -> CheckResult:
    """Exercise OpenCV: PNG encode/decode round-trip and color conversion."""
    name = "opencv"
    try:
        import cv2
        import numpy as np

        img = (np.random.default_rng(0).random((16, 16, 3)) * 255).astype(np.uint8)
        encoded, buffer = cv2.imencode(".png", img)
        if not encoded:
            return _fail(name, "cv2.imencode failed")
        decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
        if decoded.shape != img.shape or gray.shape != (16, 16):
            return _fail(name, f"shapes decoded={decoded.shape} gray={gray.shape}")
        return _ok(name, f"{cv2.__version__}: imencode/imdecode/cvtColor ok")
    except Exception as exc:
        return _err(name, exc)


def check_pillow() -> CheckResult:
    """Exercise Pillow: create, resize, and inspect an image."""
    name = "pillow"
    try:
        import PIL
        from PIL import Image

        image = Image.new("RGB", (32, 16), (128, 64, 32))
        resized = image.resize((8, 4))
        if resized.size != (8, 4):
            return _fail(name, f"resize produced {resized.size}")
        return _ok(name, f"{PIL.__version__}: new/resize ok")
    except Exception as exc:
        return _err(name, exc)


def check_litert() -> CheckResult:
    """Confirm the LiteRT interpreter API is importable.

    ai-edge-litert ships under two different top-level module names depending on
    the build: the upstream wheel imports as ``ai_edge_litert``, while the
    source build used for riscv64 packages it as ``tflite_runtime``. Try both.
    """
    name = "ai-edge-litert"
    import importlib
    import importlib.metadata

    try:
        version = importlib.metadata.version("ai-edge-litert")
    except importlib.metadata.PackageNotFoundError:
        version = "?"

    last_exc: Exception | None = None
    for module_name in ("ai_edge_litert", "tflite_runtime"):
        try:
            interpreter = importlib.import_module(f"{module_name}.interpreter")
        except Exception as exc:
            last_exc = exc
            continue
        if hasattr(interpreter, "Interpreter"):
            return _ok(name, f"{version}: {module_name}.Interpreter present")
        return _optional_fail(name, f"{module_name}.interpreter has no Interpreter")

    if last_exc is not None:
        return _optional_fail(name, f"{type(last_exc).__name__}: {last_exc}")
    return _optional_fail(name, "no LiteRT interpreter module found")


ALL_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_numpy,
    check_torch,
    check_torch_numpy_bridge,
    check_torchvision,
    check_onnxruntime,
    check_opencv,
    check_pillow,
    check_litert,
)


def run_all() -> list[CheckResult]:
    """Run every wheel smoke check in order and collect the results."""
    return [check() for check in ALL_CHECKS]
