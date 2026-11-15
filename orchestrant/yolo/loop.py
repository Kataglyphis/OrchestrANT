"""The detection loop: one frame read, run, decoded, drawn and shown per turn, until the camera or the user stops it."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
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
from orchestrant.yolo.describe import (
    NoDescriber,
)
from orchestrant.yolo.resource_log import log_resources
from orchestrant.yolo.ui.draw import draw_detections


if TYPE_CHECKING:
    import argparse
    from collections import deque

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
    from orchestrant.yolo.core.detector import Detector
    from orchestrant.yolo.core.postprocess import Detection
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


@dataclass(frozen=True, slots=True)
class LoopFlags:
    """The command-line choices the loop reads, typed once in build_context; the rest stays with start-up."""

    conf: float
    no_display: bool = False
    ui: str = "opencv"
    map: bool = False
    map_size: int = 260
    cpu_plot: bool = False
    debug_boxes: bool = False
    debug_output: bool = False
    debug_detections: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> LoopFlags:
        """Take each field from the parsed command line."""
        return cls(**{item.name: getattr(args, item.name) for item in fields(cls)})


@dataclass
class MonitorContext:
    """Static context for running the monitoring loop."""

    flags: LoopFlags
    camera: CameraCapture
    camera_info: dict[str, object]
    sys_monitor: SystemMonitor
    detector: Detector
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
    detections: list[Detection],
    tracks: dict[int, Track],
) -> dict[int, Track]:
    if not ctx.flags.map:
        return tracks
    fh, fw = frame.shape[:2]
    person_centroids: list[tuple[float, float]] = []
    for det in detections:
        if det["class_id"] != 0:
            continue
        x1, _y1, x2, y2 = det["bbox"][:4]
        person_centroids.append(((x1 + x2) / 2.0 / max(1, fw), y2 / max(1, fh)))
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
    if not ctx.flags.cpu_plot:
        return
    state_value = float(np.clip(state.proc_stats.get("cpu_percent", 0.0), 0.0, 100.0))
    ctx.cpu_history.append(state_value)


def _draw_frame_if_needed(
    ctx: MonitorContext,
    state: RuntimeState,
    frame: np.ndarray,
    detections: list[Detection],
    classification: dict[str, object] | None,
) -> np.ndarray:
    if ctx.flags.no_display:
        return frame
    return draw_detections(
        frame,
        detections,
        state.perf_metrics,
        state.sys_stats,
        state.proc_stats,
        ctx.camera_info,
        cpu_history=ctx.cpu_history if ctx.flags.cpu_plot else None,
        classification=classification,
        tracks=ctx.tracks if ctx.flags.map else None,
        map_size=ctx.flags.map_size,
        debug_boxes=ctx.flags.debug_boxes,
        show_stats_panel=ctx.flags.ui != "flet",
        show_detection_panel=ctx.flags.ui != "flet",
    )


def _log_periodic_metrics(
    ctx: MonitorContext,
    state: RuntimeState,
    detections: list[Detection],
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
        ctx.flags.debug_detections
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
    detections: list[Detection],
    classification: dict[str, object] | None,
) -> bool:
    if ctx.flags.no_display:
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
    for name, (p50, p95) in ctx.perf_tracker.stage_summary().items():
        logger.info("Stage {}: p50 {:.2f} ms, p95 {:.2f} ms", name, p50, p95)
    age = ctx.perf_tracker.frame_age_summary()
    if age is not None:
        logger.info("Frame age at read: p50 {:.1f} ms, p95 {:.1f} ms", *age)
    stats = _frame_stats(ctx.camera)
    if stats is None:
        logger.info("Dropped frames: not counted by this backend")
    else:
        logger.info(
            "Dropped frames: {} of {} the camera delivered",
            stats["dropped"],
            stats["produced"],
        )


def _frame_stats(camera: object) -> dict | None:
    """The backend's frame counts and last age, or None where it keeps none (OpenCV, a still image)."""
    stats = getattr(camera, "frame_stats", None)
    return stats() if callable(stats) else None


def _process_frame(ctx: MonitorContext, state: RuntimeState, frame: np.ndarray) -> bool:
    """One frame through the model, the decoder, the drawing and the window; False once the window asks to stop."""
    ctx.perf_tracker.tick_camera()
    found = ctx.detector.detect(
        frame,
        conf_threshold=ctx.flags.conf,
        debug_boxes=ctx.flags.debug_boxes,
        debug_output=ctx.flags.debug_output and not state.output_debug_logged,
    )
    tracker = ctx.perf_tracker
    tracker.add_inference_time(found.inference_ms)
    tracker.add_stage("preprocess", found.preprocess_ms)
    tracker.add_stage("inference", found.inference_ms)
    tracker.add_stage("decode", found.decode_ms)
    detections, classification = found.detections, found.classification
    if ctx.flags.debug_output and not state.output_debug_logged:
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

    started = time.perf_counter()
    frame = _draw_frame_if_needed(ctx, state, frame, detections, classification)
    tracker.add_stage("draw", (time.perf_counter() - started) * 1000)
    _log_periodic_metrics(ctx, state, detections, classification)
    ctx.describer.offer(detections)

    started = time.perf_counter()
    keep_going = _render_output(ctx, state, frame, detections, classification)
    tracker.add_stage("present", (time.perf_counter() - started) * 1000)
    return keep_going


def _run_detection_loop(ctx: MonitorContext) -> int:
    state = _init_runtime_state()
    frames = FrameWatch(ctx.camera)
    exit_code = 0
    logger.info("-" * 60)
    logger.info("Starting detection loop. Press 'q' to quit.")
    logger.info("-" * 60)

    try:
        waited = time.perf_counter()
        while True:
            frame = frames.read()
            if frame is None:
                continue
            ctx.perf_tracker.add_stage(
                "capture_wait", (time.perf_counter() - waited) * 1000
            )
            stats = _frame_stats(ctx.camera)
            if stats is not None and stats["age_ms"] is not None:
                ctx.perf_tracker.add_frame_age(stats["age_ms"])
            if not _process_frame(ctx, state, frame):
                break
            waited = time.perf_counter()

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
