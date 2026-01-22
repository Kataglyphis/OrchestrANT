from __future__ import annotations

import platform
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort
import psutil
from loguru import logger

from kataglyphispythoninference.yolo.capture import CameraCapture
from kataglyphispythoninference.yolo.cli import parse_args
from kataglyphispythoninference.yolo.draw import draw_detections
from kataglyphispythoninference.yolo.gstreamer import find_gstreamer_launch
from kataglyphispythoninference.yolo.logging import (
    attach_log_buffer,
    configure_logging,
    create_log_buffer,
)
from kataglyphispythoninference.yolo.performance import PerformanceTracker
from kataglyphispythoninference.yolo.postprocess import postprocess
from kataglyphispythoninference.yolo.preprocess import infer_input_size, preprocess
from kataglyphispythoninference.yolo.system import SystemMonitor
from kataglyphispythoninference.yolo.tracking import SimpleCentroidTracker
from kataglyphispythoninference.yolo.types import (
    CameraConfig,
    CaptureBackend,
    PerformanceMetrics,
    SystemStats,
    Track,
)
from kataglyphispythoninference.yolo.viewer import DearPyGuiViewer


def run_yolo_monitor(argv: Optional[List[str]] = None) -> int:
    """Entry point for running the YOLOv10 monitor."""

    args = parse_args(argv)
    configure_logging(args.log_level)
    log_buffer = create_log_buffer(max_lines=200)
    attach_log_buffer(log_buffer, level="INFO")

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
        return 1

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
    logger.info("System RAM: {:.1f} GB", initial_stats.ram_total_gb)
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
        return 1

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
        return 1

    camera_info = camera.get_info()
    logger.info("Active backend: {}", camera_info["backend"])

    perf_tracker = PerformanceTracker(avg_frames=30)

    tracker = SimpleCentroidTracker()
    tracks: Dict[int, Track] = {}

    cpu_history: Deque[float] = deque(maxlen=max(2, int(args.cpu_history)))

    log_interval = 2.0
    last_log_time = time.perf_counter()
    resource_log_interval = 5.0
    last_resource_log_time = time.perf_counter()
    debug_log_interval = 3.0
    last_debug_log_time = time.perf_counter()

    frame_count = 0
    sys_stats = SystemStats()
    proc_stats = {"cpu_percent": 0.0, "memory_mb": 0.0, "threads": 0}
    perf_metrics = PerformanceMetrics()

    viewer: Optional[DearPyGuiViewer] = None
    if args.ui == "dearpygui" and not args.no_display:
        try:
            viewer = DearPyGuiViewer(
                width=camera_info["width"],
                height=camera_info["height"],
                title="YOLO Monitor",
            )
            logger.success("DearPyGui viewer initialized")
        except Exception as exc:
            logger.error("Failed to initialize DearPyGui viewer: {}", exc)
            camera.release()
            sys_monitor.shutdown()
            return 1
    output_debug_logged = False

    logger.info("-" * 60)
    logger.info("Starting detection loop. Press 'q' to quit.")
    logger.info("-" * 60)

    try:
        while True:
            ret, frame = camera.read()

            if not ret or frame is None:
                logger.warning("Failed to grab frame")
                continue

            perf_tracker.tick_camera()

            blob, scale, pad_x, pad_y = preprocess(frame, input_size=input_size)

            inference_start = time.perf_counter()
            outputs = session.run(None, {input_name: blob})
            inference_ms = (time.perf_counter() - inference_start) * 1000

            perf_tracker.add_inference_time(inference_ms)

            detections, classification = postprocess(
                [np.asarray(output) for output in outputs],
                scale,
                pad_x,
                pad_y,
                input_size=input_size,
                conf_threshold=args.conf,
                debug_output=args.debug_output and not output_debug_logged,
                debug_boxes=args.debug_boxes,
            )
            if args.debug_output and not output_debug_logged:
                output_debug_logged = True

            if args.map:
                fh, fw = frame.shape[:2]
                person_centroids: List[Tuple[float, float]] = []
                for det in detections:
                    if det.get("class_id") != 0:
                        continue
                    x1, y1, x2, y2 = det["bbox"]
                    cx = (x1 + x2) / 2.0
                    cy = y2
                    person_centroids.append(
                        (float(cx) / max(1, fw), float(cy) / max(1, fh))
                    )
                tracks = tracker.update(person_centroids, now_ts=time.perf_counter())

            frame_count += 1
            current_time = time.perf_counter()

            if frame_count % 10 == 0:
                sys_stats = sys_monitor.get_stats()
                proc_stats = sys_monitor.get_process_stats()
                perf_metrics = perf_tracker.get_metrics()

            if args.cpu_plot:
                cpu_history.append(
                    float(np.clip(proc_stats.get("cpu_percent", 0.0), 0.0, 100.0))
                )

            if not args.no_display:
                frame = draw_detections(
                    frame,
                    detections,
                    perf_metrics,
                    sys_stats,
                    proc_stats,
                    camera_info,
                    cpu_history=cpu_history if args.cpu_plot else None,
                    classification=classification,
                    tracks=tracks if args.map else None,
                    map_size=args.map_size,
                    debug_boxes=args.debug_boxes,
                    show_stats_panel=args.ui != "dearpygui",
                    show_detection_panel=args.ui != "dearpygui",
                )

            if current_time - last_log_time >= log_interval:
                metrics = perf_tracker.get_metrics()
                logger.info(
                    "Camera: {:.1f} FPS | Inference: {:.1f}ms | Budget: {:.0f}% | Detections: {}",
                    metrics.camera_fps,
                    metrics.inference_ms,
                    metrics.frame_budget_percent,
                    len(detections),
                )
                last_log_time = current_time

            if args.debug_detections and (
                current_time - last_debug_log_time >= debug_log_interval
            ):
                sample = detections[:3]
                logger.info("Sample detections: {}", sample)
                if classification is not None:
                    logger.info("Classification: {}", classification)
                last_debug_log_time = current_time

            if current_time - last_resource_log_time >= resource_log_interval:
                metrics = perf_tracker.get_metrics()
                logger.info(
                    "System CPU: {:.1f}% | RAM: {:.1f}/{:.1f}GB",
                    sys_stats.cpu_percent,
                    sys_stats.ram_used_gb,
                    sys_stats.ram_total_gb,
                )

                if sys_stats.gpu_name != "N/A":
                    logger.info(
                        "GPU: {:.0f}% | VRAM: {:.1f}/{:.1f}GB | Temp: {:.0f}°C",
                        sys_stats.gpu_percent,
                        sys_stats.gpu_memory_used_gb,
                        sys_stats.gpu_memory_total_gb,
                        sys_stats.gpu_temp_celsius,
                    )

                logger.info(
                    "Process: CPU {:.1f}% | {:.0f}MB RAM",
                    proc_stats["cpu_percent"],
                    proc_stats["memory_mb"],
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
                last_resource_log_time = current_time

            if not args.no_display:
                if viewer is not None:
                    if not viewer.is_open():
                        logger.info("Quit requested by user")
                        break
                    viewer.render(
                        frame,
                        perf_metrics=perf_metrics,
                        sys_stats=sys_stats,
                        proc_stats=proc_stats,
                        camera_info=camera_info,
                        detections_count=len(detections),
                        classification=classification,
                        log_lines=list(log_buffer),
                    )
                else:
                    cv2.imshow("YOLOv10 Detection", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        logger.info("Quit requested by user")
                        break

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as exc:
        logger.exception("Error during detection: {}", exc)
    finally:
        logger.info("=" * 60)
        logger.info("Session Summary")

        final_metrics = perf_tracker.get_metrics()
        logger.info("Capture backend: {}", camera_info["backend"])
        logger.info("Total frames: {}", frame_count)
        logger.info("Avg throughput: {:.1f} FPS", final_metrics.actual_throughput_fps)
        logger.info("Avg inference: {:.1f}ms", final_metrics.inference_ms)
        logger.info("Avg budget used: {:.1f}%", final_metrics.frame_budget_percent)

        camera.release()
        if viewer is not None:
            viewer.close()
        cv2.destroyAllWindows()
        sys_monitor.shutdown()
        logger.success("Cleanup complete. Goodbye!")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_yolo_monitor())
