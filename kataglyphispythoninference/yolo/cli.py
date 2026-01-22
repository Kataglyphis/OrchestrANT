from __future__ import annotations

import argparse


def parse_args(argv: list | None = None) -> argparse.Namespace:
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
    parser.add_argument("--model", type=str, default="resources/models/yolov26m.onnx")
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--no-display", action="store_true")
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

    return parser.parse_args(argv)
