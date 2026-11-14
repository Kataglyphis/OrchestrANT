"""Main entry point for the YOLO monitoring pipeline."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from orchestrant.genai.engine import GenaiError
from orchestrant.paths import default_log_dir, missing_model_message
from orchestrant.pipeline.logging import (
    attach_log_buffer,
    configure_logging,
    create_log_buffer,
)
from orchestrant.pipeline.ui.flet_viewer import FLET_AVAILABLE
from orchestrant.yolo.cli import parse_args
from orchestrant.yolo.describe import (
    start_describer,
)
from orchestrant.yolo.loop import (
    _run_detection_loop,
)
from orchestrant.yolo.providers import (
    select_providers as select_providers,  # noqa: PLC0414 -- preserve the monitor API
)
from orchestrant.yolo.self_test import run_self_test
from orchestrant.yolo.startup import MonitorInitError, build_context


if TYPE_CHECKING:
    import argparse
    from collections import deque

    from orchestrant.yolo.loop import (
        MonitorContext,
        ViewerLoopProtocol,
    )


def resolve_ui(args: argparse.Namespace) -> str:
    """`auto` is Flet where installed, else OpenCV's window; --web always means Flet."""
    if args.ui == "auto":
        return "flet" if FLET_AVAILABLE or args.web else "opencv"
    return args.ui


def _start_up(
    args: argparse.Namespace, log_buffer: deque[str]
) -> MonitorContext | None:
    """Load the language model before the camera opens, so a bad one leaves nothing to release."""
    try:
        describer = start_describer(args)
        ctx = build_context(args, log_buffer)
    except GenaiError as exc:
        logger.error("--describe: {}", exc)
        return None
    except MonitorInitError:
        return None
    ctx.describer = describer
    return ctx


def run_yolo_monitor(argv: list[str] | None = None) -> int:
    """Entry point for running the YOLO monitor."""
    args = parse_args(argv)
    if args.self_test:
        return run_self_test(args)
    configure_logging(args.log_level, log_dir=str(default_log_dir()))
    args.ui = resolve_ui(args)
    if args.web and args.ui != "flet":
        logger.error("--web serves the Flet viewer; it cannot go with --ui {}", args.ui)
        return 1
    if not Path(args.model).is_file():
        logger.error("{}", missing_model_message(args.model))
        return 1
    log_buffer = create_log_buffer(max_lines=200)
    attach_log_buffer(log_buffer, level="INFO")
    ctx = _start_up(args, log_buffer)
    if ctx is None:
        return 1

    if ctx.viewer is None:
        return _run_detection_loop(ctx)

    # The Flet viewer owns the main thread until its window closes; detection runs beside it.
    exit_code = 0
    viewer = cast("ViewerLoopProtocol", ctx.viewer)

    def _run_loop() -> None:
        nonlocal exit_code
        exit_code = _run_detection_loop(ctx)

    worker = threading.Thread(target=_run_loop, name="yolo-detection", daemon=True)
    worker.start()
    viewer.run()
    worker.join()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run_yolo_monitor())
