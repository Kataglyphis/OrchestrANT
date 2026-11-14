"""The detection loop: one frame read, run, decoded, drawn and shown per turn, until the camera or the user stops it."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import cv2
import numpy as np
from loguru import logger

from orchestrant.pipeline.capture.watch import (
    CameraLostError,
    FrameWatch,
)
from orchestrant.pipeline.constants import (
    DEBUG_LOG_INTERVAL_SECONDS,
    ENERGY_WH_INITIAL,
    LOG_INTERVAL_SECONDS,
    POWER_WATTS_INITIAL,
    RESOURCE_LOG_INTERVAL_SECONDS,
    STATS_UPDATE_INTERVAL_FRAMES,
)
from orchestrant.pipeline.logging import (
    log_buffer_lines,
)
from orchestrant.pipeline.monitoring.power import (
    get_cpu_freq_ratio,
)
from orchestrant.pipeline.types import (
    PerformanceMetrics,
    SystemStats,
)
from orchestrant.yolo.core.postprocess import DecodeConfig, postprocess
from orchestrant.yolo.core.preprocess import preprocess
from orchestrant.yolo.describe import (
    NoDescriber,
)
from orchestrant.yolo.resource_log import log_resources
from orchestrant.yolo.ui.draw import draw_detections


if TYPE_CHECKING:
    import argparse
    from collections import deque

    import onnxruntime as ort

    from orchestrant.pipeline.capture import CameraCapture
    from orchestrant.pipeline.metrics.performance import PerformanceTracker
    from orchestrant.pipeline.monitoring.power import (
        PowerMonitor,
    )
    from orchestrant.pipeline.monitoring.system import SystemMonitor
    from orchestrant.pipeline.tracking.centroid import SimpleCentroidTracker
    from orchestrant.pipeline.types import (
        Track,
    )
    from orchestrant.yolo.describe import SceneDescriber


class ViewerProtocol(Protocol):
    """Runtime viewer contract used by the monitor."""

    def is_open(self) -> bool:  # noqa: D102
        ...

    def render(
        self,
        frame: np.ndarray,
        *,
        perf_metrics: PerformanceMetrics | None = None,
        sys_stats: SystemStats | None = None,
        proc_stats: dict | None = None,
        camera_info: dict | None = None,
        detections_count: int | None = None,
        classification: dict | None = None,
        log_lines: list[str] | None = None,
        hardware_info: dict | None = None,
        power_info: dict | None = None,
    ) -> None:
        """Render a frame and associated telemetry in the active viewer."""
        ...

    def close(self) -> None:  # noqa: D102
        ...


class ViewerLoopProtocol(ViewerProtocol, Protocol):
    """Viewer contract for backends with their own event loop."""

    def run(self) -> None:  # noqa: D102
        ...


@dataclass
class MonitorContext:
    """Static context for running the monitoring loop."""

    args: argparse.Namespace
    camera: CameraCapture
    camera_info: dict[str, object]
    sys_monitor: SystemMonitor
    session: ort.InferenceSession
    input_name: str
    input_size: tuple[int, int]
    stretch: bool
    layout: str | None
    perf_tracker: PerformanceTracker
    tracker: SimpleCentroidTracker
    tracks: dict[int, Track]
    cpu_history: deque[float]
    log_buffer: deque[str]
    power_monitor: PowerMonitor
    hardware_info: dict[str, object]
    viewer: ViewerProtocol | None
    cpu_tdp_watts: float
    describer: SceneDescriber | NoDescriber = field(default_factory=NoDescriber)


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
            "system_power_watts": POWER_WATTS_INITIAL,
            "cpu_power_watts": POWER_WATTS_INITIAL,
            "gpu_power_watts": POWER_WATTS_INITIAL,
            "energy_wh": ENERGY_WH_INITIAL,
        },
        energy_wh=ENERGY_WH_INITIAL,
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
        bbox_obj = det.get("bbox")
        if not isinstance(bbox_obj, list | tuple) or len(bbox_obj) < 4:
            continue
        x1_obj, _y1_obj, x2_obj, y2_obj = bbox_obj[:4]
        if not isinstance(x1_obj, int | float):
            continue
        if not isinstance(x2_obj, int | float):
            continue
        if not isinstance(y2_obj, int | float):
            continue
        cx = (float(x1_obj) + float(x2_obj)) / 2.0
        cy = float(y2_obj)
        person_centroids.append((float(cx) / max(1, fw), float(cy) / max(1, fh)))
    return ctx.tracker.update(person_centroids, now_ts=time.perf_counter())


def _update_stats_if_needed(
    ctx: MonitorContext,
    state: RuntimeState,
) -> None:
    if state.frame_count % STATS_UPDATE_INTERVAL_FRAMES != 0:
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
        show_stats_panel=ctx.args.ui != "flet",
        show_detection_panel=ctx.args.ui != "flet",
    )


def _log_periodic_metrics(
    ctx: MonitorContext,
    state: RuntimeState,
    detections: list[dict[str, object]],
    classification: dict[str, object] | None,
) -> None:
    current_time = time.perf_counter()
    if current_time - state.last_log_time >= LOG_INTERVAL_SECONDS:
        metrics = ctx.perf_tracker.get_metrics()
        logger.info(
            "Camera: {:.1f} FPS | Inference: {:.1f}ms | Budget: {:.0f}% | Detections: {}",
            metrics.camera_fps,
            metrics.inference_ms,
            metrics.frame_budget_percent,
            len(detections),
        )
        state.last_log_time = current_time

    if (
        ctx.args.debug_detections
        and current_time - state.last_debug_log_time >= DEBUG_LOG_INTERVAL_SECONDS
    ):
        sample = detections[:3]
        logger.info("Sample detections: {}", sample)
        if classification is not None:
            logger.info("Classification: {}", classification)
        state.last_debug_log_time = current_time

    if current_time - state.last_resource_log_time >= RESOURCE_LOG_INTERVAL_SECONDS:
        log_resources(ctx.perf_tracker.get_metrics(), state)
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
            log_lines=log_buffer_lines(ctx.log_buffer),
            hardware_info=ctx.hardware_info,
            power_info=state.power_info,
        )
        return True

    cv2.imshow("Object Detection", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        logger.info("Quit requested by user")
        return False
    return True


def _log_session_summary(ctx: MonitorContext, state: RuntimeState) -> None:
    logger.info("=" * 60)
    logger.info("Session Summary")

    final_metrics = ctx.perf_tracker.get_metrics()
    logger.info("Capture backend: {}", ctx.camera_info["backend"])
    logger.info("Total frames: {}", state.frame_count)
    logger.info("Avg throughput: {:.1f} FPS", final_metrics.actual_throughput_fps)
    logger.info("Avg inference: {:.1f}ms", final_metrics.inference_ms)
    logger.info("Avg budget used: {:.1f}%", final_metrics.frame_budget_percent)


def _process_frame(ctx: MonitorContext, state: RuntimeState, frame: np.ndarray) -> bool:
    """One frame through the model, the decoder, the drawing and the window; False once the window asks to stop."""
    ctx.perf_tracker.tick_camera()
    blob, scale, pad_x, pad_y = preprocess(
        frame, input_size=ctx.input_size, stretch=ctx.stretch
    )

    inference_start = time.perf_counter()
    outputs = ctx.session.run(None, {ctx.input_name: blob})
    inference_ms = (time.perf_counter() - inference_start) * 1000
    ctx.perf_tracker.add_inference_time(inference_ms)

    detections, classification = postprocess(
        [np.asarray(output) for output in outputs],
        DecodeConfig(
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            input_size=ctx.input_size,
            conf_threshold=ctx.args.conf,
            debug_boxes=ctx.args.debug_boxes,
            layout=ctx.layout,
            frame_size=(frame.shape[1], frame.shape[0]),
        ),
        debug_output=ctx.args.debug_output and not state.output_debug_logged,
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
    ctx.describer.offer(detections)

    return _render_output(ctx, state, frame, detections, classification)


def _run_detection_loop(ctx: MonitorContext) -> int:
    state = _init_runtime_state()
    frames = FrameWatch(ctx.camera)
    exit_code = 0
    logger.info("-" * 60)
    logger.info("Starting detection loop. Press 'q' to quit.")
    logger.info("-" * 60)

    try:
        while True:
            frame = frames.read()
            if frame is None:
                continue
            if not _process_frame(ctx, state, frame):
                break

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except CameraLostError as exc:
        logger.error("Stopping: {}", exc)
        exit_code = 1
    except Exception as exc:
        logger.exception("Error during detection: {}", exc)
        exit_code = 1
    finally:
        _log_session_summary(ctx, state)

        ctx.describer.stop()
        ctx.camera.release()
        if ctx.viewer is not None:
            ctx.viewer.close()
        cv2.destroyAllWindows()
        ctx.power_monitor.shutdown()
        ctx.sys_monitor.shutdown()
        logger.success("Cleanup complete. Goodbye!")

    return exit_code
