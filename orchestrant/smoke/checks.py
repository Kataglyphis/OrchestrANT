"""Runtime smoke checks that exercise the installed ML wheels with real work."""

from __future__ import annotations

import base64
import dataclasses
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable


# Y = X + [10, 10] (opset 9), inline so the check needs no model file and no onnx package.
_TINY_ONNX_ADD_B64 = (
    "CAk6SwoOCgFYCgFDEgFZIgNBZGQSBHRpbnkqEQgCEAEiCAAAIEEAACBBQgFDWg8K"
    "AVgSCgoICAESBAoCCAJiDwoBWRIKCggIARIECgIIAkIECgAQEg=="
)


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of a single wheel smoke check.

    Attributes:
        optional: ``True`` when a failure is a warning, not a gate failure.
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
        import torch  # ty: ignore[unresolved-import]
        from torch import nn  # ty: ignore[unresolved-import]

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
        import torch  # ty: ignore[unresolved-import]

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
        import torch  # ty: ignore[unresolved-import]
        import torchvision  # ty: ignore[unresolved-import]
        from torchvision.ops import nms  # ty: ignore[unresolved-import]

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

        providers = ort.get_available_providers()
        if "CPUExecutionProvider" not in providers:
            return _fail(name, f"CPUExecutionProvider missing from {providers}")
        model = base64.b64decode(_TINY_ONNX_ADD_B64)
        session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        out = session.run(None, {"X": np.array([1.0, 2.0], dtype=np.float32)})[0]
        # run() returns a union and only an ndarray carries numbers; ravel gives a flat shape.
        if not isinstance(out, np.ndarray):
            return _fail(name, f"output is {type(out).__name__}, expected ndarray")
        values = [float(v) for v in np.ravel(out)]
        if values != [11.0, 12.0]:
            return _fail(name, f"inference output {values} != [11.0, 12.0]")
        return _ok(name, f"{ort.__version__}: CPU-EP inference ok {values}")
    except Exception as exc:
        return _err(name, exc)


def check_onnxruntime_genai() -> CheckResult:
    """Confirm ONNX Runtime GenAI registered its entry points; generation needs a model dir."""
    name = "onnxruntime-genai"
    try:
        import onnxruntime_genai as og
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")
    version = getattr(og, "__version__", "?")
    missing = [
        attr
        for attr in ("Model", "Tokenizer", "GeneratorParams")
        if not hasattr(og, attr)
    ]
    if missing:
        return _fail(name, f"{version}: missing entry points {missing}")
    return _ok(name, f"{version}: native binding + entry points present")


def check_tvm() -> CheckResult:
    """Exercise TVM's runtime: an NDArray round-trip through the FFI layer."""
    name = "tvm"
    try:
        import numpy as np
        import tvm  # ty: ignore[unresolved-import]
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")
    try:
        original = np.array([1.5, 2.5, 3.5], dtype=np.float32)
        if hasattr(tvm.runtime, "tensor"):
            # TVM >= 0.25: the FFI split replaced tvm.nd with runtime.tensor.
            restored = tvm.runtime.tensor(original).numpy()
            api = "runtime.tensor"
        else:
            restored = tvm.nd.array(original, device=tvm.cpu(0)).numpy()
            api = "nd.array"
        if not np.array_equal(original, restored):
            return _fail(name, f"{api} round-trip mismatch")
        return _ok(name, f"{tvm.__version__}: {api} round-trip ok")
    except Exception as exc:
        return _err(name, exc)


def check_pyav() -> CheckResult:
    """Exercise PyAV with mpeg4 by name: ``h264`` may resolve to a GPU-only encoder."""
    name = "pyav"
    try:
        import av  # ty: ignore[unresolved-import]
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")
    try:
        import io

        buf = io.BytesIO()
        with av.open(buf, mode="w", format="mp4") as container:
            stream = container.add_stream("mpeg4", rate=24)
            stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
            frame = av.VideoFrame(64, 64, "yuv420p")
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        if not buf.getvalue():
            return _fail(name, "mpeg4 encode produced no bytes")
        return _ok(name, f"{av.__version__}: in-memory mpeg4 encode ok")
    except Exception as exc:
        return _err(name, exc)


def check_iree() -> CheckResult:
    """Exercise IREE end-to-end: compile MLIR and execute it on local-task."""
    name = "iree"
    try:
        import iree.compiler.tools as compiler_tools
        import iree.runtime as iree_runtime
        import numpy as np
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")
    try:
        mlir = (
            "func.func @abs(%input : tensor<f32>) -> (tensor<f32>) {"
            " %result = math.absf %input : tensor<f32>"
            " return %result : tensor<f32> }"
        )
        vmfb = compiler_tools.compile_str(mlir, target_backends=["llvm-cpu"])
        module = iree_runtime.load_vm_flatbuffer(vmfb, driver="local-task")
        # A bare float dies in the VM marshaling layer with FAILED_PRECONDITION.
        value = float(module.abs(np.asarray(-5.0, dtype=np.float32)).to_host())
        if value != 5.0:
            return _fail(name, f"abs(-5) returned {value}, expected 5.0")
        version = getattr(iree_runtime, "__version__", "n/a")
        return _ok(name, f"{version}: MLIR compile + local-task run ok (abs(-5)=5)")
    except Exception as exc:
        return _err(name, exc)


def check_opencv() -> CheckResult:
    """Exercise OpenCV: PNG and JPEG (the MJPEG stream codec) round-trips, color conversion."""
    name = "opencv"
    try:
        import cv2
        import numpy as np

        img = (np.random.default_rng(0).random((16, 16, 3)) * 255).astype(np.uint8)
        decoded = None
        for ext, params in ((".png", []), (".jpg", [cv2.IMWRITE_JPEG_QUALITY, 80])):
            encoded, buffer = cv2.imencode(ext, img, params)
            if not encoded:
                return _fail(name, f"cv2.imencode({ext}) failed")
            decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
            if decoded is None or decoded.shape != img.shape:
                shape = None if decoded is None else decoded.shape
                return _fail(name, f"cv2.imdecode({ext}) gave shape {shape}")
        gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
        if gray.shape != (16, 16):
            return _fail(name, f"cvtColor gave shape {gray.shape}")
        return _ok(name, f"{cv2.__version__}: imencode/imdecode (png+jpg)/cvtColor ok")
    except Exception as exc:
        return _err(name, exc)


def check_opencv_dnn() -> CheckResult:
    """Exercise the OpenCV DNN module (protobuf-linked) without a model file."""
    name = "opencv-dnn"
    try:
        import cv2
        import numpy as np

        if not hasattr(cv2, "dnn"):
            return _optional_fail(name, "cv2.dnn module not present")
        img = (np.random.default_rng(0).random((32, 32, 3)) * 255).astype(np.uint8)
        blob = cv2.dnn.blobFromImage(img, size=(32, 32))
        if blob.shape != (1, 3, 32, 32):
            return _optional_fail(name, f"blobFromImage shape {blob.shape}")
        return _ok(name, f"{cv2.__version__}: cv2.dnn.blobFromImage ok")
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")


def check_opencv_codecs() -> CheckResult:
    """Round-trip TIFF/WEBP/OpenEXR; optional, since per-arch builds can drop these codecs."""
    name = "opencv-codecs"
    try:
        import cv2
        import numpy as np

        rng = np.random.default_rng(0)
        img = (rng.random((16, 16, 3)) * 255).astype(np.uint8)
        exr_img = rng.random((16, 16, 3)).astype(np.float32)
        ok_exts: list[str] = []
        bad_exts: list[str] = []
        for ext, src in ((".tif", img), (".webp", img), (".exr", exr_img)):
            try:
                encoded, buffer = cv2.imencode(ext, src)
                if encoded and cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED) is not None:
                    ok_exts.append(ext)
                else:
                    bad_exts.append(ext)
            except cv2.error:
                bad_exts.append(ext)
        if bad_exts:
            return _optional_fail(name, f"ok={ok_exts} unavailable={bad_exts}")
        return _ok(name, f"{cv2.__version__}: tiff/webp/exr round-trip ok")
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")


def check_opencv_freetype() -> CheckResult:
    """Render text via OpenCV's freetype module (source-built freetype on riscv64)."""
    name = "opencv-freetype"
    try:
        from pathlib import Path

        import cv2
        import numpy as np

        if not hasattr(cv2, "freetype"):
            return _optional_fail(name, "cv2.freetype module not present")
        # matplotlib (a core dep) bundles DejaVuSans.ttf; scanning /opt would be slow.
        import matplotlib as mpl

        fonts = list((Path(mpl.get_data_path()) / "fonts" / "ttf").glob("*.ttf"))
        if not fonts:
            fonts = list(Path("/usr/share/fonts").rglob("*.ttf"))
        if not fonts:
            return _optional_fail(name, "no .ttf font found in image to render")
        ft = cv2.freetype.createFreeType2()
        ft.loadFontData(str(fonts[0]), 0)
        canvas = np.zeros((32, 64, 3), dtype=np.uint8)
        bottom_left_origin = True
        ft.putText(
            canvas,
            "Ok",
            (2, 24),
            16,
            (255, 255, 255),
            -1,
            cv2.LINE_AA,
            bottom_left_origin,
        )
        if int(canvas.sum()) == 0:
            return _optional_fail(name, "putText drew no pixels")
        return _ok(name, f"cv2.freetype rendered text ok ({fonts[0].name})")
    except Exception as exc:
        return _optional_fail(name, f"{type(exc).__name__}: {exc}")


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
    """Confirm LiteRT imports as ``ai_edge_litert`` or (riscv64 build) ``tflite_runtime``."""
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
    check_onnxruntime_genai,
    check_opencv,
    check_opencv_dnn,
    check_opencv_codecs,
    check_opencv_freetype,
    check_pillow,
    check_pyav,
    check_tvm,
    check_iree,
    check_litert,
)


def run_all() -> list[CheckResult]:
    """Run every wheel smoke check in order and collect the results."""
    return [check() for check in ALL_CHECKS]
