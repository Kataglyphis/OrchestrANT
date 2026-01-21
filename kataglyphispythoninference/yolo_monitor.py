from __future__ import annotations

import argparse
import os
import platform
import shlex
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from queue import Empty, Queue
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort
import psutil
from loguru import logger

try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    logger.warning("pynvml not available - GPU monitoring disabled")


def configure_logging(log_level: str = "DEBUG") -> None:
    """Configure loguru logging for the monitor."""

    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{message}</cyan>",
        level=log_level,
    )
    logger.add(
        "detection_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="7 days",
        level=log_level,
    )


CLASS_NAMES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]

np.random.seed(42)
COLORS = np.random.randint(0, 255, size=(len(CLASS_NAMES), 3), dtype=np.uint8)


class CaptureBackend(Enum):
    """Video capture backend options."""

    OPENCV = "opencv"
    GSTREAMER = "gstreamer"


@dataclass
class CameraConfig:
    """Camera configuration settings."""

    device_index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    backend: CaptureBackend = CaptureBackend.OPENCV


@dataclass
class SystemStats:
    """Container for system statistics."""

    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu_percent: float = 0.0
    gpu_memory_used_gb: float = 0.0
    gpu_memory_total_gb: float = 0.0
    gpu_memory_percent: float = 0.0
    gpu_temp_celsius: float = 0.0
    gpu_power_watts: float = 0.0
    gpu_name: str = "N/A"


@dataclass
class PerformanceMetrics:
    """Container for performance metrics."""

    camera_fps: float = 0.0
    inference_ms: float = 0.0
    inference_capacity_fps: float = 0.0
    frame_budget_percent: float = 0.0
    actual_throughput_fps: float = 0.0


def find_gstreamer_launch() -> Tuple[Optional[str], str]:
    """Find gst-launch-1.0 executable."""

    windows_paths = [
        Path(r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\Program Files (x86)\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\gstreamer\1.0\x86_64\bin"),
    ]

    exe_name = (
        "gst-launch-1.0.exe" if platform.system() == "Windows" else "gst-launch-1.0"
    )

    try:
        result = subprocess.run(
            ["gst-launch-1.0", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            return "gst-launch-1.0", f"Found in PATH: {version}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if platform.system() == "Windows":
        for base_path in windows_paths:
            exe_path = base_path / exe_name
            if exe_path.exists():
                try:
                    result = subprocess.run(
                        [str(exe_path), "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    if result.returncode == 0:
                        version = result.stdout.strip().split("\n")[0]
                        return str(exe_path), f"Found at {exe_path}: {version}"
                except Exception:
                    continue

    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["where.exe", "gst-launch-1.0"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        else:
            result = subprocess.run(
                ["which", "gst-launch-1.0"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

        if result.returncode == 0:
            exe_path = result.stdout.strip().split("\n")[0]
            if os.path.exists(exe_path):
                return exe_path, f"Found via system search: {exe_path}"
    except Exception:
        pass

    return None, "gst-launch-1.0 not found"


def get_gstreamer_env() -> dict:
    """Get environment variables needed for GStreamer on Windows."""

    env = os.environ.copy()
    if platform.system() != "Windows":
        return env

    gst_paths = [
        r"C:\Program Files\gstreamer\1.0\msvc_x86_64",
        r"C:\gstreamer\1.0\msvc_x86_64",
    ]

    for gst_root in gst_paths:
        if os.path.exists(gst_root):
            bin_path = os.path.join(gst_root, "bin")
            lib_path = os.path.join(gst_root, "lib")
            plugin_path = os.path.join(lib_path, "gstreamer-1.0")

            current_path = env.get("PATH", "")
            if bin_path not in current_path:
                env["PATH"] = bin_path + os.pathsep + current_path

            env["GST_PLUGIN_PATH"] = plugin_path
            env["GST_PLUGIN_SYSTEM_PATH"] = plugin_path

            logger.debug("GStreamer environment configured from: {}", gst_root)
            break

    return env


class GStreamerSubprocessCapture:
    """GStreamer video capture using subprocess and raw frames on stdout."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.frame_queue: Queue[np.ndarray] = Queue(maxsize=2)
        self.running = False
        self.reader_thread: Optional[threading.Thread] = None

        self.actual_width = config.width
        self.actual_height = config.height
        self.actual_fps = float(config.fps)
        self.pipeline_string = ""
        self.frame_size = 0

        self.gst_launch_path, self.gst_status = find_gstreamer_launch()
        self.gst_env = get_gstreamer_env()

    def _build_pipeline_string(
        self, width: int, height: int, fps: int, pipeline_type: str = "strict"
    ) -> str:
        if pipeline_type == "strict":
            return (
                f"mfvideosrc ! "
                f"video/x-raw,width={width},height={height},framerate={fps}/1 ! "
                f"videoconvert ! "
                f"video/x-raw,format=BGR ! "
                f"fdsink fd=1 sync=false"
            )
        if pipeline_type == "flexible":
            return (
                f"mfvideosrc ! "
                f"videoconvert ! videoscale ! "
                f"video/x-raw,width={width},height={height} ! "
                f"videorate ! video/x-raw,framerate={fps}/1 ! "
                f"videoconvert ! video/x-raw,format=BGR ! fdsink fd=1 sync=false"
            )
        return (
            f"mfvideosrc ! "
            f"videoconvert ! videoscale ! "
            f"video/x-raw,format=BGR,width={width},height={height} ! "
            f"fdsink fd=1 sync=false"
        )

    def _frame_reader(self) -> None:
        frame_size = self.actual_width * self.actual_height * 3

        while self.running and self.process and self.process.poll() is None:
            try:
                if not self.process.stdout:
                    logger.warning("GStreamer stdout not available")
                    break

                raw_data = self.process.stdout.read(frame_size)

                if len(raw_data) != frame_size:
                    if len(raw_data) == 0:
                        logger.warning("GStreamer process ended (no data)")
                        break
                    logger.warning("Incomplete frame: {}/{}", len(raw_data), frame_size)
                    continue

                frame = np.frombuffer(raw_data, dtype=np.uint8).reshape(
                    (self.actual_height, self.actual_width, 3)
                )

                if self.frame_queue.full():
                    try:
                        self.frame_queue.get_nowait()
                    except Empty:
                        pass

                self.frame_queue.put(frame.copy())

            except Exception as exc:
                if self.running:
                    logger.error("Frame reader error: {}", exc)
                break

        self.running = False

    def open(self) -> bool:
        if self.gst_launch_path is None:
            logger.error("GStreamer not available: {}", self.gst_status)
            return False

        logger.info("GStreamer: {}", self.gst_status)
        logger.debug("Executable: {}", self.gst_launch_path)

        self.frame_size = self.actual_width * self.actual_height * 3

        for pipeline_type in ("strict", "flexible", "auto"):
            logger.info("Trying GStreamer pipeline: {}", pipeline_type)

            pipeline_str = self._build_pipeline_string(
                self.actual_width,
                self.actual_height,
                int(self.actual_fps),
                pipeline_type,
            )
            logger.debug("Pipeline: {}", pipeline_str)

            cmd = [self.gst_launch_path, "-q"] + shlex.split(pipeline_str)

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    env=self.gst_env,
                )

                time.sleep(1.0)

                if self.process.poll() is not None:
                    stderr = (
                        self.process.stderr.read().decode("utf-8", errors="ignore")
                        if self.process.stderr
                        else ""
                    )
                    logger.warning("Pipeline failed: {}", stderr[:300])
                    self.release()
                    continue

                self.running = True
                self.reader_thread = threading.Thread(
                    target=self._frame_reader, daemon=True
                )
                self.reader_thread.start()

                try:
                    frame = self.frame_queue.get(timeout=5.0)

                    if (
                        frame.shape[0] != self.actual_height
                        or frame.shape[1] != self.actual_width
                    ):
                        logger.info("Adjusting dimensions: {}", frame.shape[:2])
                        self.actual_height, self.actual_width = frame.shape[:2]
                        self.frame_size = self.actual_width * self.actual_height * 3

                    self.frame_queue.put(frame)
                    self.pipeline_string = pipeline_str
                    logger.success(
                        "GStreamer started: {}x{}",
                        self.actual_width,
                        self.actual_height,
                    )
                    return True

                except Empty:
                    logger.warning("No frames from pipeline: {}", pipeline_type)
                    self.release()
                    continue

            except Exception as exc:
                logger.warning("Error with pipeline {}: {}", pipeline_type, exc)
                self.release()
                continue

        fallback_width = 1280
        fallback_height = 720
        fallback_fps = int(self.actual_fps) if self.actual_fps > 0 else 30
        logger.info("Trying fallback pipeline (1280x720)")

        pipeline_str = self._build_pipeline_string(
            fallback_width, fallback_height, fallback_fps, pipeline_type="strict"
        )
        logger.debug("Fallback pipeline: {}", pipeline_str)
        cmd = [self.gst_launch_path, "-q"] + shlex.split(pipeline_str)

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=self.gst_env,
            )

            time.sleep(1.0)

            if self.process.poll() is not None:
                stderr = (
                    self.process.stderr.read().decode("utf-8", errors="ignore")
                    if self.process.stderr
                    else ""
                )
                logger.warning("Fallback pipeline failed: {}", stderr[:300])
                self.release()
                logger.error("All GStreamer pipelines failed!")
                return False

            self.running = True
            self.reader_thread = threading.Thread(
                target=self._frame_reader, daemon=True
            )
            self.reader_thread.start()

            try:
                frame = self.frame_queue.get(timeout=5.0)
                self.actual_height, self.actual_width = frame.shape[:2]
                self.frame_size = self.actual_width * self.actual_height * 3
                self.frame_queue.put(frame)
                self.pipeline_string = pipeline_str
                logger.success(
                    "GStreamer started (fallback): {}x{}",
                    self.actual_width,
                    self.actual_height,
                )
                return True
            except Empty:
                logger.warning("No frames from fallback pipeline")
                self.release()
                logger.error("All GStreamer pipelines failed!")
                return False

        except Exception as exc:
            logger.warning("Fallback pipeline error: {}", exc)
            self.release()
            logger.error("All GStreamer pipelines failed!")
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.running:
            return False, None

        try:
            frame = self.frame_queue.get(timeout=1.0)
            return True, frame
        except Empty:
            return False, None

    def release(self) -> None:
        self.running = False

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception:
                pass
            self.process = None

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)

        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except Empty:
                break

    def is_opened(self) -> bool:
        return self.running and self.process is not None and self.process.poll() is None

    def get_info(self) -> dict:
        return {
            "backend": "GStreamer (subprocess)",
            "pipeline": self.pipeline_string,
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
        }


class OpenCVCapture:
    """OpenCV video capture wrapper."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.cap: Optional[cv2.VideoCapture] = None
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

    def open(self) -> bool:
        logger.info("Opening camera {} with OpenCV...", self.config.device_index)

        self.cap = cv2.VideoCapture(self.config.device_index)
        if not self.cap.isOpened():
            logger.error("Cannot open camera with OpenCV!")
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        self.actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_fps = float(self.cap.get(cv2.CAP_PROP_FPS))

        logger.success(
            "Camera opened: {}x{} @ {:.1f} FPS",
            self.actual_width,
            self.actual_height,
            self.actual_fps,
        )
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self.cap is None:
            return False, None
        return self.cap.read()

    def release(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None

    def is_opened(self) -> bool:
        return self.cap is not None and self.cap.isOpened()

    def get_info(self) -> dict:
        return {
            "backend": "OpenCV",
            "pipeline": f"OpenCV DirectShow (device {self.config.device_index})",
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
        }


class CameraCapture:
    """Unified camera capture supporting OpenCV and GStreamer subprocess backends."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._capture: Optional[object] = None
        self.backend_name = ""

    def open(self) -> bool:
        if self.config.backend == CaptureBackend.GSTREAMER:
            logger.info("Using GStreamer subprocess capture...")
            self._capture = GStreamerSubprocessCapture(self.config)
            if self._capture.open():
                self.backend_name = "GStreamer (subprocess)"
                return True
            logger.warning("GStreamer failed, falling back to OpenCV...")

        self._capture = OpenCVCapture(self.config)
        if self._capture.open():
            self.backend_name = "OpenCV"
            return True

        return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._capture is None:
            return False, None
        return self._capture.read()

    def release(self) -> None:
        if self._capture:
            self._capture.release()
            self._capture = None

    def is_opened(self) -> bool:
        return self._capture is not None and self._capture.is_opened()

    def get_info(self) -> dict:
        if self._capture:
            return self._capture.get_info()
        return {"backend": "None", "pipeline": "", "width": 0, "height": 0, "fps": 0}


class SystemMonitor:
    """Monitor system resources including CPU, RAM, and GPU."""

    def __init__(self, gpu_device_id: int = 0) -> None:
        self.gpu_device_id = gpu_device_id
        self.gpu_handle = None
        self.gpu_available = False
        self.gpu_name = "N/A"

        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_device_id)
                gpu_name = pynvml.nvmlDeviceGetName(self.gpu_handle)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode("utf-8")
                self.gpu_name = gpu_name
                self.gpu_available = True
                logger.success("GPU monitoring initialized: {}", self.gpu_name)
            except Exception as exc:
                logger.warning("Failed to initialize GPU monitoring: {}", exc)

        psutil.cpu_percent(interval=None)
        self.process = psutil.Process()
        self.process.cpu_percent()

    def get_stats(self) -> SystemStats:
        stats = SystemStats()

        stats.cpu_percent = psutil.cpu_percent(interval=None)

        ram = psutil.virtual_memory()
        stats.ram_percent = ram.percent
        stats.ram_used_gb = ram.used / (1024**3)
        stats.ram_total_gb = ram.total / (1024**3)

        if self.gpu_available and self.gpu_handle:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                stats.gpu_percent = util.gpu

                mem = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                stats.gpu_memory_used_gb = mem.used / (1024**3)
                stats.gpu_memory_total_gb = mem.total / (1024**3)
                stats.gpu_memory_percent = (
                    (mem.used / mem.total) * 100 if mem.total > 0 else 0
                )

                stats.gpu_temp_celsius = pynvml.nvmlDeviceGetTemperature(
                    self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                )

                try:
                    power = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle)
                    stats.gpu_power_watts = power / 1000.0
                except Exception:
                    stats.gpu_power_watts = 0.0

                stats.gpu_name = self.gpu_name

            except Exception as exc:
                logger.warning("Error reading GPU stats: {}", exc)

        return stats

    def get_process_stats(self) -> dict:
        try:
            return {
                "cpu_percent": self.process.cpu_percent(),
                "memory_mb": self.process.memory_info().rss / (1024**2),
                "threads": self.process.num_threads(),
            }
        except Exception:
            return {"cpu_percent": 0, "memory_mb": 0, "threads": 0}

    def shutdown(self) -> None:
        if PYNVML_AVAILABLE and self.gpu_available:
            try:
                pynvml.nvmlShutdown()
                logger.debug("GPU monitoring shutdown complete")
            except Exception:
                pass


class PerformanceTracker:
    """Track performance metrics with moving averages."""

    def __init__(self, avg_frames: int = 30) -> None:
        self.avg_frames = avg_frames
        self.camera_times: List[float] = []
        self.inference_times: List[float] = []
        self.last_camera_time: Optional[float] = None
        self.frame_count = 0
        self.start_time = time.perf_counter()

    def tick_camera(self) -> None:
        now = time.perf_counter()
        if self.last_camera_time is not None:
            self.camera_times.append(now - self.last_camera_time)
            if len(self.camera_times) > self.avg_frames:
                self.camera_times.pop(0)
        self.last_camera_time = now
        self.frame_count += 1

    def add_inference_time(self, elapsed_ms: float) -> None:
        self.inference_times.append(elapsed_ms)
        if len(self.inference_times) > self.avg_frames:
            self.inference_times.pop(0)

    def get_metrics(self) -> PerformanceMetrics:
        metrics = PerformanceMetrics()

        if self.camera_times:
            avg_camera_time = sum(self.camera_times) / len(self.camera_times)
            metrics.camera_fps = 1.0 / avg_camera_time if avg_camera_time > 0 else 0.0

        if self.inference_times:
            metrics.inference_ms = sum(self.inference_times) / len(self.inference_times)
            metrics.inference_capacity_fps = (
                1000.0 / metrics.inference_ms if metrics.inference_ms > 0 else 0.0
            )

            if metrics.camera_fps > 0:
                frame_budget_ms = 1000.0 / metrics.camera_fps
                metrics.frame_budget_percent = (
                    metrics.inference_ms / frame_budget_ms
                ) * 100

        elapsed = time.perf_counter() - self.start_time
        metrics.actual_throughput_fps = (
            self.frame_count / elapsed if elapsed > 0 else 0.0
        )

        return metrics


def infer_input_size(input_shape: Optional[List[object]]) -> Tuple[int, int]:
    """Infer (height, width) from an ONNX input shape."""

    if not input_shape or len(input_shape) < 4:
        return (640, 640)

    height = input_shape[-2]
    width = input_shape[-1]

    if isinstance(height, int) and isinstance(width, int):
        return (height, width)

    return (640, 640)


def preprocess(frame: np.ndarray, input_size: Tuple[int, int] = (640, 640)) -> tuple:
    """Preprocess frame for YOLOv10."""

    original_h, original_w = frame.shape[:2]

    scale = min(input_size[0] / original_h, input_size[1] / original_w)
    new_w, new_h = int(original_w * scale), int(original_h * scale)

    resized = cv2.resize(frame, (new_w, new_h))

    padded = np.full((input_size[0], input_size[1], 3), 114, dtype=np.uint8)
    pad_x, pad_y = (input_size[1] - new_w) // 2, (input_size[0] - new_h) // 2
    padded[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

    blob = padded[:, :, ::-1].astype(np.float32) / 255.0
    blob = blob.transpose(2, 0, 1)[np.newaxis, ...]

    return blob, scale, pad_x, pad_y


def _softmax(scores: np.ndarray) -> np.ndarray:
    shifted = scores - np.max(scores)
    exp_scores = np.exp(shifted)
    return exp_scores / np.sum(exp_scores)


def _xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    x, y, w, h = boxes.T
    x1 = x - w / 2
    y1 = y - h / 2
    x2 = x + w / 2
    y2 = y + h / 2
    return np.stack([x1, y1, x2, y2], axis=1)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


def _squeeze_to_2d(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    data = np.squeeze(data)
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    return data


def _looks_like_xywh(boxes: np.ndarray) -> bool:
    if boxes.size == 0:
        return False
    x2_lt_x1 = np.mean(boxes[:, 2] < boxes[:, 0])
    y2_lt_y1 = np.mean(boxes[:, 3] < boxes[:, 1])
    return (x2_lt_x1 > 0.3) or (y2_lt_y1 > 0.3)


def postprocess(
    outputs: np.ndarray,
    scale: float,
    pad_x: int,
    pad_y: int,
    input_size: Tuple[int, int],
    conf_threshold: float = 0.5,
    debug_output: bool = False,
    debug_boxes: bool = False,
) -> Tuple[list, Optional[dict]]:
    """Parse model outputs for detection or classification models."""

    detections: List[dict] = []
    classification: Optional[dict] = None

    if outputs is None or len(outputs) == 0:
        return detections, classification

    if debug_output:
        logger.info("Model outputs: {}", [np.asarray(out).shape for out in outputs])

    output = _squeeze_to_2d(outputs[0])

    if output.ndim == 0:
        return detections, classification

    if output.ndim == 1 or (output.ndim == 2 and output.shape[0] == 1):
        scores = output if output.ndim == 1 else output[0]
        scores = scores.astype(np.float32)
        if np.any(scores < 0) or np.any(scores > 1):
            scores = _softmax(scores)
        class_id = int(np.argmax(scores))
        score = float(scores[class_id])
        label = CLASS_NAMES[class_id] if class_id < len(CLASS_NAMES) else str(class_id)
        classification = {
            "class_id": class_id,
            "score": score,
            "label": label,
        }
        return detections, classification

    if len(outputs) >= 3:
        boxes = _squeeze_to_2d(outputs[0])
        scores = _squeeze_to_2d(outputs[1])
        class_ids = _squeeze_to_2d(outputs[2])

        if boxes.ndim == 2 and boxes.shape[-1] == 4:
            if scores.ndim > 1:
                scores = scores.reshape(-1)
            if class_ids.ndim > 1:
                class_ids = class_ids.reshape(-1)
            height, width = input_size
            if np.max(boxes) <= 1.5:
                boxes = boxes * np.array(
                    [width, height, width, height], dtype=np.float32
                )

            if _looks_like_xywh(boxes):
                boxes = _xywh_to_xyxy(boxes)

            if debug_boxes:
                logger.info(
                    "Decoded boxes (first 3): {}",
                    boxes[:3].round(2).tolist(),
                )
                logger.info(
                    "Decoded scores/classes (first 3): {}",
                    list(zip(scores[:3].round(3).tolist(), class_ids[:3].tolist())),
                )

            for box, score, class_id in zip(boxes, scores, class_ids, strict=False):
                if score < conf_threshold:
                    continue
                x1, y1, x2, y2 = box
                x1 = (x1 - pad_x) / scale
                y1 = (y1 - pad_y) / scale
                x2 = (x2 - pad_x) / scale
                y2 = (y2 - pad_y) / scale
                detections.append(
                    {
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "score": float(score),
                        "class_id": int(class_id),
                    }
                )
            return detections, classification

    if len(outputs) >= 2:
        scores = _squeeze_to_2d(outputs[0])
        boxes = _squeeze_to_2d(outputs[1])

        if scores.ndim == 2 and boxes.ndim == 2 and boxes.shape[1] == 4:
            height, width = input_size
            probs = _softmax(scores) if scores.shape[1] > 1 else scores
            class_ids = np.argmax(probs, axis=1)
            confs = probs[np.arange(len(class_ids)), class_ids]

            if np.min(boxes) < 0.0 or np.max(boxes) > 1.5:
                boxes = _sigmoid(boxes)
            boxes = boxes * np.array([width, height, width, height], dtype=np.float32)
            boxes = _xywh_to_xyxy(boxes)

            if debug_boxes:
                logger.info(
                    "Decoded boxes (first 3): {}",
                    boxes[:3].round(2).tolist(),
                )
                logger.info(
                    "Decoded scores/classes (first 3): {}",
                    list(
                        zip(
                            confs[:3].round(3).tolist(),
                            class_ids[:3].tolist(),
                        )
                    ),
                )

            for box, score, class_id in zip(boxes, confs, class_ids, strict=False):
                if score < conf_threshold:
                    continue
                x1, y1, x2, y2 = box
                x1 = (x1 - pad_x) / scale
                y1 = (y1 - pad_y) / scale
                x2 = (x2 - pad_x) / scale
                y2 = (y2 - pad_y) / scale
                detections.append(
                    {
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "score": float(score),
                        "class_id": int(class_id),
                    }
                )
            return detections, classification

    data = output
    if data.ndim == 3:
        if data.shape[0] == 1:
            data = data[0]
        elif data.shape[0] > 1 and data.shape[-1] == 1:
            data = np.squeeze(data, axis=-1)

    if data.ndim == 2 and data.shape[0] < data.shape[1] and data.shape[1] >= 6:
        data = data.T

    if data.ndim != 2 or data.shape[1] < 6:
        return detections, classification

    channels = data.shape[1]
    height, width = input_size

    if channels == 6:
        boxes = data[:, :4]
        scores = data[:, 4]
        class_ids = data[:, 5].astype(int)
    else:
        boxes = data[:, :4]
        if channels >= 85:
            obj = data[:, 4]
            class_scores = data[:, 5:]
            class_ids = np.argmax(class_scores, axis=1)
            scores = obj * class_scores[np.arange(len(class_ids)), class_ids]
        else:
            class_scores = data[:, 4:]
            class_ids = np.argmax(class_scores, axis=1)
            scores = class_scores[np.arange(len(class_ids)), class_ids]

    if np.max(boxes) <= 1.5:
        boxes = boxes * np.array([width, height, width, height], dtype=np.float32)

    if channels != 6 or _looks_like_xywh(boxes):
        boxes = _xywh_to_xyxy(boxes)

    if debug_boxes:
        logger.info(
            "Decoded boxes (first 3): {}",
            boxes[:3].round(2).tolist(),
        )
        logger.info(
            "Decoded scores/classes (first 3): {}",
            list(zip(scores[:3].round(3).tolist(), class_ids[:3].tolist())),
        )

    for box, score, class_id in zip(boxes, scores, class_ids, strict=False):
        if score < conf_threshold:
            continue
        x1, y1, x2, y2 = box

        x1 = (x1 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        x2 = (x2 - pad_x) / scale
        y2 = (y2 - pad_y) / scale

        detections.append(
            {
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "score": float(score),
                "class_id": int(class_id),
            }
        )

    return detections, classification


@dataclass
class Track:
    track_id: int
    points_norm: Deque[Tuple[float, float]]
    last_seen_ts: float


class SimpleCentroidTracker:
    """Very lightweight centroid tracker for a single class (e.g. persons)."""

    def __init__(
        self,
        *,
        max_age_s: float = 0.75,
        max_match_dist_norm: float = 0.08,
        max_trail_points: int = 40,
    ) -> None:
        self._max_age_s = float(max_age_s)
        self._max_match_dist_norm = float(max_match_dist_norm)
        self._max_trail_points = int(max_trail_points)

        self._next_id = 1
        self._tracks: Dict[int, Track] = {}

    def update(
        self, centroids_norm: List[Tuple[float, float]], now_ts: float
    ) -> Dict[int, Track]:
        expired_ids = [
            tid
            for tid, tr in self._tracks.items()
            if (now_ts - tr.last_seen_ts) > self._max_age_s
        ]
        for tid in expired_ids:
            self._tracks.pop(tid, None)

        if not centroids_norm:
            return self._tracks

        if not self._tracks:
            for centroid in centroids_norm:
                self._tracks[self._next_id] = Track(
                    track_id=self._next_id,
                    points_norm=deque([centroid], maxlen=self._max_trail_points),
                    last_seen_ts=now_ts,
                )
                self._next_id += 1
            return self._tracks

        track_ids = list(self._tracks.keys())
        prev_centroids = [self._tracks[tid].points_norm[-1] for tid in track_ids]

        candidates: List[Tuple[float, int, int]] = []
        for ti, (px, py) in enumerate(prev_centroids):
            for di, (cx, cy) in enumerate(centroids_norm):
                dist = float(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5)
                candidates.append((dist, ti, di))
        candidates.sort(key=lambda item: item[0])

        used_tracks = set()
        used_dets = set()

        for dist, ti, di in candidates:
            if dist > self._max_match_dist_norm:
                break
            if ti in used_tracks or di in used_dets:
                continue
            tid = track_ids[ti]
            self._tracks[tid].points_norm.append(centroids_norm[di])
            self._tracks[tid].last_seen_ts = now_ts
            used_tracks.add(ti)
            used_dets.add(di)

        for di, centroid in enumerate(centroids_norm):
            if di in used_dets:
                continue
            self._tracks[self._next_id] = Track(
                track_id=self._next_id,
                points_norm=deque([centroid], maxlen=self._max_trail_points),
                last_seen_ts=now_ts,
            )
            self._next_id += 1

        return self._tracks


def _track_color(track_id: int) -> Tuple[int, int, int]:
    r = (track_id * 97) % 255
    g = (track_id * 57) % 255
    b = (track_id * 17) % 255
    return int(b), int(g), int(r)


def draw_2d_running_map(
    frame: np.ndarray,
    tracks: Dict[int, Track],
    *,
    map_size: int = 260,
    margin: int = 10,
) -> None:
    """Draw a simple top-down minimap of tracked centroids with motion trails."""

    if frame is None or frame.size == 0:
        return

    h, w = frame.shape[:2]
    size = int(map_size)
    x0 = max(margin, w - size - margin)
    y0 = margin
    x1 = min(w - margin, x0 + size)
    y1 = min(h - margin, y0 + size)

    cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 0), -1)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (100, 100, 100), 1)
    cv2.putText(
        frame,
        "2D running (persons)",
        (x0 + 8, y0 + 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )

    for tick in (0.25, 0.5, 0.75):
        xt = int(x0 + tick * (x1 - x0))
        yt = int(y0 + tick * (y1 - y0))
        cv2.line(frame, (xt, y0 + 24), (xt, y1), (35, 35, 35), 1)
        cv2.line(frame, (x0, yt), (x1, yt), (35, 35, 35), 1)

    usable_top = y0 + 26
    usable_h = max(1, y1 - usable_top)
    usable_w = max(1, x1 - x0)

    for tid, track in tracks.items():
        pts = list(track.points_norm)
        if not pts:
            continue

        color = _track_color(tid)
        poly: List[Tuple[int, int]] = []
        for xn, yn in pts:
            px = int(x0 + np.clip(xn, 0.0, 1.0) * (usable_w - 1))
            py = int(usable_top + np.clip(yn, 0.0, 1.0) * (usable_h - 1))
            poly.append((px, py))

        if len(poly) >= 2:
            cv2.polylines(frame, [np.array(poly, dtype=np.int32)], False, color, 2)

        cx, cy = poly[-1]
        cv2.circle(frame, (cx, cy), 4, color, -1)
        cv2.putText(
            frame,
            str(tid),
            (cx + 6, cy - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )


def draw_cpu_process_history_plot(
    frame: np.ndarray,
    cpu_history: Deque[float],
    *,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    """Draw a simple 2D line chart of process CPU% history inside a rectangle."""

    if frame is None or frame.size == 0:
        return

    if w < 30 or h < 20:
        return

    if cpu_history is None or len(cpu_history) < 1:
        return

    x0, y0 = int(x), int(y)
    x1, y1 = int(x + w), int(y + h)

    cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 0), -1)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (100, 100, 100), 1)

    for tick in (0.25, 0.5, 0.75):
        xt = int(x0 + tick * (x1 - x0))
        yt = int(y0 + tick * (y1 - y0))
        cv2.line(frame, (xt, y0), (xt, y1), (35, 35, 35), 1)
        cv2.line(frame, (x0, yt), (x1, yt), (35, 35, 35), 1)

    values = list(cpu_history)
    n = len(values)

    values = [float(np.clip(v, 0.0, 100.0)) for v in values]

    poly: List[Tuple[int, int]] = []
    for i, value in enumerate(values):
        xi = x0 + int(round(i * (w - 1) / max(1, n - 1)))
        yi = y1 - int(round((value / 100.0) * (h - 1)))
        poly.append((xi, yi))

    last_v = values[-1]
    color = get_color_by_percent(last_v)

    if len(poly) >= 2:
        cv2.polylines(frame, [np.array(poly, dtype=np.int32)], False, color, 2)
    cv2.circle(frame, poly[-1], 3, color, -1)

    label = f"Proc CPU history ({n}): {last_v:.1f}%"
    cv2.putText(
        frame,
        label,
        (x0 + 6, y0 - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )


def get_color_by_percent(percent: float, invert: bool = False) -> tuple:
    """Return color based on percentage value."""

    if invert:
        if percent > 50:
            return (0, 255, 0)
        if percent > 20:
            return (0, 165, 255)
        return (0, 0, 255)
    if percent < 70:
        return (0, 255, 0)
    if percent < 90:
        return (0, 165, 255)
    return (0, 0, 255)


def draw_detections(
    frame: np.ndarray,
    detections: list,
    perf_metrics: PerformanceMetrics,
    sys_stats: SystemStats,
    proc_stats: dict,
    camera_info: dict,
    cpu_history: Optional[Deque[float]] = None,
    classification: Optional[dict] = None,
    tracks: Optional[Dict[int, Track]] = None,
    map_size: int = 260,
    debug_boxes: bool = False,
) -> np.ndarray:
    """Draw bounding boxes, labels, FPS, and system stats on frame."""

    h, w = frame.shape[:2]

    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        class_id = int(det["class_id"])
        score = float(det["score"])

        x1 = int(np.clip(x1, 0, w - 1))
        y1 = int(np.clip(y1, 0, h - 1))
        x2 = int(np.clip(x2, 0, w - 1))
        y2 = int(np.clip(y2, 0, h - 1))
        if x2 <= x1 or y2 <= y1:
            if debug_boxes:
                logger.info(
                    "Skipping invalid bbox: {}",
                    [x1, y1, x2, y2],
                )
            continue

        color = tuple(map(int, COLORS[class_id % len(COLORS)]))
        if class_id < len(CLASS_NAMES):
            label = f"{CLASS_NAMES[class_id]}: {score:.2f}"
        else:
            label = f"class {class_id}: {score:.2f}"

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 10), (x1 + tw, y1), color, -1)
        cv2.putText(
            frame,
            label,
            (x1, y1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )

    panel_height = 320
    panel_width = 450
    cv2.rectangle(frame, (5, 5), (panel_width, panel_height), (0, 0, 0), -1)
    cv2.rectangle(frame, (5, 5), (panel_width, panel_height), (100, 100, 100), 1)

    y_offset = 25
    line_height = 22

    backend_display = camera_info["backend"]
    cv2.putText(
        frame,
        f"--- Capture: {backend_display} ---",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (150, 150, 255),
        1,
    )
    y_offset += line_height

    cv2.putText(
        frame,
        "--- Performance ---",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (150, 150, 150),
        1,
    )
    y_offset += line_height

    cv2.putText(
        frame,
        f"Camera Input: {perf_metrics.camera_fps:.1f} FPS",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 0),
        1,
    )
    y_offset += line_height

    cv2.putText(
        frame,
        f"Inference Latency: {perf_metrics.inference_ms:.1f} ms/frame",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 255),
        1,
    )
    y_offset += line_height

    budget_color = get_color_by_percent(perf_metrics.frame_budget_percent)
    cv2.putText(
        frame,
        f"Frame Budget Used: {perf_metrics.frame_budget_percent:.1f}%",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        budget_color,
        1,
    )
    y_offset += line_height

    headroom = 100 - perf_metrics.frame_budget_percent
    headroom_color = get_color_by_percent(headroom, invert=True)
    cv2.putText(
        frame,
        f"GPU Headroom: {headroom:.0f}% (capacity: {perf_metrics.inference_capacity_fps:.0f} FPS)",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        headroom_color,
        1,
    )
    y_offset += line_height + 5

    cv2.putText(
        frame,
        "--- System ---",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (150, 150, 150),
        1,
    )
    y_offset += line_height

    cv2.putText(
        frame,
        f"System CPU: {sys_stats.cpu_percent:.1f}%",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        get_color_by_percent(sys_stats.cpu_percent),
        1,
    )
    y_offset += line_height

    cv2.putText(
        frame,
        f"System RAM: {sys_stats.ram_used_gb:.1f}/{sys_stats.ram_total_gb:.1f} GB ({sys_stats.ram_percent:.1f}%)",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        get_color_by_percent(sys_stats.ram_percent),
        1,
    )
    y_offset += line_height

    if sys_stats.gpu_name != "N/A":
        cv2.putText(
            frame,
            f"GPU Load: {sys_stats.gpu_percent:.0f}%  |  Temp: {sys_stats.gpu_temp_celsius:.0f}C  |  {sys_stats.gpu_power_watts:.0f}W",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            get_color_by_percent(sys_stats.gpu_percent),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            f"VRAM: {sys_stats.gpu_memory_used_gb:.1f}/{sys_stats.gpu_memory_total_gb:.1f} GB ({sys_stats.gpu_memory_percent:.0f}%)",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            get_color_by_percent(sys_stats.gpu_memory_percent),
            1,
        )
        y_offset += line_height + 5

    cv2.putText(
        frame,
        "--- Process ---",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (150, 150, 150),
        1,
    )
    y_offset += line_height

    cv2.putText(
        frame,
        f"CPU: {proc_stats['cpu_percent']:.1f}%  |  RAM: {proc_stats['memory_mb']:.0f}MB  |  Threads: {proc_stats['threads']}",
        (10, y_offset),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        get_color_by_percent(min(proc_stats["cpu_percent"], 100)),
        1,
    )

    bar_y = panel_height - 15
    bar_width = panel_width - 20
    bar_height = 8

    if cpu_history is not None:
        overlay_w = 320
        overlay_h = 110
        margin = 10
        overlay_x = max(margin, frame.shape[1] - overlay_w - margin)
        overlay_y = 40
        draw_cpu_process_history_plot(
            frame,
            cpu_history,
            x=overlay_x,
            y=overlay_y,
            w=overlay_w,
            h=overlay_h,
        )

    det_text = f"Detections: {len(detections)}"
    (tw, th), _ = cv2.getTextSize(det_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.putText(
        frame,
        det_text,
        (frame.shape[1] - tw - 10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 255),
        2,
    )

    if classification is not None:
        class_label = classification.get("label", "unknown")
        class_score = classification.get("score", 0.0)
        cls_text = f"Class: {class_label} ({class_score:.2f})"
        (ctw, cth), _ = cv2.getTextSize(cls_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.putText(
            frame,
            cls_text,
            (frame.shape[1] - ctw - 10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )

    cv2.rectangle(
        frame, (10, bar_y), (10 + bar_width, bar_y + bar_height), (50, 50, 50), -1
    )
    fill_width = int(bar_width * min(perf_metrics.frame_budget_percent, 100) / 100)
    cv2.rectangle(
        frame,
        (10, bar_y),
        (10 + fill_width, bar_y + bar_height),
        budget_color,
        -1,
    )
    cv2.rectangle(
        frame,
        (10, bar_y),
        (10 + bar_width, bar_y + bar_height),
        (100, 100, 100),
        1,
    )

    if tracks:
        draw_2d_running_map(frame, tracks, map_size=map_size)

    return frame


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
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


def run_yolo_monitor(argv: Optional[List[str]] = None) -> int:
    """Entry point for running the YOLOv10 monitor."""

    args = parse_args(argv)
    configure_logging(args.log_level)

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
                outputs,
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
        cv2.destroyAllWindows()
        sys_monitor.shutdown()
        logger.success("Cleanup complete. Goodbye!")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_yolo_monitor())
