"""Main entry point for the YOLO monitoring pipeline."""

from __future__ import annotations

import os
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np
import onnxruntime as ort
import psutil
from loguru import logger

from kataglyphispythoninference.pipeline.capture import CameraCapture
from kataglyphispythoninference.pipeline.capture.gstreamer import find_gstreamer_launch
from kataglyphispythoninference.pipeline.logging import (
    attach_log_buffer,
    configure_logging,
    create_log_buffer,
)
from kataglyphispythoninference.pipeline.metrics.performance import PerformanceTracker
from kataglyphispythoninference.pipeline.monitoring.power import (
    PowerMonitor,
    get_cpu_freq_ratio,
)
from kataglyphispythoninference.pipeline.monitoring.system import SystemMonitor
from kataglyphispythoninference.pipeline.tracking.centroid import SimpleCentroidTracker
from kataglyphispythoninference.pipeline.types import (
    CameraConfig,
    CaptureBackend,
    PerformanceMetrics,
    SystemStats,
)
from kataglyphispythoninference.pipeline.ui.dearpygui import DearPyGuiViewer
from kataglyphispythoninference.yolo.cli import parse_args
from kataglyphispythoninference.yolo.core.postprocess import postprocess
from kataglyphispythoninference.yolo.core.preprocess import infer_input_size, preprocess
from kataglyphispythoninference.yolo.ui.draw import draw_detections


try:
    from kataglyphispythoninference.pipeline.ui.wx import WxPythonViewer
except ImportError as exc:  # pragma: no cover - optional dependency
    WxPythonViewer = None
    _WX_VIEWER_IMPORT_ERROR = exc
else:
    _WX_VIEWER_IMPORT_ERROR = None

if TYPE_CHECKING:
    from collections.abc import Callable

    from kataglyphispythoninference.pipeline.types import (
        Track,
    )


get_cpu_info: Callable[[], dict] | None = None
try:
    from cpuinfo import get_cpu_info as _real_get_cpu_info

    get_cpu_info = _real_get_cpu_info
except ImportError:  # pragma: no cover - optional dependency
    get_cpu_info = None


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


@dataclass
class MonitorContext:
    """Static context for running the monitoring loop."""

    args: object
    camera: CameraCapture
    camera_info: dict[str, str]
    sys_monitor: SystemMonitor
    session: ort.InferenceSession
    input_name: str
    input_size: tuple[int, int]
    perf_tracker: PerformanceTracker
    tracker: SimpleCentroidTracker
    tracks: dict[int, Track]
    cpu_history: deque[float]
    log_buffer: deque[str]
    power_monitor: PowerMonitor
    hardware_info: dict[str, object]
    viewer: object | None
    cpu_tdp_watts: float


@dataclass
class RuntimeState:
    """Mutable runtime state for the monitoring loop."""

    frame_count: int
    sys_stats: SystemStats
    proc_stats: dict[str, float | int]
    perf_metrics: PerformanceMetrics
    power_last_time: float
    power_info: dict[str, float]
    energy_wh: float
    output_debug_logged: bool
    last_log_time: float
    last_debug_log_time: float
    last_resource_log_time: float


def _init_runtime_state() -> RuntimeState:
    now = time.perf_counter()
    return RuntimeState(
        frame_count=0,
        sys_stats=SystemStats(),
        proc_stats={"cpu_percent": 0.0, "memory_mb": 0.0, "threads": 0},
        perf_metrics=PerformanceMetrics(),
        power_last_time=now,
        power_info={
            "system_power_watts": 0.0,
            "cpu_power_watts": 0.0,
            "gpu_power_watts": 0.0,
            "energy_wh": 0.0,
        },
        energy_wh=0.0,
        output_debug_logged=False,
        last_log_time=now,
        last_debug_log_time=now,
        last_resource_log_time=now,
    )


def _update_tracks_if_enabled(
    ctx: MonitorContext,
    frame: np.ndarray,
    detections: list[dict[str, object]],
    tracks: dict[int, Track],
) -> dict[int, Track]:
    if not ctx.args.map:
        return tracks
    fh, fw = frame.shape[:2]
    person_centroids: list[tuple[float, float]] = []
    for det in detections:
        if det.get("class_id") != 0:
            continue
        x1, _y1, x2, y2 = det["bbox"]
        cx = (x1 + x2) / 2.0
        cy = y2
        person_centroids.append((float(cx) / max(1, fw), float(cy) / max(1, fh)))
    return ctx.tracker.update(person_centroids, now_ts=time.perf_counter())


def _update_stats_if_needed(
    ctx: MonitorContext,
    state: RuntimeState,
) -> None:
    if state.frame_count % 10 != 0:
        return
    state.sys_stats = ctx.sys_monitor.get_stats()
    state.proc_stats = ctx.sys_monitor.get_process_stats()
    state.perf_metrics = ctx.perf_tracker.get_metrics()
    now_power = time.perf_counter()
    dt = max(0.0, now_power - state.power_last_time)
    state.power_last_time = now_power
    state.power_info = ctx.power_monitor.update(
        sys_gpu_power=float(getattr(state.sys_stats, "gpu_power_watts", 0.0)),
        cpu_util_percent=state.sys_stats.cpu_percent,
        cpu_tdp_watts=ctx.cpu_tdp_watts,
        freq_ratio=get_cpu_freq_ratio(),
        dt_seconds=dt,
    )
    state.energy_wh = state.power_info["energy_wh"]


def _update_cpu_history(
    ctx: MonitorContext,
    state: RuntimeState,
) -> None:
    if not ctx.args.cpu_plot:
        return
    state_value = float(np.clip(state.proc_stats.get("cpu_percent", 0.0), 0.0, 100.0))
    ctx.cpu_history.append(state_value)


def _draw_frame_if_needed(
    ctx: MonitorContext,
    state: RuntimeState,
    frame: np.ndarray,
    detections: list[dict[str, object]],
    classification: dict[str, object] | None,
) -> np.ndarray:
    if ctx.args.no_display:
        return frame
    return draw_detections(
        frame,
        detections,
        state.perf_metrics,
        state.sys_stats,
        state.proc_stats,
        ctx.camera_info,
        cpu_history=ctx.cpu_history if ctx.args.cpu_plot else None,
        classification=classification,
        tracks=ctx.tracks if ctx.args.map else None,
        map_size=ctx.args.map_size,
        debug_boxes=ctx.args.debug_boxes,
        show_stats_panel=ctx.args.ui not in {"dearpygui", "wxpython"},
        show_detection_panel=ctx.args.ui not in {"dearpygui", "wxpython"},
    )


def _log_periodic_metrics(
    ctx: MonitorContext,
    state: RuntimeState,
    detections: list[dict[str, object]],
    classification: dict[str, object] | None,
) -> None:
    current_time = time.perf_counter()
    if current_time - state.last_log_time >= 2.0:
        metrics = ctx.perf_tracker.get_metrics()
        logger.info(
            "Camera: {:.1f} FPS | Inference: {:.1f}ms | Budget: {:.0f}% | Detections: {}",
            metrics.camera_fps,
            metrics.inference_ms,
            metrics.frame_budget_percent,
            len(detections),
        )
        state.last_log_time = current_time

    if ctx.args.debug_detections and current_time - state.last_debug_log_time >= 3.0:
        sample = detections[:3]
        logger.info("Sample detections: {}", sample)
        if classification is not None:
            logger.info("Classification: {}", classification)
        state.last_debug_log_time = current_time

    if current_time - state.last_resource_log_time >= 5.0:
        metrics = ctx.perf_tracker.get_metrics()
        logger.info(
            "System CPU: {:.1f}% | RAM: {:.1f}/{:.1f}GB",
            state.sys_stats.cpu_percent,
            state.sys_stats.ram_used_gb,
            state.sys_stats.ram_total_gb,
        )

        if state.sys_stats.gpu_name != "N/A":
            logger.info(
                "GPU: {:.0f}% | VRAM: {:.1f}/{:.1f}GB | Temp: {:.0f}°C",
                state.sys_stats.gpu_percent,
                state.sys_stats.gpu_memory_used_gb,
                state.sys_stats.gpu_memory_total_gb,
                state.sys_stats.gpu_temp_celsius,
            )

        logger.info(
            "Process: CPU {:.1f}% | {:.0f}MB RAM",
            state.proc_stats["cpu_percent"],
            state.proc_stats["memory_mb"],
        )

        if state.power_info["system_power_watts"] > 0.0:
            logger.info(
                "Power (est): {:.0f}W | CPU {:.0f}W | GPU {:.0f}W | Energy {:.3f}Wh",
                state.power_info["system_power_watts"],
                state.power_info["cpu_power_watts"],
                state.power_info["gpu_power_watts"],
                state.power_info["energy_wh"],
            )
        elif state.power_info["gpu_power_watts"] > 0.0:
            logger.info(
                "Power: GPU {:.0f}W | Energy {:.3f}Wh",
                state.power_info["gpu_power_watts"],
                state.power_info["energy_wh"],
            )

        headroom = 100 - metrics.frame_budget_percent
        potential = (
            int(metrics.inference_capacity_fps / metrics.camera_fps)
            if metrics.camera_fps > 0
            else 0
        )
        logger.info(
            "Performance: {:.0f}% budget | {:.0f}% headroom | ~{} streams",
            metrics.frame_budget_percent,
            headroom,
            potential,
        )
        logger.info("-" * 60)
        state.last_resource_log_time = current_time


def _render_output(
    ctx: MonitorContext,
    state: RuntimeState,
    frame: np.ndarray,
    detections: list[dict[str, object]],
    classification: dict[str, object] | None,
) -> bool:
    if ctx.args.no_display:
        return True
    if ctx.viewer is not None:
        if not ctx.viewer.is_open():
            logger.info("Quit requested by user")
            return False
        ctx.viewer.render(
            frame,
            perf_metrics=state.perf_metrics,
            sys_stats=state.sys_stats,
            proc_stats=state.proc_stats,
            camera_info=ctx.camera_info,
            detections_count=len(detections),
            classification=classification,
            log_lines=list(ctx.log_buffer),
            hardware_info=ctx.hardware_info,
            power_info=state.power_info,
        )
        return True

    cv2.imshow("YOLOv10 Detection", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        logger.info("Quit requested by user")
        return False
    return True


def _run_detection_loop(ctx: MonitorContext) -> int:
    state = _init_runtime_state()
    logger.info("-" * 60)
    logger.info("Starting detection loop. Press 'q' to quit.")
    logger.info("-" * 60)

    try:
        while True:
            ret, frame = ctx.camera.read()
            if not ret or frame is None:
                logger.warning("Failed to grab frame")
                continue

            ctx.perf_tracker.tick_camera()
            blob, scale, pad_x, pad_y = preprocess(frame, input_size=ctx.input_size)

            inference_start = time.perf_counter()
            outputs = ctx.session.run(None, {ctx.input_name: blob})
            inference_ms = (time.perf_counter() - inference_start) * 1000
            ctx.perf_tracker.add_inference_time(inference_ms)

            detections, classification = postprocess(
                [np.asarray(output) for output in outputs],
                scale,
                pad_x,
                pad_y,
                input_size=ctx.input_size,
                conf_threshold=ctx.args.conf,
                debug_output=ctx.args.debug_output and not state.output_debug_logged,
                debug_boxes=ctx.args.debug_boxes,
            )
            if ctx.args.debug_output and not state.output_debug_logged:
                state.output_debug_logged = True

            ctx.tracks = _update_tracks_if_enabled(
                ctx,
                frame,
                detections,
                ctx.tracks,
            )

            state.frame_count += 1
            _update_stats_if_needed(ctx, state)
            _update_cpu_history(ctx, state)

            frame = _draw_frame_if_needed(ctx, state, frame, detections, classification)
            _log_periodic_metrics(ctx, state, detections, classification)

            if not _render_output(ctx, state, frame, detections, classification):
                break

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as exc:
        logger.exception("Error during detection: {}", exc)
    finally:
        logger.info("=" * 60)
        logger.info("Session Summary")

        final_metrics = ctx.perf_tracker.get_metrics()
        logger.info("Capture backend: {}", ctx.camera_info["backend"])
        logger.info("Total frames: {}", state.frame_count)
        logger.info("Avg throughput: {:.1f} FPS", final_metrics.actual_throughput_fps)
        logger.info("Avg inference: {:.1f}ms", final_metrics.inference_ms)
        logger.info("Avg budget used: {:.1f}%", final_metrics.frame_budget_percent)

        ctx.camera.release()
        if ctx.viewer is not None:
            ctx.viewer.close()
        cv2.destroyAllWindows()
        ctx.power_monitor.shutdown()
        ctx.sys_monitor.shutdown()
        logger.success("Cleanup complete. Goodbye!")

    return 0


class MonitorInitError(RuntimeError):
    """Raised when monitor initialization fails."""


def _init_viewer(
    args: object,
    camera_info: dict[str, str],
    camera: CameraCapture,
    sys_monitor: SystemMonitor,
) -> object | None:
    if args.ui not in {"dearpygui", "wxpython"} or args.no_display:
        return None

    if args.ui == "dearpygui":
        try:
            viewer = DearPyGuiViewer(
                width=camera_info["width"],
                height=camera_info["height"],
                title="YOLO Monitor",
            )
        except Exception as exc:
            logger.error("Failed to initialize DearPyGui viewer: {}", exc)
            camera.release()
            sys_monitor.shutdown()
            message = "DearPyGui viewer initialization failed"
            raise MonitorInitError(message) from exc
        else:
            logger.success("DearPyGui viewer initialized")
            return viewer

    if WxPythonViewer is None:
        logger.error("wxPython viewer requested but not available")
        if _WX_VIEWER_IMPORT_ERROR is not None:
            logger.error("wxPython import error: {}", _WX_VIEWER_IMPORT_ERROR)
        camera.release()
        sys_monitor.shutdown()
        message = "wxPython viewer not available"
        raise MonitorInitError(message)

    try:
        viewer = WxPythonViewer(
            width=camera_info["width"],
            height=camera_info["height"],
            title="YOLO Monitor",
        )
    except Exception as exc:
        logger.error("Failed to initialize wxPython viewer: {}", exc)
        camera.release()
        sys_monitor.shutdown()
        message = "wxPython viewer initialization failed"
        raise MonitorInitError(message) from exc
    else:
        logger.success("wxPython viewer initialized")
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


def _build_context(args: object, log_buffer: deque[str]) -> MonitorContext:
    logger.info("=" * 60)
    logger.info("YOLOv10 Object Detection with System Monitoring")
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

    providers = [
        (
            "CUDAExecutionProvider",
            {"device_id": args.gpu, "arena_extend_strategy": "kNextPowerOfTwo"},
        ),
        "CPUExecutionProvider",
    ]

    logger.info("Loading model: {}", args.model)
    try:
        session = ort.InferenceSession(args.model, providers=providers)
    except Exception as exc:
        logger.error("Failed to load model: {}", exc)
        sys_monitor.shutdown()
        message = "Failed to load model"
        raise MonitorInitError(message) from exc

    active_provider = session.get_providers()[0]
    logger.success("Model loaded using: {}", active_provider)

    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape
    logger.debug("Model input: {}, shape: {}", input_name, input_shape)
    input_size = infer_input_size(input_shape)

    logger.info("-" * 60)
    camera = CameraCapture(camera_config)
    if not camera.open():
        logger.error("Failed to open camera!")
        sys_monitor.shutdown()
        message = "Failed to open camera"
        raise MonitorInitError(message)

    camera_info = camera.get_info()
    logger.info("Active backend: {}", camera_info["backend"])
    logger.info("Capture pipeline: {}", camera_info.get("pipeline", ""))

    hardware_info = {
        "cpu_model": _get_cpu_model(),
        "ram_total_gb": initial_stats.ram_total_gb,
        "gpu_model": initial_stats.gpu_name,
        "vram_total_gb": initial_stats.gpu_memory_total_gb,
    }

    perf_tracker = PerformanceTracker(avg_frames=30)
    tracker = SimpleCentroidTracker()
    tracks: dict[int, Track] = {}
    cpu_history: deque[float] = deque(maxlen=max(2, int(args.cpu_history)))

    cpu_tdp_watts = float(os.getenv("KATAGLYPHIS_CPU_TDP_WATTS", "45") or 45.0)
    logger.info("CPU power baseline (TDP): {:.0f} W", cpu_tdp_watts)

    power_monitor = PowerMonitor()
    viewer = _init_viewer(args, camera_info, camera, sys_monitor)

    return MonitorContext(
        args=args,
        camera=camera,
        camera_info=camera_info,
        sys_monitor=sys_monitor,
        session=session,
        input_name=input_name,
        input_size=input_size,
        perf_tracker=perf_tracker,
        tracker=tracker,
        tracks=tracks,
        cpu_history=cpu_history,
        log_buffer=log_buffer,
        power_monitor=power_monitor,
        hardware_info=hardware_info,
        viewer=viewer,
        cpu_tdp_watts=cpu_tdp_watts,
    )


def run_yolo_monitor(argv: list[str] | None = None) -> int:
    """Entry point for running the YOLOv10 monitor."""
    args = parse_args(argv)
    configure_logging(args.log_level)
    log_buffer = create_log_buffer(max_lines=200)
    attach_log_buffer(log_buffer, level="INFO")
    try:
        ctx = _build_context(args, log_buffer)
    except MonitorInitError:
        return 1

    use_wx = args.ui == "wxpython" and not args.no_display and ctx.viewer is not None
    if use_wx:
        exit_code = 0

        def _run_loop() -> None:
            nonlocal exit_code
            exit_code = _run_detection_loop(ctx)

        worker = threading.Thread(
            target=_run_loop,
            name="yolo-detection",
            daemon=True,
        )
        worker.start()
        ctx.viewer.run()
        worker.join()
        return exit_code

    return _run_detection_loop(ctx)


if __name__ == "__main__":
    raise SystemExit(run_yolo_monitor())
