from __future__ import annotations

import os
import platform
import shlex
import subprocess
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Optional, Tuple

import numpy as np
from loguru import logger

from kataglyphispythoninference.yolo.types import CameraConfig


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
                except Exception as exc:
                    logger.debug(
                        "Failed to probe GStreamer binary at {}: {}", exe_path, exc
                    )
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

            cmd = [self.gst_launch_path, "-q", *shlex.split(pipeline_str)]

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
        cmd = [self.gst_launch_path, "-q", *shlex.split(pipeline_str)]

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
