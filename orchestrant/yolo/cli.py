"""CLI argument parsing for the YOLO monitor."""

from __future__ import annotations

import argparse
import sys

from orchestrant.paths import bundle_data_dir, default_model_path


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


def _resolve_gpu_defaults(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    """Choose a vendor flavor only for options the user did not explicitly set."""
    try:
        needs_vendor = args.device is None or (
            args.gpu is None and args.device == "directml"
        )
        vendor = _directml_vendor() if needs_vendor else None
        if args.device is None:
            args.device = "directml" if vendor else "auto"
        if args.gpu is None:
            if vendor and args.device == "directml":
                from orchestrant.smoke.directml import physical_adapter  # noqa: PLC0415 -- DXGI is needed only for vendor packages

                args.gpu = physical_adapter(vendor)[0]
            else:
                args.gpu = 0
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(
            f"Could not select the package GPU: {error}; use --device cpu to run on CPU"
        )


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
    parser.add_argument("--conf", type=float, default=0.5)
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
    parser.add_argument("--no-display", action="store_true")
    _add_overlay_args(parser)
    _add_debug_args(parser)

    args = parser.parse_args(argv)
    _resolve_gpu_defaults(args, parser)
    if args.model is None:
        args.model = str(default_model_path())
    return args


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
