"""CLI argument parsing for the YOLO monitor."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from orchestrant.paths import bundle_data_dir, default_model_path


# The family's one default score threshold, measured at the cameras; ModelANT's contract states it.
DEFAULT_SCORE_THRESHOLD = 0.25


def _directml_vendor() -> str | None:
    """Read the package's vendor marker, if this is a Windows DirectML flavor."""
    if sys.platform != "win32":
        return None
    data = bundle_data_dir()
    if data is None:
        return None
    try:
        vendor = (data / "directml-vendor.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if vendor not in {"amd", "nvidia"}:
        message = "Invalid DirectML package vendor marker"
        raise ValueError(message)
    return vendor


def default_gpu(device: str) -> int:
    """Return the index an unset ``--gpu`` means: the DirectML package's vendor adapter, otherwise 0."""
    vendor = _directml_vendor() if device == "directml" else None
    if vendor is None:
        return 0
    from orchestrant.smoke.directml import physical_adapter  # noqa: PLC0415 -- DXGI is needed only for vendor packages

    return physical_adapter(vendor)[0]


def _resolve_gpu_defaults(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    """Choose a vendor flavor only for options the user did not explicitly set."""
    try:
        if args.device is None:
            args.device = "directml" if _directml_vendor() else "auto"
        if args.gpu is None:
            args.gpu = default_gpu(args.device)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(
            f"Could not select the package GPU: {error}; use --device cpu to run on CPU"
        )


def config_intra_op_threads(path: Path) -> int | None:
    """Read ``[onnxruntime] intra_op_threads`` from a TOML config; ``None`` when unset."""
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    section = config.get("onnxruntime", {})
    if not isinstance(section, dict):
        message = f"{path}: [onnxruntime] must be a table"
        raise TypeError(message)
    threads = section.get("intra_op_threads")
    if threads is None:
        return None
    if type(threads) is not int or threads < 0:
        message = f"{path}: onnxruntime.intra_op_threads must be an integer >= 0"
        raise ValueError(message)
    return threads


def _resolve_threads(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """--threads wins over --config, which wins over the shipped default (``None``)."""
    if args.threads is not None:
        if args.threads < 0:
            parser.error("--threads must be 0 (ONNX Runtime's default) or more")
        args.intra_op_threads = args.threads
        return
    try:
        args.intra_op_threads = (
            config_intra_op_threads(args.config) if args.config else None
        )
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        parser.error(f"Could not read --config: {error}")


def _add_camera_args(parser: argparse.ArgumentParser) -> None:
    """The capture backend, the viewer and where it serves, and the camera's size and rate."""
    parser.add_argument(
        "--backend", type=str, choices=["opencv", "gstreamer"], default="opencv"
    )
    parser.add_argument(
        "--ui",
        type=str,
        choices=["auto", "flet", "opencv"],
        default="auto",
        help="flet: the Flet viewer; opencv: OpenCV's window; auto: flet where installed",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Serve the Flet viewer to browsers instead of opening a window",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Address --web binds")
    parser.add_argument("--port", type=int, default=8550, help="Port --web serves on")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    """The model, the self-test, the score threshold and the inference provider."""
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="ONNX model; default: $ORCHESTRANT_MODEL, the install's models dir, then resources/models (how to fetch it: resources/models/README.md)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Load the model, run one inference on a blank frame and exit; needs no camera or display",
    )
    parser.add_argument("--conf", type=float, default=DEFAULT_SCORE_THRESHOLD)
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda", "migraphx", "directml"],
        default=None,
        help="Inference provider; explicit GPU choices fail if unavailable (default: auto, DirectML in a vendor package)",
    )
    parser.add_argument(
        "--gpu",
        type=int,
        default=None,
        help="GPU device index (default: package vendor, otherwise 0)",
    )


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Refuse the combinations argparse cannot, then fill the defaults that depend on others."""
    if args.describe and args.describe_model is None:
        parser.error("--describe needs --describe-model, a GenAI model directory")
    if args.describe_interval <= 0:
        parser.error("--describe-interval must be above 0 seconds")
    if args.self_test_image is not None and not args.self_test:
        parser.error("--self-test-image goes with --self-test")
    _resolve_gpu_defaults(args, parser)
    _resolve_threads(args, parser)
    if args.model is None:
        try:
            args.model = str(default_model_path())
        except FileNotFoundError as exc:
            parser.error(str(exc))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Object Detection with System Monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    Examples:
      yolo-monitor --backend opencv
      yolo-monitor --backend gstreamer
      yolo-monitor --backend gstreamer --width 1280 --height 720 --fps 60
      yolo-monitor --web --host 0.0.0.0
        """,
    )

    _add_camera_args(parser)
    _add_model_args(parser)
    parser.add_argument("--no-display", action="store_true")
    _add_thread_args(parser)
    _add_overlay_args(parser)
    _add_debug_args(parser)
    _add_describe_args(parser)

    args = parser.parse_args(argv)
    _validate(args, parser)
    return args


def _add_describe_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Have a language model describe the detections in a sentence, off the frame loop",
    )
    parser.add_argument("--describe-model", type=Path, help="A GenAI model directory")
    parser.add_argument(
        "--describe-provider", default="cpu", choices=["cpu", "cuda", "dml"]
    )
    parser.add_argument("--describe-language", default="en", choices=["de", "en"])
    parser.add_argument(
        "--describe-interval",
        type=float,
        default=30.0,
        help="Seconds at least between two descriptions; an unchanged scene gets none",
    )
    parser.add_argument("--describe-max-tokens", type=int, default=64)
    parser.add_argument(
        "--describe-out", type=Path, help="Append each description as a JSON line"
    )
    parser.add_argument(
        "--self-test-image",
        type=Path,
        help="With --self-test: run on this image instead of a blank frame and report what was found",
    )


def _add_thread_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="ONNX Runtime intra-op threads; 0 is ORT's default "
        "(default: --config, else the usable CPUs but one, at most 8)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="TOML file; [onnxruntime] intra_op_threads sets the thread count --threads overrides",
    )


def _add_overlay_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cpu-plot",
        action="store_true",
        help="Show 2D time-series plot of this process CPU%% over time",
    )
    parser.add_argument(
        "--cpu-history",
        type=int,
        default=180,
        help="Number of samples kept for process CPU history plot",
    )
    parser.add_argument(
        "--map",
        action="store_true",
        help="Enable 2D running minimap (person trails) overlay",
    )
    parser.add_argument(
        "--map-size",
        type=int,
        default=260,
        help="Size (px) of the 2D running minimap overlay",
    )


def _add_debug_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--debug-output",
        action="store_true",
        help="Log raw model output shapes once at startup",
    )
    parser.add_argument(
        "--debug-detections",
        action="store_true",
        help="Log sample decoded detections every few seconds",
    )
    parser.add_argument(
        "--debug-boxes",
        action="store_true",
        help="Log decoded bbox coordinates for debugging",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="DEBUG",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
