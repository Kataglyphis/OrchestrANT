"""Starting the monitor: the banner, the model, the camera and the viewer, each released again when a later step fails."""

from __future__ import annotations

import os
import platform
from collections import deque
from typing import TYPE_CHECKING

import cv2
import psutil
from loguru import logger

from orchestrant.pipeline.capture import CameraCapture
from orchestrant.pipeline.capture.gstreamer import find_gstreamer_launch
from orchestrant.pipeline.capture.watch import (
    nominal_fps,
)
from orchestrant.pipeline.constants import (
    DEFAULT_CPU_TDP_WATTS,
)
from orchestrant.pipeline.metrics.performance import PerformanceTracker
from orchestrant.pipeline.monitoring.power import (
    PowerMonitor,
)
from orchestrant.pipeline.monitoring.system import SystemMonitor
from orchestrant.pipeline.tracking.centroid import SimpleCentroidTracker
from orchestrant.pipeline.types import (
    CameraConfig,
    CaptureBackend,
)
from orchestrant.pipeline.ui.flet_viewer import FletViewer
from orchestrant.yolo.core.detector import Detector
from orchestrant.yolo.loop import MonitorContext
from orchestrant.yolo.providers import create_session


if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    import onnxruntime as ort

    from orchestrant.pipeline.types import (
        SystemStats,
        Track,
    )
    from orchestrant.yolo.loop import ViewerProtocol


get_cpu_info: Callable[[], dict] | None = None
try:
    from cpuinfo import get_cpu_info as _real_get_cpu_info

    get_cpu_info = _real_get_cpu_info
except ImportError:  # pragma: no cover - optional dependency
    get_cpu_info = None


def _extract_camera_dimensions(
    camera_info: dict[str, object],
) -> tuple[int, int]:
    """Extract width and height from camera info dict."""
    width_raw = camera_info.get("width", 0)
    height_raw = camera_info.get("height", 0)
    width = int(width_raw) if isinstance(width_raw, int | float | str) else 0
    height = int(height_raw) if isinstance(height_raw, int | float | str) else 0
    return width, height


def _get_cpu_model() -> str:
    model = ""
    if get_cpu_info is not None:
        try:
            info = get_cpu_info()
            model = info.get("brand_raw") or info.get("brand") or ""
        except Exception:
            model = ""
    if not model:
        model = platform.processor()
    if not model and platform.system() == "Windows":
        model = os.environ.get("PROCESSOR_IDENTIFIER", "")
    return model or "Unknown"


class MonitorInitError(RuntimeError):
    """Raised when monitor initialization fails."""


def _init_viewer(
    args: argparse.Namespace,
    camera_info: dict[str, object],
    camera: CameraCapture,
    sys_monitor: SystemMonitor,
) -> ViewerProtocol | None:
    if args.ui != "flet" or args.no_display:
        return None

    width, height = _extract_camera_dimensions(camera_info)
    try:
        viewer = FletViewer(
            width,
            height,
            "YOLO Monitor",
            web=args.web,
            host=args.host,
            port=args.port,
        )
    except Exception as exc:
        logger.error("Failed to initialize the Flet viewer: {}", exc)
        camera.release()
        sys_monitor.shutdown()
        message = "Flet viewer initialization failed"
        raise MonitorInitError(message) from exc
    else:
        logger.success("Flet viewer initialized")
        return viewer


def _log_platform_info(initial_stats: SystemStats) -> None:
    logger.info("System RAM: {:.1f} GB", initial_stats.ram_total_gb)
    logger.info("CPU model: {}", _get_cpu_model())
    try:
        cpu_freq = psutil.cpu_freq()
        if cpu_freq and cpu_freq.max:
            logger.info(
                "CPU freq: {:.0f} MHz (max {:.0f} MHz)",
                cpu_freq.current or 0.0,
                cpu_freq.max,
            )
    except Exception as exc:
        logger.debug("Unable to read CPU frequency: {}", exc)
    logger.info(
        "CPU cores: {} physical, {} logical",
        psutil.cpu_count(logical=False),
        psutil.cpu_count(),
    )
    if initial_stats.gpu_name != "N/A":
        logger.info(
            "GPU: {} ({:.1f} GB VRAM)",
            initial_stats.gpu_name,
            initial_stats.gpu_memory_total_gb,
        )


def _log_startup_banner(args: argparse.Namespace) -> None:
    logger.info("=" * 60)
    logger.info("Object Detection with System Monitoring")
    logger.info("=" * 60)

    logger.info("Platform: {} {}", platform.system(), platform.release())
    logger.info("Python: {}", platform.python_version())
    logger.info("OpenCV: {}", cv2.__version__)

    gst_path, gst_status = find_gstreamer_launch()
    if gst_path:
        logger.info("GStreamer: {}", gst_status)
    else:
        logger.warning("GStreamer: {}", gst_status)

    if args.backend == "gstreamer" and gst_path is None:
        logger.error("GStreamer backend requested but not found!")
        logger.error(
            "Install GStreamer from: https://gstreamer.freedesktop.org/download/"
        )
        message = "GStreamer backend requested but not found"
        raise MonitorInitError(message)


def _load_detector(args: argparse.Namespace, sys_monitor: SystemMonitor) -> Detector:
    logger.info("Loading model: {}", args.model)
    try:
        detector = Detector.load(session_for(args, args.model))
    except Exception as exc:
        logger.error("Failed to load model: {}", exc)
        sys_monitor.shutdown()
        message = "Failed to load model"
        raise MonitorInitError(message) from exc
    logger.success("Model loaded using: {}", detector.session.get_providers()[0])
    return detector


def _open_camera(
    camera_config: CameraConfig, sys_monitor: SystemMonitor
) -> CameraCapture:
    logger.info("-" * 60)
    camera = CameraCapture(camera_config)
    if not camera.open():
        logger.error("Failed to open camera!")
        sys_monitor.shutdown()
        message = "Failed to open camera"
        raise MonitorInitError(message)
    return camera


def _cpu_tdp_watts() -> float:
    cpu_tdp_watts = float(
        os.getenv("KATAGLYPHIS_CPU_TDP_WATTS", str(DEFAULT_CPU_TDP_WATTS))
        or DEFAULT_CPU_TDP_WATTS
    )
    logger.info("CPU power baseline (TDP): {:.0f} W", cpu_tdp_watts)
    return cpu_tdp_watts


def build_context(args: argparse.Namespace, log_buffer: deque[str]) -> MonitorContext:
    """Start the monitor's parts; a failed step raises MonitorInitError after releasing them."""
    _log_startup_banner(args)

    camera_config = CameraConfig(
        device_index=args.camera,
        width=args.width,
        height=args.height,
        fps=args.fps,
        backend=CaptureBackend(args.backend),
    )

    logger.info("Requested: {}x{} @ {} FPS", args.width, args.height, args.fps)
    logger.info("Backend: {}", args.backend)
    logger.info("UI: {}", args.ui)

    sys_monitor = SystemMonitor(gpu_device_id=args.gpu)
    initial_stats = sys_monitor.get_stats()
    _log_platform_info(initial_stats)

    detector = _load_detector(args, sys_monitor)
    logger.debug("Model input: {}, size: {}", detector.input_name, detector.input_size)

    camera = _open_camera(camera_config, sys_monitor)
    camera_info = camera.get_info()
    logger.info("Active backend: {}", camera_info["backend"])
    logger.info("Capture pipeline: {}", camera_info.get("pipeline", ""))

    hardware_info = {
        "cpu_model": _get_cpu_model(),
        "ram_total_gb": initial_stats.ram_total_gb,
        "gpu_model": initial_stats.gpu_name,
        "vram_total_gb": initial_stats.gpu_memory_total_gb,
    }
    cpu_tdp_watts = _cpu_tdp_watts()
    tracks: dict[int, Track] = {}

    return MonitorContext(
        args=args,
        camera=camera,
        camera_info=camera_info,
        sys_monitor=sys_monitor,
        detector=detector,
        perf_tracker=PerformanceTracker(30, nominal_fps=nominal_fps(camera_info)),
        tracker=SimpleCentroidTracker(),
        tracks=tracks,
        cpu_history=deque(maxlen=max(2, int(args.cpu_history))),
        log_buffer=log_buffer,
        power_monitor=PowerMonitor(),
        hardware_info=hardware_info,
        viewer=_init_viewer(args, camera_info, camera, sys_monitor),
        cpu_tdp_watts=cpu_tdp_watts,
    )


def session_for(args: argparse.Namespace, model: str) -> ort.InferenceSession:
    """The session the parsed command line asks for: provider, GPU index and threads."""
    return create_session(
        model, args.gpu, args.device, intra_op_threads=args.intra_op_threads
    )
