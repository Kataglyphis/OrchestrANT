"""GStreamer subprocess capture backend."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

from orchestr_ant_ion.pipeline.constants import (
    GST_DEFAULT_TIMEOUT_SECONDS,
    GST_FALLBACK_HEIGHT,
    GST_FALLBACK_WIDTH,
    GST_FRAME_QUEUE_TIMEOUT,
    GST_PROCESS_STARTUP_DELAY,
    GST_PROCESS_WAIT_TIMEOUT,
    WINDOWS_GSTREAMER_PATHS,
)


if TYPE_CHECKING:
    from orchestr_ant_ion.pipeline.types import CameraConfig


def find_gstreamer_launch() -> tuple[str | None, str]:
    """Find the gst-launch-1.0 executable."""
    exe_name = (
        "gst-launch-1.0.exe" if platform.system() == "Windows" else "gst-launch-1.0"
    )

    resolved = shutil.which("gst-launch-1.0")
    if resolved:
        return resolved, "Found in PATH"

    if platform.system() == "Windows":
        config_paths = _get_config_gstreamer_paths()
        search_paths = config_paths or WINDOWS_GSTREAMER_PATHS
        for base_path in search_paths:
            exe_path = Path(base_path) / "bin" / exe_name
            if exe_path.exists():
                return str(exe_path), f"Found at {exe_path}"

    return None, "gst-launch-1.0 not found"


def _get_config_gstreamer_paths() -> list[str] | None:
    """Get GStreamer paths from environment configuration."""
    env_path = os.environ.get("KATAGLYPHIS_GSTREAMER_PATHS", "")
    if not env_path:
        return None
    return [p.strip() for p in env_path.split(os.pathsep) if p.strip()]


def get_gstreamer_env() -> dict[str, str]:
    """Get minimal environment variables needed for GStreamer.

    Only allowlists specific GStreamer-related environment variables.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "TMPDIR": os.environ.get("TMPDIR", ""),
        "TEMP": os.environ.get("TEMP", ""),
    }

    if platform.system() != "Windows":
        return env

    search_paths = _get_config_gstreamer_paths() or [
        p for p in WINDOWS_GSTREAMER_PATHS if Path(p).exists()
    ]

    for gst_root in search_paths:
        root_path = Path(gst_root)
        if not root_path.exists():
            continue
        bin_path = root_path / "bin"
        lib_path = root_path / "lib"
        plugin_path = lib_path / "gstreamer-1.0"

        current_path = env.get("PATH", "")
        if str(bin_path) not in current_path:
            env["PATH"] = str(bin_path) + os.pathsep + current_path

        env["GST_PLUGIN_PATH"] = str(plugin_path)
        env["GST_PLUGIN_SYSTEM_PATH"] = str(plugin_path)

        logger.debug("GStreamer environment configured from: {}", root_path)
        break

    return env


class GStreamerSubprocessCapture:
    """GStreamer video capture using subprocess and raw frames on stdout."""

    def __init__(self, config: CameraConfig) -> None:
        """Initialize the capture backend."""
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self.frame_queue: Queue[np.ndarray] = Queue(maxsize=2)
        self.running = False
        self.reader_thread: threading.Thread | None = None
        self._frame_buffer: np.ndarray | None = None

        self.actual_width = config.width
        self.actual_height = config.height
        self.actual_fps = float(config.fps)
        self.pipeline_string = ""
        self.frame_size = 0

        self.gst_launch_path, self.gst_status = find_gstreamer_launch()
        self.gst_env = get_gstreamer_env()

    def _allocate_frame_buffer(self) -> None:
        """Pre-allocate reusable frame buffer for zero-copy reading."""
        frame_size = self.actual_width * self.actual_height * 3
        self._frame_buffer = np.empty(frame_size, dtype=np.uint8)

    def _build_pipeline_string(
        self,
        source: str,
        width: int,
        height: int,
        fps: int,
        pipeline_type: str = "strict",
        *,
        with_device: bool = True,
        include_size: bool = True,
        include_fps: bool = True,
        include_format: bool = True,
    ) -> str:
        """Build a GStreamer pipeline string for the requested settings."""
        sink = "fdsink fd=1 sync=false"
        device = f" device-index={self.config.device_index}" if with_device else ""
        source_prefix = f"{source}{device}"

        caps_parts = []
        if include_size:
            caps_parts.append(f"width={width}")
            caps_parts.append(f"height={height}")
        if include_fps:
            caps_parts.append(f"framerate={fps}/1")
        if include_format:
            caps_parts.append("format=BGR")

        caps = f"video/x-raw,{','.join(caps_parts)}" if caps_parts else "video/x-raw"

        if pipeline_type == "strict":
            return f"{source_prefix} ! {caps} ! videoconvert ! {caps} ! {sink}"
        if pipeline_type == "flexible":
            return (
                f"{source_prefix} ! "
                f"videoconvert ! videoscale ! "
                f"{caps} ! "
                f"videorate ! {caps} ! "
                f"videoconvert ! {caps} ! {sink}"
            )
        return f"{source_prefix} ! videoconvert ! videoscale ! {caps} ! {sink}"

    def _frame_reader(self) -> None:
        frame_size = self.actual_width * self.actual_height * 3

        while self.running and self.process and self.process.poll() is None:
            try:
                if self.process.stdout is None:
                    break

                if (
                    self._frame_buffer is not None
                    and self._frame_buffer.size == frame_size
                ):
                    stdout = self.process.stdout
                    if hasattr(stdout, "readinto"):
                        bytes_read = stdout.readinto(self._frame_buffer)  # type: ignore[union-attr]
                        if bytes_read != frame_size:
                            if bytes_read == 0:
                                logger.warning("GStreamer process ended (no data)")
                                break
                            logger.warning(
                                "Incomplete frame: {}/{}", bytes_read, frame_size
                            )
                            continue
                        frame = self._frame_buffer.reshape(
                            (self.actual_height, self.actual_width, 3)
                        )
                    else:
                        raw_data = stdout.read(frame_size)
                        if len(raw_data) != frame_size:
                            if len(raw_data) == 0:
                                logger.warning("GStreamer process ended (no data)")
                                break
                            logger.warning(
                                "Incomplete frame: {}/{}", len(raw_data), frame_size
                            )
                            continue
                        frame = np.frombuffer(raw_data, dtype=np.uint8).reshape(
                            (self.actual_height, self.actual_width, 3)
                        )
                else:
                    raw_data = self.process.stdout.read(frame_size)
                    if len(raw_data) != frame_size:
                        if len(raw_data) == 0:
                            logger.warning("GStreamer process ended (no data)")
                            break
                        logger.warning(
                            "Incomplete frame: {}/{}", len(raw_data), frame_size
                        )
                        continue
                    frame = np.frombuffer(raw_data, dtype=np.uint8).reshape(
                        (self.actual_height, self.actual_width, 3)
                    )

                if self.frame_queue.full():
                    with suppress(Empty):
                        self.frame_queue.get_nowait()

                self.frame_queue.put(np.ascontiguousarray(frame), block=False)

            except (OSError, ValueError) as exc:
                if self.running:
                    logger.error("Frame reader error: {}", exc)
                break

        self.running = False

    def _start_pipeline(self, pipeline_str: str) -> bool:
        if self.gst_launch_path is None:
            return False

        cmd = [self.gst_launch_path, "-q", *shlex.split(pipeline_str)]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=self.gst_env,
            )
        except OSError as exc:
            logger.warning("Failed to start gst-launch: {}", exc)
            self.process = None
            return False

        time.sleep(GST_PROCESS_STARTUP_DELAY)
        if self.process.poll() is not None:
            stderr_text = ""
            if self.process.stderr is not None:
                stderr_text = self.process.stderr.read().decode(
                    "utf-8", errors="ignore"
                )
            logger.warning("Pipeline failed: {}", stderr_text[:300])
            self.release()
            return False

        self.running = True
        self.reader_thread = threading.Thread(target=self._frame_reader, daemon=True)
        self.reader_thread.start()

        try:
            frame = self.frame_queue.get(timeout=GST_FRAME_QUEUE_TIMEOUT)
        except Empty:
            logger.warning("No frames from pipeline")
            self.release()
            return False

        if frame.shape[0] != self.actual_height or frame.shape[1] != self.actual_width:
            logger.info("Adjusting dimensions: {}", frame.shape[:2])
            self.actual_height, self.actual_width = frame.shape[:2]
            self.frame_size = self.actual_width * self.actual_height * 3

        self.frame_queue.put(frame)
        self._allocate_frame_buffer()
        return True

    def _try_pipelines(
        self,
        sources: list[tuple[str, bool]],
        specs: list[tuple[str, bool, bool, bool]],
        width: int,
        height: int,
        fps: int,
        deadline: float,
    ) -> bool:
        """Try multiple pipeline configurations until one succeeds or deadline passes."""
        for source, with_device in sources:
            for pipeline_type, include_size, include_fps, include_format in specs:
                if time.monotonic() > deadline:
                    return False

                pipeline_str = self._build_pipeline_string(
                    source,
                    width,
                    height,
                    fps,
                    pipeline_type,
                    with_device=with_device,
                    include_size=include_size,
                    include_fps=include_fps,
                    include_format=include_format,
                )
                logger.debug("Pipeline: {}", pipeline_str)

                if self._start_pipeline(pipeline_str):
                    self.pipeline_string = pipeline_str
                    return True

                logger.warning("Pipeline failed: {} ({})", pipeline_type, source)
        return False

    def open(self, timeout: float = GST_DEFAULT_TIMEOUT_SECONDS) -> bool:
        """Start the GStreamer subprocess and begin frame capture.

        Args:
            timeout: Maximum wall-clock seconds to spend trying pipelines.
        """
        if self.gst_launch_path is None:
            logger.error("GStreamer not available: {}", self.gst_status)
            return False

        logger.info("GStreamer: {}", self.gst_status)
        logger.debug("Executable: {}", self.gst_launch_path)

        self.frame_size = self.actual_width * self.actual_height * 3
        deadline = time.monotonic() + timeout

        sources: list[tuple[str, bool]] = [
            ("mfvideosrc", True),
            ("ksvideosrc", True),
            ("dshowvideosrc", False),
            ("autovideosrc", False),
        ]
        pipeline_specs: list[tuple[str, bool, bool, bool]] = [
            ("strict", True, True, True),
            ("strict", True, False, True),
            ("strict", False, False, True),
            ("strict", False, False, False),
            ("flexible", True, True, True),
            ("flexible", True, False, True),
            ("flexible", False, False, True),
            ("auto", True, True, True),
            ("auto", False, False, True),
            ("auto", False, False, False),
        ]

        if self._try_pipelines(
            sources,
            pipeline_specs,
            self.actual_width,
            self.actual_height,
            int(self.actual_fps),
            deadline,
        ):
            logger.success(
                "GStreamer started: {}x{}", self.actual_width, self.actual_height
            )
            return True

        if time.monotonic() > deadline:
            logger.warning("GStreamer pipeline search timed out after {}s", timeout)
            return False

        fallback_width = GST_FALLBACK_WIDTH
        fallback_height = GST_FALLBACK_HEIGHT
        fallback_fps = int(self.actual_fps) if self.actual_fps > 0 else 30
        logger.info("Trying fallback pipeline ({}x{})", fallback_width, fallback_height)

        self.actual_width = fallback_width
        self.actual_height = fallback_height
        self.frame_size = self.actual_width * self.actual_height * 3

        if self._try_pipelines(
            sources,
            pipeline_specs,
            fallback_width,
            fallback_height,
            fallback_fps,
            deadline,
        ):
            logger.success(
                "GStreamer started (fallback): {}x{}",
                self.actual_width,
                self.actual_height,
            )
            return True

        logger.error("All GStreamer pipelines failed!")
        return False

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Read a frame from the capture queue."""
        if not self.running:
            return False, None

        try:
            frame = self.frame_queue.get(timeout=1.0)
        except Empty:
            return False, None
        else:
            return True, frame

    def release(self) -> None:
        """Stop capture and release subprocess resources."""
        self.running = False
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=GST_PROCESS_WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception as exc:
                logger.debug("Failed to terminate GStreamer process: {}", exc)
            self.process = None

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)

        while not self.frame_queue.empty():
            with suppress(Empty):
                self.frame_queue.get_nowait()

    def is_opened(self) -> bool:
        """Return True if the subprocess is running."""
        return self.running and self.process is not None and self.process.poll() is None

    def get_info(self) -> dict:
        """Return backend metadata for diagnostics."""
        return {
            "backend": "GStreamer (subprocess)",
            "pipeline": self.pipeline_string,
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
        }
