"""CLI argument parsing for the YOLO monitor."""

from __future__ import annotations

import argparse

from orchestrant.paths import default_model_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="YOLOv10 Object Detection with System Monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    Examples:
      yolo-monitor --backend opencv
      yolo-monitor --backend gstreamer
      yolo-monitor --backend gstreamer --width 1280 --height 720 --fps 60
        """,
    )

    parser.add_argument(
        "--backend", type=str, choices=["opencv", "gstreamer"], default="opencv"
    )
    parser.add_argument(
        "--ui",
        type=str,
        choices=["opencv", "dearpygui", "wxpython"],
        default="opencv",
        help="Select UI backend for display",
    )
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="ONNX model; default: $ORCHESTRANT_MODEL, the install's models dir, then resources/models",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Load the model, run one inference on a blank frame and exit; needs no camera or display",
    )
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--no-display", action="store_true")
    _add_overlay_args(parser)
    _add_debug_args(parser)

    args = parser.parse_args(argv)
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
